"""Tests for cross-UC capability demand density (DCM feature #2).

Run directly: `python test_capability_density.py` (from review-console/api), or via pytest.
"""

import sys

from app.capability_density import aggregate_density

_failures: list[str] = []


def eq(actual, expected, label):
    if actual != expected:
        _failures.append(f"{label}: got {actual!r}, expected {expected!r}")


def test_empty():
    eq(aggregate_density([], 0), [], "no rows -> empty")
    eq(aggregate_density([], 5), [], "no rows, nonzero total -> empty")


def test_counts_distinct_ucs():
    rows = [
        {"capability_id": "tenant_provisioning", "uc_uuid": "uc-1"},
        {"capability_id": "tenant_provisioning", "uc_uuid": "uc-2"},
        {"capability_id": "tenant_provisioning", "uc_uuid": "uc-2"},  # dup UC, counts once
        {"capability_id": "quota_enforcement", "uc_uuid": "uc-1"},
    ]
    out = aggregate_density(rows, total_ucs=3)
    eq(len(out), 2, "two distinct capabilities")
    top = out[0]
    eq(top["capability_id"], "tenant_provisioning", "most-demanded first")
    eq(top["uc_count"], 2, "distinct UC count (dup ignored)")
    eq(top["uc_uuids"], ["uc-1", "uc-2"], "uc_uuids sorted + deduped")
    eq(top["demand_ratio"], 2 / 3, "demand ratio = count/total")


def test_sorted_by_demand_then_id():
    rows = [
        {"capability_id": "b_cap", "uc_uuid": "uc-1"},
        {"capability_id": "a_cap", "uc_uuid": "uc-1"},
        {"capability_id": "a_cap", "uc_uuid": "uc-2"},
    ]
    out = aggregate_density(rows, total_ucs=2)
    eq([d["capability_id"] for d in out], ["a_cap", "b_cap"], "a_cap (2 UCs) before b_cap (1)")
    # equal counts tie-break alphabetically
    rows2 = [
        {"capability_id": "z_cap", "uc_uuid": "uc-1"},
        {"capability_id": "m_cap", "uc_uuid": "uc-1"},
    ]
    out2 = aggregate_density(rows2, total_ucs=1)
    eq([d["capability_id"] for d in out2], ["m_cap", "z_cap"], "tie broken by id")


def test_avg_confidence_and_namespaces():
    rows = [
        {"capability_id": "c1", "uc_uuid": "uc-1", "confidence_score": 80, "namespace": "dcm"},
        {"capability_id": "c1", "uc_uuid": "uc-2", "confidence_score": 60, "namespace": "koku"},
        {"capability_id": "c1", "uc_uuid": "uc-3", "confidence_score": None, "namespace": None},
    ]
    out = aggregate_density(rows, total_ucs=3)
    eq(out[0]["avg_confidence"], 70.0, "avg over present scores only")
    eq(out[0]["namespaces"], ["dcm", "koku"], "namespaces collected + sorted")
    eq(out[0]["uc_count"], 3, "uc count includes the no-confidence UC")


def test_zero_total_no_divide_error():
    rows = [{"capability_id": "c1", "uc_uuid": "uc-1"}]
    out = aggregate_density(rows, total_ucs=0)
    eq(out[0]["demand_ratio"], 0.0, "zero total -> 0.0 ratio, no ZeroDivisionError")


def test_skips_malformed_rows():
    rows = [
        {"capability_id": "c1", "uc_uuid": "uc-1"},
        {"capability_id": None, "uc_uuid": "uc-2"},   # no cap id
        {"uc_uuid": "uc-3"},                          # missing cap id
        {"capability_id": "c2"},                      # missing uc
    ]
    out = aggregate_density(rows, total_ucs=3)
    eq([d["capability_id"] for d in out], ["c1"], "malformed rows dropped")


def main():
    tests = [
        test_empty,
        test_counts_distinct_ucs,
        test_sorted_by_demand_then_id,
        test_avg_confidence_and_namespaces,
        test_zero_total_no_divide_error,
        test_skips_malformed_rows,
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
