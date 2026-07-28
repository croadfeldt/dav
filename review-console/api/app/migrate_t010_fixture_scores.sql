-- t010: fixture battery scores — validation is a trend, not an event (ADR-006,
-- applied to the instrument itself per Chris's process ruling #1).
--
-- One row per scored fixture run. engine_commit is real as of dav#97/#98 (the
-- image bakes it; ingest stores it per analysis), so a precision regression
-- arrives WITH the commit that caused it attached. detail carries the full
-- per-UC found/missed/noise breakdown so no consumer needs to re-derive it.
CREATE TABLE IF NOT EXISTS fixture_scores (
  id               BIGSERIAL PRIMARY KEY,
  run_id           TEXT NOT NULL UNIQUE,   -- rescore is idempotent (upsert)
  run_name         TEXT,
  model            TEXT,
  sample_count     INTEGER,
  engine_commit    TEXT,
  precision_score  NUMERIC(4,3),
  recall           NUMERIC(4,3),
  verdict_accuracy NUMERIC(4,3),
  tp INTEGER, fp INTEGER, fn INTEGER,
  verdict_ok INTEGER, verdict_total INTEGER,
  detail           JSONB,
  source           TEXT NOT NULL DEFAULT 'manual',   -- 'nightly' | 'manual' | 'prompt-loop'
  scored_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fixture_scores_time ON fixture_scores (scored_at DESC);
