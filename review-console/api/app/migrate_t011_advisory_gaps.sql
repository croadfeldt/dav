-- E3 advisory split: sub-quorum findings are stored but flagged, so the
-- primary pool (and precision math) reads quorum-backed findings only.
-- Field evidence: 24/30 real-corpus findings a frontier judge rejected were
-- 1/3-consensus retrieval hedges (F2 adjudication, 2026-07-28).
ALTER TABLE uc_gaps ADD COLUMN IF NOT EXISTS advisory BOOLEAN NOT NULL DEFAULT false;
