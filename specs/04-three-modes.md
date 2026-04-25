# DAV Specification 04 — Three Operating Modes

**Status:** Stub (not yet authored)
**Audience:** Consumer choosing a mode; anyone integrating DAV into a workflow
**Depends on:** `03-determinism-invariants.md`

## Purpose

DAV offers three operating modes that trade off between reproducibility, cost, and exploration. This spec explains each mode, when to use it, and how to invoke it.

Topics this spec will cover when authored:

- **Verification mode (default).**
  - N=3 sampled analyses, temperature=0.2, canonical seeds `[42, 123, 456]`, cache_prompt=true
  - Samples merged via ensemble consensus (see `07-analysis-output-schema.md` §sample_annotations)
  - Use for: CI regression gating, routine "is UC X still supported?" checks
  - Determinism tier: Tier 2 (verdict-level stable) / Tier 3 (predictable correctness)
  - Cost: ~3× single-sample wall time
- **Reproduce mode.**
  - N=1, greedy decode (temperature=0, top_k=1), fixed seed (default 42), cache_prompt=true for perf or false for strict audit
  - Output shape identical to historical single-sample runs
  - Use for: debugging an unexpected verification result, producing audit-grade exemplars, dev iteration
  - Determinism tier: Tier 1 (byte-exact within topology)
  - Cost: fastest; single sample
- **Explore mode.**
  - N=10, temperature=0.7, deterministic pseudo-random seeds, cache_prompt=true
  - No merge — emits per-sample YAMLs plus a variance report
  - Use for: UC authoring, adversarial testing, capability surveys, diagnosing surprising verification results
  - Determinism tier: not applicable — explore mode is about surfacing variance, not suppressing it
  - Cost: 10× single-sample wall time
- Mode selection via `--mode` CLI flag
- Sample count override via `--sample-count` for advanced tuning
- Output artifact shape per mode
- Recommended workflow: verification in CI; reproduce when investigating; explore during authoring

This spec should be authored after `03-determinism-invariants.md` so the tier mapping is consistent.
