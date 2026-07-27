# DAV ADR-013: The validator is validated — seeded ground truth, frontier ceiling, human rule

**Status:** Accepted (fixture suite shipped and self-validated; frontier tier blocked on API
key; human tier operating informally already)
**Date:** 2026-07-27
**Related:** ADR-007/008/009 (the bugs the fixture caught or proved); ADR-011 (per-lens
acceptance); `docs/validation-fixture-suite-design.md`, `docs/reference-baselines-design.md`.

## Context

DAV was validated only against the live specs, where no correct answer exists — so every metric
was self-referential ("does run A match run B?"), which a consistently wrong system passes
perfectly. One day produced four invalidations that the thing being measured never caught:
inert schema enforcement, irreproducible n=1 verdicts, ensemble union bias, and a spec that
moved mid-comparison. Separately: precision was unmeasurable, so gap inflation read as
thoroughness.

## Decision

Three reference tiers, each with a distinct job:

1. **Seeded ground truth** (shipped): a frozen synthetic corpus with deliberately planted holes
   AND `must_not_report` controls — recall and precision, not recall alone. The fixture itself
   must fail when known bugs are reintroduced; a fixture that cannot fail is false assurance.
2. **Frontier baseline**: separates model capability from harness quality — a frontier miss on
   a seeded hole indicts the seed or the prompt, cheaply. Fresh API calls only: the session
   that authored the ground truth is not a valid analyst for it.
3. **Human adjudication**: the only tier that can rule the ground truth itself. Proven
   load-bearing the day it was designed — Chris overruled a seeded claim (transaction
   semantics) that the fixture author and the analyzer had independently shared. Two models
   agreeing on a wrong prior is exactly the failure only a human catches.

## Consequences

- Prompt changes now merge on battery numbers (the prompt-optimization loop), and analyzer
  biases become named regression checks (the whole-intent invention on convergence controls).
- The boundary is explicit: fixture results calibrate the INSTRUMENT and say nothing about
  whether UDLM/DCM are good specs; reading a fixture pass as platform health is the misuse the
  docs warn against.

## Alternatives considered

Validating against the live corpus only (the self-referential trap this replaces); exhaustive
human review (does not scale; humans go where they are irreplaceable — contested seeds,
disagreements, and a small audit of agreements so the judge is judged).
