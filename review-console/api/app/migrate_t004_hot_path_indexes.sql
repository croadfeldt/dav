-- Tenant migration t004 — Wave-0 API hygiene: indexes for hot read paths.
-- Runs per tenant schema (search_path=tenant_<x>,public) via db_bootstrap's CLIENT_MIGRATIONS,
-- tracked once in public.schema_migrations. Idempotent (CREATE INDEX IF NOT EXISTS) since it can
-- run against a base-adopted schema.
--
-- These paths seq-scan today:
--  * uc_gaps(analysis_id)         — /api/analysis/gaps joins uc_analyses ON ua.id = g.analysis_id
--  * uc_capabilities(analysis_id) — capability graph/density joins on analysis_id
--  * analysis_runs(run_name)      — /api/runs enrichment + run-detail per-UC count lookups
BEGIN;

CREATE INDEX IF NOT EXISTS idx_uc_gaps_analysis         ON uc_gaps (analysis_id);
CREATE INDEX IF NOT EXISTS idx_uc_capabilities_analysis ON uc_capabilities (analysis_id);
CREATE INDEX IF NOT EXISTS idx_analysis_runs_run_name   ON analysis_runs (run_name);

COMMIT;
