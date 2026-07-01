# Ingest vs Analyze — naming decision (code + DB)

Status: **adopted 2026-07-01** · Task: #258 · Follows the user-facing rename (PR #35).

## The two verbs (settled meaning)
- **Analyze** — the gap-analysis pipeline that evaluates use cases against the architecture/repos
  (the Tekton run that produces `uc_analyses` / `uc_gaps` / `uc_capabilities`).
- **Ingest** — bringing data **into** DAV: importing/creating use cases, importing assessment
  findings, and loading a run's produced results **into Postgres**.

## What the code/DB survey found
The DB and API are **already correctly named** — no migration is warranted:

| Name | Meaning | Verdict |
|------|---------|---------|
| tables `analysis_runs`, `run_sessions`, `uc_analyses` | the analyze run + its results | ✅ already "analysis" |
| column `analyzed_at` | when the engine analyzed the UC | ✅ correct |
| column `ingested_at` | when the result row was **loaded into Postgres** (distinct from `analyzed_at`) | ✅ correct — it *is* an ingest-into-DB timestamp |
| `POST /api/analysis/ingest/{run_id}` + `_ingest_run_analyses()` | loads a run's analysis **results** into Postgres | ✅ correct — loading data in |
| `POST /api/assessments/ingest*`, `ingestAssessment*`, `_asIngest*` | assessment-findings import | ✅ correct (bringing data in) |

There is **no `ingestions` table** and no API path that calls the *analyze pipeline* "ingest". So the
breaking surface (DB migration, API contract, engine coordination) that #258 was held for **does not
exist** — the backend verbiage was already right.

## What was actually wrong (and fixed here)
Only a handful of **internal front-end helper functions** named the *analyze run* "ingestion". These
are UI-internal (no DB, no API, no external contract), renamed with eslint `no-undef` as the safety net:

| Old | New | What it does |
|-----|-----|--------------|
| `ingestStaleUCs()` | `analyzeStaleUCs()` | start an analysis over the stale/un-evaluated UCs |
| `_reingestUC()` | `_reanalyzeUC()` | re-run analysis for one UC |
| `_renderIngestionAudit()` | `_renderAnalysisAudit()` | render the per-UC analysis-coverage audit |
| `_paintIngestionAudit()` | `_paintAnalysisAudit()` | repaint that audit |

The user-facing labels for these were already corrected in PR #35 (Ingest→Analyze).

## Deliberately NOT changed
- `ingested_at`, `/api/analysis/ingest`, `_ingest_run_analyses`, assessment-ingest names — all mean
  "load data in", which is the correct sense of *ingest*.
- Renaming `ingested_at` → anything would be a breaking DB migration for a **correctly-named** column.

## Related
- #178 (masthead "Ingestion pill not updating") is a **behavioural** bug, not naming — tracked separately.
- The `run_sessions` / "run" vs "analysis" terminology is internally consistent and out of scope here.
