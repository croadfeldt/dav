-- Migration 003: Project-scoped model defaults
-- Safe to run on existing installs; idempotent via IF NOT EXISTS.

BEGIN;

CREATE TABLE IF NOT EXISTS model_defaults (
    key         VARCHAR(64)  PRIMARY KEY,
    model_config_id INTEGER  REFERENCES model_configs(id) ON DELETE SET NULL,
    updated_by  VARCHAR(256) NOT NULL DEFAULT 'system',
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMIT;
