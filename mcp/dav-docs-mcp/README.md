# dav-docs-mcp

MCP (Model Context Protocol) server that exposes one or more consumer spec source trees to the DAV stage 2 analyzer. The agent calls MCP tools to retrieve specific documents and sections by handle, search for terms, and inspect the consumer's capability inventory.

## What it does

At startup the server walks each configured docs root and indexes every `.md` file. Two source modes are supported:

**Single-source mode** (`--docs-path PATH`) — handles are unprefixed relative paths with `.md` extension:
- File: `subdir/topic.md` → handle: `subdir/topic.md`
- File: `00-foundations.md` → handle: `00-foundations.md`

Used by the minimal-consumer example and any consumer with a single repo.

**Multi-source mode** (`--source NAMESPACE:PATH`, repeatable) — handles are namespaced:
- Source `udlm:/data/udlm`, file `contracts/provider-contract.md` → handle: `udlm/contracts/provider-contract.md`
- Source `dcm:/data/dcm/architecture`, file `control-plane/components.md` → handle: `dcm/control-plane/components.md`

Used when a consumer's specs span multiple peer repos (e.g., UDLM substrate + DCM realization).

The server also extracts system-policy markers (e.g., `GRP-001`, `PLC-003`, `DPO-005`) from doc content and exposes them via `get_system_policy`. Policy IDs are global across sources.

## MCP tools

- `list_documents(namespace?)` — list all indexed docs, optionally filtered by source namespace
- `list_sources()` — show configured sources and how many docs each contributed
- `get_document(handle)` — fetch a doc by handle; returns outline + pointer for large docs
- `get_document_section(handle, section_title)` — fetch a specific section
- `search_docs(query, max_results?, namespace?)` — full-text search across all sources or one namespace
- `get_system_policy(policy_id)` — fetch a system-policy definition
- `get_profile(name)` — fetch a DCM deployment profile (minimal/dev/standard/prod/fsi/sovereign)
- `get_capability_count()` — index stats including per-source doc counts

In multi-source mode `get_document` and `get_document_section` accept the full namespaced handle OR an unqualified relpath if it's unambiguous across sources.

## How DAV deploys it

The Ansible role at `../../ansible/roles/dav/tasks/mcp_servers.yaml` builds this directory as an in-cluster image and deploys it as a Kubernetes Deployment.

The deployment template (`mcp-docs-deployment.yaml.j2`) reads the `dav-source-spec` ConfigMap mounted at `/config`. Two ConfigMap shapes are supported:

**Multi-source**: ConfigMap has a `sources` key carrying YAML:
```yaml
data:
  sources: |
    - namespace: udlm
      repo_url: https://github.com/org/udlm.git
      repo_branch: main
      root_path: ""
    - namespace: dcm
      repo_url: https://github.com/org/dcm.git
      repo_branch: main
      root_path: architecture
```
The init container parses this list, clones each repo into `/data/<namespace>`, and writes `<namespace>:<served_path>` lines to `/data/.source-flags`. The MCP container reads `.source-flags` and starts with the appropriate `--source NS:PATH` flags.

**Legacy single-source**: ConfigMap has `repo_url` and `repo_branch` top-level keys (no `sources` key). The init container clones the one repo into `/data/repo`. The MCP container starts with `--docs-path /data/repo/architecture/data-model` (the historical DCM convention).

Mode selection is automatic — the init container checks for `/config/sources` first and falls back to env vars from the legacy keys.

To retarget without changing the template: patch the ConfigMap, then roll the dav-docs-mcp Deployment (`oc rollout restart deployment/dav-docs-mcp`). The init container re-clones whatever the ConfigMap currently says.

## Run locally

```bash
cd mcp/dav-docs-mcp
pip install -r requirements.txt

# Single-source via stdio (default transport)
python server.py --docs-path /path/to/your/spec/dir

# Single-source via SSE (HTTP)
python server.py \
    --docs-path /path/to/your/spec/dir \
    --transport sse --port 8080

# Multi-source via stdio
python server.py \
    --source udlm:/path/to/udlm \
    --source dcm:/path/to/dcm/architecture

# Multi-source via SSE
python server.py \
    --source udlm:/path/to/udlm \
    --source dcm:/path/to/dcm/architecture \
    --transport sse --port 8080
```

The SSE endpoint at `/sse` is the MCP handshake; the engine connects there using the `fastmcp` client.

## Files

- `server.py` — server implementation
- `requirements.txt` — Python deps (fastmcp, uvicorn, etc.)
- `Containerfile` — container image spec (used by the in-cluster build)

## Notes

The container start command (the `command:` field in `mcp-docs-deployment.yaml.j2`) builds the right `--docs-path` or `--source` flags from `/data/.source-flags` that the init container left behind. This keeps the source-handling contract visible in the deployment template rather than hidden in image layers.
