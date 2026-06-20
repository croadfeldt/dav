-- migrate_024: first-class the evaluated git ref on a run (#branch-targeting).
-- The target branch + resolved HEAD SHA were previously only buried inside
-- run_sessions.trigger_payload (JSONB) and the per-UC uc_analyses.source_repo_shas.
-- Promote them to queryable columns so runs, results, and the decision/roadmap
-- pipeline can surface "evaluated against <branch>@<sha>" provenance.
-- branch is known at trigger time (resolved from the payload override or the
-- managed_repos registry default); the SHA is the cloned HEAD, captured at ingest.
ALTER TABLE run_sessions ADD COLUMN IF NOT EXISTS corpus_repo_branch TEXT;
ALTER TABLE run_sessions ADD COLUMN IF NOT EXISTS spec_repo_branch   TEXT;
ALTER TABLE run_sessions ADD COLUMN IF NOT EXISTS corpus_repo_sha    TEXT;
ALTER TABLE run_sessions ADD COLUMN IF NOT EXISTS spec_repo_sha      TEXT;
