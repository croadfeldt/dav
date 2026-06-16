-- migrate_022_api_tokens.sql
-- DB-backed Personal Access Tokens (PATs) for non-interactive / agent auth.
-- An agent presents `Authorization: Bearer dav_pat_<secret>`; the sha256 of the
-- token is stored (never the plaintext), and get_user() resolves it to the RBAC
-- account `email` it acts as — the normal RBAC then applies. Tokens are
-- individually revocable (revoked_at) and may carry an optional expiry.
CREATE TABLE IF NOT EXISTS api_tokens (
    id           BIGSERIAL PRIMARY KEY,
    email        TEXT        NOT NULL,            -- RBAC account the token acts as
    token_hash   TEXT        NOT NULL UNIQUE,     -- sha256(token) hex; plaintext shown once at mint
    label        TEXT        NOT NULL DEFAULT '', -- human note ("work claude pipeline", ...)
    created_by   TEXT        NOT NULL DEFAULT '', -- who minted it
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    expires_at   TIMESTAMPTZ,                     -- NULL = no expiry
    revoked_at   TIMESTAMPTZ                      -- NULL = active
);
CREATE INDEX IF NOT EXISTS api_tokens_hash_idx  ON api_tokens (token_hash);
CREATE INDEX IF NOT EXISTS api_tokens_email_idx ON api_tokens (email);

-- The least-privilege pipeline identity is created via the existing RBAC admin
-- (Users & Roles UI / /api/accounts + /api/rbac): an account e.g.
-- `pipeline-agent@roadfeldt.com` granted a role with ONLY:
--   project.usecases, assessment.view, assessment.edit, project.data.read
-- Then mint a PAT for that email via POST /api/tokens. No admin/platform privs.
