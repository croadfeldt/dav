"""Tests for the UC readiness scorer (DCM feature #4).

Run directly: `python test_uc_readiness.py` (from review-console/api), or via pytest.
"""

import sys

from app import uc_readiness as r

_failures: list[str] = []


def eq(actual, expected, label):
    if actual != expected:
        _failures.append(f"{label}: got {actual!r}, expected {expected!r}")


def ok(cond, label):
    if not cond:
        _failures.append(f"{label}: expected truthy")


def _checks_by_id(res):
    return {c["id"]: c for c in res["checks"]}


def _strong_uc():
    return {
        "title": "Tenant decommission with retention",
        "tags": ["cost", "day2"],
        "priority": "high",
        "spec_namespaces": ["dcm"],
        "scenario": {
            "description": "A sovereign-profile tenant is decommissioned while retaining audit records for the mandated period.",
            "intent": "Decommission the tenant without violating data retention policy.",
            "success_criteria": [
                "All tenant compute resources are released and billing stops.",
                "Audit records remain queryable for the full retention window.",
            ],
            "profile": "sovereign",
            "actor": {"persona": "platform operator", "profile": "sovereign"},
            "dimensions": {
                "lifecycle_phase": "decommission", "resource_complexity": "composite_service",
                "policy_complexity": "multi_policy_chain", "provider_landscape": "multiple_eligible",
                "governance_context": "sovereignty_enforced", "failure_mode": "happy_path",
            },
        },
    }


def test_strong_uc_scores_high():
    res = r.score_use_case(_strong_uc())
    ok(res["score"] >= 85, f"strong UC should score >=85, got {res['score']}")
    eq(res["band"], "strong", "strong band")
    eq(res["ready"], True, "strong UC is ready")
    eq(res["passed"], res["total"], "all checks pass for a strong UC")


def test_empty_uc_scores_low():
    res = r.score_use_case({})
    ok(res["score"] < 50, f"empty UC should score <50, got {res['score']}")
    eq(res["band"], "needs_work", "empty band")
    eq(res["ready"], False, "empty UC not ready")
    eq(res["passed"], 0, "no checks pass")


def test_single_criterion_gets_partial_credit():
    uc = _strong_uc()
    uc["scenario"]["success_criteria"] = ["All tenant compute resources are released."]
    c = _checks_by_id(r.score_use_case(uc))["testable_criteria"]
    eq(c["ok"], False, "one criterion is not full pass")
    eq(c["points"], c["weight"] // 2, "one substantive criterion earns half credit")


def test_trivial_criteria_no_credit():
    uc = _strong_uc()
    uc["scenario"]["success_criteria"] = ["works", "ok"]
    c = _checks_by_id(r.score_use_case(uc))["testable_criteria"]
    eq(c["points"], 0, "too-short criteria earn nothing")


def test_bundled_scenario_flags_single_unit():
    uc = _strong_uc()
    uc["scenario"]["description"] = "The tenant is decommissioned and then re-provisioned and then migrated."
    c = _checks_by_id(r.score_use_case(uc))["single_unit"]
    eq(c["ok"], False, "'and then' chains flag a bundled scenario")


def test_stub_description_fails_clarity():
    uc = _strong_uc()
    uc["scenario"]["description"] = "decommission"
    c = _checks_by_id(r.score_use_case(uc))["clear_description"]
    eq(c["ok"], False, "stub description fails clarity")


def test_missing_grounding_fails():
    uc = _strong_uc()
    uc.pop("spec_namespaces")
    uc.pop("scope", None)
    uc["scenario"].pop("expected_domain_interactions", None)
    c = _checks_by_id(r.score_use_case(uc))["focused_grounding"]
    eq(c["ok"], False, "no namespaces/scope/interactions fails grounding")


def test_failing_checks_carry_hints():
    res = r.score_use_case({})
    for c in res["checks"]:
        ok(bool(c["hint"]), f"check {c['id']} should carry an actionable hint")


def test_band_thresholds():
    eq(r.band_for(90), "strong", "90 strong")
    eq(r.band_for(70), "good", "70 good")
    eq(r.band_for(50), "fair", "50 fair")
    eq(r.band_for(49), "needs_work", "49 needs_work")


def test_non_dict_input_safe():
    res = r.score_use_case(None)
    eq(res["score"], 0, "None input scores 0 without raising")


def main():
    tests = [
        test_strong_uc_scores_high,
        test_empty_uc_scores_low,
        test_single_criterion_gets_partial_credit,
        test_trivial_criteria_no_credit,
        test_bundled_scenario_flags_single_unit,
        test_stub_description_fails_clarity,
        test_missing_grounding_fails,
        test_failing_checks_carry_hints,
        test_band_thresholds,
        test_non_dict_input_safe,
    ]
    for t in tests:
        t()
    if _failures:
        print(f"FAIL: {len(_failures)} assertion(s) failed:")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"OK: {len(tests)} tests passed")


if __name__ == "__main__":
    main()
