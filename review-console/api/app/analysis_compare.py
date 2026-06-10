"""Semantic A/B comparison of two runs' Stage-2 analyses.

Backport of the static `compare_analyses` tool into the experiments framework: reuses
the ENGINE's pure comparator (`app/_vendor/compare.py`, materialized from
`engine/src/dav/evaluator/compare.py` at image-build time — single source, no fork) to
diff two runs' per-UC analyses into equivalent/changed + per-finding severity.

Server-side by design: the analyses reside on the run-workspace PVC (read via
`results.get_analysis`); only the computed diff crosses to the browser, so raw
(potentially client-confidential) analyses never leave the cluster. See
docs/prompt-management-design.md (A/B section).
"""
from __future__ import annotations

import logging
from typing import Optional

from . import results as _results

log = logging.getLogger("dav.analysis_compare")

try:  # materialized into the image at build time; absent in a bare checkout
    from ._vendor.compare import compare as _compare
except Exception:  # pragma: no cover
    _compare = None

_SEV_ORDER = {"": 0, "trivial": 1, "minor": 2, "major": 3, "critical": 4}


def available() -> bool:
    return _compare is not None


def _analysis_for(run_id: str, uc_uuid: str) -> Optional[dict]:
    """The comparable analysis dict for a UC in a run. Explore-mode runs (multiple
    samples) collapse to the first sample — the comparator wants one Analysis dict."""
    a = _results.get_analysis(run_id, uc_uuid)
    if not a:
        return None
    if a.get("_source") == "explore":
        samples = a.get("samples") or []
        return samples[0] if samples else None
    return a


def compare_runs(run_a: str, run_b: str, uc_uuids: list[str]) -> dict:
    """Compare two runs' analyses over the given UCs. Returns a summary + per-UC pairs.
    Pure read + compute; no mutation."""
    if _compare is None:
        raise RuntimeError("analysis comparator unavailable (vendored module missing)")
    pairs = []
    summary = {
        "compared": 0, "changed": 0, "equivalent": 0, "missing": 0,
        "by_severity": {"critical": 0, "major": 0, "minor": 0, "": 0},
    }
    for u in uc_uuids:
        aa, ab = _analysis_for(run_a, u), _analysis_for(run_b, u)
        if aa is None or ab is None:
            summary["missing"] += 1
            pairs.append({"uc_uuid": u, "verdict": "missing"})
            continue
        try:
            res = _compare(aa, ab)
        except Exception as e:  # never let one bad analysis kill the whole compare
            log.warning("compare failed for uc %s: %s", u, e)
            summary["missing"] += 1
            pairs.append({"uc_uuid": u, "verdict": "error", "error": str(e)})
            continue
        summary["compared"] += 1
        ms = res.max_severity
        if res.verdict == "changed":
            summary["changed"] += 1
        else:
            summary["equivalent"] += 1
        summary["by_severity"][ms] = summary["by_severity"].get(ms, 0) + 1
        pairs.append({
            "uc_uuid": u, "verdict": res.verdict, "max_severity": ms,
            "findings": [
                {"severity": f.severity, "field": f.field, "description": f.description}
                for f in res.findings
            ],
        })
    summary["max_severity"] = max(
        (p.get("max_severity", "") for p in pairs if p.get("verdict") == "changed"),
        key=lambda s: _SEV_ORDER.get(s, 0), default="")
    summary["verdict"] = "changed" if summary["changed"] > 0 else "equivalent"
    return {"run_a": run_a, "run_b": run_b, "summary": summary, "pairs": pairs}
