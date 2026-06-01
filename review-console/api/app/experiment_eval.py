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


_EXPLORATION_KEYS = (
    "distinct_gaps", "total_gaps", "mean_gaps_per_uc", "ucs_with_gaps", "consistency",
)

# #45b: grounding-density signal (from results.get_run_shallowness) — the lever the
# grounding nudge targets. mean_distinct_spec_refs ↑ and shallow_fraction ↓ are the
# improvements; advisory like exploration (counts can be gamed by hallucination).
_GROUNDING_KEYS = (
    "mean_distinct_spec_refs", "shallow_fraction", "ucs_shallow", "ucs_eligible",
)


def score_run(summary: Optional[dict], failures: list[dict],
              exploration: Optional[dict] = None,
              grounding: Optional[dict] = None) -> dict:
    """Score a run for A/B comparison.

    Returns {total, succeeded, failed, success_rate, signatures, worst_severity,
    high_sev_classes} and — when `exploration` is supplied (from
    results.get_run_exploration) — an `exploration` block. `success_rate` is the
    primary metric; the signature set is the guardrail dimension (a candidate
    must not introduce new high-severity failure classes even if its success_rate
    ties/wins). Exploration depth/consistency is a SECONDARY dimension (the lever
    the 72B eval isolated): measured and surfaced, but not a promote trigger on
    its own — raw gap counts can be inflated by hallucination, so the gate only
    acts on it when an operator opts in via `exploration_min_delta`.
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
    score = {
        "total": total,
        "succeeded": succeeded,
        "failed": tax.get("failed"),
        "success_rate": round(success_rate, 4),
        "signature_classes": sorted({s["signature_class"] for s in sigs}),
        "high_sev_classes": high_sev,
        "worst_severity": worst_label,
    }
    if exploration:
        score["exploration"] = {k: exploration.get(k) for k in _EXPLORATION_KEYS}
    if grounding:
        score["grounding"] = {k: grounding.get(k) for k in _GROUNDING_KEYS}
    return score


def _exploration_delta(baseline: dict, candidate: dict) -> Optional[dict]:
    """Candidate-minus-baseline for each exploration metric, or None when neither
    run carried exploration data."""
    be = baseline.get("exploration") or {}
    ce = candidate.get("exploration") or {}
    if not be and not ce:
        return None

    def d(k):
        bv, cv = be.get(k), ce.get(k)
        if isinstance(bv, (int, float)) and isinstance(cv, (int, float)):
            return round(cv - bv, 3)
        return None

    return {"distinct_gaps": d("distinct_gaps"),
            "mean_gaps_per_uc": d("mean_gaps_per_uc"),
            "consistency": d("consistency")}


def _grounding_delta(baseline: dict, candidate: dict) -> Optional[dict]:
    """Candidate-minus-baseline grounding (mean_distinct_spec_refs, shallow_fraction),
    or None when neither run carried grounding data. The #45b A/B reads this:
    +mean_distinct_spec_refs and -shallow_fraction mean the nudge worked."""
    bg = baseline.get("grounding") or {}
    cg = candidate.get("grounding") or {}
    if not bg and not cg:
        return None

    def d(k):
        bv, cv = bg.get(k), cg.get(k)
        if isinstance(bv, (int, float)) and isinstance(cv, (int, float)):
            return round(cv - bv, 3)
        return None

    return {"mean_distinct_spec_refs": d("mean_distinct_spec_refs"),
            "shallow_fraction": d("shallow_fraction")}


def gate(baseline: dict, candidate: dict, *, min_delta: float = 0.0,
         exploration_min_delta: Optional[float] = None) -> dict:
    """Decide promote / revert / inconclusive from two scores.

    Rules (in order):
      1. REVERT if the candidate introduces a NEW high-severity failure class
         absent in the baseline — even if success_rate improved. (The v1.9
         lesson: never trade a headline gain for a new failure mode.)
      2. REVERT if candidate success_rate is meaningfully WORSE than baseline.
      3. PROMOTE if candidate success_rate is meaningfully BETTER and rule 1
         is clear.
      4. On a success_rate TIE: PROMOTE only if the operator opted into the
         exploration tie-breaker (`exploration_min_delta` set) AND the candidate
         finds at least that many more distinct gaps with no new high-severity
         class — the 72B lever (deeper exploration at equal correctness). This is
         OFF by default because gap counts alone can be inflated by hallucination.
      5. INCONCLUSIVE otherwise — "promote only on a real improvement".

    `min_delta` is the success-rate margin that counts as "meaningful" (default
    0.0 = any strict change). The candidate-minus-baseline exploration delta is
    attached to every verdict as `exploration_delta` (advisory) when present.
    """
    b_rate = baseline.get("success_rate", 0.0)
    c_rate = candidate.get("success_rate", 0.0)
    delta = round(c_rate - b_rate, 4)
    expl = _exploration_delta(baseline, candidate)
    grnd = _grounding_delta(baseline, candidate)

    def verdict(kind: str, reason: str, new_high_sev=None) -> dict:
        out = {"verdict": kind, "reason": reason, "success_delta": delta,
               "new_high_sev": new_high_sev or []}
        if expl is not None:
            out["exploration_delta"] = expl
        if grnd is not None:
            out["grounding_delta"] = grnd
        return out

    new_high = sorted(set(candidate.get("high_sev_classes") or [])
                      - set(baseline.get("high_sev_classes") or []))
    if new_high:
        return verdict("revert",
                       f"candidate introduces new high-severity failure class(es): "
                       f"{', '.join(new_high)} — not promoting despite "
                       f"Δsuccess={delta:+.2%}", new_high)
    if delta < -min_delta:
        return verdict("revert",
                       f"candidate success_rate worse ({b_rate:.2%} → {c_rate:.2%}, "
                       f"Δ={delta:+.2%})")
    if delta > min_delta:
        return verdict("promote",
                       f"candidate improves success_rate ({b_rate:.2%} → {c_rate:.2%}, "
                       f"Δ={delta:+.2%}) with no new high-severity failure class")

    # success_rate tie — optional exploration tie-breaker.
    gaps_d = (expl or {}).get("distinct_gaps")
    if (exploration_min_delta is not None and exploration_min_delta > 0
            and isinstance(gaps_d, (int, float)) and gaps_d >= exploration_min_delta):
        return verdict("promote",
                       f"success_rate tied ({b_rate:.2%}); candidate explores "
                       f"+{gaps_d} distinct gaps (≥{exploration_min_delta}) with no "
                       f"new high-severity class")
    expl_note = ""
    if isinstance(gaps_d, (int, float)) and gaps_d != 0:
        expl_note = f"; exploration {gaps_d:+g} distinct gaps (advisory)"
    return verdict("inconclusive",
                   f"no meaningful change (success_rate {b_rate:.2%} → {c_rate:.2%}, "
                   f"Δ={delta:+.2%}){expl_note}; promote only on a real improvement")
