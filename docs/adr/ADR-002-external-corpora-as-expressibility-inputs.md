# DAV ADR-002: External corpora as expressibility inputs — test the universality claim against the universe

**Status:** Proposed
**Date:** 2026-07-25
**Related — the complete picture, each cited once.** The hammer this feeds (ADR-001's H6 —
expressibility at scale), the stage that scores it (the Layer-2 deterministic
model-expressibility stage in
[`data-model-validation-design.md`](../data-model-validation-design.md)), and the standing
methodology it applies (adopt wide standards where they cleanly fit, grounded in real producers
and consumers).

## Context

Every validation input the program owns was authored by the same minds that authored the model:
the specs, the mutations derived from them, the use-case corpus. That closed loop can prove the
model self-consistent and can never prove it sufficient. Meanwhile the world publishes enormous
corpora of real intent — TOSCA service templates, Kubernetes manifests, Terraform modules, Heat
stacks, C4/ArchiMate model libraries — every one an intent somebody actually needed to express,
none of them shaped by our assumptions.

## Decision

Public intent corpora become a first-class Layer-2 input alongside the use-case corpus. An
ingest adapter per source format maps each external artifact to the expressibility question —
*which registry types, fields, and relationships would carry this?* — and the deterministic
stage scores it exactly as it scores a use case: typed findings (`missing_type`,
`missing_field`, `missing_edge`, `thin_outputs`) with the external artifact as provenance.

Adapters are per-format, not per-tool: one for the TOSCA node-template shape, one for the
Kubernetes resource shape, one for the HCL resource shape. The adapter maps structure, never
judges; judgment stays in the scoring stages. Corpora are pinned by ref like every other input,
so runs are reproducible and coverage is a trend (`external_corpus_coverage` on the
model-health scoreboard, ADR-006).

This also resolves the sequencing of the architecture-format interop work: the ingest half of
that program is built here first, as a validation instrument, before any emit/derive work.

## Consequences

- Candidate types and fields arrive with real-world provenance ("this Kubernetes field pattern
  appears in N manifests and has no home") — the most defensible artifact to hand engineering.
- Coverage against a named external corpus becomes a publishable claim ("expresses 94% of the
  reference TOSCA template library"), which serves adoption, not just validation.
- The finding stream will include noise from platform-specific minutiae the model deliberately
  excludes; triage must route those to documented exclusions (rule 36a's cross-walk discipline)
  rather than new types, or the model accretes the union of every tool's quirks.
- Licenses of ingested corpora are checked and recorded at adapter level before any corpus is
  committed to a run.
