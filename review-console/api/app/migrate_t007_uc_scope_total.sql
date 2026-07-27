-- t007: record a run's DECLARED UC scope, so live progress has a fixed denominator.
--
-- run_sessions.uc_total is written from ingested results, so while a run is in
-- flight it counts what has finished — and the UI was dividing by it. Numerator
-- and denominator moved together, so a 6-UC run reads "4/4 UC ✓4" at the moment
-- its 4th use case finishes: indistinguishable from a completed run. Observed on
-- dav-stage2-console-114714, where the masthead pill and the run header both said
-- 4/4 while the log-derived progress panel correctly said "3 / 6 · 50% done".
--
-- The scope is already known at trigger time — main.py computes it for the
-- timeout ETA and then discards it. This column keeps it.
--
-- NULL means "scope not recorded" (every pre-existing run, and full-corpus runs
-- where the count isn't known up front). Consumers must fall back to uc_total
-- rather than rendering 0.
ALTER TABLE run_sessions ADD COLUMN IF NOT EXISTS uc_scope_total INTEGER;

COMMENT ON COLUMN run_sessions.uc_scope_total IS
  'Declared UC count at trigger (uc_handles + uc_uuids + managed_uc_uuids). '
  'Fixed denominator for live progress. NULL = unknown scope (full-corpus run '
  'or a run created before t007); fall back to uc_total.';
