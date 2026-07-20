# dav-ops-mcp

An MCP server that exposes the DAV operations surface — use-case / set / run /
analysis CRUD — as tools, so agents drive DAV through typed calls instead of
hand-rolled `curl` with guessed request shapes.

It is a **stateless pass-through** in front of the review-console API
(`dav-review-api.<ns>.svc:8000`). It holds no database and no domain logic:
every tool maps to one `/api/...` call.

## Authorization — delegated to DAV

There is no separate auth system. The server relays the **caller's own DAV
Personal Access Token** to the API, so DAV's existing RBAC decides what each
caller may do. Connect to the SSE endpoint with:

```
Authorization: Bearer dav_pat_...
```

and that exact token is forwarded on every call. Mint a PAT in the DAV UI
(Agents panel) or via `POST /api/agent-tokens`. For unattended/pipeline use, a
service PAT can be supplied via the `DAV_API_TOKEN` env (from a Secret); a
per-request `Authorization` header always wins over it.

## Tools

- **Discovery:** `list_projects`, `whoami`
- **Use cases:** `list_use_cases`, `get_use_case`, `validate_use_case`,
  `create_use_case`, `update_use_case`, `delete_use_case`
- **Sets:** `list_sets`, `create_set`, `add_uc_to_set`, `remove_uc_from_set`
- **Runs & analysis (read):** `list_runs`, `get_use_case_runs`, `get_gaps`, `get_roadmap`

Most tools take an optional `project_id` (falls back to the `DAV_DEFAULT_PROJECT`
env); it becomes the `X-DAV-Project` header.

## Config (env)

| Var | Default | Meaning |
|---|---|---|
| `DAV_API_BASE` | `http://dav-review-api.dav.svc:8000` | DAV API base URL |
| `DAV_API_TOKEN` | — | optional fallback PAT (only when the caller sends no `Authorization`) |
| `DAV_API_VERIFY` | `true` | TLS verify; set `false` for the self-signed external LB |
| `DAV_DEFAULT_PROJECT` | — | default project id when a tool's `project_id` is omitted |

## Run locally

```bash
pip install -r requirements.txt
# stdio (for a local MCP client):
DAV_API_BASE=https://10.0.90.22:8843 DAV_API_VERIFY=false \
  DAV_API_TOKEN=dav_pat_... python server.py --transport stdio
# SSE (as deployed):
python server.py --transport sse --port 8080
```

## Deployment

Built and deployed by the `dav` Ansible role (`tasks/mcp_servers.yaml`,
template `mcp-ops-deployment.yaml.j2`) as a separate container in the DAV
deployment: a binary Docker BuildConfig → ImageStream (`dav-ops-mcp:latest`) →
Deployment + Service (`dav-ops-mcp:8080`) + edge-TLS Route. It shares the
namespace with the review-console API and reaches it over the in-cluster
Service.
