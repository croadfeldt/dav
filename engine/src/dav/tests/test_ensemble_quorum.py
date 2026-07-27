"""The ensemble must not make verdicts worse as sample count grows.

`_consolidate_gaps` merges gaps by UNION — a gap found by any one sample enters
the merged set. `derive_verdict` is downgrade-only. Feeding the whole union to it
made the verdict a function of N: since P(at least one sample finds a gap) rises
with sample count, verdicts weakened monotonically until everything read
`partially_supported` regardless of the spec or the model.

Measured on the six must-reject UCs before this fix, same model and prompts:

    gpt-oss n=1    4 gaps    5 of 6 `supported`
    gpt-oss n=3   20 gaps    0 of 6 `supported`
    Qwen3-32B n=1 12 gaps
    Qwen3-32B n=3 23 gaps

Both models returned all-`partially_supported` at n=3, which erased the model
difference the per-stage routing design (#73) was built on. That difference was
an n=1 artifact of this bias, not a property of the models.

The agreement was already computed (`gap_consensus`) and attached to the merged
Analysis — it was simply never used to filter, and never persisted. These tests
pin that it now gates the derivation, and that sub-quorum gaps SURVIVE: they are
often a real finding the other samples missed and are exactly what the capability
catalog needs, so they must stay visible while not voting.
"""
import pytest

from dav.core.ensemble import _consolidate_gaps, merge_analyses
from dav.core.use_case_schema import Analysis, GapIdentified
from dav.tests.test_ensemble import _analysis as _full_analysis


def _gap(title, cap="", consensus=""):
    return GapIdentified(
        title=title, description=f"{title} description",
        severity="major", confidence="high",
        rationale="r", recommendation="rec",
        spec_refs_consulted=[], spec_refs_missing=[],
        capability_id=cap, consensus=consensus,
    )


# --- the quorum rule itself -------------------------------------------------

@pytest.mark.parametrize("consensus,expected", [
    ("3/3", True),    # unanimous
    ("2/3", True),    # majority
    ("1/3", False),   # one sample out of three — must not move a verdict
    ("2/4", True),    # tie counts as agreement (never resolves against the spec)
    ("1/4", False),
    ("1/1", True),    # single sample: nothing to disagree with
    ("", True),       # not an ensemble analysis
])
def test_quorum_rule(consensus, expected):
    assert _gap("g", consensus=consensus).quorum_backed is expected


def test_unparseable_consensus_fails_open():
    """A malformed value must never silently delete a finding — fail toward
    keeping it, and let the visible gap prompt the fix."""
    for bad in ("garbage", "3/0", "x/y", "3/"):
        assert _gap("g", consensus=bad).quorum_backed is True


# --- consolidation stamps agreement on the gap ------------------------------

def _analysis(uc, gaps):
    a = Analysis.__new__(Analysis)          # bypass required-field ctor
    a.use_case_uuid = uc
    a.gaps_identified = gaps
    return a


def test_consolidate_stamps_consensus_on_each_gap():
    """Previously the agreement lived only in a side-channel dict, so nothing
    downstream — verdict derivation, ingest, UI — could act on it."""
    samples = [
        _analysis("uc-1", [_gap("Missing audit record", cap="CMP-001")]),
        _analysis("uc-1", [_gap("Missing audit record", cap="CMP-001")]),
        _analysis("uc-1", [_gap("Something only one sample saw")]),
    ]
    merged, consensus = _consolidate_gaps(samples, 3)
    by_title = {g.title: g for g in merged}
    assert by_title["Missing audit record"].consensus == "2/3"
    assert by_title["Something only one sample saw"].consensus == "1/3"
    assert consensus, "side-channel consensus dict still populated"


def test_sub_quorum_gaps_survive_the_merge():
    """They must remain visible for roadmap + catalog work. Dropping them would
    lose real findings that only one sample surfaced."""
    samples = [
        _analysis("uc-1", [_gap("seen once")]),
        _analysis("uc-1", []),
        _analysis("uc-1", []),
    ]
    merged, _ = _consolidate_gaps(samples, 3)
    assert [g.title for g in merged] == ["seen once"]
    assert merged[0].quorum_backed is False, "kept, but must not vote"


def test_more_samples_does_not_add_voting_gaps_for_the_same_evidence():
    """The regression this whole change exists for: a single dissenting sample
    must not tip a verdict just because N grew."""
    unanimous = [_analysis("uc-1", [_gap("real", cap="CMP-001")]) for _ in range(3)]
    one_off = unanimous[:2] + [_analysis("uc-1", [
        _gap("real", cap="CMP-001"), _gap("noise from one sample")])]

    def voting(samples, n):
        merged, _ = _consolidate_gaps(samples, n)
        return [g.title for g in merged if g.quorum_backed]

    assert voting(unanimous, 3) == ["real"]
    assert voting(one_off, 3) == ["real"], "sub-quorum noise entered the vote"


# --- the property the whole change exists for -------------------------------
#
# The tests above exercise _consolidate_gaps and quorum_backed in isolation, and
# a mutation that removed the quorum filter from the DERIVATION still passed
# them: the helper was never the bug, the call site was. These drive the real
# merge_analyses path and assert the verdict.

def _sample(verdict, gaps):
    return _full_analysis(uuid="uc-inv-001", verdict=verdict, gaps=gaps)


def test_verdict_is_invariant_under_sample_count():
    """The decisive acceptance test from docs/derived-verdicts-design.md.

    The same evidence, sampled more times, must not produce a worse verdict.
    Before the fix, one dissenting sample's gap entered the union and the
    downgrade-only derivation flipped `supported` to `partially_supported` —
    so the answer depended on how many samples you happened to take.
    """
    clean = _sample("supported", [])
    dissent = _sample("supported", [_gap("only one sample saw this")])

    v1 = merge_analyses([clean]).summary.verdict
    v3 = merge_analyses([clean, clean, dissent]).summary.verdict
    v5 = merge_analyses([clean, clean, clean, clean, dissent]).summary.verdict

    assert v1 == v3 == v5, (
        f"verdict moved with sample count: n=1 {v1}, n=3 {v3}, n=5 {v5}")


def test_quorum_backed_gap_still_downgrades():
    """The filter must not defang the derivation — evidence a majority agrees on
    has to keep moving the verdict, or this trades one bug for a worse one."""
    real = _gap("majority agrees this is missing", cap="CMP-001")
    samples = [_sample("supported", [real]) for _ in range(3)]
    merged = merge_analyses(samples)
    assert merged.summary.verdict != "supported", (
        "a 3/3 gap no longer downgrades — quorum filter is too aggressive")


def test_sub_quorum_gap_is_reported_but_does_not_vote():
    """Both halves of the contract in one assertion: kept for the roadmap,
    excluded from the verdict."""
    clean = _sample("supported", [])
    dissent = _sample("supported", [_gap("seen once")])
    merged = merge_analyses([clean, clean, dissent])

    titles = [g.title for g in merged.gaps_identified]
    assert "seen once" in titles, "sub-quorum finding was dropped, not just muted"
    assert merged.summary.verdict == "supported", "sub-quorum finding voted anyway"
