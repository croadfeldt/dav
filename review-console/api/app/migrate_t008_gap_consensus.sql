-- t008: persist per-gap ensemble agreement.
--
-- The ensemble computes agreement for every merged gap ("k of n samples found
-- this") and, until dav#80, threw it away: the verdict was derived from the
-- unfiltered union, so a 1-of-3 gap weighed exactly as much as a 3-of-3 one and
-- verdicts got monotonically worse the more samples you took.
--
-- #80 fixed the derivation. This fixes the record. Without it the console, the
-- roadmap and any cross-run comparison still cannot tell a finding all three
-- samples agreed on from one a single sample imagined — which is precisely the
-- distinction that decides whether a gap is worth acting on.
--
-- NULL means "not an ensemble analysis" (single-sample run, or a gap ingested
-- before this migration). Consumers must treat NULL as "no disagreement known",
-- never as low agreement.
ALTER TABLE uc_gaps ADD COLUMN IF NOT EXISTS consensus TEXT;

COMMENT ON COLUMN uc_gaps.consensus IS
  'Ensemble agreement as "k/n" — k of n samples identified this gap. NULL = '
  'single-sample run or pre-t008 ingest. A gap is quorum-backed (and therefore '
  'votes in verdict derivation) when k*2 >= n; sub-quorum gaps are kept and '
  'reported but do not move the verdict. See engine ensemble._consolidate_gaps.';

-- Partial index: the queries that care about this are "show me the findings the
-- samples actually agreed on", so index only the rows that carry a value.
CREATE INDEX IF NOT EXISTS idx_uc_gaps_consensus
  ON uc_gaps (run_id, consensus) WHERE consensus IS NOT NULL;
