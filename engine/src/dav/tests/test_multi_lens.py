"""ADR-011 multi-lens: quorum within a lens, union across lenses.

The design rule under test: a gap only one persona sees is THE POINT — union
across lenses must never quorum-suppress it, and cross-lens agreement is
recorded (personas list) as display signal, never a gate.
"""
from dav.core.ensemble import union_lens_merges
from dav.tests.test_ensemble import _analysis, _gap


def _a(verdict="partially_supported", gaps=(), advisory=()):
    a = _analysis(verdict=verdict, gaps=list(gaps))
    a.advisory_gaps = list(advisory)
    return a


def test_union_keeps_single_lens_finding_and_tags_it():
    g_aud = _gap("No refusal audit record", sev="major", conf="high")
    merged = union_lens_merges(
        {"application-team-member": _a(gaps=[]),
         "compliance-auditor": _a(gaps=[g_aud])},
        actor_lens="application-team-member")
    assert [g.description for g in merged.gaps_identified] == ["No refusal audit record"]
    assert merged.gaps_identified[0].personas == ["compliance-auditor"]


def test_cross_lens_agreement_accumulates_personas():
    g1 = _gap("Partial outcome not surfaced", sev="major", conf="high")
    g2 = _gap("Partial outcome not surfaced", sev="major", conf="high")
    merged = union_lens_merges(
        {"application-team-member": _a(gaps=[g1]),
         "sre": _a(gaps=[g2])},
        actor_lens="application-team-member")
    assert len(merged.gaps_identified) == 1
    assert sorted(merged.gaps_identified[0].personas) == ["application-team-member", "sre"]


def test_capability_id_key_beats_title_churn_across_lenses():
    g1 = _gap("Missing enforcement point", sev="major", conf="high")
    g1.capability_id = "FIX-QUOTA-001"
    g2 = _gap("Quota check location unspecified", sev="major", conf="high")
    g2.capability_id = "FIX-QUOTA-001"
    merged = union_lens_merges(
        {"a": _a(gaps=[g1]), "b": _a(gaps=[g2])}, actor_lens="a")
    assert len(merged.gaps_identified) == 1
    assert sorted(merged.gaps_identified[0].personas) == ["a", "b"]


def test_quorum_in_any_lens_wins_over_advisory_elsewhere():
    # primary in the auditor lens, advisory (sub-quorum) in the actor lens:
    # the gap is primary, with both personas recorded.
    gp = _gap("Audit record shape unspecified", sev="major", conf="high")
    ga = _gap("Audit record shape unspecified", sev="major", conf="high")
    merged = union_lens_merges(
        {"actor": _a(advisory=[ga]), "compliance-auditor": _a(gaps=[gp])},
        actor_lens="actor")
    assert len(merged.gaps_identified) == 1
    assert len(merged.advisory_gaps) == 0
    assert sorted(merged.gaps_identified[0].personas) == ["actor", "compliance-auditor"]


def test_verdict_comes_from_actor_lens():
    merged = union_lens_merges(
        {"actor": _a(verdict="supported"),
         "compliance-auditor": _a(verdict="not_supported")},
        actor_lens="actor")
    assert merged.summary.verdict == "supported"


def test_advisory_unions_and_tags_too():
    ga = _gap("one-sample concern", sev="minor", conf="low")
    merged = union_lens_merges(
        {"actor": _a(), "sre": _a(advisory=[ga])}, actor_lens="actor")
    assert [g.description for g in merged.advisory_gaps] == ["one-sample concern"]
    assert merged.advisory_gaps[0].personas == ["sre"]
