-- Migration 015: self-improvement loop (Phase 1) — diagnose & propose.
--
-- Stores the typed change proposals the diagnoser (diagnose.py) files for a
-- failed run, plus a snapshot of the failure taxonomy that produced them so
-- the evidence survives workspace cleanup. Proposals are review artifacts;
-- nothing here applies a change (that is Phase 2, human/auto-gated).
-- See docs/dav-self-improvement-vision.md.

BEGIN;

-- One row per diagnose() call — groups its proposals and keeps the taxonomy.
CREATE TABLE IF NOT EXISTS run_diagnoses (
    batch_id    TEXT PRIMARY KEY,           -- uuid generated at diagnose time
    run_id      TEXT NOT NULL,              -- workspace results-dir id (timestamped)
    run_name    TEXT,                       -- Tekton pipelinerun name, if resolved
    taxonomy    JSONB NOT NULL DEFAULT '{}'::jsonb,
    used_llm    BOOLEAN NOT NULL DEFAULT false,
    rule_count  INTEGER NOT NULL DEFAULT 0,
    llm_count   INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by  TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_diagnoses_run ON run_diagnoses(run_id, created_at DESC);

-- One row per proposed change.
CREATE TABLE IF NOT EXISTS improvement_proposals (
    id               SERIAL PRIMARY KEY,
    batch_id         TEXT NOT NULL REFERENCES run_diagnoses(batch_id) ON DELETE CASCADE,
    run_id           TEXT NOT NULL,
    run_name         TEXT,
    signature_class  TEXT,                  -- failure signature that triggered it
    kind             TEXT NOT NULL,         -- prompt|profile|route|tool|code|infra|data
    target           TEXT,                  -- the knob/file to change
    rationale        TEXT,
    proposed_change  TEXT,
    predicted_effect TEXT,
    confidence       TEXT,                  -- high|medium|low
    source           TEXT,                  -- rule|llm
    evidence         JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Review lifecycle. Phase 1 only moves through proposed→accepted/rejected;
    -- 'applied' is set by Phase 2 once a change actually ships.
    status           TEXT NOT NULL DEFAULT 'proposed',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by       TEXT,
    reviewed_at      TIMESTAMPTZ,
    reviewed_by      TEXT,
    review_note      TEXT
);
CREATE INDEX IF NOT EXISTS idx_improvement_proposals_run    ON improvement_proposals(run_id);
CREATE INDEX IF NOT EXISTS idx_improvement_proposals_status ON improvement_proposals(status);
CREATE INDEX IF NOT EXISTS idx_improvement_proposals_batch  ON improvement_proposals(batch_id);

COMMIT;
