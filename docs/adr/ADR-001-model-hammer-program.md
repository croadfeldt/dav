# DAV ADR-001: The model-hammer program — six surfaces, three failure spaces

**Status:** Proposed
**Date:** 2026-07-25
**Related — the complete picture, each cited once.** The program this structures
([`data-model-validation-design.md`](../data-model-validation-design.md) — the model, not the
architecture, is the primary validation target), the deterministic seed it grows from (udlm's
instance-fuzz gate — every spec proven satisfiable and discriminating by synthesized-instance
mutation), and the extensions that complete it (ADRs 002–006 here, plus udlm ADR-044 —
consumers declare what they read).

## Context

The architecture era validated one thing: whether intent the corpus imagined could be realized.
That leaves the model itself — definitions, contracts, resources, providers, portability —
validated only incidentally, as the vocabulary the analysis happened to be written in. A model
that anchors portability and sovereignty claims needs its own hammer: systematic, adversarial,
repeatable attack on every surface, with each finding class assigned to the method that can
actually produce it.

## Decision

Six hammers, each owning a surface and a defect class:

- **H1 — definitions**: deep mutation fuzzing, every node path in every spec mutated against
  its local subschema. Finds over-permissiveness at depth.
- **H2 — contracts and composition**: generated adversarial and legal catalog items against the
  composition validator. Finds non-discriminating composition rules and unbindable
  (zero-output) types.
- **H3 — resources**: real estate payloads replayed against pinned spec versions. Finds fields
  the world needs that the model lacks.
- **H4 — providers**: provider contracts cross-checked against the registry and the
  standards-adoption register. Finds contract drift and dangling claims.
- **H5 — portability**: provider-swap diffs over the class system's portable surface. Finds
  portability claims that do not survive a swap. Gated on the class-realization pilot.
- **H6 — expressibility**: generated stress use cases over the full coverage matrix (every
  type × the six rule-36 capability axes), scored by the deterministic expressibility stage and
  the analysis engine. Finds under-expressiveness at scale. GPU-gated.

The assignment is exhaustive over three failure spaces: what the model wrongly **accepts**
(H1, H2), what the world needs that the model **lacks** (H3, H6), and where types and providers
**disagree** (H4, H5). A proposed validation method that does not extend one of these spaces is
redundant; a surface not reachable by any hammer is a program gap.

Deterministic hammers live where their subject lives (udlm CI, estate CI) and run on every
commit; generated and LLM-driven hammers live in DAV and run as campaigns. Both report into the
model-health scoreboard (ADR-006) so hammering accumulates into a trend rather than a pile of
run reports.

## Consequences

- Every hammer finding is typed by failure space, which decides its destination: accepted-wrong
  → spec fix with rotation; lacking → candidate type/field with provenance; disagreement →
  contract fix on whichever side is wrong.
- The matrix is the review checklist for new validation ideas: name the surface, name the
  failure space, or it is not a new hammer.
- Cost concentrates where it should: the always-on hammers are cheap and deterministic; the
  expensive generated campaigns run only when the deterministic floor is green.
