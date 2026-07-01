# Semi-automated Use-Case fix — design

Status: **adopted 2026-07-01** · Task: TODO 2 ("semi-automated Use Case fix — identify and suggest a
fix; need a UI for it and a bulk option"). Builds on #122 (UC health/validate/repair), #182
(valid-by-construction), and `uc_assist` (LLM authoring). Related: #181 (dedup), #179 (UC review).

## Problem
DAV can already **identify** invalid use cases (`/api/use-cases/health` → `_validate_uc_yaml`), and
can **deterministically repair** exactly one thing (a missing `handle`, `/repair`). Everything else an
author must hand-edit YAML for. The ask: close the loop — **identify → suggest a concrete fix →
review → apply**, for a single UC and in **bulk**.

## The three tiers of "fix"
A validation error is fixable at one of three levels. The design routes each error to the cheapest
tier that can handle it — deterministic first, LLM only when a value must be *invented*.

| Tier | Handles | Cost | Trust |
|------|---------|------|-------|
| **1 · Deterministic** | structural/enum errors with a mechanical fix: misplaced enum values, missing enums (→ safe default), missing `handle` (derive), invalid `generated_by.*` (→ default), invalid optional `priority` (drop), profile mismatch (copy the valid twin) | free, offline, no API key | high — always yields a *valid* structure; never fabricates meaning |
| **2 · LLM-assisted** | *semantic* gaps a machine can't invent: empty `scenario.description` / `intent` / `success_criteria`, empty `actor.persona` | one `uc_assist` call | medium — proposal must be re-validated + human-reviewed |
| **3 · Human** | anything the LLM can't confidently propose | — | — |

The **key deterministic move** is *enum relocation*. The validator's own schema hint enumerates the
common cross-dimension confusions (`expiry_enforcement` put in `failure_mode` when it's a
`lifecycle_phase`; policy values put in `governance_context`; …). Because every dimension's allowed
set is known, a value invalid in dimension A but valid in dimension B is **relocated** to B (if B is
empty/invalid) or dropped to A's default (if B is already set) — deterministically, no model needed.

## Suggest, then apply — always separated
The engine **suggests** (dry-run, never writes); **apply** is a distinct, gated step (reuses the
existing save path). This gives a reviewable diff every time and keeps the write path's RBAC/validation
guards unchanged. A suggestion carries: the original errors, the ordered list of **changes**
(`field · from → to · kind`), the **proposed YAML**, whether it re-validates clean, and any
**remaining** errors that still need tier 2/3.

## API
- `POST /api/use-cases/{uuid}/suggest-fix` — deterministic dry-run. Returns
  `{valid_before, errors_before[], changes[], proposed_yaml, valid_after, remaining_errors[],
  needs_semantic[], method:"deterministic"}`. With `?apply=true` (after review) it saves via the same
  update path as `/repair` (re-validates first; refuses to apply if it wouldn't improve validity).
- `GET /api/use-cases/fix-suggestions` — bulk dry-run: runs the deterministic suggester over every
  invalid managed UC in the active project (optionally `?set_id=`), returning one proposal per UC +
  a rollup (`fixable_clean`, `partial`, `needs_semantic`). Powers the bulk preview + count.

Deterministic-only in slice A → both endpoints work with **no model endpoint configured**.

## UI
- **Single** (slice A): the UC editor + the list health flag gain a **"Suggest fix"** action → a
  panel showing the change list + proposed-vs-current, with **Apply** / dismiss. Replaces the
  narrow "⚕ Repair" (missing-handle-only) affordance with the general suggester.
- **Bulk** (slice C): the masthead Coverage / health pill's "Repair N" becomes **"Fix N invalid"** →
  a review list (one row per UC, its changes, a checkbox), **Apply selected** / **Apply all clean**.

## Rollout slices
- **Slice A (this) — deterministic engine + endpoints + single-UC UI.** No LLM. Fixes the mechanical
  majority (enum relocation/defaults, handle, gen_by, priority, profile) safely and offline.
- **Slice B — LLM tier.** For `needs_semantic` errors, offer a `uc_assist`-backed suggestion
  (re-validated) as an opt-in second pass in the same panel.
- **Slice C — bulk-apply UI.** Review-list + apply-selected/all over the bulk endpoint.

## Verification
- `py_compile` + `node build.mjs --check` + eslint no-undef + e2e 100/0.
- Property the engine must hold: **a proposed fix always re-validates to ≥ the original validity**
  (the suggester never makes a UC *more* invalid); `apply=true` enforces this server-side.
- Deterministic output is stable (same input → same proposal); no `Date`/random in the suggester.
