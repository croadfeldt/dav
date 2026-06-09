"""Capability catalog ↔ taxonomy operations (the keystone).

Realizes the UDLM Knowledge family (migrate_017): the catalog is the independent
living inventory, the taxonomy is the normalization authority, and the catalog
back-fills the taxonomy where gaps exist. See docs/capability-catalog-design.md
and udlm/entities/knowledge-family.md.

This module:
  • seeds the canonical DCM vocabulary into capability_taxonomy_terms /
    capability_aliases (idempotent, family='dcm', scope_tier='global',
    lifecycle_state='CANONICAL') from the vendored taxonomy snapshot;
  • normalizes a free-form capability string onto a canonical term (exact match,
    then alias) or flags a taxonomy gap (the back-fill signal);
  • resolves the existing free-form `uc_capabilities` strings into catalog
    entries (OBSERVED, source uc-analysis) so cross-UC aggregation stops
    miscounting synonyms.

All DB work uses asyncpg connections from the app pool. No confidential data —
the seed is the public DCM taxonomy.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from . import capability_taxonomy

log = logging.getLogger("dav.capability_catalog")

# Vendored snapshot; override with DCM_TAXONOMY_PATH for a live source.
_SEED_PATH = Path(__file__).parent / "seed" / "DCM-Taxonomy.md"


def _taxonomy_text() -> Optional[str]:
    import os
    p = Path(os.environ.get("DCM_TAXONOMY_PATH", _SEED_PATH))
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        log.warning("capability taxonomy seed not found at %s — skipping seed", p)
        return None


async def seed_dcm_taxonomy(conn) -> dict:
    """Idempotently seed the canonical DCM vocabulary. Safe to run every startup.

    Inserts each domain + term as a global/canonical TaxonomyTerm and each
    anti-vocabulary entry as a global/canonical Alias, all tagged family='dcm'.
    Existing canonical rows (matched on family+pillar+global+lower(handle)) are
    left untouched, so the back-fill loop's later edits are never clobbered.
    """
    text = _taxonomy_text()
    if not text:
        return {"seeded": False}
    parsed = capability_taxonomy.parse_taxonomy(text)
    terms_in = 0

    # Domains (Part 4) become terms with a distinct category so the domain
    # vocabulary is queryable; domain_prefix is set for join-back.
    for d in parsed["domains"]:
        terms_in += await _seed_term(
            conn, handle=d["prefix"], definition=d["domain"],
            domain_prefix=d["prefix"], domain=d["domain"],
            category="Capability Domain Prefixes",
        )
    for t in parsed["terms"]:
        terms_in += await _seed_term(
            conn, handle=t["term"], definition=t["definition"],
            category=t.get("category"),
        )

    aliases_in = 0
    for a in parsed["anti_aliases"]:
        aliases_in += await _seed_alias(
            conn, avoid=a["avoid"], use_instead=a["use_instead"], reason=a["reason"],
        )

    log.info("DCM taxonomy seed: +%d terms, +%d aliases (idempotent)", terms_in, aliases_in)
    return {"seeded": True, "terms_added": terms_in, "aliases_added": aliases_in}


async def _seed_term(conn, handle, definition="", domain_prefix=None, domain=None,
                     category=None, pillar="platform") -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO capability_taxonomy_terms
            (handle, family, pillar, scope_tier, lifecycle_state, definition,
             domain_prefix, domain, category, created_via)
        SELECT $1, 'dcm', $2, 'global', 'CANONICAL', $3, $4, $5, $6, 'taxonomy-seed:dcm'
        WHERE NOT EXISTS (
            SELECT 1 FROM capability_taxonomy_terms
            WHERE family='dcm' AND pillar=$2 AND scope_tier='global' AND project_id IS NULL
              AND lower(handle)=lower($1) AND lifecycle_state='CANONICAL' AND is_current
        )
        RETURNING id
        """,
        handle, pillar, definition or "", domain_prefix, domain, category,
    )
    return 1 if row else 0


async def _seed_alias(conn, avoid, use_instead="", reason="", pillar="platform") -> int:
    # Best-effort link to a canonical term whose handle the alias points at.
    term_id = await conn.fetchval(
        """SELECT id FROM capability_taxonomy_terms
           WHERE family='dcm' AND scope_tier='global' AND lifecycle_state='CANONICAL'
             AND is_current AND lower(handle)=lower($1) LIMIT 1""",
        use_instead,
    )
    row = await conn.fetchrow(
        """
        INSERT INTO capability_aliases
            (handle, family, pillar, scope_tier, lifecycle_state, use_instead,
             resolves_to_term_id, reason, created_via)
        SELECT $1, 'dcm', $2, 'global', 'CANONICAL', $3, $4, $5, 'taxonomy-antivocab'
        WHERE NOT EXISTS (
            SELECT 1 FROM capability_aliases
            WHERE family='dcm' AND pillar=$2 AND scope_tier='global' AND project_id IS NULL
              AND lower(handle)=lower($1) AND lifecycle_state='CANONICAL' AND is_current
        )
        RETURNING id
        """,
        avoid, pillar, use_instead or "", term_id, reason or "",
    )
    return 1 if row else 0


async def normalize(conn, name: str, family: str = "dcm", pillar: str = "platform") -> dict:
    """Resolve a free-form capability string onto a canonical taxonomy term.

    Returns {term_id, status} where status is 'normalized' (exact term or alias
    hit) or 'proposed-taxonomy-gap' (no match → a back-fill candidate).
    """
    name = (name or "").strip()
    if not name:
        return {"term_id": None, "status": "unmapped"}
    tid = await conn.fetchval(
        """SELECT id FROM capability_taxonomy_terms
           WHERE family=$1 AND lower(handle)=lower($2)
             AND lifecycle_state='CANONICAL' AND is_current LIMIT 1""",
        family, name,
    )
    if tid:
        return {"term_id": tid, "status": "normalized"}
    tid = await conn.fetchval(
        """SELECT resolves_to_term_id FROM capability_aliases
           WHERE family=$1 AND lower(handle)=lower($2) AND is_current
             AND resolves_to_term_id IS NOT NULL LIMIT 1""",
        family, name,
    )
    if tid:
        return {"term_id": tid, "status": "normalized"}
    return {"term_id": None, "status": "proposed-taxonomy-gap"}


async def resolve_uc_capabilities(conn, family: str = "dcm") -> dict:
    """Project the distinct free-form `uc_capabilities.capability_id` strings into
    the catalog as OBSERVED Capability entries (source uc-analysis), each
    normalized onto a taxonomy term or flagged as a gap. Idempotent on
    (family, global, lower(handle)). This is the synonym-miscount fix.
    """
    names = await conn.fetch(
        "SELECT DISTINCT capability_id FROM uc_capabilities WHERE coalesce(capability_id,'') <> ''"
    )
    created = mapped = gaps = 0
    for r in names:
        name = r["capability_id"]
        norm = await normalize(conn, name, family=family)
        if norm["term_id"]:
            mapped += 1
        else:
            gaps += 1
        row = await conn.fetchrow(
            """
            INSERT INTO capability_catalog
                (handle, family, pillar, scope_tier, lifecycle_state,
                 normalized_to_term_id, normalization_status, created_via)
            SELECT $1, $2, 'platform', 'global', 'OBSERVED', $3, $4, 'uc-analysis'
            WHERE NOT EXISTS (
                SELECT 1 FROM capability_catalog
                WHERE family=$2 AND scope_tier='global' AND project_id IS NULL
                  AND lower(handle)=lower($1) AND is_current
            )
            RETURNING id
            """,
            name, family, norm["term_id"], norm["status"],
        )
        if row:
            created += 1
    return {"distinct": len(names), "created": created, "mapped": mapped, "gaps": gaps}


async def stats(conn) -> dict:
    return {
        "taxonomy_terms": await conn.fetchval(
            "SELECT count(*) FROM capability_taxonomy_terms WHERE is_current"),
        "canonical_terms": await conn.fetchval(
            "SELECT count(*) FROM capability_taxonomy_terms WHERE is_current AND lifecycle_state='CANONICAL'"),
        "proposed_terms": await conn.fetchval(
            "SELECT count(*) FROM capability_taxonomy_terms WHERE is_current AND lifecycle_state='PROPOSED'"),
        "aliases": await conn.fetchval(
            "SELECT count(*) FROM capability_aliases WHERE is_current"),
        "antipatterns": await conn.fetchval(
            "SELECT count(*) FROM capability_antipatterns WHERE is_current"),
        "catalog": await conn.fetchval(
            "SELECT count(*) FROM capability_catalog WHERE is_current"),
        "catalog_gaps": await conn.fetchval(
            "SELECT count(*) FROM capability_catalog WHERE is_current AND normalization_status='proposed-taxonomy-gap'"),
    }
