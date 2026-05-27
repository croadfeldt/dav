-- Migration 009: per-repo credentials on managed_repos
--
-- Adds two Fernet-encrypted credential columns:
--   github_pat_encrypted          — PAT used by the M5 poller (and any
--                                    future GitHub API consumer for this
--                                    repo). Replaces the cluster-wide
--                                    GITHUB_TOKEN env var design.
--   github_webhook_secret_encrypted — Shared secret used by the M6
--                                    webhook endpoint to HMAC-validate
--                                    inbound events from this repo.
--
-- Both columns hold Fernet tokens (URL-safe base64 strings). Encryption
-- key lives in DAV_FERNET_KEY env (from the dav-fernet-key Secret).
--
-- See ADR-004 for why per-repo + Fernet-in-DB now (HashiCorp Vault later).
--
-- Idempotent.

BEGIN;

ALTER TABLE managed_repos
    ADD COLUMN IF NOT EXISTS github_pat_encrypted            TEXT,
    ADD COLUMN IF NOT EXISTS github_webhook_secret_encrypted TEXT;

-- No index — these are looked up alongside the row, not as standalone
-- query keys.

COMMIT;
