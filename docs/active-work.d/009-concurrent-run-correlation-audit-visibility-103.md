## ✅ SHIPPED 2026-06-24 — concurrent-run correlation + audit visibility (#103, #200/#201)
**Why:** two concurrent ingestions showed each other's live stats, and — more seriously — the same
timestamp re-correlation decided which PROJECT owns ingested results, so cross-project concurrent runs
could persist results under the wrong project (sovereignty). Builds #348–#353.
- **Root cause:** the engine generates its own workspace `run_id` and never records the PipelineRun
  name, so the API correlated run→workspace by start time. Variable pod-init delay made that swap
  concurrent runs (a 6-UC DAV run showed a 15-UC DCM run's stats, and vice-versa).
- **Fix (API-only):** correlate by the run's **scope size** (`len(trigger_payload.uc_uuids)` or its
  set's member count) matched to the workspace `total_ucs`; timestamp is only a tiebreak. Applied to
  the **live display** (`_correlate_inflight_progress` → get_run_detail + turns; each dir claimed once)
  AND the **ingestion attribution** (the `run_sessions` lookup that sets `analysis_runs.project_id`/
  `run_name`). Verified: DCM's 15-UC run correlates correctly; the DAV 6-UC run no longer steals it.
- **Limits (→ #201):** forward-only (already-ingested runs keep their `project_id`); two concurrent
  **same-size** runs still fall back to timestamp. Durable fix = engine stamps the PipelineRun name into
  `run-summary.yaml`/`run-progress.yaml` (`$(context.pipelineRun.name)` via Tekton) and the API matches
  on it. Optional one-time audit/repair of historically mis-attributed runs.
- **Masthead pill is project-scoped (sovereignty-correct)** — it shows only the active project's
  ingestions, not a bug; cross-tenant aggregation belongs in a platform-admin operator view (→ #200).
- **Audit visibility (#103):** `audit.query` now returns `object_type`/`object_id`/`detail` (were
  stored but dropped from the projection); the Audit view gained Object + Detail columns. The
  delete-propagation impact is now inspectable.

