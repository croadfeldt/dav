"""Shallow-analysis detector unit tests — pure module, no pytest.

Run from review-console/api/:  python3 test_shallowness.py

Asserts the grounding-density scorer flags thin-but-successful analyses (few
distinct spec refs, mostly-ungrounded "generic label" claims, or a too-early
commit) and leaves well-grounded / not-eligible analyses alone — the per-UC
form of the 72B-eval shallowness signal.
"""
import sys

from app import shallowness as sh

_fails = 0


def check(cond, msg):
    global _fails
    mark = "ok  " if cond else "FAIL"
    if not cond:
        _fails += 1
    print(f"  [{mark}] {msg}")


def _component(id_, refs):
    return {"id": id_, "role": "r", "rationale": "x", "spec_refs": refs, "confidence": "high"}


def _gap(consulted):
    return {"severity": "minor", "description": "d", "rationale": "r",
            "spec_refs_consulted": consulted, "spec_refs_missing": "",
            "recommendation": "x", "confidence": "medium"}


def _analysis(components=None, gaps=None, tool_calls=8, verdict="supported"):
    return {
        "components_required": components or [],
        "data_model_touched": [],
        "capabilities_invoked": [],
        "policy_modes_required": [],
        "gaps_identified": gaps or [],
        "summary": {"verdict": verdict, "overall_confidence": "high", "notes": ""},
        "analysis_metadata": {"tool_call_count": tool_calls},
    }


def test_well_grounded_not_shallow():
    comps = [_component(f"C{i}", [f"RSE-00{i}"]) for i in range(1, 9)]  # 8 distinct refs
    a = _analysis(components=comps, gaps=[_gap(["OBS-001", "OBS-002"])], tool_calls=17)
    r = sh.score_and_flag(a)
    check(r["distinct_spec_refs"] >= 8, f"well-grounded distinct_spec_refs={r['distinct_spec_refs']}")
    check(r["eligible"] and not r["shallow"], f"well-grounded -> NOT shallow ({r['reasons']})")


def test_generic_labels_shallow():
    comps = [_component(f"G{i}", []) for i in range(4)]  # 4 ungrounded "generic labels"
    a = _analysis(components=comps, tool_calls=8)
    r = sh.score_and_flag(a)
    check(r["distinct_spec_refs"] == 0, "generic: 0 distinct spec refs")
    check(r["ungrounded_ratio"] == 1.0, "generic: ungrounded_ratio == 1.0")
    check(r["shallow"] and any("spec ref" in x for x in r["reasons"]),
          f"generic labels -> shallow ({r['reasons']})")


def test_few_tool_calls_shallow():
    comps = [_component(f"C{i}", [f"REQ-00{i}"]) for i in range(1, 5)]  # grounded...
    a = _analysis(components=comps, tool_calls=2)                       # ...but committed early
    r = sh.score_and_flag(a)
    check(r["shallow"] and any("tool call" in x for x in r["reasons"]),
          f"2 tool calls -> shallow ({r['reasons']})")


def test_not_supported_not_eligible():
    r = sh.score_and_flag(_analysis(components=[], gaps=[], verdict="not_supported"))
    check(not r["eligible"] and not r["shallow"], "not_supported/empty -> not eligible")


def test_aggregate_majority():
    rich = sh.score_and_flag(_analysis(
        components=[_component(f"C{i}", [f"X-00{i}"]) for i in range(1, 6)], tool_calls=12))
    thin = sh.score_and_flag(_analysis(components=[_component("G", [])], tool_calls=2))
    agg = sh.aggregate_samples([rich, rich, thin])      # 1/3 thin -> minority
    check(agg["eligible"] and not agg["shallow"], "explore agg: 1/3 thin -> not shallow")
    agg2 = sh.aggregate_samples([rich, thin, thin])     # 2/3 thin -> majority
    check(agg2["shallow"], "explore agg: 2/3 thin -> shallow")
    check(isinstance(agg["distinct_spec_refs"], (int, float)), "explore agg: mean computed")


def test_thresholds_configurable():
    comps = [_component(f"C{i}", [f"Z-00{i}"]) for i in range(1, 3)]    # 2 distinct refs
    a = _analysis(components=comps, tool_calls=8)
    check(sh.score_and_flag(a)["shallow"], "2 refs shallow at default (min=3)")
    loose = sh.ShallowThresholds(min_distinct_spec_refs=2)
    check(not sh.score_and_flag(a, loose)["shallow"], "2 refs ok at min=2")


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    print()
    if _fails:
        print(f"FAILED: {_fails} check(s)")
        sys.exit(1)
    print("all shallowness checks passed")


if __name__ == "__main__":
    main()
