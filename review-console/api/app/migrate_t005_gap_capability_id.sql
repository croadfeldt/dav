-- Tenant migration t005 — Wave-1 gap identity (ADR-009): catalog-anchored gaps.
-- Runs per tenant schema (search_path=tenant_<x>,public) via db_bootstrap's CLIENT_MIGRATIONS,
-- tracked once in public.schema_migrations. Idempotent (ADD COLUMN IF NOT EXISTS) since it can
-- run against a base-adopted schema that already has uc_gaps.
--
-- A gap's cross-run identity is the catalog capability it concerns. Mirrors the
-- assessment_findings pattern (catalog_capability_id + normalization_status): the engine
-- emits an optional capability_id (a capability_catalog cap_key); ingest resolves it to a
-- catalog row when it matches, else records it flagged as a back-fill candidate — never
-- silently minting a fabricated GAP-NNN that churns across runs.
BEGIN;

ALTER TABLE uc_gaps ADD COLUMN IF NOT EXISTS catalog_capability_id bigint;
ALTER TABLE uc_gaps ADD COLUMN IF NOT EXISTS normalization_status  text NOT NULL DEFAULT 'unmapped';

-- Same vocabulary as assessment_findings.normalization_status:
--   normalized            — capability_id matched a capability_catalog row
--   proposed-taxonomy-gap — model emitted a capability_id with no catalog match (back-fill candidate)
--   unmapped              — no capability_id emitted (untagged gap; legacy behavior)
-- Add the constraint idempotently. A catalog-wide `conname` check would be wrong under
-- multi-tenancy (each tenant schema has its own uc_gaps + same-named constraint); catching
-- duplicate_object is per-table and schema-correct.
DO $$
BEGIN
  ALTER TABLE uc_gaps ADD CONSTRAINT chk_uc_gaps_norm
    CHECK (normalization_status IN ('normalized','proposed-taxonomy-gap','unmapped'));
EXCEPTION WHEN duplicate_object THEN NULL;
END$$;

CREATE INDEX IF NOT EXISTS idx_uc_gaps_catalog_capability ON uc_gaps (catalog_capability_id);

COMMIT;
