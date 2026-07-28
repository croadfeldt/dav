"""Parity tests for app.fixture_scoring — the semantics contract with
fixtures/score.py, one test per rule that once produced a wrong number.

Every assertion reads the OUTPUT dict a consumer (the trend table, the gate)
reads — not helpers. Each rule-test is written so that deleting the rule from
score() fails the test (mutation-checked by hand while writing).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.fixture_scoring import score  # noqa: E402


def _exp(uc, verdict="supported", gaps=(), forbid=()):
    return {"uc": uc, "expected_verdict": verdict,
            "expected_gaps": [{"capability_id": g} for g in gaps],
            "must_not_report": [{"capability_id": f} for f in forbid]}


def _row(uc, verdict="supported", cap="", title=""):
    return {"uc_handle": uc, "verdict": verdict,
            "capability_id": cap, "title": title}


def test_clean_hit():
    r = score([_exp("a", "not_supported", gaps=["FIX-X"])],
              [_row("a", "not_supported", cap="FIX-X")])
    assert (r["tp"], r["fp"], r["fn"]) == (1, 0, 0)
    assert r["precision"] == 1.0 and r["recall"] == 1.0
    assert r["verdict_accuracy"] == 1.0


def test_absent_uc_counts_missed_not_free_pass():
    # The anti-vacuous-pass rule: a UC with no analysis row at all.
    r = score([_exp("a", gaps=["FIX-X", "FIX-Y"]), _exp("b")],
              [_row("b")])
    assert r["fn"] == 2                      # both expected gaps missed
    assert r["recall"] == 0.0                # NOT 1.0-on-what-ran
    assert r["verdict_total"] == 2           # absent UC still counted a verdict
    assert r["verdict_ok"] == 1
    nr = [d for d in r["detail"] if d["uc"] == "a"][0]
    assert nr["verdict"] == "NOT RUN" and nr["missed"] == ["FIX-X", "FIX-Y"]


def test_off_topic_seeded_hole_is_neutral():
    # FIX-Y is seeded on UC b; reported on UC a it is true-but-off-topic.
    r = score([_exp("a"), _exp("b", "not_supported", gaps=["FIX-Y"])],
              [_row("a", cap="FIX-Y"), _row("b", "not_supported", cap="FIX-Y")])
    assert r["fp"] == 0                      # neutral, not noise
    assert r["tp"] == 1                      # ...and not a hit either
    a = [d for d in r["detail"] if d["uc"] == "a"][0]
    assert a["off_topic_ok"] == ["FIX-Y"] and a["noise"] == []


def test_invented_id_is_fp():
    r = score([_exp("a")], [_row("a", cap="FIX-NEVER-SEEDED")])
    assert r["fp"] == 1
    assert r["precision"] == 0.0


def test_untagged_gap_is_fp():
    r = score([_exp("a")], [_row("a", title="some untagged finding")])
    assert r["fp"] == 1
    assert [d for d in r["detail"] if d["uc"] == "a"][0]["noise"] == \
        ["untagged:some untagged finding"]


def test_forbidden_is_fp_even_when_seeded_elsewhere():
    # Per-UC forbid scoping: FIX-Y is a real seeded hole on b, but control a
    # explicitly forbids it — reporting it there is the false positive the
    # control exists to catch, and the off-topic neutrality must NOT save it.
    r = score([_exp("a", forbid=["FIX-Y"]),
               _exp("b", "not_supported", gaps=["FIX-Y"])],
              [_row("a", cap="FIX-Y"), _row("b", "not_supported", cap="FIX-Y")])
    assert r["fp"] == 1
    assert "FIX-Y" in [d for d in r["detail"] if d["uc"] == "a"][0]["noise"]


def test_verdict_mismatch_counted():
    r = score([_exp("a", "not_supported")], [_row("a", "supported")])
    assert r["verdict_accuracy"] == 0.0
    d = r["detail"][0]
    assert d["verdict_ok"] is False and d["expected_verdict"] == "not_supported"


def test_empty_rows_all_missed():
    # Zero ingested rows must score 0 recall, never 1.0-by-vacuity.
    r = score([_exp("a", gaps=["FIX-X"])], [])
    assert r["recall"] == 0.0 and r["fn"] == 1 and r["verdict_total"] == 1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok {fn.__name__}")
    print(f"{len(fns)} passed")
