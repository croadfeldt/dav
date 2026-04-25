# DAV Specification 09 — Deployment Standards

**Status:** Stub (not yet authored)
**Audience:** Operators and platform engineers deploying DAV
**Depends on:** None directly; references `08-consumer-integration.md`

## Purpose

Defines how DAV is deployed, what infrastructure it expects, and how operators configure it. Covers today's Ansible-based deployment and sketches the future operator model.

Topics this spec will cover when authored:

- Supported deployment targets: OpenShift 4.x, Kubernetes (via kubectl), bare Docker/Podman (development)
- Minimum resource requirements: engine pod (memory for stage2 agent), MCP pod (memory for corpus cache), review-console pods
- Required external dependencies: OpenAI-compatible inference endpoint (user-supplied — llama.cpp, vLLM, API-hosted)
- Namespace conventions: `dav` for framework; consumer deployments named `<consumer>-dav` or similar
- Network model: engine pod outbound to inference endpoint, outbound to MCP pod, inbound from Tekton pipeline runners
- RBAC: service accounts, role bindings, which permissions each pod needs
- Ansible deployment (today):
  - Playbook structure
  - Required variables: consumer repo URL, content path, inference endpoint URL, model name
  - Running the playbook
  - Reconfiguring / redeploying
- Tekton pipeline (today):
  - Pipeline structure
  - Triggering: manual, scheduled, or webhook-based
  - Parameters per run
- Container images:
  - `dav-engine` — stages and agents
  - `dav-mcp` — MCP server
  - `dav-review-api` — Review Console backend
  - Image registry: `quay.io/croadfeldt/dav-*` or `ghcr.io/croadfeldt/dav-*`
  - Tagging strategy
- Observability: what logs each pod emits, metrics exposed, recommended log aggregation
- Sovereignty: how to deploy fully on-premise including LLM; what data paths exist
- Operator model (future):
  - `DavValidation` CRD shape (see ADR-002 §9.1)
  - Operator responsibilities
  - Migration path from Ansible to operator
- Helm chart (future): lightweight alternative to operator, same configuration surface

This spec's "today" section should be authored when the ansible tree is extracted into the DAV repo. The "future" section can remain as a sketch until operator work begins.
