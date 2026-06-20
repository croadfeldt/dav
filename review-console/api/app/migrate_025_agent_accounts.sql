-- migrate_025: first-class AGENT identities as a kind of account (reuse RBAC).
-- An agent is an account that cannot interactively log in (no password / SSO) and
-- authenticates only via a Personal Access Token. It gets its own role bindings
-- through the existing rbac_account_roles matrix — exactly like a person — so there
-- is NO separate agent-permission system. Mirrors the OpenShift ServiceAccount⇆User
-- relationship. kind='person' is the default; existing rows are unaffected.
ALTER TABLE users ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'person';
-- kind ∈ 'person' | 'agent'
