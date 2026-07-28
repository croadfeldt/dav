"""E1 gap-tagging post-pass: classify untagged gaps onto the catalog.

The measured motivation: id-recall 0.50 with the missed ids' substance present
as untagged findings (fixture battery, 2026-07-28). Classification is easier
than generation, so we ask it as its own question — enum-constrained, one call
per untagged gap, BEFORE the ensemble merge (merge keys on capability_id).

Assertions read what consumers read: the mutated analysis and the report.
"""
import json
from types import SimpleNamespace

from dav.ai.gap_tagger import NONE_SENTINEL, tag_untagged_gaps


class FakeClient:
    """Returns queued answers; records every request for schema assertions."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def chat(self, messages, temperature=None, max_tokens=None,
             guided_json_schema=None, seed=None, **kw):
        self.calls.append({"messages": messages, "temperature": temperature,
                           "schema": guided_json_schema, "seed": seed})
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return SimpleNamespace(content=json.dumps({"capability_id": a}))


def _gap(title, cap="", description="", rationale=""):
    return SimpleNamespace(title=title, capability_id=cap,
                           description=description, rationale=rationale)


def _analysis(*gaps):
    return SimpleNamespace(gaps_identified=list(gaps))


CATALOG = {"FIX-DEPS-001": "Dependent-member refusal on broken chain",
           "FIX-AUDIT-001": "Refusal audit record requirement"}


def test_untagged_gap_gets_classified():
    a = _analysis(_gap("Broken chain members not refused",
                       description="dependents realize against a refused parent"))
    c = FakeClient(["FIX-DEPS-001"])
    report = tag_untagged_gaps(a, CATALOG, c)
    assert a.gaps_identified[0].capability_id == "FIX-DEPS-001"
    assert report == {"attempted": 1, "tagged": 1, "none": 0, "failed": 0,
                      "tags": {"Broken chain members not refused": "FIX-DEPS-001"}}


def test_already_tagged_gap_untouched_and_uncharged():
    a = _analysis(_gap("tagged", cap="FIX-AUDIT-001"), _gap("untagged"))
    c = FakeClient(["FIX-DEPS-001"])
    report = tag_untagged_gaps(a, CATALOG, c)
    assert a.gaps_identified[0].capability_id == "FIX-AUDIT-001"
    assert len(c.calls) == 1                     # one call for one untagged gap
    assert report["attempted"] == 1


def test_none_answer_leaves_gap_untagged():
    a = _analysis(_gap("genuinely novel concern"))
    c = FakeClient([NONE_SENTINEL])
    report = tag_untagged_gaps(a, CATALOG, c)
    assert a.gaps_identified[0].capability_id == ""
    assert report["none"] == 1 and report["tagged"] == 0


def test_classify_failure_never_raises_and_leaves_untagged():
    a = _analysis(_gap("first"), _gap("second"))
    c = FakeClient([RuntimeError("inference down"), "FIX-AUDIT-001"])
    report = tag_untagged_gaps(a, CATALOG, c)
    assert a.gaps_identified[0].capability_id == ""       # failure → untagged
    assert a.gaps_identified[1].capability_id == "FIX-AUDIT-001"
    assert report == {"attempted": 2, "tagged": 1, "none": 0, "failed": 1,
                      "tags": {"second": "FIX-AUDIT-001"}}


def test_answer_grammar_is_catalog_plus_none():
    a = _analysis(_gap("x"))
    c = FakeClient([NONE_SENTINEL])
    tag_untagged_gaps(a, CATALOG, c)
    schema = c.calls[0]["schema"]
    assert sorted(schema["properties"]["capability_id"]["enum"]) == \
        sorted([*CATALOG, NONE_SENTINEL])
    assert c.calls[0]["temperature"] == 0.0


def test_off_catalog_answer_rejected():
    # An id outside the catalog (guided decoding should prevent it, but the
    # tagger must not trust the wire) stays untagged.
    a = _analysis(_gap("x"))
    c = FakeClient(["NOT-A-REAL-ID"])
    report = tag_untagged_gaps(a, CATALOG, c)
    assert a.gaps_identified[0].capability_id == ""
    assert report["none"] == 1


def test_empty_catalog_is_a_noop():
    a = _analysis(_gap("x"))
    c = FakeClient([])
    report = tag_untagged_gaps(a, {}, c)
    assert report["attempted"] == 0 and c.calls == []
