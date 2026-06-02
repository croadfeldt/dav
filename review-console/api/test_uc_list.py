"""Tests for UC list de-duplication (same uuid across corpus paths / sources).

Run directly: `python test_uc_list.py` (from review-console/api), or via pytest.
"""

import sys

from app.uc_list import collapse_duplicates

_failures: list[str] = []


def eq(actual, expected, label):
    if actual != expected:
        _failures.append(f"{label}: got {actual!r}, expected {expected!r}")


def _by_uuid(rows):
    return {r["uuid"]: r for r in rows}


def test_corpus_same_uuid_multiple_paths_collapses():
    corpus = [
        {"uuid": "uc-1", "source": "corpus", "path": "dcm/a.yaml"},
        {"uuid": "uc-1", "source": "corpus", "path": "udlm/a.yaml"},
        {"uuid": "uc-1", "source": "corpus", "path": "koku/a.yaml"},
    ]
    out = collapse_duplicates([], corpus)
    eq(len(out), 1, "three corpus paths -> one row")
    row = out[0]
    eq(row["path_count"], 3, "path_count counts all corpus paths")
    eq(row["namespaces"], ["dcm", "koku", "udlm"], "namespaces collected + sorted")
    eq(row["paths"], ["dcm/a.yaml", "koku/a.yaml", "udlm/a.yaml"], "paths sorted")


def test_managed_wins_over_corpus_same_uuid():
    managed = [{"uuid": "uc-1", "source": "managed", "title": "Managed title"}]
    corpus = [
        {"uuid": "uc-1", "source": "corpus", "path": "dcm/a.yaml"},
        {"uuid": "uc-1", "source": "corpus", "path": "udlm/a.yaml"},
    ]
    out = collapse_duplicates(managed, corpus)
    eq(len(out), 1, "managed + corpus same uuid -> one row")
    row = out[0]
    eq(row["source"], "managed", "managed copy preferred")
    eq(row["title"], "Managed title", "managed fields preserved")
    eq(row["path_count"], 2, "managed row surfaces corpus copy count")
    eq(row["namespaces"], ["dcm", "udlm"], "managed row carries corpus namespaces")


def test_distinct_uuids_all_kept():
    managed = [{"uuid": "uc-1", "source": "managed"}]
    corpus = [
        {"uuid": "uc-2", "source": "corpus", "path": "dcm/b.yaml"},
        {"uuid": "uc-3", "source": "corpus", "path": "dcm/c.yaml"},
    ]
    out = collapse_duplicates(managed, corpus)
    eq(len(out), 3, "distinct uuids preserved")
    eq(_by_uuid(out)["uc-1"]["path_count"], 0, "managed-only path_count is 0")
    eq(_by_uuid(out)["uc-2"]["path_count"], 1, "single-path corpus path_count is 1")


def test_managed_first_then_corpus_only():
    managed = [{"uuid": "uc-1", "source": "managed"}]
    corpus = [{"uuid": "uc-2", "source": "corpus", "path": "dcm/b.yaml"}]
    out = collapse_duplicates(managed, corpus)
    eq([r["uuid"] for r in out], ["uc-1", "uc-2"], "managed before corpus-only")


def test_duplicate_path_not_double_counted():
    # Defensive: identical path twice (shouldn't happen — path is a PK) counts once.
    corpus = [
        {"uuid": "uc-1", "source": "corpus", "path": "dcm/a.yaml"},
        {"uuid": "uc-1", "source": "corpus", "path": "dcm/a.yaml"},
    ]
    out = collapse_duplicates([], corpus)
    eq(out[0]["path_count"], 1, "identical path counted once")


def test_rows_without_uuid_skipped():
    corpus = [{"source": "corpus", "path": "dcm/x.yaml"}]  # no uuid
    eq(collapse_duplicates([], corpus), [], "uuid-less corpus row dropped")


def main():
    tests = [
        test_corpus_same_uuid_multiple_paths_collapses,
        test_managed_wins_over_corpus_same_uuid,
        test_distinct_uuids_all_kept,
        test_managed_first_then_corpus_only,
        test_duplicate_path_not_double_counted,
        test_rows_without_uuid_skipped,
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
