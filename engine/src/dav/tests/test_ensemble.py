"""
Tests for the verification-mode ensemble merger.

Run:  python -m dav.tests.test_ensemble
Or:   pytest engine/src/dav/tests/test_ensemble.py
"""

from __future__ import annotations

import sys

from dav.core.use_case_schema import (
    Analysis, AnalysisMetadata, AnalysisSummary,
    ComponentRequired, DataModelTouched, CapabilityInvoked,
    ProviderTypeInvolved, PolicyModeRequired, GapIdentified,
    Verdict, normalize_severity, normalize_confidence,
)
from dav.core.ensemble import (
    canonicalize, merge_analyses,
    _resolve_verdict, _lowest_confidence, _highest_severity,
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

# --- Builders for hand-crafted samples ---

def _summary(verdict: str, conf: str = "medium", notes: str = "") -> AnalysisSummary:
    return AnalysisSummary(
        verdict=verdict,
        overall_confidence=normalize_confidence(conf),
        notes=notes,
    )

def _meta(model: str = "test", tcc: int = 5, tokens: int = 100, wt: float = 10.0,
          stage2: str = "test-run") -> AnalysisMetadata:
    return AnalysisMetadata(
        model=model,
        tool_call_count=tcc,
        total_tokens=tokens,
        wall_time_seconds=wt,
        stage2_run_id=stage2,
    )

def _comp(id_: str, conf: str = "high", role: str = "x", rat: str = "y") -> ComponentRequired:
    return ComponentRequired(
        id=id_, role=role, rationale=rat, spec_refs=[],
        confidence=normalize_confidence(conf),
    )

def _gap(desc: str, sev: str = "major", conf: str = "medium",
         rat: str = "r", rec: str = "rec") -> GapIdentified:
    return GapIdentified(
        description=desc,
        severity=normalize_severity(sev),
        confidence=normalize_confidence(conf),
        rationale=rat,
        recommendation=rec,
        spec_refs_consulted=[],
        spec_refs_missing=None,
    )

def _analysis(uuid: str = "uc-test-001", verdict: str = "supported",
              overall_conf: str = "high",
              comps: list = None, gaps: list = None,
              meta: AnalysisMetadata = None) -> Analysis:
    return Analysis(
        use_case_uuid=uuid,
        analysis_metadata=meta or _meta(),
        summary=_summary(verdict, overall_conf),
        components_required=comps or [],
        gaps_identified=gaps or [],
    )

# --- canonicalize tests ---

def test_canonicalize_basic():
    assert_eq(canonicalize("Tenant Boundary"), "tenant_boundary", "basic")
    assert_eq(canonicalize("VM Service"), "vm_service", "vm")
    assert_eq(canonicalize(""), "", "empty")

def test_canonicalize_collapses_plurals():
    assert_eq(canonicalize("policies"), "policy", "policies → policy")
    assert_eq(canonicalize("Capabilities Invoked"), "capability_invoked", "capabilities")

def test_canonicalize_handles_punctuation():
    assert_eq(canonicalize("Gate-Keeper Policy"), "gate_keeper_policy", "hyphen")
    assert_eq(canonicalize("config.policy.v2"), "config_policy_v2", "dots")

# --- _resolve_verdict tests ---

def test_verdict_strict_majority():
    v, dist, tied = _resolve_verdict(["supported", "supported", "partially_supported"])
    assert_eq(v, "supported", "majority verdict")
    assert_eq(dist, {"supported": 2, "partially_supported": 1}, "vote dist")
    assert_eq(tied, False, "not tied")

def test_verdict_unanimous():
    v, dist, tied = _resolve_verdict(["supported", "supported", "supported"])
    assert_eq(v, "supported", "unanimous verdict")
    assert_eq(tied, False, "unanimous not tied")

def test_verdict_three_way_tie():
    v, dist, tied = _resolve_verdict([
        "supported", "partially_supported", "not_supported",
    ])
    assert_eq(v, "not_supported", "3-way tie → most conservative")
    assert_eq(tied, True, "tied=True")

def test_verdict_two_way_tie_conservative():
    # 2 supported, 2 not_supported; conservative wins
    v, dist, tied = _resolve_verdict([
        "supported", "supported", "not_supported", "not_supported",
    ])
    assert_eq(v, "not_supported", "2-2 tie → not_supported")
    assert_eq(tied, True, "2-2 tied")

def test_verdict_two_way_tie_supported_partial():
    v, dist, tied = _resolve_verdict([
        "supported", "supported", "partially_supported", "partially_supported",
    ])
    assert_eq(v, "partially_supported", "supported-partial tie → partial")
    assert_eq(tied, True, "supp-partial tied")

def test_verdict_n_equal_2_disagree():
    # Edge case: 2 samples disagreeing. 1-1 = tied; conservative wins.
    v, dist, tied = _resolve_verdict(["supported", "not_supported"])
    assert_eq(v, "not_supported", "2-sample disagree → not_supported")
    assert_eq(tied, True, "1-1 tied")

# --- _lowest_confidence / _highest_severity tests ---

def test_lowest_confidence():
    descs = [normalize_confidence("high"), normalize_confidence("medium"),
             normalize_confidence("high")]
    result = _lowest_confidence(descs)
    assert_eq(result.label, "medium", "lowest confidence")

def test_highest_severity():
    descs = [normalize_severity("minor"), normalize_severity("major"),
             normalize_severity("moderate")]
    result = _highest_severity(descs)
    assert_eq(result.label, "major", "highest severity")

def test_highest_severity_with_critical():
    descs = [normalize_severity("major"), normalize_severity("critical"),
             normalize_severity("moderate")]
    assert_eq(_highest_severity(descs).label, "critical", "critical wins")

def test_severity_order_includes_moderate():
    # added moderate; verify it sits between minor and major
    descs = [normalize_severity("minor"), normalize_severity("moderate")]
    assert_eq(_highest_severity(descs).label, "moderate", "moderate > minor")
    descs = [normalize_severity("moderate"), normalize_severity("major")]
    assert_eq(_highest_severity(descs).label, "major", "major > moderate")

# --- merge_analyses validation ---

def test_merge_empty_raises():
    assert_raises(lambda: merge_analyses([]), ValueError, "empty samples")

def test_merge_uuid_mismatch_raises():
    s1 = _analysis(uuid="uc-001")
    s2 = _analysis(uuid="uc-002")
    assert_raises(lambda: merge_analyses([s1, s2]), ValueError, "uuid mismatch")

def test_merge_seeds_length_mismatch_raises():
    s1 = _analysis()
    assert_raises(
        lambda: merge_analyses([s1], sample_seeds=[1, 2]),
        ValueError, "seeds length mismatch"
    )

# --- Single-sample merge (verification with N=1, per decision #3) ---

def test_merge_n_equals_1():
    """Verification with N=1 still produces sample_annotations."""
    s = _analysis(
        verdict="supported",
        comps=[_comp("tenant_boundary", "high")],
        gaps=[],
    )
    merged = merge_analyses([s])
    assert_eq(merged.summary.verdict, "supported", "N=1 verdict preserved")
    assert_eq(merged.summary.overall_confidence.label, "high", "N=1 confidence preserved")
    assert_true(merged.sample_annotations is not None, "N=1 has sample_annotations")
    assert_eq(merged.sample_annotations.sample_count, 1, "N=1 sample_count")
    assert_eq(merged.sample_annotations.verdict_tied, False, "N=1 not tied")
    assert_eq(merged.sample_annotations.component_consensus, {"tenant_boundary": "1/1"},
              "N=1 component consensus")

# --- Three-sample unanimous merge ---

def test_merge_unanimous():
    """All samples agree → straightforward merge."""
    samples = [
        _analysis(verdict="supported", overall_conf="high",
                  comps=[_comp("tenant_boundary", "high"), _comp("policy_engine", "medium")]),
        _analysis(verdict="supported", overall_conf="high",
                  comps=[_comp("tenant_boundary", "high"), _comp("policy_engine", "medium")]),
        _analysis(verdict="supported", overall_conf="high",
                  comps=[_comp("tenant_boundary", "high"), _comp("policy_engine", "medium")]),
    ]
    merged = merge_analyses(samples, sample_seeds=[10, 20, 30])
    assert_eq(merged.summary.verdict, "supported", "unanimous verdict")
    assert_eq(merged.summary.overall_confidence.label, "high", "unanimous confidence")
    assert_eq(len(merged.components_required), 2, "unanimous components")
    assert_eq(merged.sample_annotations.verdict_votes, {"supported": 3}, "vote dist")
    assert_eq(merged.sample_annotations.verdict_tied, False, "not tied")
    assert_eq(merged.sample_annotations.sample_seeds, [10, 20, 30], "seeds preserved")
    assert_eq(merged.sample_annotations.component_consensus["tenant_boundary"], "3/3",
              "tenant_boundary consensus")
    assert_eq(merged.sample_annotations.component_consensus["policy_engine"], "3/3",
              "policy_engine consensus")
    assert_eq(len(merged.sample_annotations.per_sample), 3, "3 per-sample records")

# --- Three-sample majority merge ---

def test_merge_majority():
    """2 of 3 samples agree → majority wins, no cap."""
    samples = [
        _analysis(verdict="supported", overall_conf="high"),
        _analysis(verdict="supported", overall_conf="high"),
        _analysis(verdict="partially_supported", overall_conf="medium"),
    ]
    merged = merge_analyses(samples)
    assert_eq(merged.summary.verdict, "supported", "majority verdict")
    # Confidence is lowest across samples (medium, since one sample said medium)
    assert_eq(merged.summary.overall_confidence.label, "medium", "lowest confidence wins")
    assert_eq(merged.sample_annotations.verdict_votes,
              {"supported": 2, "partially_supported": 1}, "vote dist")
    assert_eq(merged.sample_annotations.verdict_tied, False, "majority not tied")

# --- Tied verdict caps confidence at medium ---

def test_merge_tied_verdict_caps_confidence():
    """Tied verdict → overall_confidence capped at medium even if all samples said high."""
    samples = [
        _analysis(verdict="supported", overall_conf="high"),
        _analysis(verdict="not_supported", overall_conf="high"),
    ]
    merged = merge_analyses(samples)
    # 1-1 tie resolves to not_supported (more conservative)
    assert_eq(merged.summary.verdict, "not_supported", "tied → conservative")
    assert_eq(merged.sample_annotations.verdict_tied, True, "tied=True")
    # Both samples said high, but tied verdict caps at medium
    assert_eq(merged.summary.overall_confidence.label, "medium",
              "tied verdict caps confidence at medium")
    assert_eq(merged.summary.overall_confidence.factors.get("override_rationale"),
              "capped to medium due to tied verdict", "cap rationale recorded")

def test_merge_tied_verdict_doesnt_raise_low_confidence():
    """Tied verdict caps AT medium, but if confidence was already low, it stays low."""
    samples = [
        _analysis(verdict="supported", overall_conf="low"),
        _analysis(verdict="not_supported", overall_conf="low"),
    ]
    merged = merge_analyses(samples)
    assert_eq(merged.summary.overall_confidence.label, "low",
              "low confidence stays low even with tied verdict")

# --- Gap merging across samples ---

def test_merge_gaps_canonicalizes_descriptions():
    """Two samples write the same gap with different wording but same canonical
    description → merged into one gap."""
    g1 = _gap("Atomic onboarding gap", sev="major", conf="medium")
    g2 = _gap("atomic_onboarding_gap", sev="moderate", conf="high")  # same canonical
    samples = [
        _analysis(verdict="partially_supported", gaps=[g1]),
        _analysis(verdict="partially_supported", gaps=[g2]),
    ]
    merged = merge_analyses(samples)
    assert_eq(len(merged.gaps_identified), 1, "gaps deduped by canonicalization")
    # Severity merged: highest (major)
    assert_eq(merged.gaps_identified[0].severity.label, "major",
              "merged severity is highest")
    # Confidence merged: lowest (medium)
    assert_eq(merged.gaps_identified[0].confidence.label, "medium",
              "merged confidence is lowest")
    assert_eq(merged.sample_annotations.gap_consensus["atomic_onboarding_gap"], "2/2",
              "gap consensus")

def test_merge_gap_only_in_one_sample():
    """A gap that appears in only 1 of N samples is still included; consensus 1/N."""
    g_unique = _gap("Edge case in modify flow", sev="moderate", conf="medium")
    samples = [
        _analysis(verdict="supported", gaps=[g_unique]),
        _analysis(verdict="supported", gaps=[]),
        _analysis(verdict="supported", gaps=[]),
    ]
    merged = merge_analyses(samples)
    assert_eq(len(merged.gaps_identified), 1, "unique gap included")
    canon_key = canonicalize("Edge case in modify flow")
    assert_eq(merged.sample_annotations.gap_consensus[canon_key], "1/3",
              "low-consensus gap annotated")

# --- Component dedup ---

def test_merge_components_canonicalizes_ids():
    """Same component named slightly differently across samples → one entry."""
    samples = [
        _analysis(comps=[_comp("tenant_boundary", "high")]),
        _analysis(comps=[_comp("Tenant Boundary", "medium")]),  # canonical match
        _analysis(comps=[_comp("tenant boundary", "high")]),    # canonical match
    ]
    merged = merge_analyses(samples)
    assert_eq(len(merged.components_required), 1, "components deduped")
    # Confidence: lowest (medium)
    assert_eq(merged.components_required[0].confidence.label, "medium",
              "merged component confidence is lowest")

def test_merge_components_consensus_uses_first_seen_id():
    """Consensus dict key uses the canonical key — but more importantly
    the merged component's id is the first-seen original wording."""
    samples = [
        _analysis(comps=[_comp("Tenant Boundary", "high")]),
        _analysis(comps=[_comp("tenant_boundary", "high")]),
    ]
    merged = merge_analyses(samples)
    # Original wording from first sample preserved
    assert_eq(merged.components_required[0].id, "Tenant Boundary",
              "first-seen id preserved")
    # Consensus dict keyed on first-seen original
    assert_true("Tenant Boundary" in merged.sample_annotations.component_consensus,
                "consensus keyed on original id")

# --- Per-sample records ---

def test_merge_per_sample_records():
    """sample_annotations.per_sample includes one record per input sample."""
    samples = [
        _analysis(verdict="supported", overall_conf="high",
                  meta=_meta(tcc=5, tokens=100, wt=10.0)),
        _analysis(verdict="supported", overall_conf="high",
                  meta=_meta(tcc=7, tokens=140, wt=12.0)),
        _analysis(verdict="partially_supported", overall_conf="medium",
                  meta=_meta(tcc=4, tokens=80, wt=9.0)),
    ]
    merged = merge_analyses(samples, sample_seeds=[100, 200, 300])
    records = merged.sample_annotations.per_sample
    assert_eq(len(records), 3, "3 records")
    assert_eq(records[0].seed, 100, "seed 0")
    assert_eq(records[0].tool_call_count, 5, "tcc 0")
    assert_eq(records[0].verdict, "supported", "verdict 0")
    assert_eq(records[2].seed, 300, "seed 2")
    assert_eq(records[2].verdict, "partially_supported", "verdict 2")
    assert_eq(records[2].confidence.label, "medium", "confidence 2")

# --- Metadata aggregation ---

def test_merge_metadata_aggregates():
    samples = [
        _analysis(meta=_meta(tcc=5, tokens=100, wt=10.0)),
        _analysis(meta=_meta(tcc=7, tokens=140, wt=12.0)),
        _analysis(meta=_meta(tcc=4, tokens=80, wt=9.0)),
    ]
    merged = merge_analyses(samples, sample_seeds=[1, 2, 3])
    meta = merged.analysis_metadata
    assert_eq(meta.tool_call_count, 16, "tcc summed")
    assert_eq(meta.total_tokens, 320, "tokens summed")
    assert_eq(meta.wall_time_seconds, 12.0, "wt is max")
    assert_eq(meta.sample_count, 3, "sample_count")
    assert_eq(meta.sample_seeds, [1, 2, 3], "sample_seeds")
    assert_eq(meta.mode, "verification", "mode set")

# --- Tool call trace selection ---

def test_merge_picks_trace_from_winning_verdict():
    """tool_call_trace comes from a sample whose verdict matches the merged verdict."""
    from dav.core.use_case_schema import ToolCall
    samples = [
        _analysis(verdict="not_supported"),
        _analysis(verdict="supported"),  # this one's trace should be picked
        _analysis(verdict="supported"),
    ]
    samples[0].tool_call_trace = [ToolCall(tool="x", args={}, result_summary="losing", purpose="z")]
    samples[1].tool_call_trace = [ToolCall(tool="y", args={}, result_summary="winning", purpose="z")]
    samples[2].tool_call_trace = [ToolCall(tool="z", args={}, result_summary="also winning", purpose="z")]
    merged = merge_analyses(samples)
    assert_eq(merged.summary.verdict, "supported", "majority verdict")
    assert_eq(len(merged.tool_call_trace), 1, "1 trace entry")
    assert_eq(merged.tool_call_trace[0].result_summary, "winning",
              "trace from first sample matching verdict")

# --- Round-trip through to_dict/from_dict ---

def test_merged_analysis_serializes():
    """Merged analysis with sample_annotations can serialize to dict and re-parse."""
    samples = [
        _analysis(verdict="supported", overall_conf="high",
                  comps=[_comp("tenant_boundary", "high")]),
        _analysis(verdict="supported", overall_conf="high",
                  comps=[_comp("tenant_boundary", "high")]),
    ]
    merged = merge_analyses(samples, sample_seeds=[42, 43])
    serialized = merged.to_dict()
    # Sanity-check shape
    assert_true("sample_annotations" in serialized, "sample_annotations in dict")
    assert_eq(serialized["sample_annotations"]["sample_count"], 2, "sample_count in dict")
    assert_eq(serialized["analysis_metadata"]["mode"], "verification", "mode in dict")
    # Re-parse
    re_parsed = Analysis.from_dict(serialized)
    assert_eq(re_parsed.summary.verdict, "supported", "roundtrip verdict")
    assert_eq(re_parsed.sample_annotations.sample_count, 2, "roundtrip sample_count")
    assert_eq(re_parsed.sample_annotations.verdict_votes, {"supported": 2},
              "roundtrip verdict_votes")

# --- Multi-finding-type merge ---

def test_merge_full_finding_types():
    """All 6 finding types deduped and confidence-merged correctly."""
    s1 = _analysis(comps=[_comp("c1", "high")])
    s1.capabilities_invoked = [
        CapabilityInvoked(id="cap1", usage="u", rationale="r", spec_refs=[],
                          confidence=normalize_confidence("high")),
    ]
    s1.data_model_touched = [
        DataModelTouched(entity="e1", fields_accessed=["a"], operations=["read"],
                         rationale="r", spec_refs=[],
                         confidence=normalize_confidence("medium")),
    ]
    s1.provider_types_involved = [
        ProviderTypeInvolved(type="service", role="r",
                             confidence=normalize_confidence("high")),
    ]
    s1.policy_modes_required = [
        PolicyModeRequired(mode="Internal", rationale="r", spec_refs=[],
                           confidence=normalize_confidence("high")),
    ]
    s1.gaps_identified = [_gap("gap1", "moderate", "high")]

    s2 = _analysis(comps=[_comp("c1", "low")])
    s2.capabilities_invoked = [
        CapabilityInvoked(id="cap1", usage="u", rationale="r", spec_refs=[],
                          confidence=normalize_confidence("low")),
    ]
    s2.data_model_touched = [
        DataModelTouched(entity="e1", fields_accessed=["b"], operations=["write"],
                         rationale="r", spec_refs=[],
                         confidence=normalize_confidence("low")),
    ]
    s2.provider_types_involved = [
        ProviderTypeInvolved(type="service", role="r",
                             confidence=normalize_confidence("low")),
    ]
    s2.policy_modes_required = [
        PolicyModeRequired(mode="Internal", rationale="r", spec_refs=[],
                           confidence=normalize_confidence("low")),
    ]
    s2.gaps_identified = [_gap("gap1", "major", "low")]

    merged = merge_analyses([s1, s2])

    # Component: 1 entry, confidence=low (lowest)
    assert_eq(len(merged.components_required), 1, "1 component")
    assert_eq(merged.components_required[0].confidence.label, "low", "component low")

    # Capability: 1 entry, confidence=low
    assert_eq(len(merged.capabilities_invoked), 1, "1 capability")
    assert_eq(merged.capabilities_invoked[0].confidence.label, "low", "capability low")

    # Data model: 1 entry, ops and fields unioned
    assert_eq(len(merged.data_model_touched), 1, "1 data_model")
    assert_eq(merged.data_model_touched[0].operations, ["read", "write"], "ops unioned")
    assert_eq(merged.data_model_touched[0].fields_accessed, ["a", "b"], "fields unioned")
    assert_eq(merged.data_model_touched[0].confidence.label, "low", "data_model low")

    # Provider: 1 entry, low
    assert_eq(len(merged.provider_types_involved), 1, "1 provider")
    assert_eq(merged.provider_types_involved[0].confidence.label, "low", "provider low")

    # Policy mode: 1 entry, low
    assert_eq(len(merged.policy_modes_required), 1, "1 policy_mode")
    assert_eq(merged.policy_modes_required[0].confidence.label, "low", "policy_mode low")

    # Gap: 1 entry, severity=major (highest), confidence=low (lowest)
    assert_eq(len(merged.gaps_identified), 1, "1 gap")
    assert_eq(merged.gaps_identified[0].severity.label, "major", "gap severity highest")
    assert_eq(merged.gaps_identified[0].confidence.label, "low", "gap confidence lowest")

# --- Run ---

def main():
    tests = [
        # canonicalize
        test_canonicalize_basic,
        test_canonicalize_collapses_plurals,
        test_canonicalize_handles_punctuation,
        # _resolve_verdict
        test_verdict_strict_majority,
        test_verdict_unanimous,
        test_verdict_three_way_tie,
        test_verdict_two_way_tie_conservative,
        test_verdict_two_way_tie_supported_partial,
        test_verdict_n_equal_2_disagree,
        # ordering helpers
        test_lowest_confidence,
        test_highest_severity,
        test_highest_severity_with_critical,
        test_severity_order_includes_moderate,
        # validation
        test_merge_empty_raises,
        test_merge_uuid_mismatch_raises,
        test_merge_seeds_length_mismatch_raises,
        # behavior
        test_merge_n_equals_1,
        test_merge_unanimous,
        test_merge_majority,
        test_merge_tied_verdict_caps_confidence,
        test_merge_tied_verdict_doesnt_raise_low_confidence,
        test_merge_gaps_canonicalizes_descriptions,
        test_merge_gap_only_in_one_sample,
        test_merge_components_canonicalizes_ids,
        test_merge_components_consensus_uses_first_seen_id,
        test_merge_per_sample_records,
        test_merge_metadata_aggregates,
        test_merge_picks_trace_from_winning_verdict,
        test_merged_analysis_serializes,
        test_merge_full_finding_types,
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
