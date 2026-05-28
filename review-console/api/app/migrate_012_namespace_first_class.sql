-- Migration 012: namespace as a first-class output-artifact field
-- Persists the per-run namespace scope on run_sessions and tags each gap
-- with the namespace it touches. Enables:
--   * Cross-namespace drift warnings on /api/enhancements/apply
--   * "Which spec is this gap actually against?" queries
--   * Per-namespace gap aggregation in future UI panels
-- Idempotent — safe to re-apply.

ALTER TABLE run_sessions
  ADD COLUMN IF NOT EXISTS spec_namespaces   TEXT[],
  ADD COLUMN IF NOT EXISTS corpus_namespaces TEXT[];

ALTER TABLE uc_gaps
  ADD COLUMN IF NOT EXISTS namespace TEXT;

CREATE INDEX IF NOT EXISTS idx_uc_gaps_namespace ON uc_gaps(namespace);
