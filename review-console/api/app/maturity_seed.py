"""Seed the default platform maturity framework (the one seeded template; #147 slice 1b).

Idempotent (ON CONFLICT DO NOTHING on natural keys), runs after schema in lifespan, wrapped
in try/except by the caller so a seed surprise can never crash boot. The framework is a GLOBAL
seed template (project_id NULL); projects copy + edit it (the model is fully configurable —
this is just a useful starting point, not a hard-coded taxonomy). The category/capability
structure is a generic platform-maturity wall (Function Appraisal 0–5). See maturity-wall-design.md.
"""
import json
import logging

log = logging.getLogger("dav-review-api")

FRAMEWORK_KEY = "platform-maturity-v1"
_LEGACY_KEY = "flightpath-v1"   # rename the previously-seeded template in place (drop the branding)

# 0..5 Function Appraisal scale (+ '-' Not Assessed = NULL maturity), heat-mapped low→high.
SCALE = [
    {"value": 0, "label": "Manual", "color": "#c0392b"},
    {"value": 1, "label": "Provisional", "color": "#e67e22"},
    {"value": 2, "label": "Operational", "color": "#e0a800"},
    {"value": 3, "label": "Optimal", "color": "#9acd32"},
    {"value": 4, "label": "Scalable", "color": "#4caf50"},
    {"value": 5, "label": "Highly Optimized", "color": "#1b7a3d"},
]

# states rendered as wall columns
STATES = [
    ("current", "Current State", "current"),
    ("phase-1", "Phase 1", "target"),
    ("phase-2", "Phase 2", "target"),
    ("phase-3", "Phase 3", "target"),
    ("desired", "Customer Desired", "desired"),
]

# band → [(category label, inflection_side, [capabilities])]
BANDS = [
    ("Automation as a Product", [
        ("Foundational Automation", "pre", [
            "Security Services Provisioning", "Network Provisioning", "Storage Provisioning",
            "Compute Provisioning", "Configuration Management"]),
        ("Infrastructure Automation", "pre", [
            "Platform Workflow", "Runbook Automation", "Provisioning Management",
            "Version Control", "Automation Workflow"]),
    ]),
    ("Platform Operating Model", [
        ("Container Development", "pre", [
            "Prod like Sandbox", "Container Landing Zones", "Dev Workstations",
            "Single Containers", "IDP"]),
        ("Container Platform Essentials", "pre", [
            "CI Framework", "Observability", "Declarative Infrastructure", "Platform Security",
            "Secrets Management", "Storage/Network integration", "Workload Analysis"]),
        ("Workload Intake Acceleration", "post", [
            "Sidecar integration", "Operator Onboarding", "Build Pipelines",
            "Deployment Integration", "Image Ingestion"]),
        ("Platform Services", "post", [
            "SRE Patterns", "Financial Management", "Resiliency Validation",
            "Disaster Recovery / Active Failover", "Fleet Management"]),
    ]),
    ("Strategy", [
        ("Platform Strategy", "post", [
            "Platform Focus", "Offerings", "People", "Governance", "Metrics", "Marketing"]),
    ]),
]


def _slug(s: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in s.lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


async def seed_default_framework(conn) -> None:
    """Idempotently ensure the default platform-maturity seed framework + its
    categories/capabilities/states exist. Safe to call every boot; inserts nothing on a second run.
    Renames the legacy flightpath-v1 seed in place (drops the branding) if present."""
    # Drop the old branding: rename the previously-seeded template, preserving its children.
    await conn.execute(
        """UPDATE assessment_frameworks SET key=$1, name=$2
           WHERE key=$3 AND project_id IS NULL
             AND NOT EXISTS (SELECT 1 FROM assessment_frameworks WHERE key=$1 AND project_id IS NULL)""",
        FRAMEWORK_KEY, "Platform Maturity Model", _LEGACY_KEY)
    fid = await conn.fetchval(
        "SELECT id FROM assessment_frameworks WHERE key=$1 AND project_id IS NULL", FRAMEWORK_KEY)
    if fid is None:
        fid = await conn.fetchval(
            """INSERT INTO assessment_frameworks (project_id, key, name, version, status, is_seed, scale)
               VALUES (NULL, $1, $2, 1, 'active', true, $3::jsonb)
               ON CONFLICT (key) WHERE project_id IS NULL DO NOTHING
               RETURNING id""",
            FRAMEWORK_KEY, "Platform Maturity Model", json.dumps(SCALE))
        if fid is None:  # lost a race; re-read
            fid = await conn.fetchval(
                "SELECT id FROM assessment_frameworks WHERE key=$1 AND project_id IS NULL", FRAMEWORK_KEY)

    for ord_s, (skey, slabel, kind) in enumerate(STATES):
        await conn.execute(
            """INSERT INTO framework_states (framework_id, key, label, ord, kind)
               VALUES ($1,$2,$3,$4,$5) ON CONFLICT (framework_id, key) DO NOTHING""",
            fid, skey, slabel, ord_s, kind)

    cat_ord = 0
    for band, cats in BANDS:
        for clabel, side, caps in cats:
            ckey = _slug(clabel)
            cid = await conn.fetchval(
                """INSERT INTO framework_categories (framework_id, key, label, band, ord, inflection_side)
                   VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (framework_id, key) DO NOTHING
                   RETURNING id""",
                fid, ckey, clabel, band, cat_ord, side)
            if cid is None:
                cid = await conn.fetchval(
                    "SELECT id FROM framework_categories WHERE framework_id=$1 AND key=$2", fid, ckey)
            cat_ord += 1
            for cap_ord, cap in enumerate(caps):
                await conn.execute(
                    """INSERT INTO framework_capabilities (category_id, key, label, ord)
                       VALUES ($1,$2,$3,$4) ON CONFLICT (category_id, key) DO NOTHING""",
                    cid, _slug(cap), cap, cap_ord)

    n_cat = await conn.fetchval("SELECT count(*) FROM framework_categories WHERE framework_id=$1", fid)
    n_cap = await conn.fetchval(
        """SELECT count(*) FROM framework_capabilities fc
           JOIN framework_categories c ON c.id=fc.category_id WHERE c.framework_id=$1""", fid)
    log.info("seeded maturity framework %s: %s categories, %s capabilities", FRAMEWORK_KEY, n_cat, n_cap)
