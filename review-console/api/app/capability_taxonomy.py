"""Parse the DCM Taxonomy markdown into structured capability vocabulary.

Foundational seed source for the capability catalog (see
docs/capability-catalog-design.md). The catalog is the independent living
inventory; the taxonomy is the normalization authority. This module turns the
human-authored taxonomy doc (`dcm/taxonomy/DCM-Taxonomy.md`) into machine-usable
structures the catalog normalizes against and back-fills into:

  - domains      ← Part 4 "Capability Domain Prefixes"  (prefix → domain name)
  - terms        ← Part 1 "### … Terms" tables           (canonical vocabulary)
  - anti_aliases ← Part 2 "Anti-Vocabulary"              (avoid → use-instead rules)

Pure + dependency-free (stdlib only) so it unit-tests in isolation and runs the
same whether the taxonomy is read from a vendored file, a configured path, or
the corpus. It does NOT touch the DB — projecting these into the
`capability_taxonomy_terms` seed is a separate, idempotent step.
"""
from __future__ import annotations

import re
from typing import Optional

# A markdown table row: "| a | b | c |" → ["a", "b", "c"] (trimmed, bold stripped).
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_SEP_ROW = re.compile(r"^\|[\s:|-]+\|?\s*$")  # the |---|---| separator line


def _strip(cell: str) -> str:
    return _BOLD.sub(r"\1", cell).strip()


def _table_rows(lines: list[str], start: int) -> tuple[list[list[str]], int]:
    """Collect the contiguous markdown table starting at/after `start`.

    Returns (data_rows, index_after_table). Header + separator are dropped;
    each data row is a list of trimmed, bold-stripped cells.
    """
    i = start
    rows: list[list[str]] = []
    seen_sep = False
    while i < len(lines):
        ln = lines[i].rstrip()
        if not ln.startswith("|"):
            if rows or seen_sep:
                break  # table ended
            i += 1
            continue   # pre-table prose
        if _SEP_ROW.match(ln):
            seen_sep = True
            i += 1
            continue
        cells = [_strip(c) for c in ln.strip().strip("|").split("|")]
        # First non-separator row is the header; keep only rows after the sep.
        if seen_sep:
            rows.append(cells)
        i += 1
    return rows, i


def _sections(md: str) -> list[tuple[str, int, list[str]]]:
    """Return (heading_text, level, body_lines) for each ## / ### section."""
    lines = md.splitlines()
    out: list[tuple[str, int, list[str]]] = []
    cur_head: Optional[str] = None
    cur_level = 0
    cur_body: list[str] = []
    for ln in lines:
        m = re.match(r"^(#{2,3})\s+(.*)$", ln)
        if m:
            if cur_head is not None:
                out.append((cur_head, cur_level, cur_body))
            cur_head = m.group(2).strip()
            cur_level = len(m.group(1))
            cur_body = []
        elif cur_head is not None:
            cur_body.append(ln)
    if cur_head is not None:
        out.append((cur_head, cur_level, cur_body))
    return out


def parse_taxonomy(md: str) -> dict:
    """Parse DCM-Taxonomy.md text → {domains, terms, anti_aliases}.

    - domains:      [{prefix, domain}]                  (Part 4)
    - terms:        [{term, definition, category}]      (Part 1 "### … Terms")
    - anti_aliases: [{avoid, reason, use_instead}]      (Part 2)
    `category` is the section heading the term came from (e.g. "Provider Types (11)").
    Pillar is 'platform' for everything sourced from the DCM taxonomy.
    """
    domains: list[dict] = []
    terms: list[dict] = []
    anti_aliases: list[dict] = []

    for head, _level, body in _sections(md):
        h = head.lower()
        rows, _ = _table_rows(body, 0)
        if not rows:
            continue
        if "capability domain prefixes" in h:
            for r in rows:
                if len(r) >= 2 and r[0]:
                    domains.append({"prefix": r[0], "domain": r[1]})
        elif "anti-vocabulary" in h:
            for r in rows:
                if len(r) >= 3 and r[0]:
                    anti_aliases.append(
                        {"avoid": r[0], "reason": r[1], "use_instead": r[2]}
                    )
        elif h.endswith("terms") or "foundational abstractions" in h or "provider types" in h \
                or "policy types" in h:
            # A canonical-vocabulary table: | Term | Definition |
            for r in rows:
                if len(r) >= 2 and r[0]:
                    terms.append({"term": r[0], "definition": r[1], "category": head})

    return {"domains": domains, "terms": terms, "anti_aliases": anti_aliases}


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import json
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "/Users/chris/git/dcm/taxonomy/DCM-Taxonomy.md"
    with open(path, encoding="utf-8") as fh:
        parsed = parse_taxonomy(fh.read())
    print(f"domains={len(parsed['domains'])} terms={len(parsed['terms'])} "
          f"anti_aliases={len(parsed['anti_aliases'])}")
    print(json.dumps({k: v[:3] for k, v in parsed.items()}, indent=2))
