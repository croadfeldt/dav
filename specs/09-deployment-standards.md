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
  - Required variables: consumer repo URL(s), content path, inference endpoint URL, model name
  - Single-source legacy: `consumer_spec_repo_url` + `consumer_spec_repo_branch`
  - Multi-source: `consumer_spec_sources: [{namespace, repo_url, repo_branch, root_path}]`
  - Running the playbook
  - Reconfiguring / redeploying
- Managed repos registry (M1+, [ADR-003](../adr/003-multi-repo-registry-and-mcp-source-of-truth.md)):
  - `managed_repos` table is source-of-truth for which repos DAV operates on
  - Roles: `spec` (served by MCP), `corpus` (cloned per run by pipeline), `issue-source` (polled / webhook'd for PR comments)
  - Projection contract: when rows with `role=spec` change, the API regenerates the `dav-source-spec` ConfigMap and triggers a `dav-docs-mcp` rollout. The ConfigMap is downstream cache, not source-of-truth.
  - Seeding: first-run only, the API seeds the registry from existing source ConfigMaps so operators don't lose their config across upgrade
  - CRUD via `GET/POST/PUT/DELETE /api/repos`; UI lands in M3 (Config → Repos)
  - `tenant_id` column ungated in v1 (multi-tenant request filtering deferred per ADR-003 §3.A)
- PR-comment ingestion (M5+):
  - role=issue-source repos are polled every 5 min by a background async task in review-api
  - `GITHUB_TOKEN` env (from the `dav-review-api-tokens` Secret, alongside `DAV_CORPUS_PUSH_TOKEN`): GitHub PAT with `repo` (or `public_repo`) scope. Without it, anonymous mode runs at 60 req/hr per IP — unsuitable for periodic polling.
  - To add the token: `oc patch secret dav-review-api-tokens -n {{ dav_namespace }} -p '{"stringData":{"GITHUB_TOKEN":"ghp_..."}}'` then rollout-restart review-api.
  - `PR_COMMENTS_POLL_INTERVAL_SECONDS` (default 300) and `PR_COMMENTS_POLL_STARTUP_DELAY_SECONDS` (default 30) env vars tune cadence.
  - Webhook receiver (M6) pushes individual comments via the same upsert path so the poller becomes a fallback for missed events / repos without webhook configured.
- Tekton pipeline (today):
  - Pipeline structure
  - Triggering: manual, scheduled, or webhook-based
  - Parameters per run
- Container images:
  - `dav-engine` — stages and agents
  - `dav-mcp` — MCP server
  - `dav-review-api` — Review Console backend
  - Image registry: `quay.io/<your-org>/dav-*` or `ghcr.io/<your-org>/dav-*`
  - Tagging strategy
- Observability: what logs each pod emits, metrics exposed, recommended log aggregation
- Sovereignty: how to deploy fully on-premise including LLM; what data paths exist
- Operator model (future):
  - `DavValidation` CRD shape (see ADR-002 §9.1)
  - Operator responsibilities
  - Migration path from Ansible to operator
- Helm chart (future): lightweight alternative to operator, same configuration surface

This spec's "today" section should be authored when the ansible tree is extracted into the DAV repo. The "future" section can remain as a sketch until operator work begins.
