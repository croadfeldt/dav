# DAV Specification 10 — Calibration and Predictable Correctness

**Status:** Stub (not yet authored)
**Audience:** Consumer maintaining calibration set; anyone evaluating DAV's trustworthiness
**Depends on:** `03-determinism-invariants.md`, `05-use-case-schema.md`, `07-analysis-output-schema.md`

## Purpose

DAV claims "predictable correctness" as its core property (`03-determinism-invariants.md` Tier 3). This spec defines what that claim means, how it's measured, and how consumers participate in keeping it honest.

This spec is also the home for the full **scoring composition rules** that build on the severity and confidence representation defined in `05-use-case-schema.md` §6. While §6 defines the representation (labels, scores, bands, descriptor form), this spec defines the arithmetic: how to combine severity and confidence into actionability, how to aggregate scores across findings in an analysis, and how to compose DAV scores with DCM scores at the integration boundary (ADR-002).

Topics this spec will cover when authored:

- What predictable correctness means: consensus analyses from DAV align with human-reviewed reference analyses within declared agreement bounds
- What it does NOT mean: DAV is never wrong, DAV is deterministic, DAV is AGI
- **Scoring composition rules** (detailed arithmetic using scoring representation from `05-use-case-schema.md` §6):
  - Actionability score: `actionability = severity.score × (confidence.score / 100)`, returns 0-100
  - Cross-finding aggregation: UC-level severity is max across gap scores (not average) — one critical dominates many advisory
  - Confidence-weighted gate thresholds: when gates use composite scores (severity × confidence), thresholds are in the same 0-100 space
  - Cross-system composition (DAV + DCM): when DAV is an integrated DCM capability, DAV severity/confidence composes with DCM confidence/trust under the same 0-100 representation; specific policy rules belong in DCM's capability manifest per ADR-002
- Calibration references:
  - Definition: a human-reviewed golden Analysis for a specific UC
  - Format: identical to a regular Analysis YAML plus a `calibration_metadata` block
  - Authorship: human-written, not machine-generated
  - Location: in the consumer's `dav/calibration/` directory
- Scoring a DAV run against a calibration reference:
  - Field-by-field agreement
  - Verdict bucket match (binary: matches or doesn't)
  - Component set agreement: F1 score over component IDs
  - Capability set agreement: F1 score
  - Gap identification agreement: F1 score over normalized gap keys
  - Weighted aggregate: a single correctness percentage
- Agreement thresholds:
  - A calibration reference declares expected agreement bounds per field
  - Runs below bounds flag an alert; they may indicate framework regression or reference drift
- Calibration drift:
  - Specs evolve; calibration references go stale
  - How to detect staleness (DAV flags references older than a threshold; or references pointing at non-existent sections)
  - Refresh workflow
- When calibration references should be updated vs when DAV's output is wrong:
  - If human review confirms DAV's new output is correct, update the reference
  - If human review says DAV's old output was correct, the current run is a regression — investigate
  - Reviewer discipline: neither auto-update references nor auto-believe-DAV
- Seed calibration: which UCs to calibrate first (~3-5 UCs covering a spread of verdict buckets)
- Calibration governance: who authorizes reference updates
- Reporting: running a calibration suite and producing a dashboard; CI integration of calibration scoring
- Relationship to CI regression gating: calibration correctness drift is not CI-blocking by default but is surfaced strongly

This spec is the "how do we know DAV is trustworthy" document. Should be authored once the framework has been in real use long enough to have learned what actually matters — probably 3-6 months post-initial-release.
