## 🔭 NEW EPIC 2026-06-13 — Maturity Wall: goal-driven, backward-chained assessment (#147)
Design + requirements: **`docs/maturity-wall-design.md`**. Models the Red Hat **FlightPath**
assessment (Function Appraisal maturity wall + per-phase recommended states + high-level
roadmap) as a first-class, **configurable** capability in the **Assessments** domain.
- **Organizing principle:** goal-driven, backward-chained — **Goals are the apex**; Desired
  State (target maturity per capability) → vs Current State (the assessment) → **Gap** →
  **Roadmap** (backward plan) → Swimlanes/Gantt → Execute (enhancement actions + Measure-By).
  This is the #120 outcomes-triangle made concrete. The **maturity wall is first-class and
  standalone** (valuable with no goals); goal↔maturity is **bidirectional** (goals drive
  desired-state; the assessment *informs* goals). Goals have **all 3 origins**
  (human/derived/customer); **themes group per-capability targets** with rollups.
- **Reuse:** builds on the existing assessment model (#91, migration 019) — `assessment_findings`
  already carries `category`/`capability_handle`/`maturity`/`catalog_capability_id`. Shares the
  capability spine with the architecture roadmap (#141) → maturity-gap becomes a free
  prioritization axis + a shared Gantt renderer (design §Bridges).
- **✅ SHIPPED slice 1 (schema, migration 021):** themes · goals · goal_targets · goal_measures ·
  assessment_frameworks (configurable 0–5 scale) · framework_categories (band + Inflection-Point)
  · framework_capabilities (catalog-linked) · framework_states · assessment_capability_scores
  (capability×state→0–5, source llm|human). Migration wrapped in try/except (can't crash boot);
  applied + verified via API boot logs. **Deferred (slice 1b):** FlightPath framework data seed +
  back-fill `current` from findings (separate verifiable pass).
- **✅ SHIPPED slice 1b (seed):** `maturity_seed.py` — the global `platform-maturity-v1` template
  (0–5 scale + 5 states + bands→categories→capabilities), idempotent, seeded on boot.
- **✅ SHIPPED slice 3 (UI):** Assessments → Maturity Wall heat-map + state switcher (reads
  `/api/assessments/{id}/maturity-wall?state=` / the framework skeleton).
- **✅ SHIPPED slice 2 (backend) 2026-06-17 (#149):** the write-side the UI consumes —
  - **Framework CRUD** (`app/maturity_scoring.py` + thin endpoints): `POST /api/assessment-frameworks`
    (project-scoped; `clone_from=<seed id>` deep-copies scale + states + categories + capabilities,
    reuse-first), `PUT`/`DELETE /api/assessment-frameworks/{id}`, and category / capability / state
    sub-resources (`…/categories[/{cid}]`, `…/categories/{cid}/capabilities`, `…/capabilities/{capid}`,
    `…/states[/{key}]`). **Seed templates (`project_id IS NULL`) are read-only** — projects clone +
    edit. All gated by `assessment.edit` in the owning project (`_gate_framework_edit`).
  - **`POST /api/assessments/{id}/score`** — LLM scoring through DAV's **existing** model call path
    (`_make_diagnosis_call_fn` over a `model_configs` row, resolved via the
    assessment-ingest → arch-review → evaluation default chain — the same path assessment-ingest uses).
    Reads findings + the linked framework, proposes 0–5 per capability × **target/desired** state,
    persists as `source='llm'`. **Never clobbers a `source='human'` cell** (curated scores are the
    truth — the conflict `DO UPDATE … WHERE source <> 'human'` enforces it). Returns
    `{proposed, written, skipped_human}`.
  - **`PUT /api/assessments/{id}/scores`** — human override of any cell(s) with **provenance**
    (`source='human'`, `updated_by`, `updated_at`); `maturity=null` deliberately clears to '-' Not
    Assessed. A human score always wins and survives the next LLM pass.
  - **Tests:** `test_maturity_scoring.py` (8) — maturity coercion, prompt build (targets-only +
    cap-id listing), response parse/validation (drops out-of-range / unknown-cap / non-target,
    strips code fences, rejects non-JSON), and the LLM-vs-human provenance rules via a fake conn.
    Route-shadow + migration-wiring guards pass (272 routes / 22 migrations).
- **NEXT:** per-phase targets · Recommendations-per-Phase · High-Level Roadmap Gantt · export
  (feeds SOW #142). Tasks #150+.

