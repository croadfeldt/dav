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
