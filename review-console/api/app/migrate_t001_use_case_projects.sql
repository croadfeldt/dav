-- Tenant migration t001 — UC↔project M:N "applied-to" (UC tenant-scoping, #199).
-- Runs per tenant schema (search_path=tenant_<x>,public) via db_bootstrap's CLIENT_MIGRATIONS,
-- tracked once in public.schema_migrations. Idempotent (may run on a base-adopted schema).
-- Unqualified names resolve to the tenant schema; public.* is the cross-schema FK to control.
BEGIN;

CREATE TABLE IF NOT EXISTS use_case_projects (
  uc_uuid    TEXT   NOT NULL,
  project_id BIGINT NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
  applied_by TEXT   NOT NULL DEFAULT '',
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (uc_uuid, project_id)
);
CREATE INDEX IF NOT EXISTS idx_use_case_projects_project ON use_case_projects(project_id);
CREATE INDEX IF NOT EXISTS idx_use_case_projects_uc      ON use_case_projects(uc_uuid);

-- Backfill: each managed UC is applied to its current (legacy) project. `project_id` is retained
-- during the transition (dual-read); writes/reads cut over to this table in later phases.
INSERT INTO use_case_projects (uc_uuid, project_id, applied_by)
  SELECT uuid, project_id, 'migration'
  FROM managed_use_cases
  WHERE project_id IS NOT NULL
  ON CONFLICT (uc_uuid, project_id) DO NOTHING;

COMMIT;
