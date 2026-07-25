# DAV ADR-004: Interpretability probes — validate the human contract, not just the machine one

**Status:** Proposed
**Date:** 2026-07-25
**Related — the complete picture, each cited once.** The surface it validates (the plain-English
context layer — every type carries purpose/uses/context, projected into the generated type
catalog), the gate that guards the machine half (udlm's instance-fuzz gate — the schema
discriminates; this ADR asks whether the *description* does), and the mission it serves
(engineers author from the model without wasting time on ambiguity).

## Context

The context layer exists so an engineer can understand and author against a type without
reading its JSON Schema. Nothing tests that. A context block can drift from its schema, omit
the one constraint that matters, or describe the type so loosely that two readers produce
incompatible instances — and every gate stays green, because every gate reads the schema, not
the prose. The defect lands later, as an engineer's wasted afternoon.

## Decision

An automated probe per type, run as a DAV campaign: give a model **only** the type's context
block and a concrete scenario — never the schema — and have it author a spec instance. Validate
the result against the real schema. Sample N attempts per type across scenario variations.

Two metrics fall out. **Authoring success rate**: how often context-only authoring produces a
valid instance — low means the context under-specifies the contract. **Divergence**: how much
structure varies across valid attempts — high means the context permits materially different
readings of the same scenario. Both land on the model-health scoreboard (ADR-006) per type.

Findings are doc defects with a precise location: the failing probe names the type, the
scenario, and the constraint the context failed to convey. The fix is a context edit (with the
rotation that implies), re-proven by re-running the probe — the same find/fix/re-verify loop
every other hammer uses.

## Consequences

- The context layer gets a quality bar that scales past hand review: 47 types × N samples per
  campaign, repeatable on every context change.
- The probe model's failures are a *proxy* for human misreading, not proof — a systematically
  wrong probe model could flag good context. Divergence across samples, not single failures, is
  the signal; single-sample findings stay triage-only.
- GPU-gated like every generation campaign; runs ride the same one-at-a-time scheduling.
- A second-order use falls out free: probe transcripts that succeed are worked examples authored
  from the docs alone — candidate material for the examples the type standard requires.
