-- DAV Console schema — append-only, idempotent.
-- Advisory lock prevents concurrent startup races on CREATE TABLE.

BEGIN;
SELECT pg_advisory_xact_lock(7402983);

-- ── Legacy corpus review tables (kept for backward compat) ─────────────

CREATE TABLE IF NOT EXISTS files (
  path            TEXT PRIMARY KEY,
  content         TEXT NOT NULL,
  content_sha256  TEXT NOT NULL,
  size_bytes      INTEGER NOT NULL,
  folder          TEXT NOT NULL,
  first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder);

CREATE TABLE IF NOT EXISTS review_events (
  id                      BIGSERIAL PRIMARY KEY,
  file_path               TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
  reviewer                TEXT NOT NULL,
  action                  TEXT NOT NULL CHECK (action IN ('review','update','clear')),
  status                  TEXT CHECK (status IN ('unreviewed','in-review','needs-work','approved','stale')),
  notes                   TEXT,
  file_sha256_at_review   TEXT,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_review_events_file_created  ON review_events(file_path, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_events_reviewer_created ON review_events(reviewer, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_events_created ON review_events(created_at DESC);

CREATE OR REPLACE VIEW review_current AS
WITH latest AS (
  SELECT DISTINCT ON (file_path, reviewer)
    file_path, reviewer, action, status, notes, file_sha256_at_review,
    created_at AS reviewed_at
  FROM review_events
  ORDER BY file_path, reviewer, created_at DESC
)
SELECT file_path, reviewer, status, notes, file_sha256_at_review, reviewed_at
FROM latest WHERE action <> 'clear';

CREATE OR REPLACE VIEW review_drift AS
SELECT rc.file_path, rc.reviewer, rc.status, rc.reviewed_at,
       rc.file_sha256_at_review, f.content_sha256 AS current_sha256,
       (rc.file_sha256_at_review IS DISTINCT FROM f.content_sha256) AS is_drifted
FROM review_current rc
JOIN files f ON f.path = rc.file_path;

CREATE OR REPLACE VIEW file_current_status AS
SELECT DISTINCT ON (file_path)
  file_path, status, reviewer, reviewed_at, file_sha256_at_review
FROM review_current
ORDER BY file_path, reviewed_at DESC;

-- ── Managed use cases ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS managed_use_cases (
  uuid            TEXT PRIMARY KEY,
  title           TEXT NOT NULL DEFAULT '',
  yaml_content    TEXT NOT NULL,
  lifecycle_state TEXT NOT NULL DEFAULT 'draft',
  created_by      TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by      TEXT NOT NULL,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  tags            TEXT[] NOT NULL DEFAULT '{}'
);
-- Migrate existing deployments that predate lifecycle_state.
-- Must run before the index on lifecycle_state below.
ALTER TABLE managed_use_cases ADD COLUMN IF NOT EXISTS lifecycle_state TEXT NOT NULL DEFAULT 'draft';
-- UC priority (roadmap weighting, spec 05 §6.8). Projected from yaml_content on
-- write, like title/tags. `priority` holds the label (NULL = unranked);
-- `priority_score` is the 0-100 roadmap weight used for sorting (higher first).
ALTER TABLE managed_use_cases ADD COLUMN IF NOT EXISTS priority       TEXT;
ALTER TABLE managed_use_cases ADD COLUMN IF NOT EXISTS priority_score INTEGER;
-- UC definition readiness (DCM feature #4): 0-100 quality score projected from
-- yaml_content at save, so the list/Set views can show it without re-scoring.
ALTER TABLE managed_use_cases ADD COLUMN IF NOT EXISTS readiness_score INTEGER;
CREATE INDEX IF NOT EXISTS idx_managed_uc_updated ON managed_use_cases(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_managed_uc_state   ON managed_use_cases(lifecycle_state);
-- Priority-ordered roadmap views sort by weight desc, unranked (NULL) last.
CREATE INDEX IF NOT EXISTS idx_managed_uc_priority ON managed_use_cases(priority_score DESC NULLS LAST);

-- ── Lifecycle event log ──────────────────────────────────────────────────
-- Append-only audit trail for UC state transitions.

CREATE TABLE IF NOT EXISTS lifecycle_events (
  id          BIGSERIAL PRIMARY KEY,
  uc_uuid     TEXT NOT NULL REFERENCES managed_use_cases(uuid) ON DELETE CASCADE,
  from_state  TEXT,
  to_state    TEXT NOT NULL,
  actor       TEXT NOT NULL,
  notes       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lifecycle_events_uc ON lifecycle_events(uc_uuid, created_at DESC);

-- ── Named UC sets ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS use_case_sets (
  id          BIGSERIAL PRIMARY KEY,
  name        TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_by  TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_uc_sets_name ON use_case_sets(lower(name));

CREATE TABLE IF NOT EXISTS use_case_set_members (
  set_id     BIGINT NOT NULL REFERENCES use_case_sets(id) ON DELETE CASCADE,
  uc_uuid    TEXT NOT NULL,
  uc_source  TEXT NOT NULL DEFAULT 'managed',
  uc_handle  TEXT,
  uc_path    TEXT,
  added_by   TEXT NOT NULL,
  added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (set_id, uc_uuid)
);
CREATE INDEX IF NOT EXISTS idx_set_members_uc ON use_case_set_members(uc_uuid);

-- ── Run sessions ─────────────────────────────────────────────────────────
-- User-facing metadata + accumulated runtime stats for each Tekton
-- PipelineRun triggered from the console. The PipelineRun itself stays
-- the source of truth for params + phase; this table adds:
--   - human-meaningful name / description / category for sorting + filtering
--   - resource accounting (GPU energy, peak/avg power, tokens, wall time)
-- Stats are computed lazily on first run-detail view after the run
-- reaches a terminal phase (Succeeded/Failed/Cancelled/TimedOut).

CREATE TABLE IF NOT EXISTS run_sessions (
  run_name           TEXT PRIMARY KEY,    -- Tekton PipelineRun name
  name               TEXT NOT NULL DEFAULT '',
  description        TEXT NOT NULL DEFAULT '',
  category           TEXT NOT NULL DEFAULT 'ad-hoc',
  tags               TEXT[] NOT NULL DEFAULT '{}',
  mode               TEXT,                -- verification / reproduce / explore
  created_by         TEXT NOT NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at         TIMESTAMPTZ,
  completed_at       TIMESTAMPTZ,
  phase              TEXT,
  wall_time_seconds  DOUBLE PRECISION,
  -- GPU + inference resource accounting (filled in at finalization)
  gpu_energy_joules     DOUBLE PRECISION,
  gpu_avg_power_watts   DOUBLE PRECISION,
  gpu_peak_power_watts  DOUBLE PRECISION,
  gpu_avg_gfx_activity  DOUBLE PRECISION,
  total_prompt_tokens   BIGINT,
  total_gen_tokens      BIGINT,
  -- Counter snapshots captured at trigger time so the run-detail drawer can
  -- compute live session deltas (current counter - baseline) without
  -- resetting on page reload. These are the absolute vllm:*_tokens_total
  -- values observed when the PipelineRun was created.
  baseline_gen_tokens   DOUBLE PRECISION,
  baseline_prompt_tokens DOUBLE PRECISION,
  uc_total              INTEGER,
  uc_succeeded          INTEGER,
  uc_failed             INTEGER,
  finalized_at          TIMESTAMPTZ        -- when stats were computed
);
-- Add baseline columns to existing tables that predate them.
ALTER TABLE run_sessions ADD COLUMN IF NOT EXISTS baseline_gen_tokens DOUBLE PRECISION;
ALTER TABLE run_sessions ADD COLUMN IF NOT EXISTS baseline_prompt_tokens DOUBLE PRECISION;
CREATE INDEX IF NOT EXISTS idx_run_sessions_created   ON run_sessions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_sessions_category  ON run_sessions(category);
CREATE INDEX IF NOT EXISTS idx_run_sessions_phase     ON run_sessions(phase);

-- ── Analysis ingestion ──────────────────────────────────────────────────
-- Structured storage for per-run + per-UC + per-gap analysis results read
-- from the workspace PVC. Enables cross-run gap aggregation + trend queries.

CREATE TABLE IF NOT EXISTS analysis_runs (
  run_id           TEXT PRIMARY KEY,      -- workspace run directory name
  run_name         TEXT,                  -- Tekton PipelineRun name (may be NULL)
  mode             TEXT,
  started_at       TIMESTAMPTZ,
  finished_at      TIMESTAMPTZ,
  total_ucs        INTEGER,
  successful       INTEGER,
  failed           INTEGER,
  total_samples    INTEGER,
  ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_analysis_runs_started ON analysis_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS uc_analyses (
  id               BIGSERIAL PRIMARY KEY,
  run_id           TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
  uc_uuid          TEXT NOT NULL,
  uc_handle        TEXT,
  status           TEXT,                  -- 'success' | 'failed'
  verdict          TEXT,                  -- supported | partially_supported | not_supported | null
  overall_assessment TEXT,
  wall_time_seconds DOUBLE PRECISION,
  sample_count     INTEGER,
  engine_version   TEXT,
  model            TEXT,
  endpoint_url     TEXT,
  analyzed_at      TIMESTAMPTZ,
  ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_id, uc_uuid)
);
CREATE INDEX IF NOT EXISTS idx_uc_analyses_run  ON uc_analyses(run_id);
CREATE INDEX IF NOT EXISTS idx_uc_analyses_uuid ON uc_analyses(uc_uuid, ingested_at DESC);
CREATE INDEX IF NOT EXISTS idx_uc_analyses_verdict ON uc_analyses(verdict);

CREATE TABLE IF NOT EXISTS uc_gaps (
  id               BIGSERIAL PRIMARY KEY,
  analysis_id      BIGINT NOT NULL REFERENCES uc_analyses(id) ON DELETE CASCADE,
  run_id           TEXT NOT NULL,
  uc_uuid          TEXT NOT NULL,
  gap_id           TEXT,
  title            TEXT,
  description      TEXT,
  severity         TEXT,
  ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE uc_gaps ADD COLUMN IF NOT EXISTS recommendation TEXT;
ALTER TABLE uc_gaps ADD COLUMN IF NOT EXISTS rationale TEXT;
CREATE INDEX IF NOT EXISTS idx_uc_gaps_run    ON uc_gaps(run_id);
CREATE INDEX IF NOT EXISTS idx_uc_gaps_uuid   ON uc_gaps(uc_uuid);
CREATE INDEX IF NOT EXISTS idx_uc_gaps_gap_id ON uc_gaps(gap_id);

-- ── UC capabilities (DCM feature #2: cross-UC demand density) ─────────────
-- One row per capability a UC's analysis says it invokes. Projected from each
-- analysis's structured `capabilities_invoked` during ingest (mirrors uc_gaps).
-- Cleared with its run via the analysis_runs → uc_analyses CASCADE, so
-- re-ingestion stays idempotent. Aggregating capability_id across distinct
-- uc_uuids in a run/set yields "capability X demanded by N/M UCs".
CREATE TABLE IF NOT EXISTS uc_capabilities (
  id               BIGSERIAL PRIMARY KEY,
  analysis_id      BIGINT NOT NULL REFERENCES uc_analyses(id) ON DELETE CASCADE,
  run_id           TEXT NOT NULL,
  uc_uuid          TEXT NOT NULL,
  capability_id    TEXT NOT NULL,
  usage            TEXT,
  confidence       TEXT,                  -- confidence label (high/medium/low)
  confidence_score INTEGER,
  rationale        TEXT,
  namespace        TEXT,                  -- derived from spec_refs, like uc_gaps
  ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_uc_caps_run  ON uc_capabilities(run_id);
CREATE INDEX IF NOT EXISTS idx_uc_caps_uuid ON uc_capabilities(uc_uuid);
CREATE INDEX IF NOT EXISTS idx_uc_caps_cap  ON uc_capabilities(capability_id);

-- ── Capability dependency edges (DCM feature #3: foundational detection) ──────
-- One row per (capability depends_on other-capability) edge the engine emits,
-- projected at ingest. Edge points dependant → dependency (A requires B).
-- Aggregating these across a run and computing transitive dependents surfaces
-- foundational capabilities. Cleared with its run via the uc_analyses CASCADE.
CREATE TABLE IF NOT EXISTS uc_capability_deps (
  id               BIGSERIAL PRIMARY KEY,
  analysis_id      BIGINT NOT NULL REFERENCES uc_analyses(id) ON DELETE CASCADE,
  run_id           TEXT NOT NULL,
  uc_uuid          TEXT NOT NULL,
  capability_id    TEXT NOT NULL,   -- the dependant
  depends_on_id    TEXT NOT NULL,   -- the dependency it requires
  ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_uc_capdeps_run ON uc_capability_deps(run_id);
CREATE INDEX IF NOT EXISTS idx_uc_capdeps_cap ON uc_capability_deps(capability_id);

-- ── Centralized model configs ────────────────────────────────────────────────
-- All LLM endpoint registrations live here.  Per-endpoint use-flags control
-- which DAV features each model may be selected for.
-- api_key stored at rest; masked ('••••••••') on GET responses.

CREATE TABLE IF NOT EXISTS model_configs (
  id              BIGSERIAL PRIMARY KEY,
  name            TEXT NOT NULL,
  provider        TEXT NOT NULL CHECK (provider IN ('openai', 'anthropic')),
  endpoint_url    TEXT NOT NULL,
  model_id        TEXT NOT NULL,
  api_key         TEXT NOT NULL DEFAULT '',
  enabled         BOOLEAN NOT NULL DEFAULT true,
  is_local        BOOLEAN NOT NULL DEFAULT false,
  use_arch_review BOOLEAN NOT NULL DEFAULT true,
  use_uc_assist   BOOLEAN NOT NULL DEFAULT false,
  created_by      TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Name uniqueness is per-project; the composite index is created in the
-- project-scope block below (after project_id is added).

-- ── MCP server registry ─────────────────────────────────────────────────────
-- User-registered MCP servers (SSE transport) shown in the Integrations panel.
-- use_uc_assist controls whether this server's tools are surfaced in UC assist.
-- Health is polled on demand; no credentials stored here.

CREATE TABLE IF NOT EXISTS mcp_server_configs (
  id              BIGSERIAL PRIMARY KEY,
  name            TEXT NOT NULL,
  description     TEXT NOT NULL DEFAULT '',
  sse_url         TEXT NOT NULL,
  enabled         BOOLEAN NOT NULL DEFAULT true,
  use_uc_assist   BOOLEAN NOT NULL DEFAULT false,
  -- Fernet-encrypted bearer token DAV presents to the server (Authorization:
  -- Bearer …); masked on GET, never returned. Empty = no auth.
  auth_token_encrypted TEXT NOT NULL DEFAULT '',
  created_by      TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE mcp_server_configs ADD COLUMN IF NOT EXISTS auth_token_encrypted TEXT NOT NULL DEFAULT '';
-- Name uniqueness is per-project; composite index created in the project-scope
-- block below (after project_id is added).

-- ── Code repository configs ─────────────────────────────────────────────────
-- User-registered git repos for branch + PR/MR creation from enhancement findings.
-- token stored at rest; masked ('••••••••') on GET responses.

CREATE TABLE IF NOT EXISTS code_repo_configs (
  id              BIGSERIAL PRIMARY KEY,
  name            TEXT NOT NULL,
  provider        TEXT NOT NULL CHECK (provider IN ('github', 'gitlab')),
  repo_url        TEXT NOT NULL,
  default_branch  TEXT NOT NULL DEFAULT 'main',
  token           TEXT NOT NULL DEFAULT '',
  enabled         BOOLEAN NOT NULL DEFAULT true,
  created_by      TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_code_repos_name ON code_repo_configs(lower(name));

-- MCP servers are not statically seeded; dav-docs-mcp self-registers at boot
-- (see _seed_docs_mcp). openshift-mcp / frc-scheduler-mcp are not used by DAV.

-- ── Projects (tenancy foundation — uc-driven-roadmaps-design.md §9) ──────────
-- A project is a user-defined analysis scope. Tenancy-ready from birth: new
-- entities carry project_id. A 'default' project gives existing single-project
-- data a home; the full multi-project UX comes later.
CREATE TABLE IF NOT EXISTS projects (
  id          BIGSERIAL PRIMARY KEY,
  slug        TEXT UNIQUE NOT NULL,
  name        TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_by  TEXT NOT NULL DEFAULT 'system',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  archived    BOOLEAN NOT NULL DEFAULT false
);
INSERT INTO projects (slug, name, description, created_by)
  VALUES ('default', 'Default', 'Default project (auto-created)', 'system')
  ON CONFLICT (slug) DO NOTHING;

-- ── Project-scope the config registries ─────────────────────────────────────
-- model_configs / mcp_server_configs / managed_repos / model_defaults become
-- project-owned (strict isolation). Runs here (after `projects` exists) so it is
-- safe on both fresh and existing DBs, and idempotent (re-applied every boot).
-- Existing rows backfill to the DCM project (id 20) with a fallback to 'default'
-- so DBs lacking id 20 still satisfy NOT NULL.
DO $$
DECLARE _pid BIGINT;
BEGIN
  SELECT COALESCE((SELECT id FROM projects WHERE id=20),
                  (SELECT id FROM projects WHERE slug='default')) INTO _pid;

  -- model_configs
  ALTER TABLE model_configs ADD COLUMN IF NOT EXISTS project_id BIGINT REFERENCES projects(id);
  EXECUTE format('UPDATE model_configs SET project_id=%s WHERE project_id IS NULL', _pid);
  ALTER TABLE model_configs ALTER COLUMN project_id SET NOT NULL;

  -- mcp_server_configs
  ALTER TABLE mcp_server_configs ADD COLUMN IF NOT EXISTS project_id BIGINT REFERENCES projects(id);
  EXECUTE format('UPDATE mcp_server_configs SET project_id=%s WHERE project_id IS NULL', _pid);
  ALTER TABLE mcp_server_configs ALTER COLUMN project_id SET NOT NULL;

  -- managed_repos (keep tenant_id; project_id is the scoping key)
  ALTER TABLE managed_repos ADD COLUMN IF NOT EXISTS project_id BIGINT REFERENCES projects(id);
  EXECUTE format('UPDATE managed_repos SET project_id=%s WHERE project_id IS NULL', _pid);
  ALTER TABLE managed_repos ALTER COLUMN project_id SET NOT NULL;

  -- model_defaults: per-use default-model pointers become per-project
  ALTER TABLE model_defaults ADD COLUMN IF NOT EXISTS project_id BIGINT REFERENCES projects(id);
  EXECUTE format('UPDATE model_defaults SET project_id=%s WHERE project_id IS NULL', _pid);
  ALTER TABLE model_defaults ALTER COLUMN project_id SET NOT NULL;
  IF EXISTS (SELECT 1 FROM pg_constraint
             WHERE conname='model_defaults_pkey'
               AND conrelid='model_defaults'::regclass
               AND array_length(conkey,1)=1) THEN
    ALTER TABLE model_defaults DROP CONSTRAINT model_defaults_pkey;
    ALTER TABLE model_defaults ADD PRIMARY KEY (project_id, key);
  END IF;
END $$;
-- Per-project name uniqueness (retire any old GLOBAL unique on lower(name)).
DROP INDEX IF EXISTS idx_model_configs_name;
DROP INDEX IF EXISTS idx_mcp_servers_name;
CREATE UNIQUE INDEX IF NOT EXISTS idx_model_configs_proj_name ON model_configs(project_id, lower(name));
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_servers_proj_name   ON mcp_server_configs(project_id, lower(name));
CREATE INDEX IF NOT EXISTS idx_model_configs_project ON model_configs(project_id);
CREATE INDEX IF NOT EXISTS idx_mcp_servers_project   ON mcp_server_configs(project_id);
CREATE INDEX IF NOT EXISTS idx_managed_repos_project ON managed_repos(project_id);

-- MCP servers are no longer statically seeded here. openshift-mcp /
-- frc-scheduler-mcp are not used by DAV. dav-docs-mcp is self-registered at boot
-- (_seed_docs_mcp) from DAV_DOCS_MCP_URL/DAV_DOCS_MCP_TOKEN so it carries its
-- secured LoadBalancer URL + Fernet-encrypted bearer token.

-- Many-to-many users↔projects with a per-project role.
CREATE TABLE IF NOT EXISTS project_members (
  project_id  BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  reviewer    TEXT NOT NULL,
  role        TEXT NOT NULL DEFAULT 'member',
  added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, reviewer)
);

-- Per-stage context/instructions the architect injects to further inform the LLM
-- at each stage; scoped to the project ("saved as part of the project itself").
CREATE TABLE IF NOT EXISTS project_stage_context (
  project_id  BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  stage       TEXT NOT NULL,
  content     TEXT NOT NULL DEFAULT '',
  updated_by  TEXT NOT NULL DEFAULT '',
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, stage)
);
-- F8: per-section prompt overrides {section_name: replacement_text}. `content` is the
-- append-context (trailing project section); section_overrides replace named base
-- sections from the stage registry (prompts_registry.py). Empty = base prompt unchanged.
ALTER TABLE project_stage_context ADD COLUMN IF NOT EXISTS section_overrides JSONB NOT NULL DEFAULT '{}'::jsonb;
-- F8: Review and Enhancement are now independent stages (were a shared 'arch_review'
-- context). One-time, idempotent copy of existing shared context into 'enhancement' so
-- current enhancement behavior carries over as an independent starting point. ON CONFLICT
-- preserves any enhancement-specific edits made later.
INSERT INTO project_stage_context (project_id, stage, content, section_overrides, updated_by, updated_at)
  SELECT project_id, 'enhancement', content, section_overrides, updated_by, updated_at
  FROM project_stage_context WHERE stage='arch_review'
ON CONFLICT (project_id, stage) DO NOTHING;

-- ── Capability catalog (Phase 1 keystone — manual-curated, LLM-suggested) ────
-- Project-scoped canonical capabilities. The architect curates; suggestions are
-- derived from analysis-emitted uc_capabilities (the LLM's proposals). This is
-- the canonical axis Track 2 (engineering roadmap) reads instead of raw strings.
CREATE TABLE IF NOT EXISTS capability_catalog (
  id          BIGSERIAL PRIMARY KEY,
  project_id  BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  cap_key     TEXT NOT NULL,
  name        TEXT NOT NULL DEFAULT '',
  definition  TEXT NOT NULL DEFAULT '',
  domain      TEXT NOT NULL DEFAULT '',            -- grouping axis (roadmap lanes / Jira epics)
  spec_refs   TEXT[] NOT NULL DEFAULT '{}',
  depends_on  TEXT[] NOT NULL DEFAULT '{}',
  status      TEXT NOT NULL DEFAULT 'confirmed',   -- confirmed | suggested | rejected
  created_by  TEXT NOT NULL DEFAULT '',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by  TEXT NOT NULL DEFAULT '',
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_id, cap_key)
);
-- Migrate already-deployed catalog tables that predate `domain`.
ALTER TABLE capability_catalog ADD COLUMN IF NOT EXISTS domain TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_capability_catalog_project ON capability_catalog(project_id);

-- UDLM Knowledge-family Capability fields (migration 020 reconciliation). The catalog
-- IS the UDLM Capability entity: cap_key = handle, status = lifecycle (confirmed/
-- suggested/rejected curated + 'observed' from assessments/analysis), depends_on/
-- spec_refs reused. These ADDs are idempotent and run after capability_taxonomy_terms
-- (migration 017) so the normalization FK binds on both fresh and deployed DBs.
ALTER TABLE capability_catalog ADD COLUMN IF NOT EXISTS family                TEXT NOT NULL DEFAULT 'dcm';
ALTER TABLE capability_catalog ADD COLUMN IF NOT EXISTS domain_prefix         TEXT;
ALTER TABLE capability_catalog ADD COLUMN IF NOT EXISTS normalized_to_term_id UUID REFERENCES capability_taxonomy_terms(id) ON DELETE SET NULL;
ALTER TABLE capability_catalog ADD COLUMN IF NOT EXISTS normalization_status  TEXT NOT NULL DEFAULT 'unmapped';
ALTER TABLE capability_catalog ADD COLUMN IF NOT EXISTS created_via           TEXT NOT NULL DEFAULT 'curated';
ALTER TABLE capability_catalog ADD COLUMN IF NOT EXISTS evidence              JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE capability_catalog ADD COLUMN IF NOT EXISTS provenance            JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE capability_catalog ADD COLUMN IF NOT EXISTS classification        TEXT NOT NULL DEFAULT 'public';
-- Curated capabilities are project-scoped; OBSERVED capabilities discovered across
-- runs (uc-analysis) are cross-project, so project_id becomes nullable (NULL = global
-- observed). The existing Catalog CRUD always filters WHERE project_id=$1, so global
-- rows stay invisible to it — clean separation of curated vs observed in one table.
ALTER TABLE capability_catalog ALTER COLUMN project_id DROP NOT NULL;
CREATE INDEX IF NOT EXISTS idx_capcat_family ON capability_catalog(family);
CREATE INDEX IF NOT EXISTS idx_capcat_status ON capability_catalog(status);
CREATE INDEX IF NOT EXISTS idx_capcat_term   ON capability_catalog(normalized_to_term_id);

-- Run management: soft-archive (hide from default lists; reversible). Delete is
-- a hard purge handled in the API (DB + workspace + Tekton), not a flag.
ALTER TABLE run_sessions ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT false;

-- Cached Architectural Review / Enhancement Plan output per (run, scope, UC).
-- These are LLM generations over a run's immutable analysis; caching avoids
-- regenerating on every view. source_ingested_at records the analysis_runs
-- ingest timestamp at generation time — if the run is re-ingested (newer
-- ingested_at), the cache is stale and the UI offers a refresh. uc_uuid is ''
-- (not NULL) for run-scope so the UNIQUE key is deterministic for upserts.
CREATE TABLE IF NOT EXISTS analysis_output_cache (
  id                 BIGSERIAL PRIMARY KEY,
  run_id             TEXT NOT NULL,
  kind               TEXT NOT NULL,            -- 'review' | 'enhancement'
  scope              TEXT NOT NULL,            -- 'run' | 'uc'
  uc_uuid            TEXT NOT NULL DEFAULT '',  -- '' for run scope
  content            TEXT NOT NULL,
  model_label        TEXT NOT NULL DEFAULT '',
  source_ingested_at TIMESTAMPTZ,
  created_by         TEXT NOT NULL DEFAULT '',
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_id, kind, scope, uc_uuid)
);
CREATE INDEX IF NOT EXISTS idx_analysis_output_cache_run ON analysis_output_cache(run_id);

-- Multi-user: approved users + roles. Identity comes from the oauth-proxy;
-- `approved` is synced from the LDAP approval group; role (admin/editor/viewer)
-- is managed in-app. `reviewer` is the canonical identity (oauth username or
-- email); `email` is also stored so the gate can match either header.
CREATE TABLE IF NOT EXISTS users (
  reviewer     TEXT PRIMARY KEY,
  email        TEXT NOT NULL DEFAULT '',
  display_name TEXT NOT NULL DEFAULT '',
  role         TEXT NOT NULL DEFAULT 'editor',   -- admin | editor | viewer
  approved     BOOLEAN NOT NULL DEFAULT false,
  source       TEXT NOT NULL DEFAULT 'ldap',     -- ldap | bootstrap | manual
  last_seen    TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(lower(email));
-- Internal (local) users authenticate app-natively; argon2 hash, never plaintext.
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false;

-- Platform settings (LDAP, SMTP, …) configured in-app by platform admins instead
-- of env. Secret fields inside `value` are Fernet-encrypted by the API before
-- storage. Env vars remain a fallback when a key is absent.
CREATE TABLE IF NOT EXISTS app_settings (
  key        TEXT PRIMARY KEY,            -- 'ldap' | 'smtp'
  value      JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_by TEXT NOT NULL DEFAULT '',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Email invitations: a platform admin invites a user (by email) into a project;
-- the tokened link lets them set a password and join. Single-use.
CREATE TABLE IF NOT EXISTS user_invitations (
  token        TEXT PRIMARY KEY,
  email        TEXT NOT NULL,
  display_name TEXT NOT NULL DEFAULT '',
  project_id   BIGINT REFERENCES projects(id) ON DELETE CASCADE,
  project_role TEXT NOT NULL DEFAULT 'editor',
  global_role  TEXT NOT NULL DEFAULT 'editor',
  invited_by   TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at   TIMESTAMPTZ NOT NULL,
  accepted_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_user_invitations_email ON user_invitations(lower(email));

-- Ensure a default project exists so single-user installs and the project
-- switcher always have a home.
INSERT INTO projects (slug, name, description, created_by)
VALUES ('default', 'Default', 'Default project', 'system')
ON CONFLICT (slug) DO NOTHING;

-- Phase 3: data tenancy. Scope the ROOT entities to a project; child rows
-- (uc_analyses, uc_gaps, uc_capabilities, set members, …) inherit via their FK
-- to a scoped parent. Columns are nullable + backfilled into 'default' so this
-- is a safe in-place migration with no data loss.
ALTER TABLE managed_use_cases     ADD COLUMN IF NOT EXISTS project_id BIGINT REFERENCES projects(id);
ALTER TABLE analysis_runs         ADD COLUMN IF NOT EXISTS project_id BIGINT REFERENCES projects(id);
ALTER TABLE run_sessions          ADD COLUMN IF NOT EXISTS project_id BIGINT REFERENCES projects(id);

-- Rerun fidelity: the exact RunTriggerIn payload that created this run.
-- Tekton prunes PipelineRuns (their params with them); this column is the
-- durable record so Rerun reproduces the run regardless of cluster state.
ALTER TABLE run_sessions ADD COLUMN IF NOT EXISTS trigger_payload JSONB;
ALTER TABLE use_case_sets         ADD COLUMN IF NOT EXISTS project_id BIGINT REFERENCES projects(id);
ALTER TABLE analysis_output_cache ADD COLUMN IF NOT EXISTS project_id BIGINT REFERENCES projects(id);
UPDATE managed_use_cases     SET project_id=(SELECT id FROM projects WHERE slug='default') WHERE project_id IS NULL;
UPDATE analysis_runs         SET project_id=(SELECT id FROM projects WHERE slug='default') WHERE project_id IS NULL;
UPDATE run_sessions          SET project_id=(SELECT id FROM projects WHERE slug='default') WHERE project_id IS NULL;
UPDATE use_case_sets         SET project_id=(SELECT id FROM projects WHERE slug='default') WHERE project_id IS NULL;
UPDATE analysis_output_cache SET project_id=(SELECT id FROM projects WHERE slug='default') WHERE project_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_muc_project    ON managed_use_cases(project_id);
CREATE INDEX IF NOT EXISTS idx_aruns_project  ON analysis_runs(project_id);
CREATE INDEX IF NOT EXISTS idx_rsess_project  ON run_sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_ucsets_project ON use_case_sets(project_id);

-- ── Phase 2: UC-location repos + destination assignment ──────────────────────
-- UCs are git-backed (see uc-driven-roadmaps-design.md / review-console-design.md
-- "Use-case git model"). A repo is marked a writable UC destination by carrying
-- the 'uc-store' role in managed_repos.roles — no schema change needed there,
-- roles is an open TEXT[]. A pvc-local repo (DAV-hosted bare repo on the RWX
-- workspace PVC) is just a managed_repos row with metadata.provider='pvc-local'.
--
-- Per-PROJECT default UC destination (where this project writes its UCs).
-- uc_repo_uuid NULL = fall back to the global default uc-store/corpus repo.
-- Soft reference (no FK): removing a repo must not cascade-delete project data.
ALTER TABLE projects ADD COLUMN IF NOT EXISTS uc_repo_uuid UUID;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS uc_path      TEXT NOT NULL DEFAULT '';
ALTER TABLE projects ADD COLUMN IF NOT EXISTS uc_branch    TEXT NOT NULL DEFAULT '';
-- Per-UC destination/provenance (overrides the project default when set). The
-- git round-trip itself (commit-on-save, origin/fork tracking) is Phase 3; here
-- we only record where each UC's git home is.
ALTER TABLE managed_use_cases ADD COLUMN IF NOT EXISTS source_repo_uuid UUID;
ALTER TABLE managed_use_cases ADD COLUMN IF NOT EXISTS source_path      TEXT NOT NULL DEFAULT '';
ALTER TABLE managed_use_cases ADD COLUMN IF NOT EXISTS source_ref       TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_muc_source_repo ON managed_use_cases(source_repo_uuid);

-- ── RBAC: accounts × roles × privileges (review-console-design.md) ───────────
-- Identity-source-agnostic. An account (a `users` row, whatever the auth source)
-- is matrixed to roles; roles are matrixed to privileges. Authorization is the
-- union of privileges across the account's roles — platform-scoped roles apply
-- everywhere, project-scoped roles apply to their project_id. Adding a privilege
-- or a custom role is data, not a migration.

-- The gate flag (source stays informational only). Approval == enabled account.
ALTER TABLE users ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT true;
-- Per-user default project (the one selected on login when none is set client-side).
ALTER TABLE users ADD COLUMN IF NOT EXISTS default_project_id BIGINT;

CREATE TABLE IF NOT EXISTS rbac_privileges (
  key         TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  scope       TEXT NOT NULL DEFAULT 'project'   -- 'platform' | 'project'
);

CREATE TABLE IF NOT EXISTS rbac_roles (
  id          BIGSERIAL PRIMARY KEY,
  key         TEXT UNIQUE NOT NULL,
  name        TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  scope       TEXT NOT NULL DEFAULT 'project',  -- 'platform' | 'cross-project' | 'project'
  is_system   BOOLEAN NOT NULL DEFAULT false,   -- built-in; cannot be deleted
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rbac_role_privileges (
  role_id       BIGINT NOT NULL REFERENCES rbac_roles(id) ON DELETE CASCADE,
  privilege_key TEXT   NOT NULL REFERENCES rbac_privileges(key) ON DELETE CASCADE,
  PRIMARY KEY (role_id, privilege_key)
);

-- account × role × project. project_id NULL for platform-scoped roles. A
-- surrogate id PK + a COALESCE unique index (PKs can't span a nullable column).
CREATE TABLE IF NOT EXISTS rbac_account_roles (
  id          BIGSERIAL PRIMARY KEY,
  reviewer    TEXT   NOT NULL,
  role_id     BIGINT NOT NULL REFERENCES rbac_roles(id) ON DELETE CASCADE,
  project_id  BIGINT REFERENCES projects(id) ON DELETE CASCADE,
  granted_by  TEXT   NOT NULL DEFAULT 'system',
  granted_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rbac_acct_role_uniq
  ON rbac_account_roles (lower(reviewer), role_id, COALESCE(project_id, 0));
CREATE INDEX IF NOT EXISTS idx_rbac_acct_role_acct ON rbac_account_roles (lower(reviewer));

-- External group → role mappings (managed by platform admins). When a user
-- authenticates from a source carrying group memberships (LDAP today; OCP/others
-- later), matching groups grant the mapped roles. Source-agnostic via `source`.
-- The sync that *applies* these (writes derived account_roles) is a later slice;
-- this is the structure + the platform-admin-managed config.
CREATE TABLE IF NOT EXISTS rbac_group_role_mappings (
  id          BIGSERIAL PRIMARY KEY,
  source      TEXT   NOT NULL DEFAULT 'ldap',   -- 'ldap' | 'ocp' | ...
  group_key   TEXT   NOT NULL,                  -- group DN / name / id
  role_id     BIGINT NOT NULL REFERENCES rbac_roles(id) ON DELETE CASCADE,
  project_id  BIGINT REFERENCES projects(id) ON DELETE CASCADE,  -- for project-scoped roles
  created_by  TEXT   NOT NULL DEFAULT 'system',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rbac_grp_role_uniq
  ON rbac_group_role_mappings (source, lower(group_key), role_id, COALESCE(project_id, 0));

-- Seed the v1 privilege vocabulary.
INSERT INTO rbac_privileges (key, name, description, scope) VALUES
  ('platform.admin',     'Platform settings', 'All platform settings (LDAP, SMTP, accounts, roles, repos); see all projects; grant self project roles', 'platform'),
  ('project.create',     'Create projects',   'Create new projects', 'cross-project'),
  ('project.delete',     'Delete projects',   'Delete a project (after its data is moved/removed)', 'project'),
  ('project.settings',   'Project settings',  'Manage a project''s settings (name, UC destination, archive)', 'project'),
  ('project.members',    'Project members',   'Manage a project''s membership and role assignments', 'project'),
  ('project.data.read',  'Read project data', 'Read all project data (use cases, runs, results, sets)', 'project'),
  -- Workflow / execution privileges (atomic; compose into roles).
  ('project.usecases',           'Manage use cases',       'Create, edit, delete, import use cases and use-case sets', 'project'),
  ('project.runs.manage',        'Manage runs',            'Archive, delete and rename run records', 'project'),
  ('project.runs.execute',       'Execute runs',           'Trigger an analysis run', 'project'),
  ('project.archreview.execute', 'Run arch review',        'Execute the architecture-review stage', 'project'),
  ('project.archreview.context', 'Edit arch-review context','Edit the architecture-review stage context', 'project'),
  ('project.enhancement.execute','Run enhancements',       'Execute enhancement generation', 'project'),
  ('project.enhancement.pr',     'Submit enhancement PRs', 'Create branches / pull requests from enhancements (external push)', 'project'),
  ('project.catalog',            'Manage catalog',         'Manage the capability catalog', 'project'),
  -- Config-registry privileges (project-owned, strict isolation).
  ('project.models',             'Manage models',          'Manage a project''s AI model registrations', 'project'),
  ('project.integrations',       'Manage integrations',    'Manage a project''s MCP server registrations', 'project'),
  ('project.repos',              'Manage repos',           'Manage a project''s managed repositories', 'project'),
  -- F8: per-project prompt management (append context + section overrides, all stages).
  -- Supersedes project.archreview.context (kept as an alias for back-compat in rbac.py).
  ('prompt.manage',              'Manage prompts',         'Edit per-project prompt customizations (additional context + section overrides) for all DAV stages', 'project'),
  -- F7 assessments + blueprints (task #95; blueprint privileges inert until built).
  ('assessment.view',            'View assessments',       'View assessments and their capability findings', 'project'),
  ('assessment.edit',            'Edit assessments',       'Ingest and edit assessments', 'project'),
  ('blueprint.view',             'View blueprints',        'View blueprint/template projects and their setup', 'project'),
  ('blueprint.edit',             'Edit blueprints',        'Create, clone and manage blueprint/template projects', 'project')
ON CONFLICT (key) DO NOTHING;
-- Reclassify project.create from its original 'platform' scope to 'cross-project'
-- (project-related but not tied to a specific project). Idempotent.
UPDATE rbac_privileges SET scope='cross-project' WHERE key='project.create' AND scope<>'cross-project';

-- Seed the 4 built-in roles.
INSERT INTO rbac_roles (key, name, description, scope, is_system) VALUES
  ('platform-admin', 'Platform Admin', 'Full platform administration', 'platform', true),
  ('project-admin',  'Project Admin',  'Full administration of a project', 'project', true),
  ('project-edit',   'Project Edit',   'Edit access to a project''s data', 'project', true),
  ('project-viewer', 'Project Viewer', 'Read-only access to a project''s data', 'project', true)
ON CONFLICT (key) DO NOTHING;

-- Seed the role × privilege matrix for the built-in roles. Re-runs every boot
-- with ON CONFLICT DO NOTHING, so adding keys here backfills existing roles.
INSERT INTO rbac_role_privileges (role_id, privilege_key)
  SELECT r.id, p.key FROM rbac_roles r CROSS JOIN rbac_privileges p
  WHERE (r.key='platform-admin' AND p.key IN ('platform.admin','project.create'))
     OR (r.key='project-admin'  AND p.key IN (
            'project.settings','project.members','project.delete','project.data.read',
            'project.usecases','project.runs.manage','project.runs.execute',
            'project.archreview.execute','project.archreview.context','prompt.manage',
            'project.enhancement.execute','project.enhancement.pr','project.catalog',
            'project.models','project.integrations','project.repos',
            'assessment.view','assessment.edit','blueprint.view','blueprint.edit'))
     OR (r.key='project-edit'   AND p.key IN (
            'project.data.read','project.usecases','project.runs.manage','project.runs.execute',
            'project.archreview.execute','project.archreview.context','prompt.manage',
            'project.enhancement.execute','project.catalog',
            'assessment.view','assessment.edit','blueprint.view'))
     OR (r.key='project-viewer' AND p.key IN ('project.data.read','assessment.view','blueprint.view'))
ON CONFLICT DO NOTHING;

-- Retire the legacy umbrella `project.data.write` from the BUILT-IN roles (custom
-- roles untouched). The granular workflow privileges above replace it.
DELETE FROM rbac_role_privileges rp
  USING rbac_roles r
  WHERE rp.role_id = r.id
    AND rp.privilege_key = 'project.data.write'
    AND r.key IN ('project-admin','project-edit','project-viewer');

-- Backfill: any role (incl. operator-created) holding project.settings is an
-- admin-like role and gets the full project-admin privilege set. Idempotent.
INSERT INTO rbac_role_privileges (role_id, privilege_key)
  SELECT DISTINCT rp.role_id, np.key
  FROM rbac_role_privileges rp
  CROSS JOIN (VALUES
    ('project.members'),('project.delete'),('project.data.read'),
    ('project.usecases'),('project.runs.manage'),('project.runs.execute'),
    ('project.archreview.execute'),('project.archreview.context'),('prompt.manage'),
    ('project.enhancement.execute'),('project.enhancement.pr'),('project.catalog'),
    ('project.models'),('project.integrations'),('project.repos'),
    ('assessment.view'),('assessment.edit'),('blueprint.view'),('blueprint.edit')
  ) AS np(key)
  WHERE rp.privilege_key = 'project.settings'
ON CONFLICT DO NOTHING;

-- One-time migration of legacy roles → account_roles (idempotent).
INSERT INTO rbac_account_roles (reviewer, role_id, project_id, granted_by)
  SELECT lower(u.reviewer), r.id, NULL, 'migration'
  FROM users u CROSS JOIN rbac_roles r
  WHERE r.key='platform-admin' AND u.role='platform-admin'
ON CONFLICT DO NOTHING;
INSERT INTO rbac_account_roles (reviewer, role_id, project_id, granted_by)
  SELECT lower(pm.reviewer), r.id, pm.project_id, 'migration'
  FROM project_members pm
  JOIN rbac_roles r ON r.key = CASE pm.role
        WHEN 'admin'    THEN 'project-admin'
        WHEN 'uc-admin' THEN 'project-admin'
        WHEN 'editor'   THEN 'project-edit'
        WHEN 'viewer'   THEN 'project-viewer'
        ELSE 'project-viewer' END
ON CONFLICT DO NOTHING;

COMMIT;
