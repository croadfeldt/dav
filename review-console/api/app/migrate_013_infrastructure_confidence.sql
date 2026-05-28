-- Migration 013: Infrastructure-confidence persistence
-- Captures the per-UC infrastructure-induced quality assessment that the
-- engine writes to metadata.infrastructure_confidence on every Analysis.
-- Surfaces in the Results tab card + Run drawer aggregate so operators
-- can spot UCs whose grounding was constrained by hardware/context limits.
-- Idempotent — safe to re-apply.

ALTER TABLE uc_analyses
  ADD COLUMN IF NOT EXISTS infra_confidence_label       TEXT,
  ADD COLUMN IF NOT EXISTS infra_confidence_score       INT,
  ADD COLUMN IF NOT EXISTS infra_confidence_signals     JSONB,
  ADD COLUMN IF NOT EXISTS infra_confidence_explanation TEXT,
  ADD COLUMN IF NOT EXISTS infra_confidence_recommendations JSONB;

CREATE INDEX IF NOT EXISTS idx_uc_analyses_infra_label ON uc_analyses(infra_confidence_label);
