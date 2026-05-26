-- Migration 004: Default UC Set marker
-- Adds an is_default flag to use_case_sets. Enforces at most one
-- default Set via a partial unique index (only one row with is_default=TRUE).
-- Idempotent.

BEGIN;

ALTER TABLE use_case_sets
    ADD COLUMN IF NOT EXISTS is_default BOOLEAN NOT NULL DEFAULT FALSE;

CREATE UNIQUE INDEX IF NOT EXISTS idx_uc_sets_one_default
    ON use_case_sets((is_default)) WHERE is_default;

COMMIT;
