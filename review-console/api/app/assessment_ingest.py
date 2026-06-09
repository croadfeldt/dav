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

_STATES = {"present", "partial", "absent"}
_PILLARS = {"platform", "people-process", "enablement"}


# ── Parsers ──────────────────────────────────────────────────────────────────
def _norm_state(v) -> str:
    s = str(v or "").strip().lower()
    aliases = {
        "yes": "present", "full": "present", "complete": "present", "mature": "present",
        "partial": "partial", "in-progress": "partial", "in progress": "partial",
        "emerging": "partial", "developing": "partial",
        "no": "absent", "none": "absent", "missing": "absent", "gap": "absent",
    }
    return s if s in _STATES else aliases.get(s, "absent")


def parse_generic(payload: dict) -> dict:
    """Canonical neutral format — the format DAV speaks regardless of source tool.

        {handle, assessment_type, pillar, source, summary,
         findings: [{capability, state, maturity?, evidence?, notes?, domain_prefix?, pillar?}]}
    """
    findings = []
    for f in payload.get("findings") or []:
        cap = str(f.get("capability") or "").strip()
        if not cap:
            continue
        findings.append({
            "capability": cap,
            "state": _norm_state(f.get("state")),
            "maturity": f.get("maturity"),
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
            "capability": cap, "state": state,
            "maturity": r.get("maturity") or r.get("maturity_score"),
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
    by_state = {"present": 0, "partial": 0, "absent": 0}
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
                (assessment_id, capability_handle, state, maturity, evidence, notes,
                 pillar, family, domain_prefix, normalized_to_term_id,
                 catalog_capability_id, normalization_status, classification)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            """,
            assessment_id, f["capability"], f["state"], f["maturity"], f["evidence"],
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
    by_state = {"present": 0, "partial": 0, "absent": 0}
    by_norm = {"normalized": 0, "proposed-taxonomy-gap": 0, "unmapped": 0}
    for r in rows:
        by_state[r["state"]] = by_state.get(r["state"], 0) + r["n"]
        by_norm[r["normalization_status"]] = by_norm.get(r["normalization_status"], 0) + r["n"]
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
            "gaps": [dict(r) for r in gaps]}


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
        """SELECT f.capability_handle, f.state, f.maturity, f.evidence, f.notes,
                  f.domain_prefix, f.normalization_status, t.handle AS normalized_to
           FROM assessment_findings f
           LEFT JOIN capability_taxonomy_terms t ON t.id = f.normalized_to_term_id
           WHERE f.assessment_id=$1::uuid
           ORDER BY CASE f.state WHEN 'absent' THEN 0 WHEN 'partial' THEN 1 ELSE 2 END,
                    lower(f.capability_handle)""",
        str(assessment_id),
    )
    out = dict(a)
    out["findings"] = [dict(r) for r in findings]
    out["gap_summary"] = await gap_summary(conn, assessment_id)
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
                   "Mixes DCM-taxonomy hits (normalized) with clear gaps (back-fill).",
        "findings": [
            # Taxonomy hits — normalize onto canonical DCM terms.
            {"capability": "Policy Engine", "state": "present", "maturity": 4,
             "evidence": "Central policy evaluation in place across environments."},
            {"capability": "Component Identity", "state": "partial", "maturity": 2,
             "evidence": "Workload identity issued; rotation manual.",
             "notes": "Target: automated short-lived credentials."},
            {"capability": "Service Provider", "state": "present", "maturity": 3,
             "evidence": "Provisioning providers registered for compute + network."},
            {"capability": "Request Orchestrator", "state": "partial", "maturity": 2,
             "evidence": "Event bus exists; not all flows routed through it."},
            # Gaps — no canonical term yet (back-fill candidates) and not implemented.
            {"capability": "Self-service developer portal", "state": "absent",
             "maturity": 0, "notes": "No internal platform/portal — clear gap."},
            {"capability": "Policy as Code", "state": "absent", "maturity": 1,
             "evidence": "Policies documented but not enforced in pipelines."},
        ],
    }
