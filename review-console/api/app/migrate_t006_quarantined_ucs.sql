-- t006 — surface quarantined UCs on the run record.
--
-- The engine already partitions the corpus before the analyze pass and records
-- what it dropped (run_summary.json: `quarantined` count + `quarantined_ucs`
-- detail, run_corpus.py). Nothing on the console side ever read those fields, so
-- a UC that failed to load or failed profile validation vanished from the run
-- with NO author-visible signal — the silent-quarantine trap. Five corpus files
-- sat quarantined this way until someone noticed by hand.
--
-- Give the data a home so the API and UI can show it. Nullable + defaulted:
-- pre-existing runs simply report nothing rather than a false zero, which
-- matters because "0 quarantined" and "we never recorded it" are different
-- claims and the run list should not conflate them.
ALTER TABLE analysis_runs
    ADD COLUMN IF NOT EXISTS quarantined integer,
    ADD COLUMN IF NOT EXISTS quarantined_ucs jsonb;

COMMENT ON COLUMN analysis_runs.quarantined IS
    'Count of corpus UCs excluded before the analyze pass (load or profile-validation failure). NULL = the run predates quarantine recording, which is not the same as zero.';
COMMENT ON COLUMN analysis_runs.quarantined_ucs IS
    'Per-UC quarantine detail from run_summary.quarantined_ucs: path plus the reason it was excluded.';
