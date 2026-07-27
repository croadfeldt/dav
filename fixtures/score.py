#!/usr/bin/env python3
"""Score a DAV run against the fixture ground truth.

Reports precision, recall and verdict accuracy — the three things DAV could not
measure while it was validated only against the live spec, where there is no
correct answer to compare to.

Precision matters most right now. The ensemble's union merge produced MORE gaps as
sample count rose, and with no false-positive measure that reads as thoroughness
rather than noise (see dav#80).

Usage:
    score.py --run-id <RID> [--db-pod <pod>] [--namespace dav] [--schema tenant_flightpath]
    score.py --gaps-json <file>      # offline: [{"uc_handle":..,"capability_id":..}, ...]

Exit code is 0 when every UC matches its expected verdict AND recall is 1.0 AND
precision is 1.0 — so this is usable as a gate, not only as a report.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required")

HERE = pathlib.Path(__file__).resolve().parent
EXPECTED = HERE / "expected"


def load_expected() -> dict:
    out = {}
    for f in sorted(EXPECTED.glob("*.yaml")):
        d = yaml.safe_load(f.read_text())
        out[d["uc"]] = d
    if not out:
        sys.exit(f"no expectations found in {EXPECTED}")
    return out


def fetch_from_db(run_id: str, pod: str, ns: str, schema: str) -> list[dict]:
    """Pull (uc_handle, capability_id, verdict) rows for a run."""
    sql = (
        f"SET search_path={schema},public; "
        "SELECT a.uc_handle||E'\\t'||coalesce(a.verdict,'')||E'\\t'"
        "||coalesce(g.catalog_capability_id::text,'')||E'\\t'||coalesce(g.title,'') "
        "FROM uc_analyses a LEFT JOIN uc_gaps g ON g.analysis_id=a.id "
        f"WHERE a.run_id='{run_id}' ORDER BY 1;"
    )
    proc = subprocess.run(
        ["oc", "-n", ns, "exec", pod, "--", "bash", "-lc",
         f"echo {json.dumps(sql)} | psql -U postgres -d dav_review -tA"],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        sys.exit(f"psql failed: {proc.stderr[:300]}")
    rows = []
    for line in proc.stdout.splitlines():
        if not line.strip() or line.strip() == "SET":
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        rows.append({"uc_handle": parts[0], "verdict": parts[1],
                     "capability_id": parts[2], "title": parts[3]})
    return rows


def score(expected: dict, rows: list[dict]) -> dict:
    by_uc: dict[str, dict] = {}
    for r in rows:
        u = by_uc.setdefault(r["uc_handle"], {"verdict": r["verdict"], "gaps": []})
        if r["capability_id"] or r["title"]:
            u["gaps"].append(r)

    tp = fp = fn = 0
    verdict_ok = verdict_total = 0
    detail = []

    for handle, exp in sorted(expected.items()):
        got = by_uc.get(handle)
        if got is None:
            # A UC that did not produce an analysis is NOT a free pass. Skipping it
            # would let a run where half the corpus failed score recall 1.00 on the
            # half that worked — the same vacuous-pass shape that made an empty
            # determinism diff read as six SAME rows.
            want_absent = {g["capability_id"] for g in (exp.get("expected_gaps") or [])}
            fn += len(want_absent)
            verdict_total += 1
            detail.append((handle, "*** NOT RUN — counted as missed ***",
                           [], sorted(want_absent), []))
            continue

        verdict_total += 1
        v_ok = got["verdict"] == exp["expected_verdict"]
        verdict_ok += int(v_ok)

        want = {g["capability_id"] for g in (exp.get("expected_gaps") or [])}
        forbid = {n["capability_id"] for n in (exp.get("must_not_report") or [])}
        # A gap with no catalog id cannot be matched by id. It is not a hit, and it
        # IS noise the consumer has to read, so it counts against precision — which
        # is also the honest pressure toward catalog coverage.
        seen_ids = {g["capability_id"] for g in got["gaps"] if g["capability_id"]}
        untagged = [g["title"] for g in got["gaps"] if not g["capability_id"]]

        hits = want & seen_ids
        missed = want - seen_ids
        forbidden_hits = forbid & seen_ids
        other = seen_ids - want - forbid

        tp += len(hits)
        fn += len(missed)
        fp += len(forbidden_hits) + len(other) + len(untagged)

        vtxt = got["verdict"] if v_ok else f"{got['verdict']} (want {exp['expected_verdict']})"
        detail.append((handle, vtxt,
                       sorted(hits), sorted(missed),
                       sorted(forbidden_hits) + sorted(other) + [f"untagged:{t}" for t in untagged]))

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return {"precision": precision, "recall": recall,
            "verdict_accuracy": (verdict_ok / verdict_total) if verdict_total else 0.0,
            "tp": tp, "fp": fp, "fn": fn,
            "verdict_ok": verdict_ok, "verdict_total": verdict_total,
            "detail": detail}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id")
    ap.add_argument("--gaps-json")
    ap.add_argument("--db-pod", default="dav-review-db-857446c6db-jjfjc")
    ap.add_argument("--namespace", default="dav")
    ap.add_argument("--schema", default="tenant_flightpath")
    ap.add_argument("--gate", action="store_true",
                    help="exit non-zero unless precision=recall=verdict_accuracy=1.0")
    a = ap.parse_args()

    expected = load_expected()
    if a.gaps_json:
        rows = json.loads(pathlib.Path(a.gaps_json).read_text())
    elif a.run_id:
        rows = fetch_from_db(a.run_id, a.db_pod, a.namespace, a.schema)
    else:
        return ap.error("need --run-id or --gaps-json")

    s = score(expected, rows)
    print(f"  precision        {s['precision']:.2f}   ({s['tp']} true / {s['fp']} false positives)")
    print(f"  recall           {s['recall']:.2f}   ({s['tp']} found / {s['fn']} seeded holes missed)")
    print(f"  verdict accuracy {s['verdict_accuracy']:.2f}   ({s['verdict_ok']}/{s['verdict_total']})")
    print()
    for handle, verdict, hits, missed, noise in s["detail"]:
        print(f"  {handle}")
        print(f"      verdict: {verdict}")
        if hits:   print(f"      found:   {', '.join(hits)}")
        if missed: print(f"      MISSED:  {', '.join(missed)}")
        if noise:  print(f"      NOISE:   {', '.join(noise[:6])}")
    if a.gate:
        perfect = s["precision"] == 1.0 and s["recall"] == 1.0 and s["verdict_accuracy"] == 1.0
        return 0 if perfect else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
