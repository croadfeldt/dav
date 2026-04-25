# DAV Specification 03 — Determinism Invariants

**Status:** Stub (not yet authored)
**Audience:** Anyone reasoning about regression gating, CI integration, or result trust
**Depends on:** `04-three-modes.md`, `07-analysis-output-schema.md`

## Purpose

DAV produces analyses that must be trustworthy enough to gate CI/CD decisions yet rich enough to contain LLM-generated content. These two goals create tension. This spec formalizes how DAV navigates that tension via three tiers of determinism guarantees.

Topics this spec will cover when authored:

- **Tier 1 — Byte-exact within topology.** Given identical inference endpoint configuration, engine version, consumer content version, mode configuration, and seed, two runs produce byte-identical Analysis outputs (modulo timestamps and run IDs). Reproduce mode guarantees Tier 1.
- **Tier 2 — Ensemble-verdict-stable across topology.** Under verification mode with N≥3 samples, the consensus verdict is stable across inference endpoint topology changes (split-mode, quantization, model-family within the same capability tier) with high probability.
- **Tier 3 — Predictable correctness.** At the ensemble level, DAV produces findings that align with human-reviewed calibration references within declared agreement bounds.
- Empirical grounding: the row-split vs layer-split uc008 finding from the perf tuning session is a reference point
- What each tier enables: Tier 1 for audit and debug; Tier 2 for CI regression gating; Tier 3 for ongoing trust calibration
- What violates each tier: topology changes break Tier 2 byte equality but not verdict equality; calibration reference drift can violate Tier 3
- CI gating guidance: gate at Tier 2 (verdict-level), not Tier 1 (byte-level). Tier 1 is for audit exemplars, not gating.
- Testing and verification procedures for each tier

This spec captures the hardest-won learning from the perf tuning session: that bitwise determinism is not the right goal, and predictable correctness at the ensemble level is.
