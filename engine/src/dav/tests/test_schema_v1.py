"""
schema verification tests.

Covers:
  - normalize_severity / normalize_confidence: shorthand and nested input
  - Out-of-band scores rejected with clear errors
  - Round-trip: old-format YAML input → normalize → new-format output → parse → identical objects
  - New dataclasses (SampleAnnotations, AssertionResult) serialize cleanly
  - ANALYSIS_JSON_SCHEMA includes MODERATE severity

Run directly: `python -m dav.tests.test_schema_v1` or via pytest.
"""

from __future__ import annotations

import sys
import json

from dav.core.use_case_schema import (
    Severity, Confidence, Band,
    SeverityDescriptor, ConfidenceDescriptor,
    normalize_severity, normalize_confidence, score_to_band,
    ComponentRequired, CapabilityInvoked, DataModelTouched,
    ProviderTypeInvolved, PolicyModeRequired, GapIdentified,
    Analysis, AnalysisMetadata, AnalysisSummary,
    SampleRecord, SampleAnnotations, AssertionResult,
    ToolCall,
    ANALYSIS_JSON_SCHEMA,
)

# --- Helpers ---

_failures: list[str] = []

def assert_eq(actual, expected, label: str) -> None:
    if actual != expected:
        _failures.append(f"{label}: got {actual!r}, expected {expected!r}")

def assert_true(cond: bool, label: str) -> None:
    if not cond:
        _failures.append(f"{label}: expected truthy, got falsy")

def assert_raises(fn, expected_exc, label: str) -> None:
    try:
        fn()
    except expected_exc:
        return
    except Exception as e:
        _failures.append(f"{label}: expected {expected_exc.__name__}, got {type(e).__name__}: {e}")
        return
    _failures.append(f"{label}: expected {expected_exc.__name__}, no exception raised")

# --- Enum / band tests ---

def test_severity_five_labels():
    """Severity enum has exactly 5 labels including MODERATE (addition)."""
    labels = {s.value for s in Severity}
    assert_eq(labels, {"critical", "major", "moderate", "minor", "advisory"}, "severity labels")

def test_confidence_three_labels():
    labels = {c.value for c in Confidence}
    assert_eq(labels, {"high", "medium", "low"}, "confidence labels")

def test_band_five_labels():
    labels = {b.value for b in Band}
    assert_eq(labels, {"very_low", "low", "medium", "high", "very_high"}, "band labels")

def test_score_to_band():
    cases = [
        (0, "very_low"), (10, "very_low"), (20, "very_low"),
        (21, "low"), (30, "low"), (40, "low"),
        (41, "medium"), (50, "medium"), (60, "medium"),
        (61, "high"), (70, "high"), (80, "high"),
        (81, "very_high"), (90, "very_high"), (100, "very_high"),
    ]
    for score, expected_band in cases:
        assert_eq(score_to_band(score), expected_band, f"score_to_band({score})")
    assert_raises(lambda: score_to_band(-1), ValueError, "score_to_band(-1)")
    assert_raises(lambda: score_to_band(101), ValueError, "score_to_band(101)")

# --- normalize_severity tests ---

def test_normalize_severity_from_shorthand():
    sev = normalize_severity("major")
    assert_eq(sev.label, "major", "shorthand severity label")
    assert_eq(sev.score, 70, "shorthand severity score (band midpoint)")
    assert_eq(sev.band, "high", "shorthand severity band")
    assert_eq(sev.factors["base_from_label"], 70, "shorthand base_from_label")
    assert_eq(sev.factors["override_rationale"], None, "shorthand override_rationale")

def test_normalize_severity_five_labels():
    expected = {
        "advisory": (10, "very_low"),
        "minor": (30, "low"),
        "moderate": (50, "medium"),
        "major": (70, "high"),
        "critical": (90, "very_high"),
    }
    for label, (score, band) in expected.items():
        sev = normalize_severity(label)
        assert_eq(sev.score, score, f"{label} default score")
        assert_eq(sev.band, band, f"{label} derived band")

def test_normalize_severity_from_nested_dict():
    sev = normalize_severity({"label": "major", "score": 75})
    assert_eq(sev.label, "major", "nested severity label")
    assert_eq(sev.score, 75, "nested severity score (override)")
    assert_eq(sev.band, "high", "nested severity band (derived from 75)")
    assert_eq(sev.factors["base_from_label"], 70, "nested base_from_label (from label)")

def test_normalize_severity_preserves_passthrough():
    original = SeverityDescriptor(label="minor", score=35, band="low", factors={"x": "y"})
    result = normalize_severity(original)
    assert_true(result is original, "passthrough identity")

def test_normalize_severity_case_insensitive():
    sev = normalize_severity("MAJOR")
    assert_eq(sev.label, "major", "case normalized")

def test_normalize_severity_rejects_invalid_label():
    assert_raises(
        lambda: normalize_severity("catastrophic"),
        ValueError, "invalid severity label"
    )
    assert_raises(
        lambda: normalize_severity({"label": "catastrophic"}),
        ValueError, "invalid severity label in dict"
    )

def test_normalize_severity_rejects_out_of_band_score():
    # major band is 61-80; score=50 is out of range (should be moderate)
    assert_raises(
        lambda: normalize_severity({"label": "major", "score": 50}),
        ValueError, "major score=50 out of band"
    )
    assert_raises(
        lambda: normalize_severity({"label": "advisory", "score": 25}),
        ValueError, "advisory score=25 out of band"
    )

def test_normalize_severity_accepts_boundary_scores():
    # Band boundaries are inclusive on both ends per spec
    cases = [
        ("advisory", 0), ("advisory", 20),
        ("minor", 21), ("minor", 40),
        ("moderate", 41), ("moderate", 60),
        ("major", 61), ("major", 80),
        ("critical", 81), ("critical", 100),
    ]
    for label, score in cases:
        sev = normalize_severity({"label": label, "score": score})
        assert_eq(sev.score, score, f"{label} boundary score {score}")

def test_normalize_severity_rejects_non_int_score():
    assert_raises(
        lambda: normalize_severity({"label": "major", "score": 70.5}),
        ValueError, "float score rejected"
    )

# --- normalize_confidence tests ---

def test_normalize_confidence_from_shorthand():
    conf = normalize_confidence("high")
    assert_eq(conf.label, "high", "shorthand confidence label")
    assert_eq(conf.score, 85, "shorthand confidence score (label default 85)")
    assert_eq(conf.band, "very_high", "shorthand confidence band")

def test_normalize_confidence_three_labels():
    expected = {
        "low": (30, "low"),
        "medium": (50, "medium"),
        "high": (85, "very_high"),
    }
    for label, (score, band) in expected.items():
        conf = normalize_confidence(label)
        assert_eq(conf.score, score, f"{label} default score")
        assert_eq(conf.band, band, f"{label} derived band")

def test_normalize_confidence_rejects_invalid():
    assert_raises(
        lambda: normalize_confidence("certain"),
        ValueError, "invalid confidence label"
    )

def test_normalize_confidence_rejects_out_of_band():
    assert_raises(
        lambda: normalize_confidence({"label": "high", "score": 65}),
        ValueError, "high score=65 out of band (expected 81-100)"
    )

# --- Dataclass from_dict / to_dict round-trip ---

def test_component_required_roundtrip_from_shorthand():
    # Shorthand input (LLM-format)
    shorthand = {
        "id": "tenant_boundary",
        "role": "Defines tenant scope",
        "rationale": "Required by doc 49",
        "spec_refs": ["49-implementation/9.1"],
        "confidence": "high",
    }
    c = ComponentRequired.from_dict(shorthand)
    assert_eq(c.id, "tenant_boundary", "component id")
    assert_true(isinstance(c.confidence, ConfidenceDescriptor), "confidence is descriptor")
    assert_eq(c.confidence.label, "high", "descriptor label")
    # Emit (nested form)
    emitted = c.to_dict()
    assert_eq(emitted["confidence"]["label"], "high", "emitted confidence label")
    assert_eq(emitted["confidence"]["score"], 85, "emitted confidence score")
    assert_eq(emitted["confidence"]["band"], "very_high", "emitted confidence band")
    # Re-parse emitted form
    c2 = ComponentRequired.from_dict(emitted)
    assert_eq(c2.confidence.label, c.confidence.label, "roundtrip label")
    assert_eq(c2.confidence.score, c.confidence.score, "roundtrip score")

def test_gap_identified_roundtrip_from_shorthand():
    shorthand = {
        "description": "Missing section",
        "severity": "major",
        "confidence": "medium",
        "rationale": "Doc 49 §9.1 doesn't cover X",
        "recommendation": "Add section on X",
        "spec_refs_consulted": ["49/9.1"],
        "spec_refs_missing": "49/atomic onboarding",
    }
    g = GapIdentified.from_dict(shorthand)
    assert_true(isinstance(g.severity, SeverityDescriptor), "gap severity is descriptor")
    assert_eq(g.severity.label, "major", "gap severity label")
    assert_eq(g.severity.score, 70, "gap severity score (label default)")
    # Emit + re-parse
    emitted = g.to_dict()
    assert_eq(emitted["severity"]["band"], "high", "emitted gap band")
    g2 = GapIdentified.from_dict(emitted)
    assert_eq(g2.severity.label, "major", "roundtrip severity")
    assert_eq(g2.severity.score, 70, "roundtrip severity score")

def test_gap_identified_with_score_override():
    """A gap author can override score within the label's band."""
    input_data = {
        "description": "Edge case",
        "severity": {
            "label": "moderate",
            "score": 58,
            "factors": {"override_rationale": "Near top of moderate range"},
        },
        "confidence": "medium",
        "rationale": "...",
        "recommendation": "...",
        "spec_refs_consulted": [],
        "spec_refs_missing": None,
    }
    g = GapIdentified.from_dict(input_data)
    assert_eq(g.severity.score, 58, "score override preserved")
    assert_eq(g.severity.band, "medium", "band derived from overridden score")
    assert_eq(g.severity.factors["override_rationale"],
              "Near top of moderate range", "rationale preserved")
    assert_eq(g.severity.factors["base_from_label"], 50, "base_from_label set from label default")

# --- Full Analysis round-trip ---

def test_analysis_full_roundtrip():
    """An Analysis in old shorthand format round-trips via from_dict/to_dict."""
    raw = {
        "use_case_uuid": "uc-test-001",
        "analysis_metadata": {
            "model": "qwen-test",
            "timestamp": "2026-04-24T23:00:00+00:00",
            "tool_call_count": 5,
            "total_tokens": 1234,
            "stage2_run_id": "test-run",
        },
        "components_required": [
            {"id": "comp1", "role": "x", "rationale": "y", "spec_refs": [], "confidence": "high"},
        ],
        "data_model_touched": [],
        "capabilities_invoked": [],
        "provider_types_involved": [],
        "policy_modes_required": [],
        "gaps_identified": [
            {
                "description": "A gap",
                "severity": "major",
                "confidence": "medium",
                "rationale": "because",
                "recommendation": "fix it",
                "spec_refs_consulted": ["a"],
                "spec_refs_missing": "b",
            },
        ],
        "summary": {
            "verdict": "partially_supported",
            "overall_confidence": "medium",
            "notes": "some notes",
        },
        "tool_call_trace": [],
    }
    a = Analysis.from_dict(raw)
    assert_true(isinstance(a.summary.overall_confidence, ConfidenceDescriptor),
                "summary confidence normalized")
    assert_true(isinstance(a.gaps_identified[0].severity, SeverityDescriptor),
                "gap severity normalized")
    emitted = a.to_dict()
    # The emitted form should have nested descriptor
    assert_eq(emitted["summary"]["overall_confidence"]["label"], "medium", "emitted summary label")
    assert_eq(emitted["gaps_identified"][0]["severity"]["label"], "major", "emitted gap label")
    # Re-parse
    a2 = Analysis.from_dict(emitted)
    assert_eq(a2.summary.overall_confidence.label, "medium", "roundtrip summary")
    assert_eq(a2.gaps_identified[0].severity.score, 70, "roundtrip gap score")

def test_analysis_with_sample_annotations():
    """Verification-mode analyses carry sample_annotations."""
    raw = {
        "use_case_uuid": "uc-test-002",
        "analysis_metadata": {"model": "qwen-test", "mode": "verification", "sample_count": 3},
        "components_required": [],
        "data_model_touched": [],
        "capabilities_invoked": [],
        "provider_types_involved": [],
        "policy_modes_required": [],
        "gaps_identified": [],
        "summary": {"verdict": "supported", "overall_confidence": "high", "notes": "ok"},
        "tool_call_trace": [],
        "sample_annotations": {
            "sample_count": 3,
            "sample_seeds": [1, 2, 3],
            "verdict_votes": {"supported": 3},
            "verdict_tied": False,
            "per_sample": [
                {"seed": 1, "tool_call_count": 5, "total_tokens": 100, "wall_time_seconds": 10.0,
                 "verdict": "supported", "confidence": "high"},
            ],
            "component_consensus": {"comp1": "3/3"},
            "capability_consensus": {},
            "data_model_consensus": {},
            "provider_type_consensus": {},
            "policy_mode_consensus": {},
            "gap_consensus": {},
        },
    }
    a = Analysis.from_dict(raw)
    assert_true(a.sample_annotations is not None, "sample_annotations present")
    assert_eq(a.sample_annotations.sample_count, 3, "sample count")
    assert_eq(a.sample_annotations.verdict_votes, {"supported": 3}, "verdict votes")
    assert_eq(a.sample_annotations.component_consensus["comp1"], "3/3", "component consensus")
    assert_true(isinstance(a.sample_annotations.per_sample[0].confidence, ConfidenceDescriptor),
                "per-sample confidence normalized")
    # Roundtrip
    emitted = a.to_dict()
    a2 = Analysis.from_dict(emitted)
    assert_eq(a2.sample_annotations.sample_count, 3, "roundtrip sample_count")

def test_analysis_with_assertion_result():
    """Assertion UCs produce analyses with assertion_result populated."""
    raw = {
        "use_case_uuid": "uc-assert-001",
        "analysis_metadata": {"model": "assertion-runner"},
        "components_required": [],
        "data_model_touched": [],
        "capabilities_invoked": [],
        "provider_types_involved": [],
        "policy_modes_required": [],
        "gaps_identified": [],
        "summary": {"verdict": "supported", "overall_confidence": "high", "notes": "assertion passed"},
        "tool_call_trace": [],
        "assertion_result": {
            "passed": True,
            "diagnostic": "All 15 handles resolved.",
            "assertion_module": "dcm.dav.assertions.handle_resolution",
            "assertion_function": "check_all_uc_handles_resolve",
            "wall_time_seconds": 0.124,
            "confidence": "high",
            "severity": None,
            "details": {"checked": 15},
        },
    }
    a = Analysis.from_dict(raw)
    assert_true(a.assertion_result is not None, "assertion_result present")
    assert_true(a.assertion_result.passed, "assertion passed")
    assert_eq(a.assertion_result.severity, None, "severity None on pass")
    assert_true(isinstance(a.assertion_result.confidence, ConfidenceDescriptor),
                "assertion confidence normalized")
    assert_eq(a.assertion_result.details["checked"], 15, "details preserved")
    # Roundtrip
    emitted = a.to_dict()
    a2 = Analysis.from_dict(emitted)
    assert_true(a2.assertion_result.passed, "roundtrip passed")

def test_json_schema_has_moderate():
    """The LLM-facing JSON schema includes moderate in severity enum."""
    sev_enum = ANALYSIS_JSON_SCHEMA["properties"]["gaps_identified"]["items"]["properties"]["severity"]["enum"]
    assert_true("moderate" in sev_enum, f"moderate in severity enum; got {sev_enum}")
    assert_eq(len(sev_enum), 5, "severity enum has 5 labels")

# --- Run all tests ---

def main():
    tests = [
        test_severity_five_labels,
        test_confidence_three_labels,
        test_band_five_labels,
        test_score_to_band,
        test_normalize_severity_from_shorthand,
        test_normalize_severity_five_labels,
        test_normalize_severity_from_nested_dict,
        test_normalize_severity_preserves_passthrough,
        test_normalize_severity_case_insensitive,
        test_normalize_severity_rejects_invalid_label,
        test_normalize_severity_rejects_out_of_band_score,
        test_normalize_severity_accepts_boundary_scores,
        test_normalize_severity_rejects_non_int_score,
        test_normalize_confidence_from_shorthand,
        test_normalize_confidence_three_labels,
        test_normalize_confidence_rejects_invalid,
        test_normalize_confidence_rejects_out_of_band,
        test_component_required_roundtrip_from_shorthand,
        test_gap_identified_roundtrip_from_shorthand,
        test_gap_identified_with_score_override,
        test_analysis_full_roundtrip,
        test_analysis_with_sample_annotations,
        test_analysis_with_assertion_result,
        test_json_schema_has_moderate,
    ]
    for t in tests:
        t()
    if _failures:
        print(f"FAIL: {len(_failures)} assertion(s) failed:")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"OK: {len(tests)} tests passed, {sum(1 for _ in tests)} assertions exercised")

if __name__ == "__main__":
    main()
