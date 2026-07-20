"""
DAV Ops MCP Server — use-case / set / run CRUD as MCP tools.

A thin, stateless pass-through in front of the DAV review-console API. Every
tool maps to a `/api/...` call; the server holds no domain logic and no
database of its own. Its whole job is to make the DAV operations surface —
create/read/update use cases, manage sets, read runs and the analysis roadmap —
callable as MCP tools instead of hand-rolled curl with guessed request shapes.

Authorization is delegated entirely to DAV (the "rudimentary auth via the DAV
interface" model). The server does NOT mint or hold privileged credentials by
default: it relays the caller's own DAV Personal Access Token. A client connects
to the SSE endpoint with

    Authorization: Bearer dav_pat_...

and the server forwards that exact token to the DAV API on every call, so DAV's
existing RBAC decides what the caller may do. For unattended/pipeline use a
service PAT may be supplied via the DAV_API_TOKEN env (from a Secret); the
per-request header always wins over it.

Config (env):
  DAV_API_BASE     DAV API base URL (default in-cluster http://dav-review-api.dav.svc:8000)
  DAV_API_TOKEN    optional fallback PAT (used only when the caller sends no Authorization)
  DAV_API_VERIFY   TLS verify for DAV_API_BASE, "true"/"false" (default true; set false for the self-signed external LB)
  DAV_DEFAULT_PROJECT  default project id when a tool's project_id is omitted

Transports: stdio (default) or sse (the in-cluster deployment uses sse:8080).
"""

import os
import json
from typing import Optional

import httpx

try:
    from fastmcp import FastMCP
except ImportError:  # pragma: no cover
    print("Install fastmcp: pip install fastmcp")
    raise

# Reading the inbound HTTP request headers (to relay the caller's PAT) is only
# possible under an HTTP transport. Import defensively so stdio mode still works.
try:
    from fastmcp.server.dependencies import get_http_headers
except Exception:  # pragma: no cover
    def get_http_headers():
        return {}


DAV_API_BASE = os.environ.get("DAV_API_BASE", "http://dav-review-api.dav.svc:8000").rstrip("/")
DAV_API_VERIFY = os.environ.get("DAV_API_VERIFY", "true").lower() not in ("false", "0", "no")
DEFAULT_PROJECT = os.environ.get("DAV_DEFAULT_PROJECT")

mcp = FastMCP("dav-ops-mcp")


# Base DAV errors on ToolError so the status + body reach the model instead of
# being masked as a generic "tool failed".
try:
    from fastmcp.exceptions import ToolError as _ToolError
except Exception:  # pragma: no cover
    _ToolError = Exception


class DavError(_ToolError):
    """A DAV API call failed; message carries status + body for the model."""


def _caller_token() -> Optional[str]:
    """The caller's DAV PAT: the inbound Authorization: Bearer header if present,
    else the DAV_API_TOKEN service fallback. Header always wins."""
    try:
        h = get_http_headers() or {}
        auth = h.get("authorization") or h.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            tok = auth.split(None, 1)[1].strip()
            if tok:
                return tok
    except Exception:
        pass
    return os.environ.get("DAV_API_TOKEN") or None


def _resolve_project(project_id: Optional[int]) -> Optional[str]:
    if project_id is not None:
        return str(project_id)
    return str(DEFAULT_PROJECT) if DEFAULT_PROJECT else None


def _call(method: str, path: str, project_id: Optional[int] = None,
          json_body=None, params=None):
    """Make one DAV API call, relaying the caller's PAT. Returns parsed JSON
    (or text). Raises DavError on a non-2xx response or a missing credential."""
    tok = _caller_token()
    if not tok:
        raise DavError(
            "No DAV credential. Connect to this MCP endpoint with "
            "'Authorization: Bearer dav_pat_...' (your DAV Personal Access Token), "
            "or set DAV_API_TOKEN for unattended use.")
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    proj = _resolve_project(project_id)
    if proj:
        headers["X-DAV-Project"] = proj
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    url = f"{DAV_API_BASE}{path}"
    try:
        with httpx.Client(verify=DAV_API_VERIFY, timeout=60.0) as client:
            resp = client.request(method, url, headers=headers, json=json_body, params=params)
    except httpx.RequestError as e:
        raise DavError(f"DAV API unreachable at {url}: {e}")
    if resp.status_code >= 400:
        body = resp.text
        try:
            body = json.dumps(resp.json())
        except Exception:
            pass
        raise DavError(f"DAV {method} {path} -> HTTP {resp.status_code}: {body[:1000]}")
    if not resp.content:
        return {"ok": True, "status": resp.status_code}
    try:
        return resp.json()
    except Exception:
        return {"text": resp.text}


# --------------------------------------------------------------------------- #
# Projects & discovery
# --------------------------------------------------------------------------- #

@mcp.tool
def list_projects() -> dict:
    """List DAV projects the caller can see (id, name). Use this first to find the
    project_id to pass to the other tools."""
    return _call("GET", "/api/projects")


@mcp.tool
def whoami() -> dict:
    """Return the identity and projects the caller's PAT resolves to (GET /api/projects/mine).
    Handy to confirm the token and its access before doing real work."""
    return _call("GET", "/api/projects/mine")


# --------------------------------------------------------------------------- #
# Use cases
# --------------------------------------------------------------------------- #

@mcp.tool
def list_use_cases(project_id: Optional[int] = None, set_id: Optional[int] = None,
                   tag: Optional[str] = None) -> dict:
    """List managed use cases in a project. Optionally filter by set_id or tag."""
    params = {}
    if set_id is not None:
        params["set_id"] = set_id
    if tag:
        params["tag"] = tag
    return _call("GET", "/api/use-cases", project_id=project_id, params=params or None)


@mcp.tool
def get_use_case(uuid: str, project_id: Optional[int] = None) -> dict:
    """Get one managed use case's full detail, including its authored YAML content."""
    return _call("GET", f"/api/use-cases/{uuid}", project_id=project_id)


@mcp.tool
def validate_use_case(yaml_content: str, project_id: Optional[int] = None) -> dict:
    """Dry-run: validate UC YAML against the engine's controlled vocabulary without
    creating anything (POST /api/use-cases/validate). Call this before create/update."""
    return _call("POST", "/api/use-cases/validate", project_id=project_id,
                 json_body={"yaml_content": yaml_content})


@mcp.tool
def create_use_case(yaml_content: str, project_id: Optional[int] = None,
                    tags: Optional[list] = None) -> dict:
    """Create a managed use case from authored YAML. The YAML carries handle,
    scenario (description/actor/intent/success_criteria/dimensions), and metadata;
    title and readiness are derived server-side. Returns the created UC (with uuid)."""
    return _call("POST", "/api/use-cases", project_id=project_id,
                 json_body={"yaml_content": yaml_content, "tags": tags or []})


@mcp.tool
def update_use_case(uuid: str, yaml_content: str, project_id: Optional[int] = None,
                    tags: Optional[list] = None) -> dict:
    """Replace a managed use case's YAML content (PUT /api/use-cases/{uuid}). If the
    YAML carries a uuid it must match the target."""
    body = {"yaml_content": yaml_content}
    if tags is not None:
        body["tags"] = tags
    return _call("PUT", f"/api/use-cases/{uuid}", project_id=project_id, json_body=body)


@mcp.tool
def delete_use_case(uuid: str, project_id: Optional[int] = None) -> dict:
    """Delete a managed use case. Check get_delete_impact first if unsure."""
    return _call("DELETE", f"/api/use-cases/{uuid}", project_id=project_id)


# --------------------------------------------------------------------------- #
# Sets
# --------------------------------------------------------------------------- #

@mcp.tool
def list_sets(project_id: Optional[int] = None) -> dict:
    """List use-case sets in a project (id, name, member_count)."""
    return _call("GET", "/api/sets", project_id=project_id)


@mcp.tool
def create_set(name: str, description: Optional[str] = None,
               project_id: Optional[int] = None) -> dict:
    """Create a use-case set. Returns the created set (with id)."""
    return _call("POST", "/api/sets", project_id=project_id,
                 json_body={"name": name, "description": description or ""})


@mcp.tool
def add_uc_to_set(set_id: int, uc_uuid: str, uc_handle: Optional[str] = None,
                  uc_source: str = "managed", project_id: Optional[int] = None) -> dict:
    """Add a use case to a set. uc_source is 'managed' (default) or 'corpus'."""
    return _call("POST", f"/api/sets/{set_id}/members", project_id=project_id,
                 json_body={"uc_uuid": uc_uuid, "uc_source": uc_source,
                            "uc_handle": uc_handle, "uc_path": None})


@mcp.tool
def remove_uc_from_set(set_id: int, uc_uuid: str, project_id: Optional[int] = None) -> dict:
    """Remove a use case from a set."""
    return _call("DELETE", f"/api/sets/{set_id}/members/{uc_uuid}", project_id=project_id)


# --------------------------------------------------------------------------- #
# Runs & analysis (read)
# --------------------------------------------------------------------------- #

@mcp.tool
def list_runs(project_id: Optional[int] = None) -> dict:
    """List analysis runs in a project (GET /api/runs)."""
    return _call("GET", "/api/runs", project_id=project_id)


@mcp.tool
def get_use_case_runs(uuid: str, project_id: Optional[int] = None) -> dict:
    """List the runs attached to one use case (GET /api/use-cases/{uuid}/runs)."""
    return _call("GET", f"/api/use-cases/{uuid}/runs", project_id=project_id)


@mcp.tool
def get_gaps(project_id: Optional[int] = None) -> dict:
    """Read the analyzed architecture gaps for a project (GET /api/analysis/gaps)."""
    return _call("GET", "/api/analysis/gaps", project_id=project_id)


@mcp.tool
def get_roadmap(project_id: Optional[int] = None) -> dict:
    """Read the tiered capability roadmap derived from analyzed gaps
    (GET /api/analysis/roadmap)."""
    return _call("GET", "/api/analysis/roadmap", project_id=project_id)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="DAV Ops MCP server")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "sse"],
                        help="MCP transport: stdio (default) or sse for HTTP")
    parser.add_argument("--port", type=int, default=8080, help="Port for SSE transport")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind for SSE")
    args = parser.parse_args()
    if args.transport == "sse":
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
