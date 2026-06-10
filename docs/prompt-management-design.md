# Prompt Management (F8) — per-project, per-stage prompt customization

_Requested by Chris 2026-06-09: "custom per-project context / prompt management system…
each project can have additional context for the existing prompt and/or modify their
specific prompt for the different DAV stages." Repurpose the Improve view into a merged
"Prompts & Improvement" nav. Build to this doc; update after changes (house rule)._

## Decisions (Chris, 2026-06-09)
1. **Granularity:** section-level overrides **+** append context (the *more granular*
   option — section overrides subsume whole-prompt override = "override every section").
2. **Nav:** **merge** under one nav — `Prompts & Improvement`, tabbed: Prompt management +
   the existing diagnose/propose/A-B experiments (no feature loss).
3. **Stages:** **all** stages.
4. **Edit rights:** **new `prompt.manage` privilege**, grantable per project.

## What the engine actually does (verified 2026-06-09, see exploration)
- **Engine stages = Stage 2 only** (`engine/src/dav/ai/prompts.py`,
  `engine/src/dav/ai/agent.py`): one base **system** prompt
  (`_STAGE2_SYSTEM_PROMPT_TEMPLATE`, ll. 30–133) + **user** prompt builder, plus pass-1
  (findings) and pass-2 (analysis) override instructions appended to the base.
- **Console stages** (post-engine, in `review-console`): **Review** + **Enhancement**
  (`arch_review.py`); also **UC bulk-extract** (generative drafting).
- Base prompts are **monolithic string blobs** — sections are prose, not named.
- Engine reads **nothing from Postgres** at prompt time. Per-project data enters via
  **Tekton params → env vars** (pattern: `DAV_GROUNDING_NUDGE`,
  `DAV_SPEC_NAMESPACES_FILTER` in `prompts.py` 158–186) or a full `_sys_prompt_override`.
- **Existing reusable seam:** `project_stage_context` table + `_stage_context()` +
  `_inject_context()` (`main.py` ~9053–9074) already append per-(project, stage) context —
  but only for Review/Enhancement. **Reuse + generalize this**, do not invent a parallel.

## Model — prompts as ordered named sections (the clean core)
Each stage's base prompt is declared as an **ordered list of named sections** in one
source-of-truth registry. A stage may start as a **single section** (`body` = today's
whole blob) → **zero behavioral change**; sections get split out incrementally.

A project's customization for a (stage) is:
- `append_context` — free text, always appended as a trailing `## Project context` section.
- `section_overrides` — `{ section_name: replacement_text }`, replaces a named section.

**Assembly:** `final = join(ordered sections, applying section_overrides) + append_context`.
- Override `body` ⇒ effectively a full override (the coarse option, as a special case).
- No customization set ⇒ **byte-identical to today** (the safety invariant).

### Who assembles where
- **Console stages (Review/Enhancement/UC):** the API owns the base prompt text, so the
  **API assembles** (extends the existing `_inject_context` into a section-aware
  assembler). Low risk — post-engine, never affects the eval verdict.
- **Engine stage-2:** the **engine** owns the base templates, so the **engine assembles**.
  The API passes the project's customization (append + section_overrides as a JSON param)
  through Tekton → env (e.g. `DAV_STAGE2_PROMPT_CUSTOMIZATION`); `build_stage2_system_prompt`
  applies it. Requires sectioning `_STAGE2_SYSTEM_PROMPT_TEMPLATE` into a names→text dict.
  **Ship inert:** default registry = one `body` section, no overrides ⇒ identical prompt;
  any real override is a prompt-quality change → A/B with Chris before trusting it
  (stage-2 prompt sensitivity is documented — Qwen3.6 saga).

## Data model (reuse + extend `project_stage_context`)
```
project_stage_prompts                 -- per (project, stage) customization
  project_id, stage,                  -- stage ∈ the stage registry keys
  append_context TEXT,                -- trailing project-context section
  section_overrides JSONB,            -- { section_name: text }
  updated_by, updated_at
  UNIQUE(project_id, stage)
```
(If `project_stage_context` is shape-compatible, evolve it in place rather than add a
table — decide at build time by inspecting its columns.)

Stage + section **registry** (code, not DB) — the catalog of stages and their base
sections, served read-only to the UI so editors see what they're overriding.

## RBAC
- New privilege **`prompt.manage`** (constant in `rbac.py`), seeded into the privilege
  catalog + default roles (project-admin gets it; grantable per project). Guards the
  PUT endpoints. Reads gated to members.

## API
- `GET  /api/prompts/stages` — registry: stages + ordered base sections (text).
- `GET  /api/prompts/project` — active project's customizations (append + overrides).
- `PUT  /api/prompts/project/{stage}` — upsert append_context / section_overrides
  (`prompt.manage`). Audited (F3).
- `GET  /api/prompts/project/{stage}/preview` — assembled final prompt (base + overrides
  + append) for the editor's live preview.

## UI — `Prompts & Improvement` (merge Improve)
- Rename the Improve nav slot; the view gets two tabs: **Prompt management** and the
  existing **Diagnose / Propose / Experiments** (move current markup under a tab).
- Prompt management tab: stage selector → for the chosen stage, show each base section
  (read-only) with an "Override" editor beside it + a single "Additional context" box;
  live **Preview** of the assembled prompt; Save (gated on `prompt.manage`).

## Build order (risk-ascending)
1. Registry + data model + RBAC privilege + API + UI (merge nav, editor, preview). ✅
2. Wire **console stages** (Review/Enhancement) through the section-aware assembler —
   safe, immediate value, reuses `_inject_context`. ✅ (append-live; split 2026-06-09)
3. Wire **engine stage-2**: section the base template (inert), thread the customization
   param/env, assemble in `build_stage2_system_prompt`. **Byte-identical-by-default**;
   any actual stage-2 override is A/B'd with Chris before use.

## A/B testing — SHIPPED 2026-06-09 (backport the static comparator, no duplication)
Chris: "don't duplicate functionality, backport the static A/B test into this framework."
The "static A/B test" = the engine's semantic Analysis comparator
(`engine/src/dav/evaluator/compare.py`, CLI `scripts/compare_analyses.py`):
equivalent/changed + per-finding severity. Backported into the experiments framework,
**server-side** (analyses reside on the run-workspace PVC; only the diff crosses to the
browser — raw, possibly-confidential analyses never leave the cluster; an in-browser JS
port would have duplicated the comparator AND shipped both full analyses to the client).

- **Reuse, no fork:** the API image **vendors `compare.py` from the engine at build
  time** (`ansible/.../review_console.yaml` → `app/_vendor/compare.py`, gitignored) —
  single source in `engine/`. `app/analysis_compare.py` wraps it: `compare_runs(run_a,
  run_b, uuids)` reads both arms' analyses via `results.get_analysis` → per-UC
  equivalent/changed + severity + aggregate. Import is guarded (`available()`), so a
  missing vendor never crashes the app.
- **Static mode:** `POST /api/experiments/static-compare {run_a, run_b, set_id}` compares
  two EXISTING runs (no new runs) and records the result in the `experiments` table
  (`change_spec.type='static_compare'`, status `scored`) — reuses the Experiments list/
  detail UI. Form: "+ Static A/B (compare two existing runs)".
- **Dynamic dimension:** `_maybe_score_experiment` now also attaches the semantic diff
  (`candidate_score.semantic_diff`) over the eval UUIDs when both arms of a launched
  experiment finish — alongside the existing success-rate gate.
- **UI:** `_renderSemanticDiff` shows changed/equivalent/missing + max severity + per-UC
  findings, in both static and dynamic experiment detail.

### (Superseded sketch) prompt-content A/B via a new engine seam
DAV already A/B tests config deltas (sampling, max_tokens, grounding_nudge) over an eval
set with scoring + a promote/revert/inconclusive **gate** (`experiment_eval.py`,
`migrate_016_experiments.sql`, `/api/experiments`, Experiments tab). Prompt A/B reuses ALL
of it — trigger two runs, score, gate, render — adding one new `change_spec.type`.

**Key finding (2026-06-09):** `DAV_GROUNDING_NUDGE` is a **boolean flag** that injects
*fixed* text (`prompts.py` ~178) — it cannot carry an arbitrary context string. So prompt
A/B needs a **new append-only engine seam**:

- **Engine:** new env `DAV_STAGE2_EXTRA_CONTEXT`; `build_stage2_system_prompt` appends it
  as a `## Project context` section when set. **Append-only, byte-identical when empty** —
  the SAME risk class as the shipped grounding nudge (not the risky full section override).
- **Plumbing (mirror grounding-nudge exactly):** `validations.trigger_run` +
  `_mk_pipelinerun` param `stage2-extra-context` → pipeline-stage2 passthrough →
  `dav-run-corpus` task param → env `DAV_STAGE2_EXTRA_CONTEXT`. Requires an **engine image
  rebuild + Tekton apply** (bigger deploy than `--tags review-console`).
- **Experiment type `stage2_context`:** candidate = a context string; baseline arm
  `extra_context=''` (or a baseline string), candidate arm `extra_context=candidate`.
  Reuse scoring/gate/UI. Touches the eval path ONLY inside an explicitly-launched
  experiment — **normal runs stay byte-identical** (the stage-2 hold is respected).
- **Promotion:** a winning `stage2_context` experiment writes the candidate into the
  project's stage-2 additional context (`project_stage_context`, stage `stage2-analysis`).
  *Enabling it on normal runs* (passing that context as `DAV_STAGE2_EXTRA_CONTEXT` every
  run) is a separate, now-evidence-backed toggle — the moment the stage-2 hold lifts.
- **UI:** "A/B test this context" in the Prompt management tab (prefill candidate = the
  stage's current additional context, pick an eval set, launch) → result + verdict in the
  Experiments tab; extend the ad-hoc experiment form with the `stage2_context` type.

Net: A/B becomes the safe, gated path by which a stage-2 customization earns its way into
normal runs — exactly the "A/B before trusting it" requirement.
```
