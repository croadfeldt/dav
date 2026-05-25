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
CREATE INDEX IF NOT EXISTS idx_managed_uc_updated ON managed_use_cases(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_managed_uc_state   ON managed_use_cases(lifecycle_state);

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

-- ── Review model configs ────────────────────────────────────────────────────
-- User-configured models for architectural review of analysis findings.
-- Supports OpenAI-compatible and Anthropic providers.
-- api_key stored at rest; masked ('••••••••') on GET responses.

CREATE TABLE IF NOT EXISTS review_model_configs (
  id           BIGSERIAL PRIMARY KEY,
  name         TEXT NOT NULL,
  provider     TEXT NOT NULL CHECK (provider IN ('openai', 'anthropic')),
  endpoint_url TEXT NOT NULL,
  model_id     TEXT NOT NULL,
  api_key      TEXT NOT NULL DEFAULT '',
  enabled      BOOLEAN NOT NULL DEFAULT true,
  is_local     BOOLEAN NOT NULL DEFAULT false,
  created_by   TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_review_models_name ON review_model_configs(lower(name));

-- ── MCP server registry ─────────────────────────────────────────────────────
-- User-registered MCP servers (SSE transport) shown in the Integrations panel.
-- Health is polled on demand; no credentials stored here.

CREATE TABLE IF NOT EXISTS mcp_server_configs (
  id           BIGSERIAL PRIMARY KEY,
  name         TEXT NOT NULL,
  description  TEXT NOT NULL DEFAULT '',
  sse_url      TEXT NOT NULL,
  enabled      BOOLEAN NOT NULL DEFAULT true,
  created_by   TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_servers_name ON mcp_server_configs(lower(name));

-- ── UC Assist config ────────────────────────────────────────────────────────
-- Single-row config for NL-assisted UC authoring.
-- Falls back to DAV_UC_ASSIST_* env vars when no row is present or enabled=false.
-- api_key stored at rest; masked on GET responses.

CREATE TABLE IF NOT EXISTS uc_assist_config (
  id           INT PRIMARY KEY DEFAULT 1,
  provider     TEXT NOT NULL CHECK (provider IN ('openai', 'anthropic')) DEFAULT 'anthropic',
  endpoint_url TEXT NOT NULL DEFAULT 'https://api.anthropic.com',
  model_id     TEXT NOT NULL DEFAULT 'claude-opus-4-7-20251001',
  api_key      TEXT NOT NULL DEFAULT '',
  enabled      BOOLEAN NOT NULL DEFAULT true,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uc_assist_single_row CHECK (id = 1)
);

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

COMMIT;
