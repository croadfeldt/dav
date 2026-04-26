# DAV — DCM Architecture Validation

An open-source framework for architectural validation, gap analysis, and recommendation generation.

DAV ingests a project's specification corpus and a set of use cases, runs an LLM-assisted analysis pipeline, and produces structured findings about whether the architecture supports each use case: what components and capabilities are invoked, what data model is touched, what gaps exist, and an overall verdict per use case.

DAV is consumer-agnostic. The first consumer is DCM (Data Center Management), but DAV is designed to work against any project with a text-based spec corpus and structured use cases. See [ADR-001](adr/001-dav-consumer-agnostic-framework.md) for the framework's core architectural decision.

## Three operating modes

- **Verification** — the default. Runs multiple sampled analyses and merges them into a single Analysis with consensus per field. Use for CI/CD gating; cross-run-comparable.
- **Reproduce** — single-sample, greedy decoding, fixed seed. Use for debugging an unexpected verification result or producing audit-grade exemplars; byte-identical on rerun.
- **Explore** — high-temperature, no merging, variance report. Use for use-case authoring and adversarial testing.

See [`specs/04-three-modes.md`](specs/04-three-modes.md) for the full mode contract.

## Repository layout

```
dav/
├── README.md
├── AI-ONBOARDING.md       Operational onboarding (how to use DAV)
├── DAV-AI-PROMPT.md       Architectural rationale (why DAV is shaped the way it is)
├── CONTRIBUTING.md
├── LICENSE
├── adr/                   Architectural decision records
├── specs/                 DAV's normative specifications — contracts consumers conform to
├── engine/                Python inference engine (stages, agents, ensemble merger)
├── mcp/                   MCP server for serving consumer spec content to the agent
├── review-console/        Web UI for reviewing analysis outputs
├── ansible/               Deployment scaffolding (OpenShift); Tekton tasks and pipelines templated from here
├── examples/              Illustrative examples, including a minimal synthetic consumer
└── docs/                  User-facing documentation
```

## Getting started

If you're an LLM picking up the project: read [`AI-ONBOARDING.md`](AI-ONBOARDING.md) for operational guidance, then [`DAV-AI-PROMPT.md`](DAV-AI-PROMPT.md) for design context.

If you're a developer integrating DAV into your own project as a consumer: read [`specs/08-consumer-integration.md`](specs/08-consumer-integration.md). You'll need a consumer profile YAML (see [`examples/dcm-reference-profile.yaml`](examples/dcm-reference-profile.yaml) for shape), a spec repo (your architecture docs), and a corpus repo (your use cases plus a `dav-version.yaml` manifest).

If you're deploying DAV: the Ansible role at [`ansible/roles/dav/`](ansible/roles/dav/) targets OpenShift 4.18+. Copy [`ansible/inventory/group_vars/all/vars.local.yaml.example`](ansible/inventory/group_vars/all/vars.local.yaml.example) to `vars.local.yaml`, fill in your site-specific values (inference endpoint, cluster apps domain, consumer repo URLs), then run `ansible-playbook ansible/playbook.yaml`. See [`docs/operator-runbook.md`](docs/operator-runbook.md) for the full deploy → smoke test → real run workflow.

If you want to run a single use case locally:

```bash
cd engine
pip install -e .
python -m dav.stages.stage2_analyze \
    --use-case path/to/case.yaml \
    --output path/to/analysis.yaml \
    --inference-endpoint http://your-llm:8000/v1 \
    --inference-model qwen \
    --mcp-url http://your-mcp:8080 \
    --no-enable-thinking
```

For a full-corpus run, use `python -m dav.stages.run_corpus`. See [`AI-ONBOARDING.md`](AI-ONBOARDING.md) for full CLI reference.

## Specifications

DAV publishes its own normative specifications. These are the contracts consumers conform to:

- [`specs/01-framework-overview.md`](specs/01-framework-overview.md) — What DAV is, what it produces, who it's for
- [`specs/02-stage-model.md`](specs/02-stage-model.md) — Pipeline architecture and stage definitions
- [`specs/03-determinism-invariants.md`](specs/03-determinism-invariants.md) — Predictable-correctness invariants
- [`specs/04-three-modes.md`](specs/04-three-modes.md) — Verification, reproduce, explore
- [`specs/05-use-case-schema.md`](specs/05-use-case-schema.md) — Use Case YAML contract
- [`specs/06-prompt-contract.md`](specs/06-prompt-contract.md) — How consumers supply domain context
- [`specs/07-analysis-output-schema.md`](specs/07-analysis-output-schema.md) — What DAV emits
- [`specs/08-consumer-integration.md`](specs/08-consumer-integration.md) — How to become a DAV consumer
- [`specs/09-deployment-standards.md`](specs/09-deployment-standards.md) — Deployment model
- [`specs/10-calibration-and-correctness.md`](specs/10-calibration-and-correctness.md) — Predictable correctness model

## Architectural decision records

- [`adr/001-dav-consumer-agnostic-framework.md`](adr/001-dav-consumer-agnostic-framework.md) — DAV is a consumer-agnostic framework
- [`adr/002-dcm-integration-model.md`](adr/002-dcm-integration-model.md) — DAV as a DCM-managed capability (future direction)

## License

Apache License 2.0. See [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
