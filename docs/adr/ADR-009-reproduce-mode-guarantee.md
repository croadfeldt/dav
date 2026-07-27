# DAV ADR-009: Reproduce mode owns its determinism — concurrency is clamped, not requested

**Status:** Accepted (shipped dav#92)
**Date:** 2026-07-27
**Related:** ADR-008 (the ensemble this bypasses by design); the fixture suite (where the proof
runs live).

## Context

Reproduce mode promises byte-identical reruns and configures everything in-process for it:
greedy decoding, temperature 0, fixed seeds, prompt cache off. Two runs with all of that
confirmed applied still produced 12 and 18 gaps with ZERO overlap. The nondeterminism was
outside the process: at `uc_concurrency: 2`, concurrent UCs share the inference server's
batches, and batch composition changes floating-point reduction order — argmax flips at ties
even under strict greedy. At concurrency 1, two passes produced 15 gaps, identical.

## Decision

Reproduce mode **clamps** `uc_concurrency` and `sample_concurrency` to 1, logging a warning when
a caller asked for more. A mode whose guarantee depends on the caller knowing an inference-server
subtlety is not a guarantee; honoring the request would silently void the mode being requested.

## Consequences

- "Same source + same UCs → same gaps" is now enforced, not hoped for — the property Chris
  required of the platform.
- Reproduce runs serialize; that is the price of the contract and is confined to the mode whose
  purpose it serves.

## Alternatives considered

Documenting the constraint (the next operator re-runs at conc 2 and trusts the result);
rejecting instead of clamping (turns a safe request into a failed run for no benefit).
