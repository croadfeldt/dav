-- Tenant migration t003 — dependency-aware UC staleness (#128).
-- Runs per tenant schema (search_path=tenant_<x>,public) via db_bootstrap's CLIENT_MIGRATIONS,
-- tracked once in public.schema_migrations. Idempotent (may run on a base-adopted schema).
-- Unqualified names resolve to the tenant schema (uc_analyses, files are client tables).
--
-- Captures, per UC analysis, the SPEC FILES the analysis depended on (resolved from the UC's
-- declared spec_refs + the model-emitted spec_refs/capabilities_invoked anchors = option (c) "both")
-- with the file content SHA AT EVAL TIME. A UC is drift-stale iff any depended-on file's CURRENT
-- content SHA differs from the captured one — i.e. only UCs whose *relevant* spec changed go stale,
-- not every UC when any repo moves (the coarse whole-repo-HEAD _repo_drifted check). Mirrors the
-- existing review_drift content-SHA pattern (schema_client.sql).
BEGIN;

CREATE TABLE IF NOT EXISTS uc_analysis_spec_deps (
  id                    BIGSERIAL PRIMARY KEY,
  analysis_id           BIGINT NOT NULL REFERENCES uc_analyses(id) ON DELETE CASCADE,
  run_id                TEXT NOT NULL,
  uc_uuid               TEXT NOT NULL,
  spec_ref              TEXT NOT NULL,          -- the raw "doc-handle" or "doc-handle/section"
  file_path             TEXT,                   -- resolved files.path (NULL if unresolvable)
  file_sha256_at_eval   TEXT,                   -- files.content_sha256 captured at eval time
  source                TEXT NOT NULL DEFAULT 'emitted'  -- 'declared' (UC spec_refs) | 'emitted' (model anchors)
                          CHECK (source IN ('declared','emitted')),
  ingested_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (analysis_id, spec_ref, source)
);
CREATE INDEX IF NOT EXISTS idx_uc_spec_deps_analysis ON uc_analysis_spec_deps(analysis_id);
CREATE INDEX IF NOT EXISTS idx_uc_spec_deps_uc       ON uc_analysis_spec_deps(uc_uuid);
CREATE INDEX IF NOT EXISTS idx_uc_spec_deps_path     ON uc_analysis_spec_deps(file_path);

-- Per-dependency drift: join captured deps to current file SHAs. is_drifted iff the depended-on
-- file's content moved since eval. Unresolved deps (file_path NULL / file gone) never falsely drift.
CREATE OR REPLACE VIEW uc_spec_drift AS
SELECT d.analysis_id, d.run_id, d.uc_uuid, d.spec_ref, d.file_path, d.source,
       d.file_sha256_at_eval, f.content_sha256 AS current_sha256,
       (d.file_path IS NOT NULL
        AND f.content_sha256 IS NOT NULL
        AND d.file_sha256_at_eval IS DISTINCT FROM f.content_sha256) AS is_drifted
FROM uc_analysis_spec_deps d
LEFT JOIN files f ON f.path = d.file_path;

COMMIT;
