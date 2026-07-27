# DAV ADR-011: Analysis is multi-perspective; a verdict names its lens

**Status:** Accepted direction (Chris-ruled); implementation staged (auditor lens first)
**Date:** 2026-07-27
**Related:** ADR-003 (whose contract elements are implicit persona demands: typed→integrator,
actionable→application-owner, non-leaking→security, auditable→auditor, whole→consumer);
ADR-008 (the quorum rule this carves an exception into); `docs/persona-perspective-analysis.md`.

## Context

Chris: "We are currently just looking at the architectural validation from the architect's
perspective. We must look at ALL the perspectives." The concrete miss behind the ruling: the
analyzer scored an EMPTY "Auditable" section as `supported` — the generic architecture lens does
not weight auditability, and no amount of vocabulary or catalog fixes changed that (a
catalog-armed pass repeated the miss). Salience, not tagging. Meanwhile the model side made
perspectives structural: `PERSONAS.yaml` (20 personas with operational|governance|oversight
tiers) and `scenario.perspectives` on every UC — the lens set for a UC is
`{actor.persona} ∪ perspectives`.

## Decision

1. A UC is analyzed from **every lens its corpus entry declares**; each persona's published
   `objectives` are that lens's targets.
2. **Quorum within a lens, union across lenses, tier preserved.** Repeated samples of one
   persona are noise (ADR-008 suppresses them); a different persona is signal — a gap only the
   auditor sees is the point, never sub-quorum noise. Tier survives the union so a report can
   say "clean operationally; the governance tier surfaces X."
3. **Verdicts are persona-qualified.** A spec area can be supported for the engineer and
   not_supported for the auditor — the empty-Auditable case is that, literally. The unqualified
   single verdict is retired as a mislabel.
4. Personas are consumed per ADR-010: published artifact, resolved through `folded_aliases`,
   never a private list.

## Consequences

- Cost is bounded by per-UC relevance (lens sets are capped, not 20×N): roughly 2–4× per UC.
- Per-stage routing (#73) becomes the execution shape: retrieval (pass 1) shared across a UC's
  lenses; judgment (pass 2) per lens.
- Acceptance is measurable now: the fixture maps every seeded hole to a responsible lens; the
  full-lens battery must recall what the single lens missed with controls clean per lens.

## Alternatives considered

Prompt-only persona hints on a single pass (does not produce per-lens verdicts and cannot be
scored per lens); DAV-defined personas (the ADR-010 fork under a new name).
