"""UC list de-duplication for the console.

The UC list is built from managed_use_cases (unique by uuid) plus one row per
corpus *file* — and the files table is keyed by path, so the same UC uuid can
appear at several corpus paths (multi-source corpus syncs a UC under multiple
namespace roots) and/or also exist as a managed row. Rendering one row per
occurrence shows the same UC several times.

collapse_duplicates() folds those into one row per uuid — managed wins (it's the
authoritative, editable copy) — while surfacing how many corpus paths carry the
uuid (path_count) and which namespaces, so the duplication is visible, not hidden.

Pure + dependency-free for unit testing (see test_uc_list.py).
"""

from __future__ import annotations


def _namespace_of(path) -> str:
    """First path segment is the corpus namespace (e.g. 'dcm/foo/bar.yaml' -> 'dcm')."""
    if not isinstance(path, str):
        return ""
    return path.split("/", 1)[0].strip()


def collapse_duplicates(managed: list, corpus: list) -> list:
    """Merge managed + corpus UC rows into one row per uuid.

    - managed rows are kept as-is (already unique by uuid) and preferred when a
      uuid exists in both sources.
    - corpus rows sharing a uuid collapse into one; `paths`/`namespaces` collect
      every path/namespace and `path_count` is how many corpus paths carry it.
    - every returned row gains `path_count` (corpus paths for this uuid; 0 for a
      managed-only UC), plus `paths` and `namespaces` when any corpus copy exists.

    Order: managed rows first (input order preserved), then corpus-only uuids in
    first-seen order — same shape the caller sorted/grouped before.
    """
    corpus_by_uuid: dict[str, dict] = {}
    for c in corpus:
        u = c.get("uuid")
        if not u:
            continue
        path = c.get("path")
        ns = _namespace_of(path)
        agg = corpus_by_uuid.get(u)
        if agg is None:
            agg = dict(c)
            agg["paths"] = [path] if path else []
            agg["namespaces"] = [ns] if ns else []
            corpus_by_uuid[u] = agg
        else:
            if path and path not in agg["paths"]:
                agg["paths"].append(path)
            if ns and ns not in agg["namespaces"]:
                agg["namespaces"].append(ns)
    for agg in corpus_by_uuid.values():
        agg["paths"].sort()
        agg["namespaces"].sort()
        agg["path_count"] = len(agg["paths"])

    managed_uuids = {m.get("uuid") for m in managed}
    merged: list = []
    for m in managed:
        row = dict(m)
        c = corpus_by_uuid.get(m.get("uuid"))
        # For a managed UC, path_count is how many corpus copies also exist.
        row["path_count"] = c["path_count"] if c else 0
        if c:
            row["paths"] = c["paths"]
            row["namespaces"] = c["namespaces"]
        merged.append(row)
    for u, agg in corpus_by_uuid.items():
        if u not in managed_uuids:
            merged.append(agg)
    return merged
