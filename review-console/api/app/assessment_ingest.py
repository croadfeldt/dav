"""Assessment ingestion (F7) — UDLM Knowledge family · Assessment + Finding.

Consumes the OUTPUTS of an existing assessment process (automation strategy,
hybrid-cloud, AI, generalized DCM) and lands each finding on the unified
capability_catalog as an OBSERVED capability (status='observed'), normalized onto
the taxonomy or flagged as a back-fill gap. The gap between OBSERVED (assessed
reality) and CANONICAL (target vocabulary) is the analysis that drives the roadmap.

WORK/PERSONAL BOUNDARY — this is the GENERIC mechanism only. Real per-format
parsers and engagement data are confidential and live inside the work env; here we
ship a neutral canonical format, a generic automation adapter, and a SYNTHETIC
fixture (no confidential data). See active-work.md and capability-catalog-design.md.

A parser maps a raw assessment payload to a list of normalized findings:
    {capability, state, maturity?, evidence?, notes?, domain_prefix?, pillar?}
state ∈ present | partial | absent. New formats register a parser by type.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from . import capability_catalog as _catalog

log = logging.getLogger("dav.assessment_ingest")

# State carries the disposition: present/partial/absent = asked (capability presence);
# 'n/a' = not asked or not applicable. Maturity (1..5) is the separate, pure rating.
_STATES = {"present", "partial", "absent", "n/a"}
_PILLARS = {"platform", "people-process", "enablement"}

# Maturity scale (Chris 2026-06-10) — PURE maturity, 1..5; NULL = no maturity (the
# disposition lives in `state`: 'absent' = asked, no capability; 'n/a' = not asked / not
# applicable). 3 ("Capable") is the engagement target — it satisfies the technical
# requirements. 4/5 are more process than technical maturity (lower ROI, higher capability).
MATURITY_TARGET = 3
MATURITY_SCALE = [
    {"value": 1, "label": "Minimal", "desc": "Minimal / ad-hoc capability"},
    {"value": 2, "label": "Basic",   "desc": "Basic capability"},
    {"value": 3, "label": "Capable", "desc": "Satisfies the technical requirements — engagement target", "target": True},
    {"value": 4, "label": "Above",   "desc": "Above target; more process than technical maturity"},
    {"value": 5, "label": "Best",    "desc": "Best-in-class; process maturity beyond technical need"},
]


def _coerce_maturity(v):
    """1..5 integer, or None (no maturity — see `state` for the disposition)."""
    if v is None or (isinstance(v, str) and v.strip().lower() in ("", "n/a", "na", "none", "not asked", "not applicable")):
        return None
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    return i if 1 <= i <= 5 else None


# ── Parsers ──────────────────────────────────────────────────────────────────
def _norm_state(v) -> str:
    s = str(v or "").strip().lower()
    aliases = {
        "yes": "present", "full": "present", "complete": "present", "mature": "present",
        "partial": "partial", "in-progress": "partial", "in progress": "partial",
        "emerging": "partial", "developing": "partial",
        "no": "absent", "none": "absent", "missing": "absent", "gap": "absent",
        "na": "n/a", "n.a.": "n/a", "not asked": "n/a", "not applicable": "n/a",
        "not-applicable": "n/a", "skipped": "n/a",
    }
    return s if s in _STATES else aliases.get(s, "absent")


def parse_generic(payload: dict) -> dict:
    """Canonical neutral format — the format DAV speaks regardless of source tool.

        {handle, assessment_type, pillar, source, summary,
         findings: [{capability, category?, state, maturity?, evidence?, notes?, domain_prefix?, pillar?}]}
    """
    findings = []
    for f in payload.get("findings") or []:
        cap = str(f.get("capability") or "").strip()
        if not cap:
            continue
        findings.append({
            "capability": cap,
            "category": (str(f.get("category")).strip() or None) if f.get("category") else None,
            "state": _norm_state(f.get("state")),
            "maturity": _coerce_maturity(f.get("maturity")),
            "evidence": (f.get("evidence") or None),
            "notes": (f.get("notes") or None),
            "domain_prefix": (f.get("domain_prefix") or None),
            "pillar": f.get("pillar") if f.get("pillar") in _PILLARS else None,
        })
    return {
        "handle": str(payload.get("handle") or "Untitled assessment").strip(),
        "assessment_type": str(payload.get("assessment_type") or "generic").strip(),
        "pillar": payload.get("pillar") if payload.get("pillar") in _PILLARS else "platform",
        "source": (payload.get("source") or None),
        "summary": (payload.get("summary") or None),
        "findings": findings,
    }


def parse_automation(payload: dict) -> dict:
    """Generic automation-assessment adapter. Accepts the automation rows shape
        {capability|control, current_state|maturity_now, target_state?, evidence?, notes?}
    and projects it onto the canonical format. The automation strategy has the most
    data/usage; the real client format stays inside the work env — this adapter is
    intentionally shape-tolerant so the inside-work parser can subclass it.
    """
    rows = payload.get("findings") or payload.get("rows") or payload.get("capabilities") or []
    norm = []
    for r in rows:
        cap = r.get("capability") or r.get("control") or r.get("name")
        if not cap:
            continue
        state = r.get("state") or r.get("current_state") or r.get("maturity_now")
        norm.append({
            "capability": cap, "category": r.get("category") or r.get("domain") or r.get("group"),
            "state": state, "maturity": r.get("maturity") or r.get("maturity_score"),
            "evidence": r.get("evidence"), "notes": r.get("notes") or r.get("target_state"),
            "domain_prefix": r.get("domain_prefix"), "pillar": r.get("pillar"),
        })
    base = dict(payload)
    base["findings"] = norm
    base.setdefault("assessment_type", "automation")
    base.setdefault("pillar", "platform")
    return parse_generic(base)


PARSERS: dict[str, Callable[[dict], dict]] = {
    "generic": parse_generic,
    "automation": parse_automation,
    # hybrid-cloud / ai / dcm register their inside-work parsers here; absent ones
    # fall back to the canonical generic parser.
}


def parse(payload: dict) -> dict:
    atype = str(payload.get("assessment_type") or "generic").strip().lower()
    return PARSERS.get(atype, parse_generic)(payload)


# ── Ingest ───────────────────────────────────────────────────────────────────
async def ingest(conn, payload: dict, *, actor: str = "", project_id=None,
                 family: str = "dcm", classification: str = "client-confidential") -> dict:
    """Parse a payload and persist it as an Assessment + Findings, landing each
    finding on capability_catalog (OBSERVED) via the shared write path. Returns a
    summary including the normalization/state breakdown (the gap signal)."""
    parsed = parse(payload)
    atype = parsed["assessment_type"]
    assessment_id = await conn.fetchval(
        """
        INSERT INTO assessments
            (handle, owned_by, created_by, created_via, assessment_type, pillar,
             source, summary, family, classification, project_id)
        VALUES ($1, $2, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        RETURNING id
        """,
        parsed["handle"], actor or None, f"import:{atype}", atype, parsed["pillar"],
        parsed["source"], parsed["summary"], family, classification, project_id,
    )

    mapped = gaps = 0
    by_state = {"present": 0, "partial": 0, "absent": 0, "n/a": 0}
    for f in parsed["findings"]:
        cap = await _catalog.upsert_observed_capability(
            conn, f["capability"], project_id=project_id, created_via="assessment",
            family=family, pillar=f["pillar"] or parsed["pillar"],
            domain_prefix=f["domain_prefix"], evidence=f["evidence"],
        )
        if cap["term_id"]:
            mapped += 1
        else:
            gaps += 1
        by_state[f["state"]] = by_state.get(f["state"], 0) + 1
        await conn.execute(
            """
            INSERT INTO assessment_findings
                (assessment_id, capability_handle, category, state, maturity, evidence, notes,
                 pillar, family, domain_prefix, normalized_to_term_id,
                 catalog_capability_id, normalization_status, classification)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            """,
            assessment_id, f["capability"], f["category"], f["state"], f["maturity"], f["evidence"],
            f["notes"], f["pillar"] or parsed["pillar"], family, f["domain_prefix"],
            cap["term_id"], cap["id"], cap["normalization_status"], classification,
        )

    return {
        "assessment_id": str(assessment_id), "handle": parsed["handle"],
        "assessment_type": atype, "findings": len(parsed["findings"]),
        "mapped": mapped, "gaps": gaps, "by_state": by_state,
    }


async def gap_summary(conn, assessment_id) -> dict:
    """The roadmap signal for one assessment: capabilities by state, by
    normalization, and the prioritized gap list (absent/partial first)."""
    rows = await conn.fetch(
        """SELECT state, normalization_status, count(*) AS n
           FROM assessment_findings WHERE assessment_id=$1::uuid
           GROUP BY state, normalization_status""",
        str(assessment_id),
    )
    by_state = {"present": 0, "partial": 0, "absent": 0, "n/a": 0}
    by_norm = {"normalized": 0, "proposed-taxonomy-gap": 0, "unmapped": 0}
    for r in rows:
        by_state[r["state"]] = by_state.get(r["state"], 0) + r["n"]
        by_norm[r["normalization_status"]] = by_norm.get(r["normalization_status"], 0) + r["n"]
    # Maturity rollup vs the engagement target (3). NULL maturity = N/A.
    mrows = await conn.fetch(
        """SELECT maturity, count(*) AS n FROM assessment_findings
           WHERE assessment_id=$1::uuid GROUP BY maturity""",
        str(assessment_id),
    )
    by_value = {str(i): 0 for i in range(6)}
    na = below = at_or_above = 0
    for r in mrows:
        m, n = r["maturity"], r["n"]
        if m is None:
            na += n
        else:
            by_value[str(m)] = by_value.get(str(m), 0) + n
            if m < MATURITY_TARGET:
                below += n
            else:
                at_or_above += n
    maturity = {"target": MATURITY_TARGET, "na": na, "below_target": below,
                "at_or_above_target": at_or_above, "by_value": by_value}
    gaps = await conn.fetch(
        """SELECT f.capability_handle, f.state, f.maturity, f.domain_prefix,
                  f.normalization_status, t.handle AS normalized_to
           FROM assessment_findings f
           LEFT JOIN capability_taxonomy_terms t ON t.id = f.normalized_to_term_id
           WHERE f.assessment_id=$1::uuid AND f.state IN ('absent','partial')
           ORDER BY CASE f.state WHEN 'absent' THEN 0 ELSE 1 END, lower(f.capability_handle)""",
        str(assessment_id),
    )
    return {"by_state": by_state, "by_normalization": by_norm,
            "maturity": maturity, "gaps": [dict(r) for r in gaps]}


async def list_assessments(conn, project_id=None) -> list:
    rows = await conn.fetch(
        """SELECT a.id::text AS id, a.handle, a.assessment_type, a.pillar, a.source,
                  a.summary, a.created_by, a.created_at, a.project_id,
                  count(f.id) AS findings,
                  count(f.id) FILTER (WHERE f.state IN ('absent','partial')) AS gaps
           FROM assessments a
           LEFT JOIN assessment_findings f ON f.assessment_id = a.id
           WHERE ($1::bigint IS NULL OR a.project_id = $1)
           GROUP BY a.id
           ORDER BY a.created_at DESC""",
        project_id,
    )
    return [dict(r) for r in rows]


async def get_assessment(conn, assessment_id) -> Optional[dict]:
    a = await conn.fetchrow(
        """SELECT id::text AS id, handle, assessment_type, pillar, source, summary,
                  created_by, created_at, classification, project_id
           FROM assessments WHERE id=$1::uuid""",
        str(assessment_id),
    )
    if not a:
        return None
    findings = await conn.fetch(
        """SELECT f.capability_handle, f.category, f.state, f.maturity, f.evidence, f.notes,
                  f.domain_prefix, f.normalization_status, t.handle AS normalized_to
           FROM assessment_findings f
           LEFT JOIN capability_taxonomy_terms t ON t.id = f.normalized_to_term_id
           WHERE f.assessment_id=$1::uuid
           ORDER BY f.category NULLS LAST, lower(f.capability_handle)""",
        str(assessment_id),
    )
    out = dict(a)
    out["findings"] = [dict(r) for r in findings]
    out["gap_summary"] = await gap_summary(conn, assessment_id)
    out["maturity_scale"] = MATURITY_SCALE
    out["maturity_target"] = MATURITY_TARGET
    return out


# ── Synthetic fixture (NO confidential data) ─────────────────────────────────
def synthetic_fixture() -> dict:
    """A neutral, synthetic automation-strategy assessment for demo/validation.
    Capability names deliberately mix taxonomy hits, anti-vocabulary (to exercise
    alias normalization), and a clear gap (to exercise back-fill)."""
    return {
        "handle": "Synthetic Automation Strategy Assessment",
        "assessment_type": "automation",
        "pillar": "platform",
        "source": "synthetic://example",
        "summary": "Illustrative assessment — synthetic data only, no client information. "
                   "Capabilities grouped by category; maturity is pure 1–5 (3 = target), "
                   "state carries the disposition (absent / n-a).",
        "findings": [
            # Category: Policy & Governance
            {"capability": "Policy Engine", "category": "Policy & Governance",
             "state": "present", "maturity": 5,
             "evidence": "Best-in-class central policy evaluation + governance."},
            {"capability": "Policy as Code", "category": "Policy & Governance",
             "state": "n/a", "evidence": "Out of scope for this engagement — not applicable."},
            # Category: Identity
            {"capability": "Component Identity", "category": "Identity",
             "state": "present", "maturity": 3,
             "notes": "At the engagement target (3 = Capable)."},
            # Category: Provisioning & Orchestration
            {"capability": "Service Provider", "category": "Provisioning & Orchestration",
             "state": "present", "maturity": 4,
             "evidence": "Providers registered for compute + network; above target."},
            {"capability": "Request Orchestrator", "category": "Provisioning & Orchestration",
             "state": "partial", "maturity": 2,
             "evidence": "Event bus exists; not all flows routed through it."},
            # Category: Developer Experience (a clear gap — asked, no capability)
            {"capability": "Self-service developer portal", "category": "Developer Experience",
             "state": "absent", "notes": "No internal platform/portal — asked, no capability."},
        ],
    }
