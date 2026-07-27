# DAV ADR-010: Runtime vocabularies are corpus-published artifacts, never private copies

**Status:** Accepted (shipped dav#83 for dimensions; personas follow the same contract)
**Date:** 2026-07-27
**Related:** ADR-011 (personas consume this pattern); `docs/run-source-resolution-design.md`
(the same principle applied to sources).

## Context

The engine carried a private list of legal `scenario.dimensions.*` values while the corpus grew
legitimate new ones. Every UC using a newer value was silently quarantined — measured at 85% of
the UDLM corpus (62/73), 24% across both corpora — for roughly a year, invisible because the
quarantine surfaced nowhere. The model side made the vocabulary a single-sourced, CI-gated
artifact (`DIMENSION-VOCABULARY.yaml`); the fork persisted only because the engine kept its
copy.

## Decision

1. The engine **reads** corpus-published vocabularies at run start. Reading removes the fork;
   copying recreates it.
2. Absence degrades to the built-in list **with a loud warning naming the fallback as the
   quarantine cause** — silence is what let the fork persist.
3. Alias folds are recorded but **not applied**: accepting two spellings of one concept is how
   counts silently corrupt.
4. The same contract governs every future shared vocabulary — `PERSONAS.yaml` next.

## Consequences

- Quarantine from vocabulary drift is structurally impossible while the corpus gates itself
  (DIM-001) and the engine reads the gated file.
- The corpus index (ADR-012 territory) validates against the same published file at sync time,
  so drift is *predicted* before launch rather than discovered at stage 2.

## Alternatives considered

Copying the six lists verbatim ("mirrors X" comments rot; that is the fork with extra steps);
schema-enum in the UC files themselves (pins each UC to a vocabulary version; the published
file moves once for all).
