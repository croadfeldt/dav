# Maturity Wall (FlightPath-style assessment) — design

_Status: design, 2026-06-13. Tracks the new assessment-maturity epic. Models the Red Hat
**FlightPath Assessment** output (Function Appraisal wall + per-phase recommended states +
high-level roadmap) as a first-class, **configurable** DAV capability. Lives in the
**Assessments** domain (not the architecture roadmap), with explicit bridges to it (§Bridges)._

## Organizing principle — goal-driven, backward-chained (the spine)
**Goals are the root. Everything else is derived backward from them to inform → plan → execute.**
The maturity wall is not the point; it is the **measurement instrument** inside a
goal → gap → plan → execute loop:

```
GOAL (desired business outcome; e.g. "Resilience and Evolution", a Focus Area)
 └─ DESIRED STATE   = the goal expressed as TARGET maturity per capability   (the deck's "Customer Desired State")
     └─ CURRENT STATE = the assessment (maturity wall as-is)
         └─ GAP        = desired − current, per capability / category / overall
             └─ ROADMAP = the backward plan to close the gap, sequenced/phased  (the "Recommended State" phases)
                 └─ SWIMLANES / GANTT = that plan over time + ownership
                     └─ EXECUTE = enhancement actions + Measure-By KPIs → re-assess → goal
```

This is DAV's **outcomes-triangle (#120)** made concrete — `Outcome {statement, desired, current}
→ gap + roadmap` — with the FlightPath wall as the current/desired rendering. So **Goal ≈ Outcome
is the apex entity**; assessment, maturity levels, roadmap, and swimlanes all hang off it and exist
to *achieve the goal effectively*. Per the signal-over-noise north star, every view earns its place
by moving a goal forward (current standing, gap size, or the next action to close it).

Build the wall first (it's the instrument you need before gaps mean anything), but model the
**Goal** as the anchor from the start and wire Desired-State ← Goal, Gap, and the backward roadmap.

## Why
Consultants hand-produce FlightPath decks today (see the anonymized sample): a **maturity
wall** of capabilities grouped into categories, each scored **0–5** ("Function Appraisal":
Manual → Highly Optimized), rendered as **Current State**, **Recommended State (Phase 1/2/3)**,
and **Customer Desired State**; then per-phase **Recommendations** (Capability Matrix
`score→target` · Actions · Measure-By) and a **High-Level Roadmap** swimlane Gantt. DAV
should *generate* this from ingested assessment data — propose scores + targets, let the
consultant curate, and render the deck-quality views.

## Decisions (locked 2026-06-13)
1. **Fully configurable framework** — not a hard-coded FlightPath template. Dynamic content:
   **categories → capabilities** inside them; each capability has a maturity level, each
   category has a (roll-up, overridable) maturity level, and there's an **overall** maturity
   level. FlightPath ships as **one seeded framework** among potentially many.
2. **LLM-assessed + human override** — DAV proposes the 0–5 current scores from the ingested
   analysis/assessment data and proposes per-phase targets (foundational-first); the
   consultant overrides any cell and sets the desired state. Curated scores are the truth.
3. **Build order:** maturity-wall data model + **Current-State render** first → per-phase
   target states → Recommendations-per-Phase → High-Level Roadmap Gantt.
4. **The maturity wall is first-class and standalone** — it is valuable on its own (current
   standing, category/overall maturity, gaps, scoring) **even with no goals defined**. Goals are
   an apex *overlay*, never a prerequisite. **Do not sacrifice the maturity aspects** to the goal
   framing; the wall must stand alone AND serve goals.
5. **Goal ↔ maturity is bidirectional.** Top-down: a goal sets desired-state targets to drive
   toward. Bottom-up ("the goal is informing"): the assessment informs goal-setting — DAV surfaces
   low-maturity / high-leverage capabilities as **candidate goals**. Both directions first-class.
6. **Goals have all three origins** — `goals.origin ∈ {human, derived, customer}`: authored
   top-down (strategic objective / Focus Area), DAV-derived bottom-up (clustered from UCs +
   findings), or customer-desired-state. All supported; origin is recorded.
7. **Themes group capability targets** (full deck fidelity): `themes` (Focus Areas) → goals →
   per-capability `goal_targets` (maturity), with **theme-level + overall** desired-state rollups.

## Reuse — what already exists (#91, migration 019)
- **`assessments`** (id, handle, version, is_current, assessment_type, pillar, project_id, …).
- **`assessment_findings`**: `capability_handle` (row), **`category`** ("grouping … anchors the
  UI columns"), `state` (present|partial|absent|n/a), **`maturity` INTEGER 1..5**, `evidence`,
  `notes`, `catalog_capability_id` + `normalized_to_term_id` (catalog/taxonomy links),
  `normalization_status`.
- So the **current-state wall is renderable from existing data today** (group findings by
  `category`, show `capability_handle` + `maturity` heat-colored). The new work is structure
  (ordered configurable framework + bands + inflection), multi-state targets, rollups, render.

## Data model (additive; builds on the above)

**Goals / Outcomes (the apex — what everything derives backward from):**
- `themes` — `id, project_id, name, color, ord` — Focus Areas (the deck's 3: Next Gen Container
  Platform / Operational Efficiency / Enhance DevEx). Group goals; carry theme-level rollups.
- `goals` — `id, project_id, theme_id?, statement, origin ('human'|'derived'|'customer'),
  description, owner, priority, status, target_date, created_*`. A goal is a desired business
  outcome. Unifies with the #120 Outcome `{statement, desired, current}`. `origin` records which
  of the three sources it came from (all supported; §Decisions 6).
- `goal_targets` — `goal_id, framework_capability_id, target_maturity (0..5), weight, rationale`.
  The goal **expressed as target maturity per capability** = the Desired-State wall. (A goal may
  target a subset of capabilities; the union of a project's goal_targets = the overall desired
  state.) **Gap** for a capability = `max(goal_targets.target) − current score`.
- `goal_measures` — `goal_id, statement, metric, baseline, target` (the deck's "Measure By";
  how you know the goal is achieved). Reuses/feeds the per-phase Measure-By.
- The **roadmap phases** (target states P1/P2/P3 below) are the **backward plan** to move each
  capability from current → its goal target; sequencing respects dependency + foundational-first
  + gap size. Swimlanes/Gantt render that plan.

**The framework (the configurable template):**
- `assessment_frameworks` — `id, project_id, key, name, version, status, scale JSONB, is_seed`.
  - `scale` defines the appraisal levels: ordered `[{value, label, color}]`, e.g. FlightPath
    `0 Manual … 5 Highly Optimized` + `'-' Not Assessed`. Configurable per framework.
- `framework_categories` — `id, framework_id, key, label, band, ord, inflection_side`
  (`band` groups categories into the deck's 3 lanes — "Automation as a Product" / "Platform
  Operating Model" / "Strategy"; `inflection_side` = pre|post for the Inflection-Point divider).
- `framework_capabilities` — `id, category_id, key, label, ord, catalog_capability_id?`
  (the expected capabilities to score; optional link to the project capability catalog).
- `framework_states` — `id, framework_id, key, label, ord, kind` (`current|target|desired`).
  Default seed: Current, Phase 1, Phase 2, Phase 3, Desired. Configurable (a framework can
  define any states/phases).

**The scores (per assessment instance):**
- `assessment_capability_scores` — `assessment_id, framework_capability_id, state_key,
  maturity SMALLINT (0..5, NULL='-'), rationale, source ('llm'|'human'), updated_by, updated_at`.
  Generalizes the single `assessment_findings.maturity` to **(capability × state)**. The
  `current` state is back-filled from existing findings; targets/desired are new.
- **Rollups** — category-level + overall maturity are computed on read (default = mean of
  child capabilities, configurable to min/weighted) but **overridable** (store an explicit
  value in `assessment_category_scores`/`assessment_overall_scores` when set — else derived).
- `assessment_framework_link` — which framework an assessment is scored against
  (`assessment_id → framework_id`), plus the chosen states.

## Backend
- Framework CRUD: `GET/POST/PUT/DELETE /api/assessment-frameworks[/{id}]` (+ category/capability/
  state sub-resources), gated by `assessment.edit`. Seed a `flightpath-v1` framework on migrate.
- **`GET /api/assessments/{id}/maturity-wall?state=current`** — returns the structured wall:
  `bands[] → categories[] → capabilities[] {maturity, rationale, source}` + category rollups +
  overall, for the requested state. One call per rendered state (current / phaseN / desired).
- **`POST /api/assessments/{id}/score`** — LLM pass: read the assessment findings + linked
  analysis, propose 0–5 current scores + per-phase targets per capability (foundational-first,
  bounded by desired), write as `source='llm'`; never clobber `source='human'` cells.
- **`PUT /api/assessments/{id}/scores`** — human override of any cell (sets `source='human'`).

## UI (Assessments domain → new "Maturity Wall" sub-view)
- Render the heat-mapped wall: bands as lane labels, categories as columns (ordered, with the
  **Inflection-Point** divider between pre/post), capabilities as cells showing the 0–5 chip in
  the scale color; a **state switcher** (Current · Phase 1/2/3 · Desired) re-renders the same
  grid; greyed cells = already-met targets (deck behavior). Category + overall maturity badges.
- Editing: click a cell → set/override maturity + rationale (gated by `assessment.edit` +
  view-mode backstop). Framework editor (categories/capabilities/scale/states) is a separate
  configurable surface (later slice).
- Reuses the `.pf-view` scroll pattern (inner `flex:1; overflow-y:auto`) and the existing
  heat/badge styling.

## Later slices (after the wall)
- **Recommendations-per-Phase** — 3-column (Capability Matrix `current→target` chips · Actions ·
  Measure-By). Actions reuse the **enhancement plan**; Measure-By is a per-capability KPI list.
- **High-Level Roadmap Gantt** — swimlanes (categories/bands or focus areas) × timeline
  (Today→phase markers→Beyond), bars from the phased target deltas. **Shared renderer with the
  architecture roadmap (#141).**
- **Focus Areas / Priorities & Findings / Recommendations** summary views + the **Value Stream
  Map** (process-flow) — render from findings + themes.
- **Export** — deck/PDF/Jira generators off the persisted model (feeds SOW #142).

## Bridges to the architecture-roadmap process (#141) — answering "can we pull this in?"
The assessment wall and the UC-driven architecture roadmap share a **capability spine**, so they
enrich each other rather than duplicate:
1. **Shared capability identity.** `framework_capabilities.catalog_capability_id` +
   `assessment_findings.catalog_capability_id` tie wall capabilities to the same
   `capability_catalog` the architecture roadmap ranks by demand/dependency. One capability can
   then carry **both** an assessment maturity (current 0–5, gap-to-target) **and** UC-demand +
   dependency signal.
2. **Maturity-gap as a prioritization axis.** The architecture roadmap today ranks by demand ×
   dependency leverage. Add **maturity gap** (target − current): a capability that is
   high-demand **and** low-maturity (big gap) is the strongest priority. This is a new, free
   axis the assessment supplies to #141's sequencing.
3. **The assessment already *is* a roadmap.** The "Recommended State Phase 1/2/3" + the
   High-Level Roadmap Gantt are a phased plan derived from **maturity gaps**; #141 derives a
   phased plan from **UC demand/dependencies**. Same output shape (phases → capabilities →
   timeline) → **one shared roadmap/Gantt renderer**, two derivation sources.
4. **Convergence = the triangle (#120).** DAV's combined-outcomes synthesis reconciles multiple
   projections of one scope. Maturity-gap roadmap + UC-demand roadmap are two projections that
   synthesize into one prioritized roadmap; assessment-desired-state and UC-derived-desired-state
   are two inputs to the same apex.
5. **Findings/Actions/Measure-By reuse.** The deck's Findings/Recommendations/Actions/Measure-By
   are the shape of DAV's Track-1 analysis findings + enhancement actions + metrics — so the
   assessment's per-phase Actions reuse the **enhancement plan** and Measure-By reuses metrics.

**Build standalone in Assessments first**, but design the capability link (catalog_capability_id)
and the shared Gantt renderer from the start so #141 can consume the maturity-gap signal without
rework.

## Open questions
- Rollup default: mean vs min vs weighted-by-demand (start mean, make configurable).
- Should `framework_capabilities` be authored fresh, or seeded from the project capability
  catalog (reuse-first)? Likely: offer "import from catalog" when authoring a framework.
- States: fixed seed (Current/P1/P2/P3/Desired) vs fully arbitrary per framework (model allows
  arbitrary; UI seeds the FlightPath five).
