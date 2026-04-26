-- dcm-review-console schema
-- Append-only review events + derived views for current state and drift.
-- Drift = a file's content SHA has changed since a review was recorded against it.

BEGIN;

-- Serialize schema application across concurrent startups.
-- Postgres' CREATE TABLE IF NOT EXISTS is not race-safe at the catalog
-- level (pg_type unique-constraint violation), so two API replicas or a
-- crash-restart sequence applying schema concurrently can collide. The
-- advisory lock is held until COMMIT/ROLLBACK; any second caller waits
-- for the first to finish, then re-runs the (now-idempotent) schema.
SELECT pg_advisory_xact_lock(7402983);

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
  action                  TEXT NOT NULL
                          CHECK (action IN ('review','update','clear')),
  status                  TEXT
                          CHECK (status IN ('unreviewed','in-review','needs-work','approved','stale')),
  notes                   TEXT,
  file_sha256_at_review   TEXT,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_review_events_file_created
  ON review_events(file_path, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_events_reviewer_created
  ON review_events(reviewer, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_events_created
  ON review_events(created_at DESC);

-- Latest non-cleared review per (file, reviewer).
CREATE OR REPLACE VIEW review_current AS
WITH latest AS (
  SELECT DISTINCT ON (file_path, reviewer)
    file_path,
    reviewer,
    action,
    status,
    notes,
    file_sha256_at_review,
    created_at AS reviewed_at
  FROM review_events
  ORDER BY file_path, reviewer, created_at DESC
)
SELECT file_path, reviewer, status, notes, file_sha256_at_review, reviewed_at
FROM latest
WHERE action <> 'clear';

-- Drift: review captured a SHA that no longer matches current content.
CREATE OR REPLACE VIEW review_drift AS
SELECT
  rc.file_path,
  rc.reviewer,
  rc.status,
  rc.reviewed_at,
  rc.file_sha256_at_review,
  f.content_sha256 AS current_sha256,
  (rc.file_sha256_at_review IS DISTINCT FROM f.content_sha256) AS is_drifted
FROM review_current rc
JOIN files f ON f.path = rc.file_path;

-- Most-recent-wins status per file (team-wide).
CREATE OR REPLACE VIEW file_current_status AS
SELECT DISTINCT ON (file_path)
  file_path, status, reviewer, reviewed_at, file_sha256_at_review
FROM review_current
ORDER BY file_path, reviewed_at DESC;

COMMIT;
