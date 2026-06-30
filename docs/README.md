# Documentation

User-facing documentation for DAV. This is the place for:

- Operational runbooks
- Tutorials
- Worked examples beyond the spec contracts
- Deployment guides for specific environments

## Available documents

- [`operating-model-decision-record.md`](operating-model-decision-record.md) — **CANONICAL operating
  model (ratified 2026-06-30). Read first.** Purpose/North Star (A-now/B-later), the one shape / two
  missions (architecture validation + assessments), the ingest→analyze→roadmap pipeline + vocabulary,
  validation (single-source loader + quarantine), isolation (project scoping, not schema-per-tenant),
  corpus-vs-spec + UC `purpose`, capability as one shared spine, scope via labels+selectors, and roadmap
  projections (capability + UC-enablement). Everything else builds to this.
- [`operator-runbook.md`](operator-runbook.md) — End-to-end runbook for first-time deploy onto OpenShift, smoke test against an exemplar UC, webhook setup, and full corpus run with findings capture.

## See also

For the framework's design and locked decisions, see `../DAV-AI-PROMPT.md`.
For getting started as a user, see `../AGENTS.md`.
For the normative contracts (use case, analysis schema), see `../specs/`.
For architectural decisions, see `../adr/`.
