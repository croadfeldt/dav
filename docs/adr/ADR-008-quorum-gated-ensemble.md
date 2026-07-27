# DAV ADR-008: Ensemble verdicts derive from quorum-backed findings; consensus is persisted

**Status:** Accepted (shipped dav#80, dav#81/t008)
**Date:** 2026-07-27
**Related:** ADR-007 (the fix that made these measurements possible);
`docs/derived-verdicts-design.md` (the proposal this bias reshaped).

## Context

The verification ensemble merged gaps across samples by **union** — any single sample's finding
entered the merged set — while verdict derivation was downgrade-only. P(some sample finds a gap)
rises with N, so verdicts weakened monotonically with sample count: measured on the same six
UCs, gpt-oss went from 4 gaps / 5-supported at n=1 to 20 gaps / 0-supported at n=3, and both
models collapsed to identical all-partial verdicts — erasing the model distinction an entire
routing feature had been justified by. The per-gap agreement was already computed
(`gap_consensus`) and then neither used nor stored.

## Decision

1. Only **quorum-backed** gaps (⌈N/2⌉ agreement; ties count as agreement) enter verdict
   derivation.
2. Sub-quorum gaps are **kept, labeled, and persisted** — a 1-of-3 finding is often real
   evidence the other samples missed and is exactly what the capability catalog needs; it is
   visible but does not vote.
3. Consensus is **persisted per gap** (`uc_gaps.consensus`, "k/n") so every downstream consumer
   can tell a 3-of-3 finding from a 1-of-3.
4. **Verdict invariance under sample count** (n=1 ≈ n=3 ≈ n=5 for the same evidence) is the
   standing acceptance property; the fixture battery measures it.

## Consequences

- Confirmed in production: verdict accuracy no longer degrades at n=3 (0.50 → 0.60 on the
  fixture baseline where the pre-fix behavior collapsed).
- Diverse-lens analysis (ADR-011) required an explicit carve-out: quorum applies WITHIN a lens;
  across lenses findings union — a deliberately different perspective is not a dissenting
  sample.

## Alternatives considered

Dropping sub-quorum gaps (loses real single-sample discoveries); majority-vote verdicts without
gap filtering (leaves the union bias in the gap list every consumer reads).
