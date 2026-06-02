"""Tests for UC priority projection (spec 05 §6.8 / DCM feature #1).

Run directly: `python test_uc_priority.py` (from review-console/api), or via pytest.
Mirrors the engine's priority tests but for the console-side projection that
feeds the priority/priority_score columns and roadmap sorting.
"""

import sys

from app import uc_priority as up

_failures: list[str] = []


def eq(actual, expected, label):
    if actual != expected:
        _failures.append(f"{label}: got {actual!r}, expected {expected!r}")


def raises(fn, label):
    try:
        fn()
    except ValueError:
        return
    except Exception as e:
        _failures.append(f"{label}: expected ValueError, got {type(e).__name__}: {e}")
        return
    _failures.append(f"{label}: expected ValueError, none raised")


def test_shorthand_labels():
    eq(up.normalize_priority("high"), ("high", 70), "shorthand high")
    eq(up.normalize_priority("critical"), ("critical", 90), "shorthand critical")
    eq(up.normalize_priority("medium"), ("medium", 50), "shorthand medium")
    eq(up.normalize_priority("low"), ("low", 20), "shorthand low")


def test_case_and_whitespace():
    eq(up.normalize_priority("  High "), ("high", 70), "case/whitespace insensitive")


def test_none_is_unranked():
    eq(up.normalize_priority(None), (None, None), "None -> unranked")


def test_nested_with_score_override():
    eq(up.normalize_priority({"label": "critical", "score": 95}), ("critical", 95), "nested override")
    # default score when only label given
    eq(up.normalize_priority({"label": "high"}), ("high", 70), "nested default score")


def test_rejects_bad_label():
    raises(lambda: up.normalize_priority("urgent"), "invalid label")
    raises(lambda: up.normalize_priority("major"), "no severity aliases")
    raises(lambda: up.normalize_priority({"label": 3}), "non-string label")


def test_rejects_out_of_band_score():
    raises(lambda: up.normalize_priority({"label": "high", "score": 50}), "score below band")
    raises(lambda: up.normalize_priority({"label": "low", "score": 70}), "score above band")


def test_rejects_non_int_score():
    raises(lambda: up.normalize_priority({"label": "high", "score": "70"}), "string score")
    raises(lambda: up.normalize_priority({"label": "high", "score": True}), "bool score rejected")


def test_derive_is_tolerant():
    # derive never raises — bad priority just projects as unranked
    eq(up.derive_priority({"priority": "bogus"}), (None, None), "bad priority -> (None, None)")
    eq(up.derive_priority({}), (None, None), "absent priority -> (None, None)")
    eq(up.derive_priority({"priority": "high"}), ("high", 70), "good priority derived")


def main():
    tests = [
        test_shorthand_labels,
        test_case_and_whitespace,
        test_none_is_unranked,
        test_nested_with_score_override,
        test_rejects_bad_label,
        test_rejects_out_of_band_score,
        test_rejects_non_int_score,
        test_derive_is_tolerant,
    ]
    for t in tests:
        t()
    if _failures:
        print(f"FAIL: {len(_failures)} assertion(s) failed:")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"OK: {len(tests)} tests passed")


if __name__ == "__main__":
    main()
