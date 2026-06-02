"""Tests for foundational capability detection (DCM feature #3).

Run directly: `python test_capability_graph.py` (from review-console/api), or via pytest.
"""

import sys

from app.capability_graph import foundational_ranking

_failures: list[str] = []


def eq(actual, expected, label):
    if actual != expected:
        _failures.append(f"{label}: got {actual!r}, expected {expected!r}")


def _by_id(rows):
    return {r["capability_id"]: r for r in rows}


def test_linear_chain():
    # A depends_on B depends_on C  →  C is most foundational (A and B rest on it)
    rows = foundational_ranking([("A", "B"), ("B", "C")])
    m = _by_id(rows)
    eq(m["C"]["transitive_dependents"], 2, "C transitively supports A and B")
    eq(m["C"]["direct_dependents"], 1, "C directly supports only B")
    eq(m["B"]["transitive_dependents"], 1, "B supports A")
    eq(m["A"]["transitive_dependents"], 0, "A supports nothing")
    eq(rows[0]["capability_id"], "C", "most foundational first")


def test_diamond():
    # A->B, A->C, B->D, C->D  →  D supports A,B,C (3)
    rows = foundational_ranking([("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")])
    m = _by_id(rows)
    eq(m["D"]["transitive_dependents"], 3, "D supports A, B, C transitively")
    eq(m["D"]["direct_dependents"], 2, "D directly supports B and C")
    eq(rows[0]["capability_id"], "D", "D is most foundational")


def test_cycle_is_safe():
    # A<->B cycle must not loop forever; each supports the other once.
    rows = foundational_ranking([("A", "B"), ("B", "A")])
    m = _by_id(rows)
    eq(m["A"]["transitive_dependents"], 1, "A supported by B (self excluded)")
    eq(m["B"]["transitive_dependents"], 1, "B supported by A (self excluded)")


def test_self_loop_and_empty_ignored():
    rows = foundational_ranking([("A", "A"), ("", "B"), ("C", None), ("X", "Y")])
    ids = {r["capability_id"] for r in rows}
    eq(ids, {"X", "Y"}, "self-loop + empty/None edges dropped")


def test_pure_dependency_node_counts():
    # 'identity_model' is never a dependant — only a dependency target — yet it's
    # the foundational one. It must still appear as a node.
    rows = foundational_ranking([("tenant", "identity_model"), ("quota", "identity_model")])
    m = _by_id(rows)
    eq(m["identity_model"]["transitive_dependents"], 2, "pure-dependency node counts dependents")
    eq(m["identity_model"]["depends_on"], [], "it depends on nothing itself")


def test_leverage_highlights_underdemanded_foundation():
    # identity_model supports 2 capabilities but only 1 UC demands it directly →
    # high leverage. tenant is demanded by 9 UCs but supports nothing → leverage 0.
    edges = [("tenant", "identity_model"), ("quota", "identity_model")]
    demand = {"identity_model": 1, "tenant": 9, "quota": 4}
    m = _by_id(foundational_ranking(edges, demand))
    eq(m["identity_model"]["leverage"], 2.0, "2 dependents / 1 demand = 2.0")
    eq(m["tenant"]["leverage"], 0.0, "tenant supports nothing → 0 leverage")
    eq(m["identity_model"]["demand_uc_count"], 1, "demand passed through")


def test_leverage_none_without_demand():
    rows = foundational_ranking([("A", "B")])
    eq(_by_id(rows)["B"]["leverage"], None, "no demand → leverage None")
    eq(_by_id(rows)["B"]["demand_uc_count"], None, "no demand → count None")


def main():
    tests = [
        test_linear_chain,
        test_diamond,
        test_cycle_is_safe,
        test_self_loop_and_empty_ignored,
        test_pure_dependency_node_counts,
        test_leverage_highlights_underdemanded_foundation,
        test_leverage_none_without_demand,
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
