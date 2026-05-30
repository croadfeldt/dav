-- Migration 016: self-improvement loop (Phase 2) — A/B candidate experiments.
--
-- An experiment A/B-tests a proposed config change: a baseline run and a
-- candidate run over the SAME eval set, differing only by the candidate's
-- config delta (e.g. max_tokens via a per-run PipelineRun param — isolated, no
-- production/profile mutation). experiment_eval.gate() scores both and decides
-- promote / revert / inconclusive. The gate refuses to promote a change that
-- introduces a new high-severity failure class (the v1.9 lesson, enforced).
-- See docs/dav-self-improvement-vision.md §3 (Phase 2).

BEGIN;

-- Structured, machine-applyable delta on a proposal (Phase 1 emits prose;
-- this is the bridge to an A/B-testable change).
ALTER TABLE improvement_proposals ADD COLUMN IF NOT EXISTS change_spec JSONB;

CREATE TABLE IF NOT EXISTS experiments (
    id               SERIAL PRIMARY KEY,
    proposal_id      INTEGER REFERENCES improvement_proposals(id) ON DELETE SET NULL,
    title            TEXT,
    -- {type:'max_tokens', baseline:<int|null>, candidate:<int>}  (extensible to sampling)
    change_spec      JSONB NOT NULL DEFAULT '{}'::jsonb,
    eval_set_id      INTEGER,
    eval_set_name    TEXT,
    sample_count     INTEGER NOT NULL DEFAULT 1,
    -- Tekton PipelineRun names for the two arms.
    baseline_run     TEXT,
    candidate_run    TEXT,
    -- Scores (experiment_eval.score_run output) once both arms finish.
    baseline_score   JSONB,
    candidate_score  JSONB,
    verdict          TEXT,            -- promote | revert | inconclusive | NULL(pending)
    verdict_reason   TEXT,
    -- running -> scored -> promoted|discarded ; error on failure to launch/score.
    status           TEXT NOT NULL DEFAULT 'running',
    auto_promote     BOOLEAN NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by       TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_experiments_status   ON experiments(status);
CREATE INDEX IF NOT EXISTS idx_experiments_proposal ON experiments(proposal_id);
CREATE INDEX IF NOT EXISTS idx_experiments_created  ON experiments(created_at DESC);

COMMIT;
