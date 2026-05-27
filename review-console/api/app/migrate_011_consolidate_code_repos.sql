-- Migration 011: consolidate code_repo_configs into managed_repos
--
-- Per ADR-006. Folds each code_repo_configs row into managed_repos
-- with the new 'enhancement-target' role. Matches by repo_url; merges
-- when the row already exists (e.g., the upstream dcm repo that's
-- already in managed_repos for spec/corpus + might want enhancement PRs
-- too). Migrates plaintext token → Fernet-encrypted github_pat_encrypted
-- (only when the target row doesn't already have a credential to avoid
-- clobbering ADR-004/005 credentials).
--
-- This SQL is the data shape; the actual encryption is a Python step
-- that runs from main.py at startup (it needs the Fernet key from env).
-- The SQL portion is idempotent: just ensures the `enhancement-target`
-- role can be applied (it's a value in the open `roles[]` array, no
-- enum constraint to alter) and doesn't touch managed_repos directly.
--
-- The actual data migration runs in main.py._migrate_code_repo_configs
-- after the Fernet key is verified available.

BEGIN;

-- No schema changes required. The migration is data-side only and runs
-- from Python (where the Fernet key is in scope).
--
-- This migration file exists to track the migration version in the
-- applied-migrations sequence and to document the consolidation step
-- for operators reading the migrate_*.sql series.

COMMIT;
