"""Cross-UC capability demand density (DCM feature #2).

Pure aggregation over the rows projected into `uc_capabilities` during ingest.
Given the capabilities each UC demands, answer "which capabilities does the most
UCs need?" — the density-of-need signal the DCM team wants for prioritization
("capability X demanded by 8/15 UCs"). Kept dependency-free so it's unit-testable
without a DB (see test_capability_density.py); the API layer just feeds it rows.
"""

from __future__ import annotations

from typing import Optional


def aggregate_density(rows, total_ucs: int) -> list[dict]:
    """Aggregate per-UC capability rows into cross-UC demand density.

    `rows`: iterable of dicts with at least `capability_id` and `uc_uuid`, and
    optionally `confidence_score` (int|None) and `namespace` (str|None). A UC
    counted once per capability even if it appears multiple times (callers
    should already dedup at ingest, but we defend here too).

    `total_ucs`: denominator — the number of UCs in scope (run or set), used for
    the demand ratio. A capability can be demanded by at most this many UCs.

    Returns a list sorted by demand (most UCs first, ties broken by capability
    id) where each item is:
        {capability_id, uc_count, total_ucs, demand_ratio,
         uc_uuids: [...], namespaces: [...], avg_confidence: float|None}
    """
    by_cap: dict[str, dict] = {}
    for r in rows:
        cap_id = r.get("capability_id")
        uc_uuid = r.get("uc_uuid")
        if not cap_id or not uc_uuid:
            continue
        entry = by_cap.setdefault(cap_id, {
            "capability_id": cap_id,
            "uc_uuids": set(),
            "namespaces": set(),
            "_conf_sum": 0,
            "_conf_n": 0,
        })
        entry["uc_uuids"].add(uc_uuid)
        ns = r.get("namespace")
        if ns:
            entry["namespaces"].add(ns)
        score = r.get("confidence_score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            entry["_conf_sum"] += score
            entry["_conf_n"] += 1

    out = []
    for entry in by_cap.values():
        uc_count = len(entry["uc_uuids"])
        avg_conf = (entry["_conf_sum"] / entry["_conf_n"]) if entry["_conf_n"] else None
        out.append({
            "capability_id": entry["capability_id"],
            "uc_count": uc_count,
            "total_ucs": total_ucs,
            # Guard against a zero/empty denominator so the API never divides by 0.
            "demand_ratio": (uc_count / total_ucs) if total_ucs else 0.0,
            "uc_uuids": sorted(entry["uc_uuids"]),
            "namespaces": sorted(entry["namespaces"]),
            "avg_confidence": round(avg_conf, 1) if avg_conf is not None else None,
        })
    # Most-demanded first; stable tiebreak on id keeps output deterministic.
    out.sort(key=lambda d: (-d["uc_count"], d["capability_id"]))
    return out
