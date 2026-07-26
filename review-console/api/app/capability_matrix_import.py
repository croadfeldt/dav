"""Import the DCM Foundational Capabilities Matrix into `capability_catalog`.

**Why this exists.** Gap identity (ADR-009) anchors a gap to the catalog capability
it concerns, which is what makes a gap trackable across runs instead of a 3-7 word
title that churns between identical runs. That machinery shipped, but measured
against the live install the catalog is effectively empty — so every gap still
falls back to a per-analysis `GAP-NNN` and the keystone is inert.

The vocabulary it needs already exists, authored and curated, as
`architecture/DCM-Capabilities-Matrix.md` in the DCM spec repo: 337 capability IDs
(`IAM-001`, `AUTH-002`, …) with names, per-perspective definitions, and declared
dependencies. This module parses that document and upserts it as CURATED catalog
entries, idempotently, in the same spirit as `capability_catalog.seed_dcm_taxonomy`.

**What it deliberately does NOT do.** Entries land with `status='confirmed'` and
`created_via='matrix-import'`, never `status='observed'`. That keeps them out of
the observed-vs-canonical analytics (`catalog_observed` / `catalog_gaps` count
`status='observed'`), so importing a curated vocabulary cannot be mistaken for
evidence that a capability was seen in a real estate. The two populations stay
distinguishable by both `status` and `created_via`.

Dry-run is the default everywhere: nothing writes unless a caller passes
``apply=True``.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("dav-review-api")

# `| IAM-001 | Actor Authentication | consumer… | provider… | platform… | IAM-001, IAM-003 |`
_ROW = re.compile(r"^\|\s*([A-Z][A-Z0-9]*-\d{3,})\s*\|(.*)$")
_EMPTY_CELL = {"", "-", "—", "–", "n/a", "none", "tbd"}


def _cells(rest: str) -> list[str]:
    """Split the remainder of a matrix row into trimmed cells."""
    return [c.strip() for c in rest.rstrip().rstrip("|").split("|")]


def _clean(cell: str) -> str:
    """Normalize a matrix cell; the document uses em-dash for 'not applicable'."""
    v = (cell or "").strip()
    return "" if v.lower() in _EMPTY_CELL else v


def _depends(cell: str) -> list[str]:
    """Parse the Depends On cell into capability ids."""
    v = _clean(cell)
    if not v:
        return []
    return [d for d in (p.strip() for p in re.split(r"[,;]", v)) if re.fullmatch(r"[A-Z][A-Z0-9]*-\d{3,}", d)]


def parse_matrix(text: str) -> list[dict[str, Any]]:
    """Parse the capabilities matrix markdown into catalog-shaped rows.

    Later duplicate ids are ignored — the first definition wins, and a duplicate
    is reported by `import_matrix` rather than silently overwriting.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    section = ""
    for line in text.splitlines():
        if line.startswith("#"):
            section = line.lstrip("#").strip()
            continue
        m = _ROW.match(line)
        if not m:
            continue
        cap_key = m.group(1).strip()
        cells = _cells(m.group(2))
        if len(cells) < 2:          # malformed / not a capability row
            continue
        if cap_key in seen:
            duplicates.append(cap_key)
            continue
        seen.add(cap_key)
        name = _clean(cells[0])
        # Definition: join the perspective columns that carry content, labelled so
        # a reader (and the LLM that sees the enum) can tell them apart.
        perspectives = []
        for label, idx in (("Consumer", 1), ("Service Provider", 2), ("Platform/Admin", 3)):
            if idx < len(cells):
                v = _clean(cells[idx])
                if v:
                    perspectives.append(f"{label}: {v}")
        out.append({
            "cap_key": cap_key,
            "name": name,
            "definition": " | ".join(perspectives),
            "depends_on": _depends(cells[4]) if len(cells) > 4 else [],
            "domain_prefix": cap_key.split("-", 1)[0],
            "domain": re.sub(r"^\d+\.\s*", "", section),
            "spec_refs": ["DCM-Capabilities-Matrix"],
        })
    if duplicates:
        log.info("capability matrix: %d duplicate id(s) ignored: %s",
                 len(duplicates), ", ".join(sorted(set(duplicates))[:10]))
    return out


def load_matrix(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"capabilities matrix not found at {p}")
    return parse_matrix(p.read_text(encoding="utf-8"))


async def import_matrix(conn, rows: list[dict[str, Any]], project_id: Optional[int],
                        *, apply: bool = False, actor: str = "matrix-import") -> dict:
    """Upsert parsed matrix rows as curated catalog entries.

    Dry-run unless `apply=True`: the summary reports exactly what WOULD change so
    the vocabulary decision can be reviewed before anything is written.
    """
    existing = {
        r["cap_key"]: r
        for r in await conn.fetch(
            "SELECT cap_key, name, definition, status, created_via "
            "FROM capability_catalog WHERE project_id IS NOT DISTINCT FROM $1",
            project_id,
        )
    }
    to_add = [r for r in rows if r["cap_key"] not in existing]
    to_update = [
        r for r in rows
        if r["cap_key"] in existing
        and (existing[r["cap_key"]]["name"] or "") != r["name"]
    ]
    # Never touch rows that came from assessments — those are evidence, not vocabulary.
    protected = [k for k, v in existing.items()
                 if (v["status"] or "") == "observed" or (v["created_via"] or "") == "assessment"]
    to_update = [r for r in to_update if r["cap_key"] not in protected]

    summary = {
        "parsed": len(rows),
        "existing": len(existing),
        "would_add": len(to_add),
        "would_update_name": len(to_update),
        "protected_observed": len(protected),
        "applied": False,
        "sample": [r["cap_key"] for r in to_add[:10]],
    }
    if not apply:
        return summary

    for r in to_add:
        await conn.execute(
            """INSERT INTO capability_catalog
                 (project_id, cap_key, name, definition, spec_refs, depends_on,
                  status, created_by, updated_by, domain, domain_prefix, created_via)
               VALUES ($1,$2,$3,$4,$5,$6,'confirmed',$7,$7,$8,$9,'matrix-import')
               ON CONFLICT DO NOTHING""",
            project_id, r["cap_key"], r["name"], r["definition"],
            r["spec_refs"], r["depends_on"], actor, r["domain"], r["domain_prefix"],
        )
    for r in to_update:
        await conn.execute(
            """UPDATE capability_catalog
                  SET name=$3, definition=$4, depends_on=$5, updated_by=$6, updated_at=now()
                WHERE project_id IS NOT DISTINCT FROM $1 AND cap_key=$2
                  AND status <> 'observed' AND created_via <> 'assessment'""",
            project_id, r["cap_key"], r["name"], r["definition"], r["depends_on"], actor,
        )
    summary["applied"] = True
    log.info("capability matrix import: +%d added, %d names refreshed (project=%s)",
             len(to_add), len(to_update), project_id)
    return summary
