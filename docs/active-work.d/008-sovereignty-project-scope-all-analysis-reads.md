## ✅ SHIPPED 2026-06-24 — sovereignty: project-scope all analysis reads (roadmaps leak)
**Why:** the Roadmaps domain (Arch Review · Enhancement · Cap Map · Roadmap) showed **another
project's** data — the cross-project run IDOR the security review flagged P1. Build #354.
- **`/api/analysis/runs` + `/api/analysis/gaps`** were GLOBAL and **unauthenticated** → now
  project-scoped (active project's runs; orphans under default; single-user sees all) + `P_PROJECT_READ`.
- **Every run_id-addressed read** now enforces the run belongs to the active project via a shared
  `_require_run_in_project()` guard: capability-density, foundational-capabilities, uc-capability-map,
  `/api/analysis/output` (cached review/enhancement), `/api/results/{run_id}`, `…/uc/{uc}`. Workspace
  reads pass `allow_uningested=True` (live runs have no DB project link yet).
- **Verified:** DAV(727)=4 runs/96 gaps (its own); DCM(20)=50/500; a DCM run via the DAV project → **404**
  (was 200) for cached output + results. Arch-review/enhancement tabs are empty for DAV until generated.
- **Follow-up (defense-in-depth):** the UC-scoped *latest-analysis-per-uc* lookups (roadmap/cap-map scope
  mode) still pull latest across projects for a UC referenced into multiple projects — scope those too.

