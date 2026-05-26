-- Migration 006: R2 — result lineage + state
-- Each run records which Set (if any) originated it and the selection mode;
-- per-UC analysis rows record the UC's lifecycle state at trigger time and
-- its source kind (managed vs corpus). Idempotent.

BEGIN;

ALTER TABLE run_sessions
    ADD COLUMN IF NOT EXISTS set_id         INTEGER REFERENCES use_case_sets(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS set_name       TEXT,
    ADD COLUMN IF NOT EXISTS selection_mode TEXT,            -- 'set' | 'selection' | 'individual' | 'corpus'
    ADD COLUMN IF NOT EXISTS uc_state_snapshot JSONB;        -- {uuid: lifecycle_state, ...}
                                                              -- captured at trigger time for managed UCs

ALTER TABLE uc_analyses
    ADD COLUMN IF NOT EXISTS lifecycle_state_at_run TEXT,    -- copied in at ingest from run_sessions.uc_state_snapshot
    ADD COLUMN IF NOT EXISTS source_kind            TEXT;    -- 'managed' | 'corpus'

CREATE INDEX IF NOT EXISTS idx_run_sessions_set      ON run_sessions(set_id);
CREATE INDEX IF NOT EXISTS idx_uc_analyses_state_at  ON uc_analyses(lifecycle_state_at_run);

COMMIT;
