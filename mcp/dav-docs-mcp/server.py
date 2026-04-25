"""
DCM Architecture Docs MCP Server

Serves the 57 data model documents + 15 specifications as structured,
queryable resources via the Model Context Protocol (MCP).

Usage:
    python server.py --docs-path /path/to/dcm-project/data-model

The server indexes all .md files at startup and exposes them via MCP tools.
Both local LLMs (via orchestrator) and Claude API (via tool definitions)
consume the same interface.
"""

import os
import re
import json
import hashlib
from pathlib import Path
from typing import Optional

try:
    from fastmcp import FastMCP
except ImportError:
    print("Install fastmcp: pip install fastmcp")
    raise

# Stopwords filtered from queries so phrases like "how does audit work"
# rank on "audit" rather than matching nearly every document on "how".
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "if", "in", "into", "is", "it", "its", "of", "on",
    "or", "that", "the", "this", "to", "was", "were", "will", "with",
    "how", "what", "when", "which", "who", "why", "does", "do",
}


# --- Document Index ---

class DocumentIndex:
    """Indexes DCM architecture documents for search and retrieval."""

    def __init__(self, docs_path: str):
        self.docs_path = Path(docs_path)
        self.documents: dict[str, dict] = {}
        self.system_policies: dict[str, dict] = {}
        self._index()

    def _index(self):
        """Walk the docs directory and index all markdown files."""
        if not self.docs_path.exists():
            raise FileNotFoundError(f"Docs path not found: {self.docs_path}")

        for md_file in sorted(self.docs_path.rglob("*.md")):
            rel_path = md_file.relative_to(self.docs_path)
            handle = md_file.stem  # e.g., "00-foundations", "A-provider-contract"

            content = md_file.read_text(encoding="utf-8")
            sections = self._extract_sections(content)
            policies = self._extract_system_policies(content)

            self.documents[handle] = {
                "handle": handle,
                "path": str(rel_path),
                "title": self._extract_title(content),
                "content": content,
                "sections": sections,
                "policies": [p["id"] for p in policies],
                "word_count": len(content.split()),
                "hash": hashlib.sha256(content.encode()).hexdigest()[:12],
            }

            for policy in policies:
                self.system_policies[policy["id"]] = {
                    **policy,
                    "source_document": handle,
                }

        print(f"Indexed {len(self.documents)} documents, {len(self.system_policies)} system policies")

    def _extract_title(self, content: str) -> str:
        """Extract the first heading as the document title."""
        for line in content.split("\n"):
            if line.startswith("# "):
                return line.lstrip("# ").strip()
        return "(untitled)"

    def _extract_sections(self, content: str) -> list[dict]:
        """Extract section headings with line numbers."""
        sections = []
        for i, line in enumerate(content.split("\n"), 1):
            match = re.match(r"^(#{1,4})\s+(.+)", line)
            if match:
                sections.append({
                    "level": len(match.group(1)),
                    "title": match.group(2).strip(),
                    "line": i,
                })
        return sections

    def _extract_system_policies(self, content: str) -> list[dict]:
        """Extract system policy references like GRP-001, PLC-003, DPO-005."""
        policies = []
        seen = set()
        pattern = r"\b([A-Z]{2,5}-\d{3})\b"
        for match in re.finditer(pattern, content):
            policy_id = match.group(1)
            if policy_id not in seen:
                seen.add(policy_id)
                # Try to find context around the policy reference
                start = max(0, match.start() - 200)
                end = min(len(content), match.end() + 200)
                context = content[start:end].replace("\n", " ").strip()
                policies.append({
                    "id": policy_id,
                    "context": context,
                })
        return policies

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        """Full-text search across all documents.

        Tokenizes the query, matches documents containing ANY term
        (OR semantics), ranks by:
          1. Number of distinct query terms matched (heavily weighted)
          2. Total occurrences across all terms
          3. Bonus for terms appearing in the document title

        A doc matching 3 of 3 terms always outranks one matching 1 of 3,
        regardless of repetition count.
        """
        # Tokenize: lowercase, split on non-word chars, drop short/stopwords
        raw_terms = re.findall(r"\w+", query.lower())
        terms = [t for t in raw_terms if len(t) >= 3 and t not in _STOPWORDS]

        # Fallback if stopword filtering left nothing (e.g. "id" alone)
        if not terms:
            terms = [t for t in raw_terms if len(t) >= 2]
        if not terms:
            return []

        scored = []
        for handle, doc in self.documents.items():
            content_lower = doc["content"].lower()
            title_lower = doc["title"].lower()

            term_hits = {}   # term -> total occurrences in body
            title_hits = 0   # distinct terms appearing in title

            for term in terms:
                body_count = content_lower.count(term)
                if body_count:
                    term_hits[term] = body_count
                if term in title_lower:
                    title_hits += 1

            if not term_hits:
                continue

            distinct_matched = len(term_hits)
            total_occurrences = sum(term_hits.values())

            # Distinct-terms dominates; occurrences tie-break; title boosts
            score = (distinct_matched * 1000) + total_occurrences + (title_hits * 50)

            scored.append({
                "handle": handle,
                "title": doc["title"],
                "matches": total_occurrences,
                "distinct_terms_matched": distinct_matched,
                "total_terms": len(terms),
                "word_count": doc["word_count"],
                "_score": score,
            })

        scored.sort(key=lambda x: x["_score"], reverse=True)

        # Strip internal score before returning
        for r in scored:
            r.pop("_score", None)

        return scored[:max_results]


# --- MCP Server ---

mcp = FastMCP("dav-docs-mcp")

# Global index — initialized in main()
index: Optional[DocumentIndex] = None


@mcp.tool()
def list_documents() -> str:
    """List all available DCM architecture documents with their handles and titles."""
    docs = []
    for handle, doc in sorted(index.documents.items()):
        docs.append(f"- **{handle}** — {doc['title']} ({doc['word_count']} words, {len(doc['policies'])} policies)")
    return "\n".join(docs)


@mcp.tool()
def get_document(handle: str) -> str:
    """Retrieve a DCM architecture document by its handle.

    Returns the full document for short docs. For documents larger than
    ~8000 characters (a context-budget guardrail), returns the table of
    sections only, with guidance to call get_document_section for the
    specific part needed. This prevents a single tool call from consuming
    the entire context window.

    Args:
        handle: Document handle, e.g., '00-foundations', 'A-provider-contract'
    """
    doc = index.documents.get(handle)
    if not doc:
        available = ", ".join(sorted(index.documents.keys())[:20])
        return f"Document '{handle}' not found. Available: {available}..."

    content = doc["content"]
    # ~8000 chars is roughly 2000 tokens — a reasonable upper bound for a
    # single tool response. DCM architecture docs routinely exceed 50k chars.
    MAX_DOC_CHARS = 8000

    if len(content) <= MAX_DOC_CHARS:
        return content

    # Too large — return outline + pointer to get_document_section
    sections_list = "\n".join(
        f"  - {'  ' * (s['level'] - 1)}{s['title']}"
        for s in doc["sections"]
    )
    return (
        f"# {doc['title']}\n\n"
        f"(Document too large to return in full: {len(content):,} characters, "
        f"{doc['word_count']:,} words. Use `get_document_section(handle='{handle}', "
        f"section_title='<title>')` to retrieve a specific section.)\n\n"
        f"## Available Sections\n\n{sections_list}\n"
    )


@mcp.tool()
def get_document_section(handle: str, section_title: str) -> str:
    """Retrieve a specific section from a DCM architecture document.

    Args:
        handle: Document handle
        section_title: Section heading text (partial match supported)
    """
    doc = index.documents.get(handle)
    if not doc:
        return f"Document '{handle}' not found."

    lines = doc["content"].split("\n")
    section_lower = section_title.lower()

    # Find the section start
    start_line = None
    start_level = None
    for section in doc["sections"]:
        if section_lower in section["title"].lower():
            start_line = section["line"] - 1
            start_level = section["level"]
            break

    if start_line is None:
        sections_list = "\n".join(f"  - {s['title']}" for s in doc["sections"])
        return f"Section '{section_title}' not found in {handle}. Available sections:\n{sections_list}"

    # Find the section end (next heading at same or higher level)
    end_line = len(lines)
    for section in doc["sections"]:
        if section["line"] - 1 > start_line and section["level"] <= start_level:
            end_line = section["line"] - 1
            break

    return "\n".join(lines[start_line:end_line])


@mcp.tool()
def search_docs(query: str, max_results: int = 5) -> str:
    """Full-text search across all DCM architecture documents.

    Args:
        query: Search query string. Multiple terms are OR-matched; results
            ranked by distinct terms matched, then total occurrences.
        max_results: Maximum number of results to return (default 5)
    """
    results = index.search(query, max_results)
    if not results:
        return f"No documents match '{query}'."

    output = []
    for r in results:
        output.append(
            f"- **{r['handle']}** — {r['title']} "
            f"({r['distinct_terms_matched']}/{r['total_terms']} terms, "
            f"{r['matches']} total matches)"
        )
    return "\n".join(output)


@mcp.tool()
def get_system_policy(policy_id: str) -> str:
    """Retrieve a specific system policy definition by its ID.

    Args:
        policy_id: System policy ID, e.g., 'GRP-001', 'PLC-003', 'DPO-005'
    """
    policy = index.system_policies.get(policy_id)
    if not policy:
        # Try to find similar
        prefix = policy_id.split("-")[0] if "-" in policy_id else ""
        similar = [pid for pid in index.system_policies if pid.startswith(prefix)]
        if similar:
            return f"Policy '{policy_id}' not found. Similar: {', '.join(sorted(similar))}"
        return f"Policy '{policy_id}' not found. {len(index.system_policies)} policies indexed."

    return json.dumps({
        "id": policy["id"],
        "source_document": policy["source_document"],
        "context": policy["context"],
    }, indent=2)


@mcp.tool()
def get_profile(name: str) -> str:
    """Retrieve a DCM deployment profile definition and its characteristics.

    Args:
        name: Profile name: minimal, dev, standard, prod, fsi, or sovereign
    """
    profiles = {
        "minimal": {
            "handle": "system/profile/minimal",
            "tenancy": "Optional — auto-created",
            "enforcement": "Advisory only",
            "cross_tenant": "allow_all",
            "audit": "None",
            "zero_trust": "none",
            "recovery_posture": "automated-reconciliation",
        },
        "dev": {
            "handle": "system/profile/dev",
            "tenancy": "Recommended",
            "enforcement": "Warn only",
            "cross_tenant": "operational_only",
            "audit": "Basic 90-day",
            "zero_trust": "boundary",
            "recovery_posture": "automated-reconciliation",
        },
        "standard": {
            "handle": "system/profile/standard",
            "tenancy": "Required",
            "enforcement": "Blocking",
            "cross_tenant": "explicit_only",
            "audit": "Compliance-grade",
            "zero_trust": "boundary",
            "recovery_posture": "automated-reconciliation",
        },
        "prod": {
            "handle": "system/profile/prod",
            "tenancy": "Required",
            "enforcement": "Blocking + SLA",
            "cross_tenant": "explicit_only",
            "audit": "Compliance-grade",
            "zero_trust": "full",
            "recovery_posture": "notify-and-wait",
        },
        "fsi": {
            "handle": "system/profile/fsi",
            "tenancy": "Hard tenancy",
            "enforcement": "Blocking",
            "cross_tenant": "explicit_only",
            "audit": "7-year retention",
            "zero_trust": "full",
            "recovery_posture": "notify-and-wait",
            "audit_granularity": "mutation (minimum)",
        },
        "sovereign": {
            "handle": "system/profile/sovereign",
            "tenancy": "Hard tenancy",
            "enforcement": "Blocking",
            "cross_tenant": "deny_all",
            "audit": "10-year retention",
            "zero_trust": "hardware_attested",
            "recovery_posture": "notify-and-wait",
            "audit_granularity": "field (minimum)",
        },
    }
    profile = profiles.get(name.lower())
    if not profile:
        return f"Profile '{name}' not found. Available: {', '.join(profiles.keys())}"
    return json.dumps(profile, indent=2)


@mcp.tool()
def get_capability_count() -> str:
    """Return current DCM capability and document counts."""
    return json.dumps({
        "capabilities": 322,
        "domains": 39,
        "data_model_documents": 57,
        "specifications": 15,
        "consumer_api_paths": 72,
        "admin_api_paths": 57,
        "events": 82,
        "event_domains": 26,  
        "provider_types": 6,
        "policy_evaluation_modes": 2,
        "control_plane_services": 9,
        "indexed_documents": len(index.documents),
        "indexed_policies": len(index.system_policies),
    }, indent=2)


# --- Entry Point ---

def main():
    import argparse
    parser = argparse.ArgumentParser(description="DCM Architecture Docs MCP Server")
    parser.add_argument(
        "--docs-path",
        required=True,
        help="Path to the DCM data-model directory containing .md files",
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse"],
        help="MCP transport: stdio (default) or sse for HTTP",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for SSE transport (default 8080)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default 0.0.0.0)",
    )
    args = parser.parse_args()

    global index
    index = DocumentIndex(args.docs_path)

    if args.transport == "sse":
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
