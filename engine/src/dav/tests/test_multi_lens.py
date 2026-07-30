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


def test_persona_verdicts_survive_the_union():
    """ADR-003 GRADUATED ruling (2026-07-29): support is stakeholder-relative.
    Every lens's verdict is kept keyed by persona; the top-level verdict stays
    the actor's; the empty-Auditable shape — supported-for-engineer,
    not_supported-for-auditor — is data, and the disagreement IS the finding."""
    merged = union_lens_merges(
        {"platform-engineer": _a(verdict="supported"),
         "compliance-auditor": _a(verdict="not_supported")},
        actor_lens="platform-engineer")
    assert merged.summary.verdict == "supported"                    # actor's, back-compat
    assert merged.persona_verdicts == {"platform-engineer": "supported",
                                       "compliance-auditor": "not_supported"}


def test_persona_verdicts_round_trip():
    from dav.core.use_case_schema import Analysis
    from dav.tests.test_ensemble import _analysis
    a = _analysis(verdict="supported")
    a.persona_verdicts = {"sre": "partially_supported", "compliance-auditor": "not_supported"}
    d = a.to_dict()
    assert d["persona_verdicts"] == a.persona_verdicts
    assert Analysis.from_dict(d).persona_verdicts == a.persona_verdicts


def _crit(cid, satisfied, note=""):
    from dav.core.use_case_schema import CriterionAnswer
    return CriterionAnswer(id=cid, satisfied=satisfied, spec_ref="spec/10-refusal-contract.md",
                           note=note, consensus="3/3")


def test_criteria_ownership_owner_lens_wins():
    """Criteria × persona composition: the OWNING persona's answer ships.
    The auditor says auditable=false while the actor says true — the merged
    vector carries the auditor's answer, marked with its source."""
    actor = _a(verdict="supported")
    actor.criteria = [_crit("auditable", "true"), _crit("typed", "true")]
    auditor = _a(verdict="not_supported")
    auditor.criteria = [_crit("auditable", "false", note="no read-side record")]
    merged = union_lens_merges(
        {"application-team-member": actor, "compliance-auditor": auditor},
        actor_lens="application-team-member")
    by_id = {c.id: c for c in merged.criteria}
    assert by_id["auditable"].satisfied == "false"
    assert by_id["auditable"].note == "per compliance-auditor — no read-side record"
    # typed's owner (platform-engineer) didn't run: actor answer ships, unmarked
    assert by_id["typed"].satisfied == "true"
    assert by_id["typed"].note == ""


def test_criteria_ownership_actor_fallback_then_any():
    """No owner lens and no actor answer → any answering lens ships, marked."""
    actor = _a()
    actor.criteria = []
    sre = _a()
    sre.criteria = [_crit("non_leaking", "unknown")]
    merged = union_lens_merges({"actor": actor, "sre": sre}, actor_lens="actor")
    assert merged.criteria[0].id == "non_leaking"
    assert merged.criteria[0].note == "per sre"


def test_criteria_ownership_absent_when_no_lens_answers():
    """Single-lens and criteria-free merges keep the actor vector untouched."""
    merged = union_lens_merges({"actor": _a()}, actor_lens="actor")
    assert not getattr(merged, "criteria", None)
