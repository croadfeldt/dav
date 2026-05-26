-- Migration 005: Track push-to-corpus state on managed UCs
-- Adds the columns the corpus-push flow updates after a successful PR open.
-- Idempotent.

BEGIN;

ALTER TABLE managed_use_cases
    ADD COLUMN IF NOT EXISTS corpus_pr_url       TEXT,
    ADD COLUMN IF NOT EXISTS corpus_pr_state     TEXT,    -- open | merged | closed
    ADD COLUMN IF NOT EXISTS corpus_commit_sha   TEXT,
    ADD COLUMN IF NOT EXISTS corpus_synced_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS corpus_synced_by    TEXT,
    ADD COLUMN IF NOT EXISTS corpus_synced_path  TEXT,
    ADD COLUMN IF NOT EXISTS corpus_branch       TEXT;

CREATE INDEX IF NOT EXISTS idx_managed_ucs_pr_state ON managed_use_cases(corpus_pr_state);

COMMIT;
