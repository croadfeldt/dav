"""Candidate evaluation + gate — Phase 2 of the self-improvement loop.

The heart of "always measure, never assume": given a baseline run and a
candidate run (same eval set, one config delta), score both and decide whether
the candidate may be promoted. The gate encodes the v1.9 lesson directly — a
change that improves the headline number but introduces a NEW high-severity
failure class does NOT promote.

Pure module: `score_run()` takes the same (summary, failures) inputs as the
failure taxonomy; `gate()` compares two scores. No DB, no I/O — unit-testable
against real run data. See docs/dav-self-improvement-vision.md §3 (Phase 2).
"""

from __future__ import annotations

from typing import Optional

from .failure_taxonomy import build_taxonomy

_SEV_RANK = {"high": 3, "medium": 2, "low": 1}


def score_run(summary: Optional[dict], failures: list[dict]) -> dict:
    """Score a run for A/B comparison.

    Returns {total, succeeded, failed, success_rate, signatures, worst_severity,
    high_sev_classes}. `success_rate` is the primary metric; the signature set
    is the guardrail dimension (a candidate must not introduce new high-severity
    failure classes even if its success_rate ties/wins).
    """
    tax = build_taxonomy(summary, failures)
    total = tax.get("total_ucs") or 0
    succeeded = tax.get("succeeded")
    if succeeded is None:
        succeeded = max(0, (total or 0) - (tax.get("failed") or 0))
    success_rate = (succeeded / total) if total else 0.0
    sigs = tax.get("signatures") or []
    high_sev = sorted({s["signature_class"] for s in sigs if s.get("severity") == "high"})
    worst = max((_SEV_RANK.get(s.get("severity"), 0) for s in sigs), default=0)
    worst_label = next((k for k, v in _SEV_RANK.items() if v == worst), None)
    return {
        "total": total,
        "succeeded": succeeded,
        "failed": tax.get("failed"),
        "success_rate": round(success_rate, 4),
        "signature_classes": sorted({s["signature_class"] for s in sigs}),
        "high_sev_classes": high_sev,
        "worst_severity": worst_label,
    }


def gate(baseline: dict, candidate: dict, *, min_delta: float = 0.0) -> dict:
    """Decide promote / revert / inconclusive from two scores.

    Rules (in order):
      1. REVERT if the candidate introduces a NEW high-severity failure class
         absent in the baseline — even if success_rate improved. (The v1.9
         lesson: never trade a headline gain for a new failure mode.)
      2. REVERT if candidate success_rate is meaningfully WORSE than baseline.
      3. PROMOTE if candidate success_rate is meaningfully BETTER and rule 1
         is clear.
      4. INCONCLUSIVE otherwise (a tie, or a change within `min_delta`) —
         "promote only on a real improvement" means a tie does NOT promote.

    `min_delta` is the success-rate margin that counts as "meaningful"
    (default 0.0 = any strict change; callers can require e.g. 0.05).
    """
    b_rate = baseline.get("success_rate", 0.0)
    c_rate = candidate.get("success_rate", 0.0)
    delta = round(c_rate - b_rate, 4)

    new_high = sorted(set(candidate.get("high_sev_classes") or [])
                      - set(baseline.get("high_sev_classes") or []))
    if new_high:
        return {
            "verdict": "revert",
            "reason": (f"candidate introduces new high-severity failure class(es): "
                       f"{', '.join(new_high)} — not promoting despite "
                       f"Δsuccess={delta:+.2%}"),
            "success_delta": delta,
            "new_high_sev": new_high,
        }
    if delta < -min_delta:
        return {
            "verdict": "revert",
            "reason": f"candidate success_rate worse ({b_rate:.2%} → {c_rate:.2%}, Δ={delta:+.2%})",
            "success_delta": delta,
            "new_high_sev": [],
        }
    if delta > min_delta:
        return {
            "verdict": "promote",
            "reason": (f"candidate improves success_rate ({b_rate:.2%} → {c_rate:.2%}, "
                       f"Δ={delta:+.2%}) with no new high-severity failure class"),
            "success_delta": delta,
            "new_high_sev": [],
        }
    return {
        "verdict": "inconclusive",
        "reason": (f"no meaningful change (success_rate {b_rate:.2%} → {c_rate:.2%}, "
                   f"Δ={delta:+.2%}); promote only on a real improvement"),
        "success_delta": delta,
        "new_high_sev": [],
    }
