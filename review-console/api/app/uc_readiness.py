"""UC readiness scorer — the author-facing quality signal (DCM feature #4).

DAV's analysis quality is bounded by the quality of the UC *definition*: a vague
or incomplete UC produces a vague analysis. `shallowness.py` flags thin
*analyses* after a run; this scores the *definition* before one, so an author
gets feedback while writing — and so a team can standardize how UCs are written
(Kevin Cattell's ask, 2026-06-02).

Deterministic and pure (no LLM, no I/O) so it runs instantly at edit/save time
and is unit-testable. It scores a parsed UC dict against a weighted checklist —
clear scenario, explicit intent, testable success criteria, complete dimensions,
focused grounding, single unit of work, curation metadata — and returns a 0-100
score, a band, and per-check feedback with actionable hints. **Advisory** — it
guides authors; it never blocks save (that's `_validate_uc_yaml`'s job).
"""

from __future__ import annotations

from typing import Any

# Per-check weights; sum to 100. Tuned so the things that most affect analysis
# quality (a testable, single-scope scenario) dominate.
_WEIGHTS = {
    "clear_description":  18,
    "explicit_intent":    12,
    "testable_criteria":  22,
    "complete_dimensions": 14,
    "focused_grounding":  12,
    "single_unit":        10,
    "curated":            12,
}

_MIN_DESC_LEN = 40        # a real one-sentence scenario, not a stub
_MAX_DESC_LEN = 220       # spec 05 wants ≤200; well over hints at bundled scope
_MIN_INTENT_LEN = 12
_MIN_CRITERION_LEN = 10   # a testable criterion, not "works"

_DIMENSION_FIELDS = (
    "lifecycle_phase", "resource_complexity", "policy_complexity",
    "provider_landscape", "governance_context", "failure_mode",
)


def _s(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def _check(cid: str, ok: bool, hint: str, points: int | None = None) -> dict:
    w = _WEIGHTS[cid]
    return {
        "id": cid,
        "ok": ok,
        "weight": w,
        "points": (w if ok else 0) if points is None else points,
        "hint": hint,
    }


def score_use_case(parsed: dict) -> dict:
    """Score a parsed UC dict for definition readiness. Pure, no I/O.

    Returns {score, band, ready, passed, total, checks: [...]} where each check is
    {id, ok, weight, points, hint}. `ready` is advisory (score >= 70).
    """
    if not isinstance(parsed, dict):
        parsed = {}
    scenario = parsed.get("scenario") if isinstance(parsed.get("scenario"), dict) else {}
    actor = scenario.get("actor") if isinstance(scenario.get("actor"), dict) else {}
    dims = scenario.get("dimensions") if isinstance(scenario.get("dimensions"), dict) else {}

    checks: list[dict] = []

    # 1. Clear scenario description — substantive, single sentence (not a stub).
    desc = _s(scenario.get("description"))
    checks.append(_check(
        "clear_description", _MIN_DESC_LEN <= len(desc) <= _MAX_DESC_LEN,
        f"Write a clear one-sentence scenario description ({_MIN_DESC_LEN}-{_MAX_DESC_LEN} chars).",
    ))

    # 2. Explicit intent — what the actor is trying to achieve.
    checks.append(_check(
        "explicit_intent", len(_s(scenario.get("intent"))) >= _MIN_INTENT_LEN,
        "State the intent — what the actor is trying to achieve.",
    ))

    # 3. Testable success criteria — partial credit for one, full for two+ specific.
    crit = scenario.get("success_criteria")
    crit = crit if isinstance(crit, list) else []
    substantive = [c for c in crit if len(_s(c if isinstance(c, str) else c.get("description") if isinstance(c, dict) else "")) >= _MIN_CRITERION_LEN]
    w3 = _WEIGHTS["testable_criteria"]
    if len(substantive) >= 2:
        pts, ok3 = w3, True
    elif len(substantive) == 1:
        pts, ok3 = w3 // 2, False
    else:
        pts, ok3 = 0, False
    checks.append(_check(
        "testable_criteria", ok3,
        "Add at least two specific, testable success criteria.",
        points=pts,
    ))

    # 4. Complete dimensions + target profile — the analysis needs the full context.
    dims_ok = all(_s(dims.get(f)) for f in _DIMENSION_FIELDS)
    profile_ok = bool(_s(actor.get("profile")) and _s(scenario.get("profile")) and _s(actor.get("persona")))
    checks.append(_check(
        "complete_dimensions", dims_ok and profile_ok,
        "Fill in all six scenario dimensions plus the actor persona/profile and scenario profile.",
    ))

    # 5. Focused grounding — tell the analysis where to look.
    has_ns = bool(parsed.get("spec_namespaces"))
    has_scope = bool(parsed.get("scope"))
    has_interactions = bool(scenario.get("expected_domain_interactions"))
    checks.append(_check(
        "focused_grounding", has_ns or has_scope or has_interactions,
        "Set spec_namespaces or scope (or list expected_domain_interactions) to focus grounding.",
    ))

    # 6. Single unit of work — a bundled, multi-step scenario analyzes poorly.
    # Requires a description to assess: an empty scenario isn't a "single unit".
    low = desc.lower()
    bundled = (len(desc) > _MAX_DESC_LEN) or (" and then " in low) or (desc.count(";") >= 2)
    checks.append(_check(
        "single_unit", bool(desc) and not bundled,
        "Scope to a single unit of work — split compound 'and then…' scenarios into separate UCs.",
    ))

    # 7. Curated — metadata that helps teams standardize and triage.
    curated = bool(parsed.get("tags")) or bool(parsed.get("priority")) or bool(_s(parsed.get("title")))
    checks.append(_check(
        "curated", curated,
        "Add a title, tags, or a priority so the UC is discoverable and rankable.",
    ))

    score = sum(c["points"] for c in checks)
    passed = sum(1 for c in checks if c["ok"])
    return {
        "score": score,
        "band": band_for(score),
        "ready": score >= 70,
        "passed": passed,
        "total": len(checks),
        "checks": checks,
    }


def band_for(score: int) -> str:
    if score >= 85:
        return "strong"
    if score >= 70:
        return "good"
    if score >= 50:
        return "fair"
    return "needs_work"
