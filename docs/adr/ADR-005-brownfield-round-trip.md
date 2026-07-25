# DAV ADR-005: Brownfield round-trip — the rehydration promise tested against an unplanned world

**Status:** Proposed
**Date:** 2026-07-25
**Related — the complete picture, each cited once.** The doctrine it tests (rehydration replays
original intent rather than restoring state), the data it reuses (the live estate discovery
loop — fragments diffed and applied into reviewed records), the axis it makes measurable (the
managed-vs-unmanaged resource question), and the standing intent-capture goal it advances
(deriving true intent from observed reality).

## Context

Rehydration-from-intent is validated today only in the forward direction: intent we authored,
realized, and compared. A real environment is harsher — it contains resources nobody planned,
configurations that accreted, and relationships no intent document describes. If the model can
only round-trip environments that were born from it, the sovereignty story has a quiet
asterisk: day-0 recovery works, brownfield adoption does not.

## Decision

A round-trip hammer over observed reality, in three legs:

1. **Observe → model**: discovery records (the estate corpus — real hosts, storage, containers,
   platform applications) validated as model instances at payload depth, not just envelope.
2. **Model → intent**: a derivation stage proposes the intent that *would have produced* each
   observed record — reversing the realization direction. Where no expressible intent exists,
   that is the finding (`underivable`), typed like every expressibility gap.
3. **Intent → compare**: the derived intent is re-projected through the normal realization path
   (dry-run/generation, not live mutation) and its typed outputs diffed against the observed
   record. Divergence is a fidelity finding: the model expressed the resource but lost
   information the environment carried.

Round-trip fidelity — the fraction of observed records that derive to intent and re-project
without loss — lands on the model-health scoreboard per resource family. Records that cannot
round-trip are the measured boundary of the managed-vs-unmanaged axis: *unmanaged* stops being
a label and becomes a computed set.

## Consequences

- Rehydration and brownfield-adoption claims get evidence from an environment that was not
  designed to flatter the model — the strongest form of the claim we can make honestly.
- The derivation stage is the seed of intent-capture: the same machinery that scores round-trip
  fidelity is the machinery that will eventually author intent from discovered estates.
- Findings split cleanly: payload-validation failures are spec gaps (H3), underivable records
  are intent-model gaps, re-projection diffs are realization-fidelity gaps — three different
  owners, one instrument.
- The compare leg must stay dry-run; this hammer reads the estate and never mutates it.
