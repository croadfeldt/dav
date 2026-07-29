-- ADR-003 GRADUATED ruling (2026-07-29): support is stakeholder-relative.
-- Per-persona verdicts from multi-lens runs, keyed by persona id. NULL for
-- single-lens analyses (every pre-ruling row).
ALTER TABLE uc_analyses ADD COLUMN IF NOT EXISTS persona_verdicts JSONB;
