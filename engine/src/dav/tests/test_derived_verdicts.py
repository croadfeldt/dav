"""Derived verdicts (derived-verdicts-design.md): the engine judges, not the model.

Motivating measurement (2026-07-28): with the broken-chain verdict anchor
DELIVERED in-prompt (536 chars, logged), 0 of 6 ensemble samples on the two
chain UCs emitted not_supported — the model reports the evidence and refuses
the harsh verdict. And on one chain UC each sample described the same finding
with different free text, so nothing reached quorum: votes must key on FIXED
criterion ids, not free-text identity.

Assertions read consumer-visible output: the derived verdict, the merged
criteria vector with consensus, the round-tripped dict.
"""
import pytest

from dav.core.ensemble import merge_criteria_by_vote
from dav.core.use_case_schema import (
    CRITERION_ANSWERS,
    REFUSAL_CRITERIA,
    CriterionAnswer,
    build_analysis_json_schema,
)
from dav.core.verdict_rules import derive_verdict_from_criteria


def _c(cid, sat, ref="spec/x.md#s"):
    return CriterionAnswer(id=cid, satisfied=sat, spec_ref=ref)


class _S:  # minimal sample stand-in
    def __init__(self, criteria):
        self.criteria = criteria


# ── derivation table ─────────────────────────────────────────────────────────

def test_all_true_cited_derives_supported():
    v, ev = derive_verdict_from_criteria([_c(c, "true") for c in REFUSAL_CRITERIA])
    assert v == "supported"
    assert ev["true"] == sorted(REFUSAL_CRITERIA) and not ev["false"] and not ev["unknown"]


def test_any_false_derives_not_supported():
    crits = [_c(c, "true") for c in REFUSAL_CRITERIA[:-1]] + [_c("whole", "false", ref="")]
    v, ev = derive_verdict_from_criteria(crits)
    assert v == "not_supported"
    assert ev["false"] == ["whole"]


def test_unknowns_derive_partial_never_supported():
    crits = [_c("typed", "true"), _c("auditable", "unknown", ref="")]
    v, ev = derive_verdict_from_criteria(crits)
    assert v == "partially_supported"
    assert ev["unknown"] == ["auditable"]


def test_hedge_relocation_guard_all_unknown_is_partial():
    # If the model answers unknown to everything, the design fails on its own
    # terms — but the VERDICT must degrade to partial, never present as support.
    v, _ = derive_verdict_from_criteria([_c(c, "unknown", ref="") for c in REFUSAL_CRITERIA])
    assert v == "partially_supported"


def test_empty_vector_is_partial():
    v, _ = derive_verdict_from_criteria([])
    assert v == "partially_supported"


# ── the citation rule ────────────────────────────────────────────────────────

def test_uncited_true_coerces_to_unknown_and_blocks_support():
    crits = [_c(c, "true") for c in REFUSAL_CRITERIA[:-1]] + [_c("whole", "true", ref="")]
    v, ev = derive_verdict_from_criteria(crits)
    assert v == "partially_supported"          # the uncited yes cannot carry support
    assert "whole" in ev["unknown"]


def test_normalized_garbage_answer_is_unknown():
    assert CriterionAnswer(id="typed", satisfied="maybe").normalized().satisfied == "unknown"


# ── ensemble voting per criterion id ─────────────────────────────────────────

def test_majority_wins_and_consensus_recorded():
    samples = [_S([_c("typed", "true")]), _S([_c("typed", "true")]),
               _S([_c("typed", "false", ref="")])]
    merged = merge_criteria_by_vote(samples)
    assert len(merged) == 1
    m = merged[0]
    assert (m.id, m.satisfied, m.consensus) == ("typed", "true", "2/3")


def test_tie_resolves_to_unknown_never_false():
    samples = [_S([_c("whole", "true")]), _S([_c("whole", "false", ref="")])]
    m = merge_criteria_by_vote(samples)[0]
    assert m.satisfied == "unknown"


def test_votes_key_on_fixed_id_not_free_text():
    # Three samples, same criterion id, three different notes/refs — they are
    # ONE vote pool (the exact thing free-text gap identity failed at).
    samples = [_S([CriterionAnswer(id="auditable", satisfied="false", note=n)])
               for n in ("Dangling Reference Prevention",
                         "Dangling Reference Not Prevented",
                         "Refusal Cause Not Named")]
    merged = merge_criteria_by_vote(samples)
    assert len(merged) == 1
    assert (merged[0].satisfied, merged[0].consensus) == ("false", "3/3")


def test_no_criteria_yields_empty_merge():
    assert merge_criteria_by_vote([_S([]), _S([])]) == []


# ── schema + round trip ──────────────────────────────────────────────────────

def test_guided_schema_gates_on_flag():
    on = build_analysis_json_schema(include_criteria=True)
    off = build_analysis_json_schema()
    assert "criteria" in on["properties"] and "criteria" in on["required"]
    assert on["properties"]["criteria"]["items"]["properties"]["satisfied"]["enum"] == list(CRITERION_ANSWERS)
    assert "criteria" not in off["properties"] and "criteria" not in off["required"]


def test_round_trip_preserves_consensus():
    c = CriterionAnswer(id="typed", satisfied="true", spec_ref="spec/a.md#1",
                        consensus="3/3", note="n")
    assert CriterionAnswer.from_dict(c.to_dict()).to_dict() == c.to_dict()
