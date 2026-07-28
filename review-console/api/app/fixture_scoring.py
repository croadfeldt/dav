"""Server-side fixture scoring — the same semantics as fixtures/score.py.

Framework-free so tests run without fastapi. The semantics are a contract with
the repo-side scorer and MUST stay identical; every rule below exists because
its absence produced a wrong number this campaign:

  - a UC absent from the results counts its expected gaps as MISSED (a run
    where half the corpus failed once scored recall 1.00 on the half that ran);
  - a seeded hole reported under a DIFFERENT UC is off-topic-but-TRUE — neutral,
    never a false positive (the synthetic spec is small; cross-visibility is
    real);
  - untagged gaps count against precision (noise a reader must wade through,
    and the honest pressure toward catalog coverage);
  - ids in must_not_report count as false positives WHERE FORBIDDEN even if
    seeded elsewhere (per-UC scoping: "covered HERE" != "missing THERE").
"""
from __future__ import annotations


def score(expected: list[dict], rows: list[dict]) -> dict:
    by_uc: dict[str, dict] = {}
    for r in rows:
        u = by_uc.setdefault(r["uc_handle"], {"verdict": r.get("verdict") or "", "gaps": []})
        if r.get("capability_id") or r.get("title"):
            u["gaps"].append(r)

    all_seeded = {g["capability_id"]
                  for e in expected for g in (e.get("expected_gaps") or [])}

    tp = fp = fn = 0
    verdict_ok = verdict_total = 0
    detail = []

    for exp in sorted(expected, key=lambda e: e["uc"]):
        handle = exp["uc"]
        want = {g["capability_id"] for g in (exp.get("expected_gaps") or [])}
        forbid = {n["capability_id"] for n in (exp.get("must_not_report") or [])}
        got = by_uc.get(handle)

        if got is None:
            fn += len(want)
            verdict_total += 1
            detail.append({"uc": handle, "verdict": "NOT RUN", "verdict_ok": False,
                           "found": [], "missed": sorted(want), "noise": []})
            continue

        verdict_total += 1
        v_ok = got["verdict"] == exp["expected_verdict"]
        verdict_ok += int(v_ok)

        seen_ids = {g["capability_id"] for g in got["gaps"] if g.get("capability_id")}
        untagged = [g.get("title") or "?" for g in got["gaps"] if not g.get("capability_id")]

        hits = want & seen_ids
        missed = want - seen_ids
        forbidden_hits = forbid & seen_ids
        other = seen_ids - want - forbid
        off_topic = other & all_seeded
        invented = other - all_seeded

        tp += len(hits)
        fn += len(missed)
        fp += len(forbidden_hits) + len(invented) + len(untagged)

        detail.append({
            "uc": handle,
            "verdict": got["verdict"], "expected_verdict": exp["expected_verdict"],
            "verdict_ok": v_ok,
            "found": sorted(hits), "missed": sorted(missed),
            "noise": sorted(forbidden_hits) + sorted(invented)
                     + [f"untagged:{t}" for t in untagged],
            "off_topic_ok": sorted(off_topic),
        })

    return {"precision": round(tp / (tp + fp), 3) if (tp + fp) else 1.0,
            "recall": round(tp / (tp + fn), 3) if (tp + fn) else 1.0,
            "verdict_accuracy": round(verdict_ok / verdict_total, 3) if verdict_total else 0.0,
            "tp": tp, "fp": fp, "fn": fn,
            "verdict_ok": verdict_ok, "verdict_total": verdict_total,
            "detail": detail}
