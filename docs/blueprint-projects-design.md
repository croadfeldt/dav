# Blueprint + linked projects — reuse a setup across datasets/engagements

_Chris 2026-06-09: "re-use projects [with] new sets of data… set up an assessment
pipeline/prompts/etc, then run multiple sets of data through it." Chosen model: **hybrid
blueprint + linked projects**. Capture → roadmap → build. See prompt-management-design.md
(prompts), capability-catalog-design.md (taxonomy tiers), active-work.md._

## Problem
A consulting methodology (e.g. automation-strategy assessment) has a reusable **setup** —
prompts (per stage), capability taxonomy, capability catalog defaults, assessment pipeline
(type + parser), eval model config. Today that setup lives inside a single project, so
applying the same methodology to a new client/dataset means rebuilding it. But clients are
**confidentiality boundaries** (data must stay isolated per engagement). So we need: one
canonical setup, many isolated datasets.

## Model — blueprint owns the setup; engagement projects inherit it
- **Project kind:** add `kind ∈ {standard, blueprint, engagement}` (default `standard`)
  and `blueprint_project_id` (nullable FK → projects). A **blueprint** project holds the
  canonical setup and (typically) no engagement data. An **engagement** project links to a
  blueprint and holds **only its own data** (its own RBAC/visibility boundary).
- **Inheritance (config, resolved blueprint→local with local override):**
  - **Prompts** (`project_stage_context`, F8): resolve a stage's context/overrides from
    the engagement, else the blueprint. Override per stage locally.
  - **Capability taxonomy** (`capability_taxonomy_terms`): the blueprint's vocabulary is
    the engagement's controlled vocabulary. Aligns with the existing scope tiers
    (global/shared/domain/project) — a blueprint ≈ the `shared` tier for its linked set.
  - **Capability catalog** canonical/curated entries: inherited as the starting catalog.
  - **Assessment pipeline:** `assessment_type` + parser selection inherited (F7).
  - **Model/eval config** (optional, phase 2): inherit the evaluation default model.
- **NOT inherited (per-engagement, isolated):** the **data** — UC sets, assessments,
  runs, results, findings. This is the dataset. Each engagement = one dataset (or holds
  several sets/assessments as sub-datasets).
- **Datasets as first-class:** within an engagement, a **dataset** = a UC set and/or an
  assessment ingest. Results group per dataset; the static comparator/experiments diff
  datasets (e.g. assessment batch A vs B gap deltas).

## Resolution mechanism
A single resolver `effective_config(project_id, key)` that returns the engagement's value
or falls back to its `blueprint_project_id`'s value (one hop; blueprints don't nest in v1).
Wire the existing readers through it:
- `_stage_context` / `_stage_customization` (prompts) → fall back to blueprint.
- taxonomy seed/normalize → read blueprint-tier terms.
- assessment ingest → inherit assessment_type/parser default.
Each is additive and back-compatible (NULL blueprint ⇒ today's behavior exactly).

## Build phases
1. **Schema + link:** `projects.kind`, `projects.blueprint_project_id`; UI to mark a
   project a blueprint and link an engagement to it; the resolver + wire prompts first
   (smallest, highest-value inheritance).
2. **Taxonomy + catalog inheritance** (reuse the scope tiers).
3. **Assessment-pipeline inheritance + dataset-grouped results + cross-dataset compare.**
4. **(opt) model/eval config inheritance.**

## Confirmed (Chris 2026-06-09)
Both patterns must coexist: **some projects hold multiple sets** (within-project datasets),
**others share a template/prompts but with isolated data** (blueprint-linked engagements).
Isolation between like projects is required — the blueprint shares *setup*, never *data*.

## Open questions
- Blueprint nesting (v1: no). Partial override granularity for taxonomy (whole vs term).
- Promotion: edit in an engagement → "push up to blueprint" affordance?
- RBAC: who can edit a blueprint (a new `blueprint.manage` privilege vs platform-admin).
