"""Tests for server-side run-selection enforcement (app.run_selection).

Repro this guards against: run dav-stage2-console-853521 — POST /api/runs with
{"mode":"verification","selection_mode":"set","set_id":29} and no UC lists
executed the whole corpus (run-corpus "Total: 420 UC(s)") because set_id /
selection_mode were lineage-only and never constrained the pipeline.

Run directly: `python test_run_selection.py` (from review-console/api), or via pytest.
"""

import sys

from app.run_selection import (
    PASS, REJECT, RESOLVE_SET, member_filter, selection_action,
)

_failures: list[str] = []


def eq(actual, expected, label):
    if actual != expected:
        _failures.append(f"{label}: got {actual!r}, expected {expected!r}")


def act(selection_mode, set_id, *, is_all=False, explicit=False):
    return selection_action(selection_mode, set_id,
                            is_all_set=is_all, has_explicit_selection=explicit)


def test_853521_shape_resolves_set():
    # The exact repro shape: selection_mode='set', real set_id, no UC lists.
    eq(act("set", 29), RESOLVE_SET, "set + set_id + no lists -> resolve")
    # set_id arrives as str via JSON too (RunTriggerIn allows int|str)
    eq(act("set", "29"), RESOLVE_SET, "string set_id -> resolve")


def test_bare_set_id_without_mode_resolves():
    eq(act(None, 29), RESOLVE_SET, "set_id alone -> resolve")


def test_explicit_lists_pass_through_unchanged():
    # The UI path: set lineage + client-resolved lists. Must not re-resolve.
    eq(act("set", 29, explicit=True), PASS, "set + explicit lists -> pass")
    eq(act("selection", None, explicit=True), PASS, "selection + lists -> pass")
    eq(act("individual", None, explicit=True), PASS, "individual + lists -> pass")


def test_full_corpus_stays_full_corpus():
    eq(act(None, None), PASS, "no selection fields -> pass (full corpus)")
    eq(act("corpus", None), PASS, "selection_mode=corpus -> pass")
    eq(act("corpus", "__all__", is_all=True), PASS, "corpus + __all__ -> pass")
    eq(act("set", "__all__", is_all=True), PASS, "set + __all__ sentinel -> pass")


def test_narrowed_scope_without_anything_to_run_rejects():
    eq(act("set", None), REJECT, "set mode without set_id -> reject")
    eq(act("selection", None), REJECT, "selection without lists -> reject")
    eq(act("individual", None), REJECT, "individual without lists -> reject")


def test_member_filter_mirrors_ui_mapping():
    members = [
        {"uc_uuid": "u-managed", "uc_source": "managed", "uc_handle": "h-ignored"},
        {"uc_uuid": "u-corpus-1", "uc_source": "corpus", "uc_handle": "RFC-0001"},
        {"uc_uuid": "u-corpus-2", "uc_source": "corpus", "uc_handle": None},
    ]
    out = member_filter(members)
    eq(out["managed_uc_uuids"], ["u-managed"], "managed member -> managed_uc_uuids")
    eq(out["uc_handles"], ["RFC-0001"], "corpus member with handle -> uc_handles")
    eq(out["uc_uuids"], ["u-corpus-2"], "corpus member without handle -> uc_uuids")


def test_member_filter_empty_and_degenerate_rows():
    eq(member_filter([]), {"uc_handles": [], "uc_uuids": [], "managed_uc_uuids": []},
       "empty set -> all-empty filter (caller must 400, not full-corpus)")
    out = member_filter([{"uc_source": "managed"}, {"uc_source": "corpus"}])
    eq(any(out.values()), False, "rows without uuid/handle contribute nothing")


def main():
    tests = [
        test_853521_shape_resolves_set,
        test_bare_set_id_without_mode_resolves,
        test_explicit_lists_pass_through_unchanged,
        test_full_corpus_stays_full_corpus,
        test_narrowed_scope_without_anything_to_run_rejects,
        test_member_filter_mirrors_ui_mapping,
        test_member_filter_empty_and_degenerate_rows,
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
