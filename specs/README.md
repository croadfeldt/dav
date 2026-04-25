# DAV Specifications

This directory contains the specifications that define DAV's contracts with consumers, its internal architecture, and its guarantees.

## Reading order

For a new user:

1. `01-framework-overview.md` — what DAV is and isn't
2. `08-consumer-integration.md` — how to use DAV for your project
3. `05-use-case-schema.md` — how to write use cases
4. `07-analysis-output-schema.md` — how to read DAV's output
5. `04-three-modes.md` — when to use which mode
6. Others, as your needs dictate

For a DAV maintainer or deep integrator:

1. `01-framework-overview.md`
2. `02-stage-model.md`
3. `03-determinism-invariants.md`
4. `05-use-case-schema.md`, `06-prompt-contract.md`, `07-analysis-output-schema.md` (the three I/O contracts)
5. `04-three-modes.md`
6. `09-deployment-standards.md`
7. `10-calibration-and-correctness.md`

## Authoring status

| Spec | Status | Last updated |
|------|--------|-------------|
| 01 — Framework Overview | stub | 2026-04-24 |
| 02 — Stage Model | stub | 2026-04-24 |
| 03 — Determinism Invariants | stub | 2026-04-24 |
| 04 — Three Modes | stub | 2026-04-24 |
| 05 — Use Case Schema | **v1.0** (with scoring model) | 2026-04-24 |
| 06 — Prompt Contract | stub | 2026-04-24 |
| 07 — Analysis Output Schema | **v1.0** | 2026-04-24 |
| 08 — Consumer Integration | stub | 2026-04-24 |
| 09 — Deployment Standards | stub | 2026-04-24 |
| 10 — Calibration and Correctness | stub | 2026-04-24 |

Specs are authored incrementally. Stubs describe what the spec will contain; see each file for scope.

## Spec versioning

Specs are versioned at the DAV framework level. A DAV v1.x release targets a specific major version of each spec. Breaking changes to any spec require a DAV major version bump.

Consumers declare which DAV version they target in their `dav-version.yaml`. DAV validates consumer content against the declared version's specs.

## Contributing to specs

See [`../CONTRIBUTING.md`](../CONTRIBUTING.md). Spec changes require:

- Motivation in the PR description
- ADR companion if the change is architectural
- Consumer impact analysis if backward-incompatible
- Version bump following semver
