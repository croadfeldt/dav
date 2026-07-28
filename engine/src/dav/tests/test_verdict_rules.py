"""
Tests for rule-based verdict derivation (ADR-010).

Run:  python -m dav.tests.test_verdict_rules
Or:   pytest engine/src/dav/tests/test_verdict_rules.py
"""

from __future__ import annotations

import sys

from dav.core.use_case_schema import (
    GapIdentified, Verdict, normalize_severity, normalize_confidence,
)
from dav.core.verdict_rules import derive_verdict

_failures: list[str] = []


def assert_eq(actual, expected, label: str) -> None:
    if actual != expected:
        _failures.append(f"{label}: got {actual!r}, expected {expected!r}")
        raise AssertionError(f"{label}: got {actual!r}, expected {expected!r}")


def assert_true(cond: bool, label: str) -> None:
    if not cond:
        _failures.append(f"{label}: expected truthy")
        raise AssertionError(f"{label}: expected truthy")


def _gap(sev: str) -> GapIdentified:
    return GapIdentified(
        description="d", severity=normalize_severity(sev),
        confidence=normalize_confidence("high"), rationale="r",
        recommendation="rec", spec_refs_consulted=[],
    )


SUP = Verdict.SUPPORTED.value
PARTIAL = Verdict.PARTIALLY_SUPPORTED.value
NOT = Verdict.NOT_SUPPORTED.value


def test_supported_with_major_gap_downgrades():
    """GATE-001: supported + a major gap → partially_supported, one rule fired."""
    v, applied = derive_verdict(SUP, [_gap("minor"), _gap("major")])
    assert_eq(v, PARTIAL, "downgraded to partial")
    assert_eq(len(applied), 1, "one rule fired")
    assert_eq(applied[0]["rule"], "GATE-001", "rule id")
    assert_eq(applied[0]["from"], SUP, "from")
    assert_eq(applied[0]["to"], PARTIAL, "to")


def test_supported_with_critical_gap_downgrades():
    v, applied = derive_verdict(SUP, [_gap("critical")])
    assert_eq(v, PARTIAL, "critical also downgrades")
    assert_eq(len(applied), 1, "one rule fired")


def test_supported_with_only_minor_gaps_unchanged():
    """A supported verdict with only minor/moderate/advisory gaps stands."""
    v, applied = derive_verdict(SUP, [_gap("minor"), _gap("moderate"), _gap("advisory")])
    assert_eq(v, SUP, "stays supported")
    assert_eq(applied, [], "no rule fired")


def test_supported_with_no_gaps_unchanged():
    v, applied = derive_verdict(SUP, [])
    assert_eq(v, SUP, "no gaps, stays supported")
    assert_eq(applied, [], "no rule fired")


def test_already_partial_not_touched_by_gate_001():
    """GATE-001 only targets a `supported` assertion — a model that already said
    partial/not_supported is trusted (downgrade-only, asserted is the ceiling)."""
    v, applied = derive_verdict(PARTIAL, [_gap("critical")])
    assert_eq(v, PARTIAL, "partial with critical gap is left as asserted")
    assert_eq(applied, [], "no rule fired on an already-conservative verdict")

    v2, applied2 = derive_verdict(NOT, [_gap("critical")])
    assert_eq(v2, NOT, "not_supported left as asserted")
    assert_eq(applied2, [], "no rule fired")


def test_severity_accepts_bare_string_gaps():
    """derive_verdict tolerates gaps whose severity is a bare string (older files)."""
    class _Bare:
        severity = "major"
    v, applied = derive_verdict(SUP, [_Bare()])
    assert_eq(v, PARTIAL, "bare-string severity still gates")
    assert_eq(len(applied), 1, "one rule fired")


def test_derivation_is_downgrade_only():
    """The support rank is monotonic non-increasing across applied steps."""
    rank = {NOT: 0, PARTIAL: 1, SUP: 2}
    v, applied = derive_verdict(SUP, [_gap("critical")])
    prev = rank[SUP]
    for step in applied:
        assert_true(rank[step["to"]] < prev, "each step strictly downgrades")
        prev = rank[step["to"]]
    assert_true(rank[v] <= rank[SUP], "derived never exceeds asserted")


def main():
    tests = [
        test_supported_with_major_gap_downgrades,
        test_supported_with_critical_gap_downgrades,
        test_supported_with_only_minor_gaps_unchanged,
        test_supported_with_no_gaps_unchanged,
        test_already_partial_not_touched_by_gate_001,
        test_severity_accepts_bare_string_gaps,
        test_derivation_is_downgrade_only,
    ]
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            _failures.append(f"{t.__name__} threw: {type(e).__name__}: {e}")
    if _failures:
        print(f"FAIL: {len(_failures)} assertion(s)/error(s):")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"OK: {len(tests)} tests passed")


if __name__ == "__main__":
    main()
