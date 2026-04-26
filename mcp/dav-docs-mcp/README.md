# dav-docs-mcp

MCP (Model Context Protocol) server that exposes a consumer's spec content to the DAV stage 2 analyzer. The agent calls MCP tools to retrieve specific documents and sections by handle, search for terms, and inspect the consumer's capability inventory.

## What it does

At startup, the server walks a configured docs directory and indexes every `.md` file:

- Document handle = stem of the filename (e.g. `00-foundations.md` → `00-foundations`)
- Section index = headings within each document
- Optional system-policy extraction from policy markers in the docs

The server then exposes MCP tools the agent uses to ground its analysis in actual spec content rather than hallucinating from training data.

## How DAV deploys it

The Ansible role at `../../ansible/roles/dav/tasks/mcp_servers.yaml` builds this directory as an in-cluster image and deploys it as a Kubernetes Deployment. An init container clones the consumer's spec repo into the docs path before the server starts; the docs path defaults to `/data/repo/architecture/data-model` (DCM convention).

The server is configured via deployment env to read repo URL + branch from the `dav-source-spec` ConfigMap, not from the image itself, so the review console can retarget the MCP at a different consumer/branch by patching the ConfigMap and rolling the Deployment.

## Run locally

```bash
cd mcp/dav-docs-mcp
pip install -r requirements.txt

# Stdio transport (default)
python server.py --docs-path /path/to/your/spec/architecture/data-model

# SSE transport (HTTP — what the in-cluster engine talks to)
python server.py \
    --docs-path /path/to/your/spec/architecture/data-model \
    --transport sse \
    --port 8080
```

The SSE endpoint at `/sse` is the MCP handshake; the engine connects there using the `fastmcp` client.

## Files

- `server.py` — server implementation
- `requirements.txt` — Python deps (fastmcp, uvicorn, etc.)
- `Containerfile` — container image spec (used by the in-cluster build)

## Notes

The hardcoded docs path inside the container (`/data/repo/architecture/data-model`) is consumer-specific. Generalizing this to be consumer-driven is captured under deployment work; for now the assumption is "the consumer's spec repo has architecture/data-model/ at its root."
