# ADR-009 — Catalog-Anchored Gap Identity

**Status:** Accepted
**Date:** 2026-07-25
**Author:** Chris Roadfeldt + Claude
**Extends:** [ADR-001](001-dav-consumer-agnostic-framework.md)

---

## 1. Context

Gaps are the product of DAV: a run's verdict is only as useful as the gaps that
justify it, and every downstream goal — cross-run drift (G4), findings→resolution
(G5), the Phase-2 capability roadmap — depends on being able to say *"this gap is
the same gap I saw last run."* Today they can't.

A `GapIdentified` carries no identity. The model emits a 3–7-word free-text
`title` plus a `description`; cross-run and cross-sample matching is
canonicalized-title guessing (`evaluator/compare.py`, `core/ensemble.py`). The
measured effect is **±5 gap-label churn between two identical 32B runs** — the
same architectural gap, reworded, reads as a different gap. On the console side
ingest *fabricates* `gap_id = GAP-001…` in file order, so `GAP-002` in one run is
unrelated to `GAP-002` in the next and the cross-run trend endpoint is
misleading.

Meanwhile the substrate to fix this already exists and is unused for gaps: each
project has a `capability_catalog` (stable `cap_key`s), and `assessment_findings`
already demonstrates the normalize-or-flag pattern
(`catalog_capability_id` + `normalization_status`).

## 2. Decision

**A gap's identity is the catalog capability it concerns.** Add an OPTIONAL
`capability_id` to `GapIdentified`, anchored to the project's `capability_catalog`.

- **Emit (engine).** `capability_id` is an optional field on the gap schema. When
  the run supplies the consumer's catalog capability keys (a new optional
  `known_capability_ids` vocabulary on `ConsumerProfile`), the guided-JSON schema
  **enum-constrains** the field to those ids — the identical mechanism that already
  constrains `provider_types` and `policy_modes` — and the system prompt renders
  the allowed set so the model reasons about which id a gap concerns before
  decoding. When the run supplies no catalog, the field is a free string and the
  model may omit it: **behavior is byte-identical to pre-ADR** (empty ids don't
  serialize, the prompt block is absent).
- **Match (engine).** Ensemble consolidation and the cross-run comparator key on
  `capability_id` when present (`cap:<id>`), falling back to the canonical
  title/description for untagged gaps. A catalog-anchored id doesn't churn, so the
  ±5 label churn collapses to a stable match.
- **Persist + normalize (console — companion slice).** `uc_gaps` gains
  `catalog_capability_id` + `normalization_status` mirroring `assessment_findings`.
  Ingest stops fabricating `GAP-NNN`: it normalizes the emitted `capability_id`
  against `capability_catalog` (matched → `normalized`; no match → recorded and
  flagged `proposed-taxonomy-gap` for back-fill, never silently dropped). The
  gaps/trend endpoints key on the real id.
- **Deliver (console — companion slice).** The trigger injects the active
  project's `capability_catalog` cap_keys → Tekton param → `run_corpus` →
  `profile.known_capability_ids`, along the same seam the corpus/spec params
  already travel.

## 3. Alternatives considered

- **UDLM rule-ID registry as the anchor.** Rejected: there is no rule registry in
  DAV, and the UDLM ADR-028 registry is a *different project*. A validator-local
  rule id is a separate, later concern (deterministic-validation analysis type),
  not a dependency of gap identity.
- **Hard-require `capability_id`.** Rejected: a gap can legitimately concern a
  capability the catalog doesn't have yet. Normalize-or-flag keeps that gap and
  surfaces it as a back-fill candidate instead of forcing a wrong id or losing it.
- **New free-string `gap_id` the model invents.** Rejected: that's what churns
  today. Identity must come from a stable, shared vocabulary, not model prose.

## 4. Consequences

- Backward compatible: existing analysis files, golden snapshots, and any run
  without a catalog are unaffected.
- Cross-run drift (G4) and findings→resolution (G5) get a stable key to build on;
  the trend endpoint becomes truthful once ingest keys on the real id.
- The catalog becomes load-bearing for gaps, so catalog quality now matters — the
  `proposed-taxonomy-gap` flag is the feedback loop that grows it.

## 5. Scope of the landing PR

Engine emit + match (this commit), then console persist/normalize + trigger
delivery (companion commits, same PR). Verdict derivation and the
deterministic-validation `analysis_type` are separate follow-on slices.
