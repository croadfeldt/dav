"""
DAV Docs MCP Server — multi-source spec indexer.

Indexes one or more spec source trees and exposes them as MCP tools the
DAV stage 2 analyzer calls during analysis.

Two source modes:

1. Single-source (legacy / single-consumer single-repo):
     python server.py --docs-path /path/to/docs

   Handles are relative paths with .md extension, e.g.
   `00-foundations.md`, `subdir/topic.md`. The minimal-consumer example
   uses this mode.

2. Multi-source (new, supports peer repos like udlm + dcm):
     python server.py \
         --source udlm:/data/udlm \
         --source dcm:/data/dcm/architecture

   Handles are `<namespace>/<relpath_with_md>`, e.g.
   `udlm/contracts/provider-contract.md`,
   `dcm/architecture/credentials-and-auth/credentials.md`.

Either mode can serve to stdio or SSE transports; the in-cluster deployment
uses SSE on port 8080.
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

# Above this size, get_document_section returns a head + structural
# guidance instead of dumping the whole section. Set from observed
# behavior (OSAC 2026-05-29): a model fetching the ~88k-char / ~22k-token
# DCM Foundational Capabilities Matrix as one section bloated context and
# triggered a ~15k-token runaway generation that blew the inference route
# timeout. Normal prose sections are well under this; only enormous
# table/reference sections trip it, and they're exactly what should be
# drilled into (get_capability for matrix rows, a narrower subsection
# otherwise) rather than dumped wholesale. Corpus-agnostic — no document
# or namespace is special-cased.
_MAX_SECTION_CHARS = 32000
_SECTION_HEAD_CHARS = 6000
# Doc window size, shared by get_document and get_document_section. A doc this
# size or smaller is returned WHOLE; a larger doc is streamed in windows of this
# many chars (get_document_section bundles the requested section + following ones
# up to this budget, with a resume pointer). The agent crawls large docs (e.g.
# the 89KB Capabilities Matrix) one small section per turn — 15-28 sequential
# calls that exhausted the 30-call budget (3/15 UCs hit the cap). Windowing cuts
# that ~4x while keeping each result small enough for the engine's context
# manager to evict — returning a whole 22K-token doc in one result is NOT
# evictable and overflowed the 86K window (7/15 failed at 90000). Env-tunable;
# keep windows well under the budget (≈14K chars / ~3.5K tok is safe).
MAX_DOC_CHARS = int(os.environ.get("DAV_MCP_MAX_DOC_CHARS", "14000"))

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
    """Indexes spec documents from one or more source trees.

    Each source is a (namespace, root_path) pair. A document's handle is
    `<namespace>/<relpath>` for multi-source mode, or just `<relpath>` for
    single-source mode (the legacy default).
    """

    def __init__(self, sources: list[tuple[str, Path]], multi_source: bool):
        """
        Args:
            sources: list of (namespace, root_path) tuples. In single-source
                mode the namespace is unused for handle construction.
            multi_source: if True, handles are prefixed with the namespace.
                If False, handles are just relpath (legacy behavior).
        """
        self.sources = [(ns, Path(p)) for ns, p in sources]
        self.multi_source = multi_source
        self.documents: dict[str, dict] = {}
        self.system_policies: dict[str, dict] = {}
        # capability_id → {id, row, table_header, section, handle, namespace}
        # Populated from markdown table rows whose first cell is an ID like
        # `OBS-001`. Distinct from system_policies, which catches the same
        # IDs but only as ±200-char prose context. Get_capability returns
        # the structured table row; get_system_policy returns prose context.
        self.capabilities: dict[str, dict] = {}
        self._index()

    def _make_handle(self, namespace: str, rel_path: Path) -> str:
        rel_str = str(rel_path).replace("\\", "/")  # normalize for Windows
        if self.multi_source:
            # Multi-source: namespace + relpath including .md extension.
            # Example: 'udlm/contracts/provider-contract.md'.
            return f"{namespace}/{rel_str}"
        # Single-source (legacy): stem only (no .md, no subdir prefix).
        # Preserves back-compat with consumers whose UCs reference docs
        # by stem (the historical convention from when the DCM data-model/
        # tree was flat).
        return rel_path.stem

    def _index(self):
        """Walk every source's docs directory and index all markdown files."""
        for namespace, root in self.sources:
            if not root.exists():
                raise FileNotFoundError(
                    f"Source root not found: {root} (namespace={namespace})"
                )

            count = 0
            for md_file in sorted(root.rglob("*.md")):
                rel_path = md_file.relative_to(root)
                handle = self._make_handle(namespace, rel_path)

                content = md_file.read_text(encoding="utf-8")
                sections = self._extract_sections(content)
                policies = self._extract_system_policies(content)
                capabilities = self._extract_capabilities(content, sections)

                self.documents[handle] = {
                    "handle": handle,
                    "namespace": namespace,
                    "path": str(rel_path),
                    "title": self._extract_title(content),
                    "content": content,
                    "sections": sections,
                    "policies": [p["id"] for p in policies],
                    "word_count": len(content.split()),
                    "hash": hashlib.sha256(content.encode()).hexdigest()[:12],
                }

                for policy in policies:
                    # Policies indexed globally; collisions across sources
                    # keep the last (deterministic given sorted source order).
                    self.system_policies[policy["id"]] = {
                        **policy,
                        "source_document": handle,
                        "source_namespace": namespace,
                    }
                for cap in capabilities:
                    # First definition wins on cross-document collisions —
                    # capability matrices are authoritative once per ID.
                    if cap["id"] not in self.capabilities:
                        self.capabilities[cap["id"]] = {
                            **cap,
                            "source_document": handle,
                            "source_namespace": namespace,
                        }
                count += 1

            print(f"Indexed {count} documents from {namespace} ({root})")

        print(
            f"Total: {len(self.documents)} documents, "
            f"{len(self.system_policies)} system policies, "
            f"{len(self.capabilities)} capability matrix rows"
        )

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

    def _extract_capabilities(self, content: str, sections: list[dict]) -> list[dict]:
        """Extract capability rows from markdown tables.

        A capability row is a markdown-table line whose first non-empty cell
        is a structured ID like `OBS-001`, `GRP-003`, `IDM-012`. The
        immediately preceding `| ID | ... |` header line is captured as the
        table header so consumers see column meanings. The most recent
        heading at or above the row's level is recorded as the section
        context. Stage-2 agents call `get_capability` with the ID rather
        than misusing `get_document_section(section_title=<ID>)` — the
        behavior pattern that drove the 233-miss fishing cascade against
        DCM-Capabilities-Matrix.md.
        """
        rows = []
        lines = content.split("\n")
        row_re = re.compile(r"^\|\s*([A-Z]{2,5}-\d{3})\s*\|")
        header_re = re.compile(r"^\|\s*ID\s*\|", re.IGNORECASE)
        last_header = None
        for i, line in enumerate(lines):
            if header_re.match(line):
                last_header = line.strip()
                continue
            m = row_re.match(line)
            if not m:
                continue
            cap_id = m.group(1)
            # Find the deepest section heading at or before this line.
            section = None
            for s in sections:
                if s["line"] <= i + 1:
                    section = s["title"]
                else:
                    break
            rows.append({
                "id": cap_id,
                "row": line.strip(),
                "table_header": last_header,
                "section": section,
                "line": i + 1,
            })
        return rows

    def _extract_system_policies(self, content: str) -> list[dict]:
        """Extract system policy references like GRP-001, PLC-003, DPO-005."""
        policies = []
        seen = set()
        pattern = r"\b([A-Z]{2,5}-\d{3})\b"
        for match in re.finditer(pattern, content):
            policy_id = match.group(1)
            if policy_id not in seen:
                seen.add(policy_id)
                start = max(0, match.start() - 200)
                end = min(len(content), match.end() + 200)
                context = content[start:end].replace("\n", " ").strip()
                policies.append({
                    "id": policy_id,
                    "context": context,
                })
        return policies

    def search(self, query: str, max_results: int = 5,
               namespace: Optional[str] = None) -> list[dict]:
        """Full-text search across indexed documents.

        Tokenizes the query, matches documents containing ANY term
        (OR semantics), ranks by:
          1. Number of distinct query terms matched (heavily weighted)
          2. Total occurrences across all terms
          3. Bonus for terms appearing in the document title

        If `namespace` is provided, restricts search to that source.
        """
        raw_terms = re.findall(r"\w+", query.lower())
        terms = [t for t in raw_terms if len(t) >= 3 and t not in _STOPWORDS]
        if not terms:
            terms = [t for t in raw_terms if len(t) >= 2]
        if not terms:
            return []

        scored = []
        for handle, doc in self.documents.items():
            if namespace and doc["namespace"] != namespace:
                continue

            content_lower = doc["content"].lower()
            title_lower = doc["title"].lower()

            term_hits = {}
            title_hits = 0

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
            score = (distinct_matched * 1000) + total_occurrences + (title_hits * 50)

            scored.append({
                "handle": handle,
                "namespace": doc["namespace"],
                "title": doc["title"],
                "matches": total_occurrences,
                "distinct_terms_matched": distinct_matched,
                "total_terms": len(terms),
                "word_count": doc["word_count"],
                "_score": score,
            })

        scored.sort(key=lambda x: x["_score"], reverse=True)
        for r in scored:
            r.pop("_score", None)

        return scored[:max_results]


# --- MCP Server ---

mcp = FastMCP("dav-docs-mcp")

# Global index — initialized in main()
index: Optional[DocumentIndex] = None


def _resolve_handle(handle: str) -> Optional[dict]:
    """Look up a document by handle.

    Resolution is namespace-agnostic — works for whatever sources are
    registered via `index.sources`. In multi-source mode, accepts:

      - The full handle (`<namespace>/<relpath>`)
      - An unqualified relpath, IF unambiguous across sources
      - A "namespace + tail-segment" shortcut where the namespace is one
        of the indexed sources AND exactly one document in that
        namespace has a path ending with the trailing segment.

    The shortcut path was added 2026-05-29 after stage-2 runs surfaced
    a 200+ tool-call-miss/run pattern: the model calls search_docs
    (which returns the full handle correctly), then constructs a
    shortened "<ns>/<filename>" form for subsequent
    get_document_section calls because the full nested path is verbose
    in conversation context. Strict resolution made every such call
    miss; this third fallback turns the model's reasonable shorthand
    into successful lookups, without coupling to any specific
    namespace's directory structure.

    Returns the document dict or None.
    """
    if handle in index.documents:
        return index.documents[handle]

    if not index.multi_source:
        return None

    # Fallback 1: unqualified relpath, unambiguous match.
    matches = [
        doc for doc in index.documents.values()
        if doc["path"] == handle or doc["path"].replace("\\", "/") == handle
    ]
    if len(matches) == 1:
        return matches[0]

    # Fallback 2: namespace + filename shortcut. Split on the first `/`
    # to separate a candidate namespace from the rest. If the namespace
    # is one of our indexed sources AND exactly one document in that
    # namespace has a path ending with the remainder, use it.
    if "/" in handle:
        ns_candidate, _, tail = handle.partition("/")
        known_namespaces = {doc["namespace"] for doc in index.documents.values()}
        if ns_candidate in known_namespaces and tail:
            # Normalize separators for cross-platform robustness.
            tail_norm = tail.replace("\\", "/")
            ns_matches = [
                doc for doc in index.documents.values()
                if doc["namespace"] == ns_candidate
                and (
                    doc["path"].replace("\\", "/").endswith("/" + tail_norm)
                    or doc["path"].replace("\\", "/") == tail_norm
                )
            ]
            if len(ns_matches) == 1:
                return ns_matches[0]

    return None


@mcp.tool()
def list_documents(namespace: Optional[str] = None) -> str:
    """List indexed documents with their handles and titles.

    Args:
        namespace: Optional source namespace to filter by (e.g., 'udlm', 'dcm').
            If omitted, lists all documents from all sources.
    """
    grouped: dict[str, list[str]] = {}
    for handle, doc in sorted(index.documents.items()):
        if namespace and doc["namespace"] != namespace:
            continue
        ns = doc["namespace"]
        grouped.setdefault(ns, []).append(
            f"- **{handle}** — {doc['title']} "
            f"({doc['word_count']} words, {len(doc['policies'])} policies)"
        )

    if not grouped:
        return f"No documents found (namespace filter: {namespace or 'none'})."

    output = []
    for ns in sorted(grouped.keys()):
        if index.multi_source:
            output.append(f"## Source: {ns}\n")
        output.extend(grouped[ns])
        output.append("")
    return "\n".join(output).strip()


@mcp.tool()
def list_sources() -> str:
    """List the configured spec sources (namespaces and their roots)."""
    out = []
    for ns, root in index.sources:
        doc_count = sum(
            1 for d in index.documents.values() if d["namespace"] == ns
        )
        out.append(f"- **{ns}** — {doc_count} docs at `{root}`")
    if not index.multi_source:
        out.append("\n_(single-source mode — handles are unprefixed relpath)_")
    else:
        out.append("\n_(multi-source mode — handles are `<namespace>/<relpath>`)_")
    return "\n".join(out)


@mcp.tool()
def get_document(handle: str) -> str:
    """Retrieve a spec document by its handle.

    Returns the full document for short docs. For documents larger than
    ~8000 characters (a context-budget guardrail), returns the table of
    sections only, with guidance to call get_document_section for the
    specific part needed. This prevents a single tool call from consuming
    the entire context window.

    Args:
        handle: Document handle. Format depends on server mode:
            - Single-source: relative path with .md, e.g. 'subdir/topic.md'
            - Multi-source: '<namespace>/<relpath_with_md>',
              e.g., 'udlm/contracts/provider-contract.md'
            In multi-source mode an unqualified relpath also works if
            unambiguous across sources.
    """
    doc = _resolve_handle(handle)
    if not doc:
        available = ", ".join(sorted(index.documents.keys())[:20])
        return f"Document '{handle}' not found. Available (first 20): {available}..."

    content = doc["content"]
    if len(content) <= MAX_DOC_CHARS:
        return content

    sections_list = "\n".join(
        f"  - {'  ' * (s['level'] - 1)}{s['title']}"
        for s in doc["sections"]
    )
    return (
        f"# {doc['title']}\n\n"
        f"⚠ DOCUMENT TOO LARGE TO RETURN IN FULL ({len(content):,} characters, "
        f"{doc['word_count']:,} words).\n\n"
        f"REQUIRED NEXT ACTION: call "
        f"`get_document_section(handle='{doc['handle']}', section_title='<exact title from list below>')` "
        f"with one of the titles listed under \"Available Sections\". "
        f"DO NOT call `get_document('{doc['handle']}')` again — you will get this same "
        f"response. Pick a specific section from the outline below instead.\n\n"
        f"## Available Sections\n\n{sections_list}\n"
    )


@mcp.tool()
def get_document_section(handle: str, section_title: str) -> str:
    """Retrieve a specific section from a spec document.

    Args:
        handle: Document handle (see get_document for format).
        section_title: Section heading text (partial match supported).
    """
    doc = _resolve_handle(handle)
    if not doc:
        return f"Document '{handle}' not found."

    # Small docs: return whole (one call, no crawl). Larger docs fall through to
    # the forward-window logic below, which streams them in MAX_DOC_CHARS-sized
    # windows — small enough to stay evictable, unlike a single whole-doc result.
    if len(doc["content"]) <= MAX_DOC_CHARS:
        return doc["content"]

    lines = doc["content"].split("\n")
    section_lower = section_title.lower()

    start_line = None
    start_level = None
    for section in doc["sections"]:
        if section_lower in section["title"].lower():
            start_line = section["line"] - 1
            start_level = section["level"]
            break

    if start_line is None:
        sections_list = "\n".join(f"  - {s['title']}" for s in doc["sections"])
        return (
            f"⚠ Section '{section_title}' NOT FOUND in '{doc['handle']}'.\n\n"
            f"REQUIRED NEXT ACTION (pick exactly one):\n"
            f"  (a) Call `get_document_section(handle='{doc['handle']}', section_title='<title>')` "
            f"with one of the EXACT titles listed below — this document does contain "
            f"the listed sections.\n"
            f"  (b) Call `search_docs(query='<different keywords>')` if the topic "
            f"you want isn't in this document — try broader or different terms.\n\n"
            f"DO NOT retry section_title='{section_title}' in a different document. "
            f"That section title was not in this document; trying it in another "
            f"document without first checking the section list is unlikely to help. "
            f"The same content might be under a different section name — read the "
            f"list below or search with different terms.\n\n"
            f"Available sections in '{doc['handle']}':\n{sections_list}"
        )

    # Forward WINDOW: bundle the requested section AND the following sections until
    # we reach ~MAX_DOC_CHARS, then stop and tell the agent where to resume. This
    # lets the agent read a large doc (e.g. the 89KB Capabilities Matrix) in a few
    # windows instead of crawling one small section per turn — which exhausted the
    # 30-call budget (3/15 UCs hit the cap doing exactly this). Each window is kept
    # small enough that the engine's context manager evicts older windows, the SAME
    # sliding-window memory dynamics as the original per-section crawl that
    # validated 15/15 — just ~4x fewer round-trips. (Returning the whole doc in one
    # result is NOT evictable and overflowed the 86K context window — 7/15 failed.)
    end_line = len(lines)
    acc = 0
    for i in range(start_line, len(lines)):
        acc += len(lines[i]) + 1
        if acc >= MAX_DOC_CHARS:
            end_line = i + 1
            break

    body = "\n".join(lines[start_line:end_line])

    # Resume pointer: if the doc continues past this window, name the next section
    # so the agent can fetch the following window in one move.
    if end_line < len(lines):
        nxt = next((s["title"] for s in doc["sections"]
                    if s["line"] - 1 >= end_line), None)
        if nxt:
            body += (
                f"\n\n[… '{doc['handle']}' continues beyond this window. To read on, "
                f"call get_document_section(handle='{doc['handle']}', "
                f"section_title='{nxt}').]"
            )

    if len(body) <= _MAX_SECTION_CHARS:
        return body

    # Oversized section — return a head + drill-down guidance instead of
    # dumping the whole thing into the agent's context.
    head = body[:_SECTION_HEAD_CHARS]
    est_tokens = len(body) // 4

    # Sub-headings deeper than this section, for "request a narrower
    # subsection" guidance.
    sub_sections = [
        s["title"] for s in doc["sections"]
        if start_line < s["line"] - 1 < end_line and s["level"] > start_level
    ]

    # Capability-matrix rows inside this section (first table cell is an
    # ID like OBS-002), so we can point the model at get_capability.
    row_re = re.compile(r"^\|\s*([A-Z]{2,5}-\d{3})\s*\|")
    cap_ids = [m.group(1) for ln in body.split("\n")
               if (m := row_re.match(ln))]

    parts = [
        f"⚠ Section '{section_title}' in '{doc['handle']}' is large "
        f"({len(body)} chars, ~{est_tokens} tokens) and was TRUNCATED to "
        f"avoid bloating your context. The first {_SECTION_HEAD_CHARS} "
        f"characters are shown below.\n",
    ]
    if cap_ids:
        prefixes = sorted({cid.split("-")[0] for cid in cap_ids})
        sample = ", ".join(cap_ids[:8])
        parts.append(
            f"This section is a capability matrix with {len(cap_ids)} row "
            f"IDs (prefixes: {', '.join(prefixes)}; e.g. {sample}). DO NOT "
            f"re-fetch the whole section. Call `get_capability(capability_id="
            f"'<ID>')` for each specific capability you need — it returns "
            f"that single row with its column headers.\n"
        )
    if sub_sections:
        listed = "\n".join(f"  - {t}" for t in sub_sections)
        parts.append(
            f"Or request a narrower subsection by its exact title:\n{listed}\n"
        )
    if not cap_ids and not sub_sections:
        parts.append(
            "This section has no subsections to narrow into. Use the head "
            "below; if you need more, call `get_document` for the full text "
            "only when truly necessary.\n"
        )
    parts.append(f"--- first {_SECTION_HEAD_CHARS} chars ---\n{head}")
    return "\n".join(parts)


@mcp.tool()
def search_docs(query: str, max_results: int = 5,
                namespace: Optional[str] = None) -> str:
    """Full-text search across indexed spec documents.

    Args:
        query: Search query string. Multiple terms are OR-matched; results
            ranked by distinct terms matched, then total occurrences.
        max_results: Maximum number of results to return (default 5).
        namespace: Optional source namespace to filter by ('udlm', 'dcm', etc.).
            If omitted, searches all sources.
    """
    results = index.search(query, max_results, namespace=namespace)
    if not results:
        scope = f" in namespace '{namespace}'" if namespace else ""
        return f"No documents match '{query}'{scope}."

    output = []
    for r in results:
        prefix = f"[{r['namespace']}] " if index.multi_source else ""
        output.append(
            f"- {prefix}**{r['handle']}** — {r['title']} "
            f"({r['distinct_terms_matched']}/{r['total_terms']} terms, "
            f"{r['matches']} total matches)"
        )
    return "\n".join(output)


@mcp.tool()
def get_system_policy(policy_id: str) -> str:
    """Retrieve a specific system policy definition by its ID.

    Args:
        policy_id: System policy ID, e.g., 'GRP-001', 'PLC-003', 'DPO-005'.
    """
    policy = index.system_policies.get(policy_id)
    if not policy:
        prefix = policy_id.split("-")[0] if "-" in policy_id else ""
        similar = [pid for pid in index.system_policies if pid.startswith(prefix)]
        if similar:
            return f"Policy '{policy_id}' not found. Similar: {', '.join(sorted(similar))}"
        return f"Policy '{policy_id}' not found. {len(index.system_policies)} policies indexed."

    return json.dumps({
        "id": policy["id"],
        "source_document": policy["source_document"],
        "source_namespace": policy.get("source_namespace"),
        "context": policy["context"],
    }, indent=2)


@mcp.tool()
def get_capability(capability_id: str) -> str:
    """Retrieve a capability matrix row by its structured ID.

    Use this for IDs like `OBS-002`, `GRP-007`, `IDM-012` that appear as
    rows in a capabilities matrix table. Returns the section heading
    above the matrix, the table header row (column meanings), and the
    matching capability row.

    Do NOT pass a capability ID to `get_document_section` as a
    `section_title` — capability IDs are table-row identifiers, not
    markdown section headers. Calling `get_document_section(handle=...,
    section_title='OBS-002')` will miss because no section has that
    title; the row lives INSIDE the matrix section.

    Args:
        capability_id: Structured ID like `OBS-002`, `GRP-001`, `IDM-005`.
            Case-insensitive.
    """
    cap_key = capability_id.strip().upper()
    cap = index.capabilities.get(cap_key)
    if not cap:
        prefix = cap_key.split("-")[0] if "-" in cap_key else cap_key
        siblings = sorted(
            cid for cid in index.capabilities if cid.startswith(prefix + "-")
        )
        if siblings:
            return (
                f"Capability '{capability_id}' not found. Other "
                f"'{prefix}-*' capabilities indexed: {', '.join(siblings)}"
            )
        return (
            f"Capability '{capability_id}' not found. "
            f"{len(index.capabilities)} capabilities indexed across "
            f"{len({c['source_document'] for c in index.capabilities.values()})} document(s). "
            f"If the ID is mentioned only in prose (not a table row), try "
            f"`get_system_policy('{capability_id}')` for surrounding context."
        )

    return json.dumps({
        "id": cap["id"],
        "source_document": cap["source_document"],
        "source_namespace": cap.get("source_namespace"),
        "section": cap["section"],
        "table_header": cap["table_header"],
        "row": cap["row"],
    }, indent=2)


@mcp.tool()
def get_profile(name: str) -> str:
    """Retrieve a DCM deployment profile definition and its characteristics.

    Args:
        name: Profile name: minimal, dev, standard, prod, fsi, or sovereign.
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
    """Return indexed document and policy counts, broken down by source."""
    by_ns: dict[str, int] = {}
    for doc in index.documents.values():
        by_ns[doc["namespace"]] = by_ns.get(doc["namespace"], 0) + 1
    return json.dumps({
        "indexed_documents": len(index.documents),
        "indexed_policies": len(index.system_policies),
        "documents_by_source": by_ns,
        "multi_source": index.multi_source,
    }, indent=2)


# --- Entry Point ---

def _parse_source_arg(arg: str) -> tuple[str, Path]:
    """Parse a --source value of the form `namespace:path`."""
    if ":" not in arg:
        raise ValueError(
            f"--source value must be 'namespace:path', got: {arg!r}"
        )
    ns, path = arg.split(":", 1)
    ns = ns.strip()
    path = path.strip()
    if not ns or not path:
        raise ValueError(
            f"--source value must have non-empty namespace and path, got: {arg!r}"
        )
    return ns, Path(path)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="DAV Docs MCP Server — multi-source spec indexer"
    )
    parser.add_argument(
        "--docs-path",
        help="Single source path (legacy mode). Handles are relpath without "
             "namespace prefix. Mutually exclusive with --source.",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help="Multi-source spec root, format `namespace:path`. Repeatable. "
             "Example: --source udlm:/data/udlm --source dcm:/data/dcm/architecture",
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

    if args.docs_path and args.source:
        parser.error("--docs-path and --source are mutually exclusive")
    if not args.docs_path and not args.source:
        parser.error("must provide either --docs-path or at least one --source")

    if args.docs_path:
        sources = [("docs", Path(args.docs_path))]
        multi_source = False
    else:
        sources = [_parse_source_arg(s) for s in args.source]
        multi_source = True

    global index
    index = DocumentIndex(sources, multi_source=multi_source)

    if args.transport == "sse":
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
