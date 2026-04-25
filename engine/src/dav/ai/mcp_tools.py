"""
MCP client for the dav-docs-mcp server.

The server runs on port 8080 with SSE transport (fastmcp).

Exposed tools (as of this writing):
  - list_documents()                                   → str (JSON list of doc handles)
  - get_document(handle)                               → str (full document content)
  - get_document_section(handle, section_title)        → str (a single section)
  - search_docs(query, max_results=5)                  → str (JSON search results)
  - get_system_policy(policy_id)                       → str (policy detail)
  - get_profile(name)                                  → str (profile detail)
  - get_capability_count()                             → str (count + domain breakdown)

This client exposes those tools to the stage 2 agent loop as
OpenAI-style ToolDefinitions, and handles the JSON-RPC-over-SSE
plumbing to actually invoke them.

Design choice: rather than speak MCP's SSE protocol directly
(stateful session, event stream parsing), this client uses fastmcp's
own client library, which handles the protocol details and exposes a
simple call interface. Falls back to a minimal HTTP shim if fastmcp
isn't importable (e.g., during local dev).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from .client import ToolDefinition

log = logging.getLogger(__name__)

# --- Tool definitions exposed to the LLM ---

DCM_DOC_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="search_docs",
        description=(
            "Search DCM architecture documents by keyword. Returns a ranked list "
            "of document handles and snippets matching the query. Use this first "
            "to find which documents are relevant to a question, then fetch full "
            "documents or specific sections with get_document or get_document_section."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords to search. Keep to 2-5 terms for best results.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return (default 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    ),
    ToolDefinition(
        name="list_documents",
        description=(
            "List all DCM architecture documents available. Returns handles and "
            "titles. Use this when you need a broad overview of what's documented."
        ),
        parameters={"type": "object", "properties": {}},
    ),
    ToolDefinition(
        name="get_document",
        description=(
            "Retrieve the full content of a DCM architecture document by its handle. "
            "Use search_docs or list_documents first to find the handle."
        ),
        parameters={
            "type": "object",
            "properties": {
                "handle": {
                    "type": "string",
                    "description": "Document handle (e.g., '00-foundations', 'A-provider-contract').",
                },
            },
            "required": ["handle"],
        },
    ),
    ToolDefinition(
        name="get_document_section",
        description=(
            "Retrieve a specific section from a DCM architecture document. Useful "
            "when a document is large and you only need one part."
        ),
        parameters={
            "type": "object",
            "properties": {
                "handle": {"type": "string"},
                "section_title": {
                    "type": "string",
                    "description": "Section heading text, case-sensitive exact match.",
                },
            },
            "required": ["handle", "section_title"],
        },
    ),
    ToolDefinition(
        name="get_system_policy",
        description=(
            "Retrieve the detail of a DCM system policy by its ID. System policies "
            "are defined in various docs; IDs look like 'P-001', 'P-042', etc."
        ),
        parameters={
            "type": "object",
            "properties": {"policy_id": {"type": "string"}},
            "required": ["policy_id"],
        },
    ),
    ToolDefinition(
        name="get_profile",
        description=(
            "Retrieve details of a DCM operational profile by name. Profiles "
            "include: minimal, dev, standard, prod, fsi, sovereign."
        ),
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    ),
    ToolDefinition(
        name="get_capability_count",
        description=(
            "Return the total number of DCM capabilities and breakdown by domain. "
            "Use this for high-level questions about scope."
        ),
        parameters={"type": "object", "properties": {}},
    ),
]

@dataclass
class McpCallResult:
    tool: str
    args: dict[str, Any]
    result: str                          # raw string from MCP server
    ok: bool
    error: str | None = None

class McpClient:
    """
    Client for dav-docs-mcp. Uses fastmcp's client library if available,
    otherwise falls back to a minimal direct HTTP shim.
    """

    def __init__(self, server_url: str):
        """
        server_url — e.g. http://dav-docs-mcp.dav.svc:8080
        The '/sse' endpoint is appended automatically.
        """
        self.server_url = server_url.rstrip("/")
        self._fastmcp_client = None
        self._try_init_fastmcp()

    def _try_init_fastmcp(self) -> None:
        try:
            from fastmcp import Client
            self._fastmcp_client = Client(f"{self.server_url}/sse")
            log.info("fastmcp client initialized for %s", self.server_url)
        except ImportError:
            log.info("fastmcp not available; will use HTTP fallback")
            self._fastmcp_client = None

    def call(self, tool: str, args: dict[str, Any]) -> McpCallResult:
        """
        Invoke a tool. Tries fastmcp first; falls back to raw HTTP JSON-RPC
        over the SSE transport if fastmcp isn't installed or fails.
        """
        if self._fastmcp_client is not None:
            try:
                return self._call_via_fastmcp(tool, args)
            except Exception as e:
                log.warning("fastmcp call for %s failed: %s; trying HTTP fallback", tool, e)
        return self._call_via_http(tool, args)

    def _call_via_fastmcp(self, tool: str, args: dict[str, Any]) -> McpCallResult:
        """
        fastmcp Client is async; run synchronously via asyncio. We do this
        per-call rather than caching a running loop because the call surface
        is small and predictable.
        """
        import asyncio

        async def _do():
            async with self._fastmcp_client:
                return await self._fastmcp_client.call_tool(tool, args)

        try:
            result = asyncio.run(_do())
            # fastmcp returns CallToolResult with a .content array; stringify
            if hasattr(result, "content"):
                parts = []
                for item in result.content:
                    if hasattr(item, "text"):
                        parts.append(item.text)
                    else:
                        parts.append(str(item))
                text = "\n".join(parts) if parts else str(result)
            else:
                text = str(result)
            return McpCallResult(tool=tool, args=args, result=text, ok=True)
        except Exception as e:
            return McpCallResult(tool=tool, args=args, result="", ok=False, error=str(e))

    def _call_via_http(self, tool: str, args: dict[str, Any]) -> McpCallResult:
        """
        Minimal fallback: a plain HTTP POST to a JSON-RPC endpoint if one exists.
        fastmcp servers also accept non-SSE JSON-RPC on a POST endpoint in some
        versions; try that. If it fails, return an error result.
        """
        import requests
        url = f"{self.server_url}/mcp"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
        try:
            r = requests.post(url, json=payload, timeout=30)
            r.raise_for_status()
            data = r.json()
            if "result" in data:
                result = data["result"]
                # Extract content text
                if isinstance(result, dict) and "content" in result:
                    parts = []
                    for item in result["content"]:
                        if isinstance(item, dict) and "text" in item:
                            parts.append(item["text"])
                    text = "\n".join(parts)
                else:
                    text = json.dumps(result)
                return McpCallResult(tool=tool, args=args, result=text, ok=True)
            else:
                err = data.get("error", {}).get("message", str(data))
                return McpCallResult(tool=tool, args=args, result="", ok=False, error=err)
        except Exception as e:
            return McpCallResult(tool=tool, args=args, result="", ok=False, error=str(e))

    def list_tools(self) -> list[str]:
        """For health checks — confirm server advertises expected tools."""
        result = self.call("list_documents", {})
        return [result.tool] if result.ok else []

def get_tool_definitions() -> list[ToolDefinition]:
    """Return the tool definitions to pass to the LLM."""
    return DCM_DOC_TOOLS
