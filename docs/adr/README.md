# DAV Architecture Decision Records

Decisions owned by the validation program. UDLM-registry decisions live in
`udlm/docs/adr/`; a decision lands here when it binds how DAV validates rather than what the
model contains. All records are Proposed until ratified by engineering review.

| ADR | Decision |
|-----|----------|
| [ADR-001](ADR-001-model-hammer-program.md) | The model-hammer program: six surfaces (definitions, composition, resources, providers, portability, expressibility) exhaust three failure spaces — wrongly accepts, lacks, disagrees |
| [ADR-002](ADR-002-external-corpora-as-expressibility-inputs.md) | Public intent corpora (TOSCA, Kubernetes, Terraform, architecture libraries) become Layer-2 expressibility inputs — the universality claim tested against the world |
| [ADR-003](ADR-003-must-reject-use-case-family.md) | A seventh use-case family with inverted success semantics: the system must refuse, and the refusal must be typed, actionable, non-leaking, audited |
| [ADR-004](ADR-004-interpretability-probes.md) | Context-only authoring probes: an instance authored from a type's plain-English context alone must validate against its schema — the human contract, tested |
| [ADR-005](ADR-005-brownfield-round-trip.md) | Observed estate → derived intent → re-projection → typed-output diff: rehydration tested against an unplanned world; unmanaged becomes a computed set |
| [ADR-006](ADR-006-model-health-scoreboard.md) | A generated, CI-current scoreboard in the registry: hammer findings accumulate into trends; 1.0 readiness becomes a threshold over named metrics |

Cross-repo: udlm ADR-044 (consumer conformance surface — consumers declare what they read, the
registry gates on it) is part of this program and lives with the registry it binds.

- **ADR-007 — Structured output enforced at the wire.** `response_format`, never `extra_body`; wire shapes verified, mutation-tested.
- **ADR-008 — Quorum-gated ensemble.** Sub-quorum gaps visible but non-voting; consensus persisted; verdict invariance under N.
- **ADR-009 — Reproduce mode owns determinism.** Concurrency clamped to 1; batch-composition nondeterminism measured and closed.
- **ADR-010 — Corpus-published vocabularies.** Read, never copied; loud fallback; aliases recorded not applied.
- **ADR-011 — Multi-perspective analysis.** Lens set per UC; quorum within a lens, union across, tier preserved; persona-qualified verdicts.
- **ADR-012 — Scope before launch.** The corpus index: validated, SHA-stamped, quarantine predicted not post-mortem.
- **ADR-013 — The validator validated.** Seeded ground truth + frontier ceiling + human rule; fixture must be able to fail.
