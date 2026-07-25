# Data-model validation — DAV as the complete package

**What this settles:** how DAV becomes the primary validator of the *data model* (UDLM), with
architecture (DCM) analysis as the secondary projection — reversing the emphasis DAV was built
with. The ruling driving it: the data model matters more than the architecture; DAV must be the
complete package for validating it. This doc defines the three validation layers, what DAV owns
versus adopts, and the build phases.

## The inversion

DAV today asks, per use case: *can the architecture support this?* Domain-interaction findings,
capability gaps, roadmap items — all architecture-shaped. The data model is only exercised
implicitly, as the vocabulary the analysis happens to be written in.

The inversion: the first question becomes *can this intent be expressed in the data model at
all* — do the types exist, do their specs carry the fields the intent implies, do the declared
relationships and typed outputs support the composition the UC describes? Only after
expressibility is settled does the architecture question mean anything: an architecture gap
against an inexpressible intent is noise.

## Three layers, two owners

The whole-system rule applies: UDLM already carries a deterministic gate suite; DAV adopts it by
reference rather than reimplementing. DAV's own build is the two layers UDLM cannot host —
per-UC deterministic mapping, and LLM judgment at scale.

### Layer 1 — registry self-consistency (owner: UDLM CI; DAV adopts)

The registry proves itself: meta-schema validation, rule-36 type standard (G1–G7), uuid
rotation, compat gating, catalog currency, the [D8.3] binding-output contract in
`validate.py`, and the instance-fuzz gate (udlm #235 — every spec proven satisfiable and
discriminating by synthesized-instance mutation, 231 mutations across 47 types). DAV's role is
consumption: a run records the registry ref it analyzed against and surfaces that ref's gate
status in the run report. A red registry invalidates the run's premises; DAV should say so, not
re-derive it.

### Layer 2 — deterministic model-expressibility (owner: DAV; new pipeline stage)

The registry becomes a first-class DAV input alongside the UC corpus: a pinned udlm ref, loaded
per run. Before any model tokens are spent, a deterministic stage maps each UC against the
registry:

- **type coverage** — every resource/process/knowledge noun the UC's
  `expected_domain_interactions` implies resolves to a registered type (or is reported as a
  candidate-type finding);
- **field reachability** — intent parameters named in the UC resolve into the matched type's
  spec properties (fuzz-gate synthesis proves the spec satisfiable; this proves the UC's
  demands land inside it);
- **relationship legality** — compositions the UC describes are expressible with declared
  `relationships[]` and edge vocabulary (depends_on / contained_by / binds_to / references);
- **binding surface** — data movement the UC implies has producer outputs to bind to
  (the [D8.3] outputs index), catching thin-outputs types per-UC instead of per-review.

Output: typed, machine-diffable findings (`missing_type`, `missing_field`, `missing_edge`,
`thin_outputs`) attached to the run before stage-2 prompts fire. Deterministic findings feed the
LLM stage as context — the model confirms, refutes, and ranks; it does not discover what a
lookup can.

### Layer 3 — LLM gap analysis, re-aimed (owner: DAV; prompt + findings change)

Per-stage prompts (the F8 surface) gain the model-expressibility frame as the *primary*
dimension: the analysis judges whether UDLM can carry the intent, then whether DCM can realize
it. Findings carry a `domain` axis — `data-model` | `architecture` — so the roadmap projection
can weight them separately, with data-model findings ranked first per the ruling. Enum changes
route through the engine vocabulary (consumer_profile.py) — authoritative, additive-only, since
a wrong enum quarantines the run.

## What already exists to build on

- **Corpus**: six seeded UC families (binding-surface, type-standard, multi-cluster, bare-metal,
  storage-redundancy, process-migration) dual-homed in udlm `use-cases/` and the dcm hammer
  sets — authored precisely as model-validation probes.
- **Console fixes in flight**: set-scoped runs enforced server-side (#54 — the run-853521
  selection bug; a set-scoped run now executes exactly the set, never a silent full-corpus
  fallback) and corpus scoping via `role_paths` repo metadata (#54's runbook — dcm's corpus
  role path and scoping the already-registered udlm repo to `use-cases/`).
- **Execution half**: dcm-at-home builds what DAV finds expressible; the blue/green typed-output
  diff from the class-realization plan (#229/#230) is the same Layer-2 machinery pointed at
  migration validation.

## Phases

| Phase | Deliverable | Gate |
|---|---|---|
| V0 | Console fixes merged; six hammer sets + udlm corpus registered | set-scoped run executes exactly the set |
| V1 | Registry-as-input: run records udlm ref + gate status | run report shows the ref it validated against |
| V2 | Layer-2 stage: typed expressibility findings, pre-LLM | deterministic findings on the six families match hand analysis |
| V3 | Layer-3 prompts + `domain` axis; roadmap weighting data-model-first | A/B against a prior run shows findings split cleanly by domain |
| V4 | Scale: hammer-generation waves over the class-realization surface (providers, resources, Process family) | full-corpus runs, one-at-a-time GPU rule intact |

V0 is unblocked now (PRs in flight); V1–V2 are console+pipeline work with no GPU dependency;
V3–V4 are GPU-gated (qwen3-32b restart is an operator action).

## Non-goals

- DAV does not build or realize — dcm-at-home owns execution; DAV owns expressibility and gaps.
- DAV does not re-run UDLM's own gates — it consumes their verdicts (Layer 1 is adopted, not
  duplicated).
- No new UC schema: the existing dimensions/generated_by vocabulary already carries what Layer 2
  needs; `domain` lands on *findings*, not on use cases.
