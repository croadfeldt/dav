"""
Tests for explore-mode variance reporting.

Run:  python -m dav.tests.test_explore
"""

from __future__ import annotations

import sys

from dav.core.use_case_schema import (
    Analysis, AnalysisMetadata, AnalysisSummary,
    ComponentRequired, CapabilityInvoked, GapIdentified,
    normalize_severity, normalize_confidence,
)
from dav.core.explore import (
    VarianceReport, build_variance_report, UNSTABLE_THRESHOLD,
)

_failures: list[str] = []

def assert_eq(actual, expected, label: str) -> None:
    if actual != expected:
        _failures.append(f"{label}: got {actual!r}, expected {expected!r}")

def assert_true(cond: bool, label: str) -> None:
    if not cond:
        _failures.append(f"{label}: expected truthy")

def assert_raises(fn, exc, label: str) -> None:
    try:
        fn()
    except exc:
        return
    except Exception as e:
        _failures.append(f"{label}: expected {exc.__name__}, got {type(e).__name__}: {e}")
        return
    _failures.append(f"{label}: expected {exc.__name__}, no exception raised")

# --- Builders ---

def _comp(id_: str, conf: str = "high") -> ComponentRequired:
    return ComponentRequired(
        id=id_, role="x", rationale="y", spec_refs=[],
        confidence=normalize_confidence(conf),
    )

def _gap(desc: str, sev: str = "major", conf: str = "medium") -> GapIdentified:
    return GapIdentified(
        description=desc,
        severity=normalize_severity(sev),
        confidence=normalize_confidence(conf),
        rationale="r", recommendation="rec",
        spec_refs_consulted=[], spec_refs_missing=None,
    )

def _analysis(
    verdict: str = "supported",
    conf: str = "high",
    comps: list = None,
    gaps: list = None,
    uuid: str = "uc-explore-001",
) -> Analysis:
    return Analysis(
        use_case_uuid=uuid,
        analysis_metadata=AnalysisMetadata(model="test"),
        summary=AnalysisSummary(verdict=verdict, overall_confidence=normalize_confidence(conf), notes=""),
        components_required=comps or [],
        gaps_identified=gaps or [],
    )

# --- Tests ---

def test_validation_empty():
    assert_raises(lambda: build_variance_report([]), ValueError, "empty samples")

def test_validation_uuid_mismatch():
    a = _analysis(uuid="uc-1")
    b = _analysis(uuid="uc-2")
    assert_raises(lambda: build_variance_report([a, b]), ValueError, "uuid mismatch")

def test_validation_seeds_mismatch():
    a = _analysis()
    assert_raises(
        lambda: build_variance_report([a], sample_seeds=[1, 2]),
        ValueError, "seeds length mismatch"
    )

def test_unanimous_verdict_high_stability():
    samples = [_analysis(verdict="supported"), _analysis(verdict="supported"),
               _analysis(verdict="supported")]
    r = build_variance_report(samples, sample_seeds=[1, 2, 3])
    assert_eq(r.sample_count, 3, "n")
    assert_eq(r.verdict_distribution, {"supported": 3}, "verdict dist")
    assert_eq(r.verdict_stability, 1.0, "stability 1.0")
    assert_eq(r.unstable_findings, [], "no unstable findings")

def test_split_verdict_low_stability():
    """5 samples, 3 supported, 2 partially_supported → stability 0.6."""
    samples = [
        _analysis(verdict="supported"),
        _analysis(verdict="supported"),
        _analysis(verdict="supported"),
        _analysis(verdict="partially_supported"),
        _analysis(verdict="partially_supported"),
    ]
    r = build_variance_report(samples)
    assert_eq(r.verdict_distribution, {"supported": 3, "partially_supported": 2}, "verdict dist")
    assert_eq(r.verdict_stability, 0.6, "stability 0.6")
    assert_eq(r.unstable_findings, [], "0.6 not below threshold (0.5)")

def test_unstable_verdict_flagged():
    """3-way split = stability ~0.33, below threshold."""
    samples = [
        _analysis(verdict="supported"),
        _analysis(verdict="partially_supported"),
        _analysis(verdict="not_supported"),
    ]
    r = build_variance_report(samples)
    assert_true(r.verdict_stability < UNSTABLE_THRESHOLD, "stability below threshold")
    flags = [f for f in r.unstable_findings if "Verdict unstable" in f]
    assert_eq(len(flags), 1, "verdict instability flagged")

def test_component_appearance_counts():
    """Component appearing in 10/10 samples is stable; 1/10 is unstable."""
    samples = []
    for i in range(10):
        comps = [_comp("tenant_boundary")]
        if i == 5:
            comps.append(_comp("orphan_widget"))   # only in 1 sample
        samples.append(_analysis(comps=comps))
    r = build_variance_report(samples)
    assert_eq(r.component_appearance["tenant_boundary"], "10/10", "stable component")
    assert_eq(r.component_appearance["orphan_widget"], "1/10", "unstable component")
    flags = [f for f in r.unstable_findings if "orphan_widget" in f]
    assert_eq(len(flags), 1, "unstable component flagged")
    flags2 = [f for f in r.unstable_findings if "tenant_boundary" in f]
    assert_eq(len(flags2), 0, "stable component NOT flagged")

def test_component_dedup_within_sample():
    """A sample listing the same component twice counts once."""
    samples = [
        _analysis(comps=[_comp("tenant_boundary"), _comp("tenant_boundary")]),
        _analysis(comps=[_comp("tenant_boundary")]),
    ]
    r = build_variance_report(samples)
    assert_eq(r.component_appearance["tenant_boundary"], "2/2",
              "dup within sample counts once")

def test_component_canonicalization():
    """Same component, different wording — collapses for appearance count."""
    samples = [
        _analysis(comps=[_comp("Tenant Boundary")]),
        _analysis(comps=[_comp("tenant_boundary")]),
        _analysis(comps=[_comp("Tenant boundary")]),
    ]
    r = build_variance_report(samples)
    assert_eq(r.component_appearance["tenant_boundary"], "3/3",
              "all three mentions collapsed")

def test_gap_appearance_and_severity_dist():
    samples = [
        _analysis(gaps=[_gap("atomic onboarding", sev="major")]),
        _analysis(gaps=[_gap("atomic onboarding", sev="major")]),
        _analysis(gaps=[_gap("atomic onboarding", sev="moderate")]),
        _analysis(gaps=[]),  # one sample doesn't see this gap
    ]
    r = build_variance_report(samples)
    assert_eq(r.gap_appearance["atomic_onboarding"], "3/4", "gap appears in 3/4")
    sev_dist = r.gap_severity_distribution["atomic_onboarding"]
    assert_eq(sev_dist, {"major": 2, "moderate": 1}, "severity distribution")

def test_seeds_default():
    samples = [_analysis(), _analysis(), _analysis()]
    r = build_variance_report(samples)
    assert_eq(r.sample_seeds, [0, 1, 2], "default seeds")

def test_seeds_provided():
    samples = [_analysis(), _analysis(), _analysis()]
    r = build_variance_report(samples, sample_seeds=[100, 200, 300])
    assert_eq(r.sample_seeds, [100, 200, 300], "seeds preserved")

def test_to_dict_serializes_cleanly():
    samples = [
        _analysis(verdict="supported", comps=[_comp("c1")], gaps=[_gap("g1")]),
        _analysis(verdict="partially_supported", comps=[_comp("c1")], gaps=[]),
    ]
    r = build_variance_report(samples, sample_seeds=[1, 2])
    d = r.to_dict()
    assert_true("verdict_distribution" in d, "has verdict_distribution")
    assert_true("gap_appearance" in d, "has gap_appearance")
    assert_true("notes" in d, "has notes")
    # Stability rounds to 3 decimals
    assert_eq(d["verdict_stability"], 0.5, "stability 0.5")

def test_unstable_threshold_uses_50_percent():
    """At N=10, threshold count should be 5. Findings in <5 samples flagged."""
    samples = []
    for i in range(10):
        comps = [_comp("always_seen")]
        if i < 4:
            comps.append(_comp("flaky_4_of_10"))   # 4/10 = below threshold
        if i < 5:
            comps.append(_comp("borderline_5_of_10"))   # 5/10 = at threshold
        samples.append(_analysis(comps=comps))
    r = build_variance_report(samples)
    flaky_flags = [f for f in r.unstable_findings if "flaky_4_of_10" in f]
    assert_eq(len(flaky_flags), 1, "4/10 flagged")
    border_flags = [f for f in r.unstable_findings if "borderline_5_of_10" in f]
    assert_eq(len(border_flags), 0, "5/10 not flagged (at threshold)")

# --- Run ---

def main():
    tests = [
        test_validation_empty,
        test_validation_uuid_mismatch,
        test_validation_seeds_mismatch,
        test_unanimous_verdict_high_stability,
        test_split_verdict_low_stability,
        test_unstable_verdict_flagged,
        test_component_appearance_counts,
        test_component_dedup_within_sample,
        test_component_canonicalization,
        test_gap_appearance_and_severity_dist,
        test_seeds_default,
        test_seeds_provided,
        test_to_dict_serializes_cleanly,
        test_unstable_threshold_uses_50_percent,
    ]
    for t in tests:
        try:
            t()
        except Exception as e:
            _failures.append(f"{t.__name__} threw: {type(e).__name__}: {e}")
    if _failures:
        print(f"FAIL: {len(_failures)} assertion(s)/error(s):")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"OK: {len(tests)} tests passed")

if __name__ == "__main__":
    main()
