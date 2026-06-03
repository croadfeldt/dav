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
CREATE UNIQUE INDEX IF NOT EXISTS idx_model_configs_name ON model_configs(lower(name));

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
  created_by      TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_servers_name ON mcp_server_configs(lower(name));

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

-- ── Seed MCP servers ───────────────────────────────────────────────────────
-- Idempotent: ON CONFLICT DO NOTHING skips rows that already exist by name.
INSERT INTO mcp_server_configs (name, description, sse_url, enabled, created_by) VALUES
  ('openshift-mcp',
   'OpenShift cluster tools — list pods, logs, events, nodes, inference services',
   'https://openshift-mcp-mcp-servers.apps.ocp.roadfeldt.com/sse',
   true, 'seed'),
  ('frc-scheduler-mcp',
   'FRC scheduler — events, teams, schedules, TBA lookup',
   'https://frc-scheduler-mcp-mcp-servers.apps.ocp.roadfeldt.com/sse',
   true, 'seed'),
  ('dav-docs-mcp',
   'DCM architecture spec — served via MCP for use with Claude Code and agents',
   'https://dav-docs-mcp-dav.apps.ocp.roadfeldt.com/sse',
   true, 'seed')
ON CONFLICT DO NOTHING;

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

-- Run management: soft-archive (hide from default lists; reversible). Delete is
-- a hard purge handled in the API (DB + workspace + Tekton), not a flag.
ALTER TABLE run_sessions ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT false;

COMMIT;
