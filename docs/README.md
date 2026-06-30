# Documentation

User-facing documentation for DAV. This is the place for:

- Operational runbooks
- Tutorials
- Worked examples beyond the spec contracts
- Deployment guides for specific environments

## Available documents

- [`operating-model-decision-record.md`](operating-model-decision-record.md) — **CANONICAL operating model (ratified 2026-06-30). Read first.** A-now/B-later north star, one-shape/two-missions (architecture validation + assessments), the ingest→analyze→roadmap pipeline + vocabulary, single-source validation + quarantine, project scoping (not schema-per-tenant), corpus-vs-spec + UC `purpose`, capability as one shared spine, roadmap projections (capability + UC-enablement). Everything builds to this.
- [`operator-runbook.md`](operator-runbook.md) — End-to-end runbook for first-time deploy onto OpenShift, smoke test against an exemplar UC, webhook setup, and full corpus run with findings capture.
- [`agent-integration.md`](agent-integration.md) — How to give an external agent / automation / CI / coding agent authenticated API access via Personal Access Tokens (PATs): minting via the Agents panel or API, the act-as-an-account identity model, least-privilege, usage, TLS, and rotation/revocation.

## See also

For the framework's design and locked decisions, see `../DAV-AI-PROMPT.md`.
For getting started as a user, see `../AI-ONBOARDING.md`.
For the normative contracts (use case, analysis schema), see `../specs/`.
For architectural decisions, see `../adr/`.
