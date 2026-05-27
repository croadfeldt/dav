-- Migration 010: shared credentials abstraction
--
-- Per ADR-005. A `credentials` table holds named, typed,
-- Fernet-encrypted secrets that multiple managed_repos rows can
-- reference. Resolution order in get_repo_secrets():
--   1. credential FK (if set) — decrypt the referenced credentials.value_encrypted
--   2. inline encrypted column (ADR-004) — backward-compat
--   3. None
--
-- Adds nullable FK columns to managed_repos with ON DELETE SET NULL so
-- credential deletion safely unlinks (but is also gated at the API
-- layer with a 409 + dependent-repo list).
--
-- Non-breaking: existing inline values keep working. Operators migrate
-- via the UI's "Convert to shared credential" button at their pace.
--
-- Idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS credentials (
    id              SERIAL PRIMARY KEY,
    uuid            UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),

    -- Human-friendly identifier. Unique per (tenant, type) — operators
    -- can have a 'my-github-pat' under both default and other tenants
    -- without conflict.
    name            TEXT NOT NULL
                    CHECK (name ~ '^[a-z0-9][a-z0-9-]{0,62}$'),

    -- Open-ended vocabulary; v1 set:
    --   'github_pat'             — used by the M5 poller + future GH API consumers
    --   'github_webhook_secret'  — used by the M6 webhook receiver
    -- Adding a type requires no migration; UI / repo form is type-aware
    -- via the credential_type filter on GET /api/credentials.
    credential_type TEXT NOT NULL,

    -- Fernet token (URL-safe base64 string). Same encryption as the
    -- inline columns from ADR-004 — shared DAV_FERNET_KEY.
    value_encrypted TEXT NOT NULL,

    description     TEXT,

    -- Tenant boundary (consistent with managed_repos.tenant_id from
    -- ADR-003). Ungated in v1; per-tenant request filtering is a future
    -- layer.
    tenant_id       TEXT NOT NULL DEFAULT 'default',

    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Audit
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      TEXT NOT NULL DEFAULT 'system',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by      TEXT NOT NULL DEFAULT 'system',

    UNIQUE (tenant_id, credential_type, name)
);

CREATE INDEX IF NOT EXISTS idx_credentials_type ON credentials (credential_type);
CREATE INDEX IF NOT EXISTS idx_credentials_tenant ON credentials (tenant_id);

-- updated_at touch trigger (mirrors managed_repos pattern from M1)
CREATE OR REPLACE FUNCTION _touch_credentials_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_credentials_updated_at ON credentials;
CREATE TRIGGER trg_credentials_updated_at
    BEFORE UPDATE ON credentials
    FOR EACH ROW
    EXECUTE FUNCTION _touch_credentials_updated_at();

-- FK columns on managed_repos. ON DELETE SET NULL so credential delete
-- doesn't cascade-delete the repo; the API layer additionally refuses
-- DELETE /api/credentials/{uuid} with 409 if any dependent repos exist.
ALTER TABLE managed_repos
    ADD COLUMN IF NOT EXISTS github_pat_credential_id            INTEGER REFERENCES credentials(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS github_webhook_secret_credential_id INTEGER REFERENCES credentials(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_managed_repos_pat_credential     ON managed_repos (github_pat_credential_id);
CREATE INDEX IF NOT EXISTS idx_managed_repos_webhook_credential ON managed_repos (github_webhook_secret_credential_id);

COMMIT;
