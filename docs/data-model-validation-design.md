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

## The hammer program (ADR-001)

Layers 1–3 define who validates; the hammer program defines *what gets attacked*. Six hammers
cover the model's surfaces, each finding a class of defect the others cannot:

| # | Surface | Method | Finds | Status |
|---|---------|--------|-------|--------|
| H1 | Definitions | Deep mutation fuzzing — every node path in every spec, mutated against its local subschema | Over-permissiveness at depth | deterministic, in build |
| H2 | Contracts & composition | Adversarial + legal catalog-item generation against the composition validator | Non-discriminating composition rules; zero-output (unbindable) types | deterministic, in build |
| H3 | Resources | Real-estate payload replay against pinned spec versions | Fields the world needs that the model lacks | deterministic, estate-side |
| H4 | Providers | Provider-contract cross-check against registry and standards register | Contract drift, dangling claims | deterministic, in build |
| H5 | Portability | Provider-swap diff over the class system's portable surface (Base/Type-scoped elements unchanged, Provider elements swapped) | Portability claims that don't survive a swap | gated on class-realization pilot |
| H6 | Expressibility | Generated stress UCs over the full coverage matrix (47 types × six capability axes), scored by Layers 2–3 | Under-expressiveness at scale | GPU-gated |

Three failure spaces, all covered: H1/H2 find what the model wrongly *accepts*, H3/H6 find what
the world needs that the model *lacks*, H4/H5 find where types and providers *disagree*. The
architecture-era corpus only ever probed the second.

## The six program extensions (ADRs 002–007)

The hammers attack what we authored; these extensions validate against what we didn't:

- **External corpora as expressibility inputs (ADR-002)** — public intent corpora (TOSCA
  templates, Kubernetes manifests, Terraform modules, architecture-model libraries) fed through
  Layer 2. The universality claim, tested against the universe rather than our imagination;
  findings arrive with real-world provenance.
- **The must-reject family (ADR-003)** — a seventh UC family with inverted success semantics:
  the system must *refuse* the intent (cross-tenant reference, sovereignty export, inline
  credential, undeclared-output binding, unauthorized provider, projection leak). The only
  validation of the policy/sovereignty spine that exists.
- **Interpretability probes (ADR-004)** — author an instance from a type's plain-English
  context alone, validate against the spec, sample N times. Divergence means the human contract
  and the machine contract disagree — the defect that wastes engineers' time.
- **Brownfield round-trip (ADR-005)** — observed estate records → re-derived intent →
  re-realization → typed-output compare. The rehydration promise tested against a real,
  unplanned environment; also the managed-vs-unmanaged axis made measurable.
- **Model-health scoreboard (ADR-006)** — discrimination density, output adequacy, strictness,
  context/UC/consumer coverage, computed per registry ref and tracked over time
  (udlm `registry/MODEL-HEALTH.md`). Validation becomes a trend with a graph; 1.0 readiness
  becomes a threshold instead of a feeling.
- **Consumer conformance surface (udlm ADR-044)** — consumers declare their read surface in
  `registry/consumers/`; the registry gates on it. A breaking change fails in registry CI
  naming the consumers it breaks, instead of failing later in their runtimes.

## Non-goals

- DAV does not build or realize — dcm-at-home owns execution; DAV owns expressibility and gaps.
- DAV does not re-run UDLM's own gates — it consumes their verdicts (Layer 1 is adopted, not
  duplicated).
- No new UC schema: the existing dimensions/generated_by vocabulary already carries what Layer 2
  needs; `domain` lands on *findings*, not on use cases.
