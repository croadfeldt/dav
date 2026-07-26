# ADR-010 — Rule-Based Verdict Derivation

**Status:** Accepted
**Date:** 2026-07-25
**Author:** Chris Roadfeldt + Claude
**Extends:** [ADR-009](009-catalog-anchored-gap-identity.md)

---

## 1. Context

DAV's verdict (`supported` / `partially_supported` / `not_supported`) is the
headline of every analysis. Today it is essentially whatever the LLM *asserts* in
its summary, with a single post-hoc correction buried in the ensemble merger:
`ensemble.py` downgraded `supported` → `partially_supported` when a major/critical
gap was present. That gate was correct but invisible, un-named, un-tested, and
lived inline in one code path — there was no seam to add a second rule, and no
record that a verdict had been adjusted at all.

The sweep's diagnosis names this directly: *"the LLM asserts verdicts … determinism
is a post-hoc gate."* For cross-run drift (G4) and any deterministic-validation
story (G3) to mean anything, the reported verdict has to be a function of the
evidence, not a model mood — and the adjustment has to be transparent.

## 2. Decision

**The LLM asserts a verdict; DAV derives the reported verdict from the evidence via
an ordered set of deterministic rules.** A small module (`core/verdict_rules.py`)
exposes `derive_verdict(asserted, gaps) -> (derived, applied_rules)`.

Invariants:

- **Downgrade-only.** A rule may only move the verdict *down* the support ladder
  (`supported → partially_supported → not_supported`). The asserted verdict is the
  ceiling: evidence can withdraw support the model over-claimed, never manufacture
  support it under-claimed. This makes the derivation safe to apply
  unconditionally — a well-calibrated model is untouched, so there is no behavior
  change except where the model over-claimed.
- **Transparent.** `derive_verdict` returns the list of rules that fired
  (`{rule, from, to, why}`), so the asserted value and the reasoning are preserved.
  The ensemble merge note now records the asserted verdict and the derivation
  steps (e.g. *"Verdict derived from asserted 'supported': supported→partially_supported
  (GATE-001: …)"*).
- **Grown rule-by-rule.** The first and only shipped rule, **GATE-001**, is the
  original ensemble gate lifted verbatim (supported + major/critical gap →
  partially_supported). New rules append to an ordered registry; each is a pure
  predicate over `(asserted_verdict, merged_gaps)`.

## 3. Alternatives considered

- **Keep the inline gate.** Rejected: no seam to grow, no record of adjustment, and
  it existed only on the ensemble path.
- **Replace the LLM verdict with a fully rule-derived one (ignore the assertion).**
  Rejected for now: DAV has exactly one rule today; discarding the model's judgment
  in favor of a one-rule engine would lose signal. Downgrade-only keeps the model's
  assertion as the ceiling and lets rules accrue underneath it.
- **Let rules raise as well as lower the verdict.** Rejected: a rule that
  *manufactures* support the model didn't claim is how you ship a false
  `supported`. Support must be earned by the model's grounded assessment; rules only
  withdraw it.

## 4. Consequences

- Behavior is unchanged today (GATE-001 ≡ the old gate) — verified by the existing
  ensemble suite — but the verdict is now a named, tested, extensible derivation.
- The merge note carries the derivation, so a downgraded verdict is legible to
  reviewers instead of silent.
- **Not yet durable.** The asserted-vs-derived split lives in the note, not a
  structured field — persisting it (engine schema + ingest + a `uc_analyses`
  column) is a deliberate follow-on so this slice stays behavior-preserving and
  reviewable. Expanding the rule set (e.g. how a *critical* gap should bound
  `partially_supported`) is a product decision, tracked separately — the engine is
  now the place to land those rules one at a time.

## 5. Scope of the landing PR

`core/verdict_rules.py` + wiring into the ensemble merger (behavior-preserving) +
tests (the rule engine + the merge-path note). Durable asserted-vs-derived
persistence and any new rules are follow-ons.
