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
CREATE INDEX IF NOT EXISTS idx_managed_uc_updated ON managed_use_cases(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_managed_uc_state   ON managed_use_cases(lifecycle_state);

-- Add lifecycle_state to existing tables that predate this column.
ALTER TABLE managed_use_cases ADD COLUMN IF NOT EXISTS lifecycle_state TEXT NOT NULL DEFAULT 'draft';

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

COMMIT;
