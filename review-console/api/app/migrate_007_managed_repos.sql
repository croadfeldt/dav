-- Migration 007: managed_repos registry
--
-- First-class registry of repos DAV knows about, replacing the per-purpose
-- source ConfigMap as the source-of-truth for "which repos do we operate on".
-- The dav-source-spec and dav-source-corpus ConfigMaps become projections
-- (queries) over this table: for each role, render the matching repos as
-- the ConfigMap's `sources` YAML list.
--
-- Roles are open-ended strings stored in a TEXT[] array; v1 uses:
--   - 'spec'         (served by dav-docs-mcp; cloned per source.namespace)
--   - 'corpus'       (cloned by the pipeline at run start; UCs read from here)
--   - 'issue-source' (polled for PR comments; future webhook target)
-- Future roles can be added without migration.
--
-- Idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS managed_repos (
    id              SERIAL PRIMARY KEY,
    uuid            UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),

    -- The namespace under which this repo's content is served by the MCP
    -- (or the directory it lands under at clone time for the corpus etc.).
    -- Must be unique across repos. URL-safe lowercase per the substrate
    -- identifier-scheme convention.
    namespace       TEXT NOT NULL UNIQUE
                    CHECK (namespace ~ '^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$'),

    -- Human-friendly display name (separate from namespace so the UI can
    -- show "UDLM — Universal Data Lifecycle Model" while the wire name is
    -- "udlm"). Defaults to namespace if not set.
    display_name    TEXT,

    -- Git clone URL (https or git@). Validated at write time by API.
    repo_url        TEXT NOT NULL,
    repo_branch     TEXT NOT NULL DEFAULT 'main',

    -- For role=spec: optional subdirectory served as the source root
    -- (e.g., dcm sets root_path='architecture' so dav-docs-mcp serves
    -- /data/dcm/architecture/* rather than the whole dcm clone).
    -- Stored uniformly; ignored for roles where it doesn't apply.
    root_path       TEXT NOT NULL DEFAULT '',

    -- The roles this repo plays. Free-form strings; v1 vocabulary is
    -- {'spec', 'corpus', 'issue-source'}. A single repo MAY have
    -- multiple roles (e.g., dcm is both 'spec' source and 'corpus' source).
    roles           TEXT[] NOT NULL DEFAULT '{}',

    -- Polling / webhook config for role='issue-source'. NULL until M5/M6.
    -- {polling_enabled: bool, polling_interval_seconds: int,
    --  webhook_enabled: bool, webhook_secret_ref: str,
    --  github_owner: str, github_repo: str}
    ingestion_config JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Free-form metadata: description, tags, etc.
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Tenant boundary. Single-tenant deployments leave this at 'default';
    -- multi-tenant filtering (in MCP requests and UI listings) is layered
    -- on later — see ADR-003. NOT NULL with default so the column is safe
    -- to add to a populated table.
    tenant_id       TEXT NOT NULL DEFAULT 'default',

    -- Audit
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      TEXT NOT NULL DEFAULT 'system',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by      TEXT NOT NULL DEFAULT 'system'
);

-- GIN index on roles for fast "all repos with role=X" lookups (projection
-- queries that rebuild the per-purpose source ConfigMaps).
CREATE INDEX IF NOT EXISTS idx_managed_repos_roles ON managed_repos USING GIN (roles);
CREATE INDEX IF NOT EXISTS idx_managed_repos_updated_at ON managed_repos (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_managed_repos_tenant ON managed_repos (tenant_id);

-- Trigger to keep updated_at fresh on UPDATE.
CREATE OR REPLACE FUNCTION _touch_managed_repos_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_managed_repos_updated_at ON managed_repos;
CREATE TRIGGER trg_managed_repos_updated_at
    BEFORE UPDATE ON managed_repos
    FOR EACH ROW
    EXECUTE FUNCTION _touch_managed_repos_updated_at();

COMMIT;
