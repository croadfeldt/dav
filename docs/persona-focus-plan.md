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

Taken from the design. **Corrected after checking the code:** steps 0 and 1 as I first
wrote them were already done, and I had them wrong in two ways worth recording, because
both came from reading greps instead of the source:

- *"Split the 18,042-line `index.html`"* — `index.html` is a **generated artifact**.
  `src/` holds the real sources (`views/*.html`, `js/app/*.js`), `build.mjs` assembles
  them, and `build.mjs --check` fails the build on drift. The shell was split already;
  I was measuring the output.
- *"Retire the run selector"* — already retired. Scope is UC/Set. What I mistook for a
  selector is a read-only run **status chip**.

What actually remains:

1. ~~Split the shell~~ — **already done** (`src/` + `build.mjs`).
2. ~~Retire the run selector; scope = UC/Set~~ — **already done**.
3. **Enablement projection.** Invert the analysis to answer affirmatively: what this
   architecture *does* support, and which capabilities carry it. **Done tonight** —
   moved ahead of the data-model work because it needs no new objects and no ruling
   from you; it reads data that already exists.
4. **Outcome/Initiative object.** The consumer lenses have little to project without it.
   Needs your ruling — it is a data-model change.
5. **Persona as a real lens.** Generalize beyond the dropdown: each persona selects a
   question + projection over the same analysis.
6. **Stakeholder value projection.** Depends on 4.

## Tonight's scope

Step 3, deployed and verified, plus a build-provenance defect found while verifying it.
Steps 4-6 are left for review-then-build: they change the data model and the product
surface, which is not work to do unattended.

## How to review

- `git diff function-focus..feat/persona-focus`
- Rollback is the `function-focus` tag; nothing is merged to `main`.
- Deployment notes and verification results: `docs/persona-focus-review.md` (written
  as the work lands).

## Deliberately not done

- No data-model changes (Outcome object) — needs your ruling first.
- No changes to the analysis engine or pipeline.
- No merges to `main`.
