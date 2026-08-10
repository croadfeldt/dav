# Persona-focus: the console rewrite plan

**Status:** in progress on `feat/persona-focus`. Baseline tagged `function-focus`.
**Author:** overnight autonomous session 2026-08-09/10, for Chris's review.

## Why

The UX paradigm was decided 2026-06-11/12 (`docs/ux-paradigm-design.md`,
`docs/uc-scoped-evaluation-design.md`) and then largely not built. Measured on `main`
at tag `function-focus`:

| decided | built |
|---|---|
| Outcome/Initiative as a first-class object above the UC | **no** — absent from the API |
| Persona as the organizing lens | **partial** — 28 refs in 2 of 19 modules; a dropdown, not a lens |
| Masthead = engagement + persona + freshness; **retire the run selector** | **no** — run selector still global (13 refs) |
| Scope = UC/Set, not run | **no** |
| Enablement / affirmative projection | **no** |
| Stakeholder value projection | **no** |
| Freshness/drift chip (#112) | **partial** (36 refs) |

So the console is still organized the way the paradigm rejected: **by function, not by
question.** Nineteen sibling feature tabs (`runs`, `results`, `comparison`, `use-cases`,
`sets`, `wizard`, `workbench`, `maturity-wall`, ...) require the reader to know DAV's
internal structure in order to find the answer they came for. A persona dropdown over
that does not change what the screens *are*.

The keystone finding from the original design still stands and is still unaddressed:
**DAV is gap-rich and enablement-poor** — it computes what is missing, but not the
affirmative "what the architecture already supports", which is the same data inverted
and merely unsurfaced. The Customer and Stakeholder lenses cannot exist without it.

## The principle (restated, from the decision)

> Persona is the scoping principle. The objectives are constant; the persona changes.
> One analysis; each persona is a lens = a characteristic **question** plus the
> **projection** that answers it.

Chrome follows from that: **engagement (project)** is the one global context;
**persona** is the active lens; **run** is not global chrome at all — it is an operator
freshness concern.

## Build order

Taken from the design, with one deliberate reordering (see note):

0. **Split the shell.** `index.html` is 18,042 lines. Every subsequent step is surgery
   on that single file, which is likely a real part of why this stalled. Doing this
   first makes steps 1-4 cheap and reviewable. *(Not in the original order; added
   because the tax is paid on every later step.)*
1. **Retire the run selector; scope = UC/Set.** The single most visible change: every
   screen stops being "a view of a run" and becomes "a view of the thing you care
   about". Runs become the operator freshness view they were always meant to be.
2. **Outcome/Initiative object.** The consumer lenses have nothing to project without
   it; this is why Customer and Stakeholder cannot meaningfully exist today.
3. **Persona as a real lens.** Generalize beyond the dropdown: each persona selects a
   question + projection over the same analysis.
4. **Enablement projection.** Invert the existing gap data to answer affirmatively.
5. **Stakeholder value projection.**

## Tonight's scope

Steps 0 and 1, deployed and verified. Steps 2-5 are left for review-then-build: they
change the data model and the product surface, which is not work to do unattended.

## How to review

- `git diff function-focus..feat/persona-focus`
- Rollback is the `function-focus` tag; nothing is merged to `main`.
- Deployment notes and verification results: `docs/persona-focus-review.md` (written
  as the work lands).

## Deliberately not done

- No data-model changes (Outcome object) — needs your ruling first.
- No changes to the analysis engine or pipeline.
- No merges to `main`.
