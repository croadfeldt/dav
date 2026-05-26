-- Migration 002: Centralize model endpoints into model_configs
-- Safe to run on existing installs; idempotent via IF NOT EXISTS / IF EXISTS guards.

BEGIN;

-- 1. Rename review_model_configs → model_configs (skip if already done)
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'review_model_configs'
  ) THEN
    ALTER TABLE review_model_configs RENAME TO model_configs;
    -- Rename the unique index to match the new table name
    ALTER INDEX IF EXISTS idx_review_models_name RENAME TO idx_model_configs_name;
  END IF;
END $$;

-- 2. Add use-flags to model_configs (idempotent)
ALTER TABLE model_configs
  ADD COLUMN IF NOT EXISTS use_arch_review BOOLEAN NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS use_uc_assist   BOOLEAN NOT NULL DEFAULT false;

-- 3. Add use_uc_assist flag to mcp_server_configs (idempotent)
ALTER TABLE mcp_server_configs
  ADD COLUMN IF NOT EXISTS use_uc_assist BOOLEAN NOT NULL DEFAULT false;

-- 4. Migrate uc_assist_config row into model_configs (if table still exists)
DO $$
DECLARE
  row_exists BOOLEAN;
  tbl_exists BOOLEAN;
BEGIN
  SELECT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'uc_assist_config'
  ) INTO tbl_exists;

  IF tbl_exists THEN
    SELECT EXISTS (SELECT 1 FROM uc_assist_config WHERE id = 1) INTO row_exists;
    IF row_exists THEN
      INSERT INTO model_configs
        (name, provider, endpoint_url, model_id, api_key, enabled,
         is_local, use_arch_review, use_uc_assist, created_by)
      SELECT
        'UC Assist (migrated)',
        provider, endpoint_url, model_id, api_key, enabled,
        false, false, true, 'migration'
      FROM uc_assist_config WHERE id = 1
      ON CONFLICT DO NOTHING;
    END IF;
    DROP TABLE uc_assist_config;
  END IF;
END $$;

COMMIT;
