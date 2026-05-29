-- Migration 014: per-model capability flags + per-(model, use) sampling
-- parameter profiles.
--
-- Background: vLLM rejects certain sampler params under specific server
-- configurations (e.g. `min_p` and `logit_bias` HTTP 400 when speculative
-- decoding is active, surfaced on the Qwen3.6-27B MTP A/B 2026-05-29).
-- DAV had three layers of sampler config (mode defaults in code, CLI
-- overrides, hard-coded model match) but no per-(model, use) table that
-- operators could tune without an engine rebuild. This migration adds:
--
--   1. `model_configs.capabilities` (JSONB) — static facts about the
--      server side of an endpoint: what params are/aren't supported,
--      max_tokens default, etc. Examples:
--        {"speculative_decoding": true, "supports_min_p": false,
--         "supports_logit_bias": false, "max_tokens_default": 16384}
--
--   2. `model_use_profiles` table — per (model_config_id, use_key)
--      sampling overrides. use_key is one of a whitelisted set covering
--      every DAV LLM consumer:
--        evaluation_verification, evaluation_explore, evaluation_reproduce,
--        arch_review, uc_assist, enhancement
--      The `params` JSONB carries any subset of:
--        {top_k, top_p, min_p, temperature, max_tokens, seed,
--         chat_template_kwargs, ...}
--      Engine resolution order at call time:
--        per-run override (CLI/UI) > use_profile row > mode default in code
--      Engine drops any param a row in `capabilities` flags as unsupported,
--      regardless of source.
--
-- Idempotent via IF NOT EXISTS / DO blocks.

BEGIN;

-- 1. Add capabilities column to model_configs.
ALTER TABLE model_configs
  ADD COLUMN IF NOT EXISTS capabilities JSONB NOT NULL DEFAULT '{}'::jsonb;

-- 2. model_use_profiles table.
CREATE TABLE IF NOT EXISTS model_use_profiles (
    id                SERIAL PRIMARY KEY,
    model_config_id   INTEGER NOT NULL REFERENCES model_configs(id) ON DELETE CASCADE,
    use_key           TEXT NOT NULL,
    params            JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes             TEXT NOT NULL DEFAULT '',
    updated_by        TEXT NOT NULL DEFAULT 'migration',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT model_use_profiles_use_key_chk CHECK (
        use_key IN (
            'evaluation_verification',
            'evaluation_explore',
            'evaluation_reproduce',
            'arch_review',
            'uc_assist',
            'enhancement'
        )
    ),
    CONSTRAINT model_use_profiles_uniq UNIQUE (model_config_id, use_key)
);

CREATE INDEX IF NOT EXISTS idx_model_use_profiles_model
  ON model_use_profiles (model_config_id);

-- 3. Seed: Qwen3.6-27B (MTP) capabilities. Flags both min_p and
-- logit_bias as unsupported because --speculative-config qwen3_next_mtp
-- is active in the ServingRuntime. The engine drops these params
-- regardless of mode default or per-use profile.
UPDATE model_configs
   SET capabilities = capabilities || jsonb_build_object(
         'speculative_decoding', true,
         'supports_min_p',       false,
         'supports_logit_bias',  false
       )
 WHERE model_id = 'qwen36-27b';

COMMIT;
