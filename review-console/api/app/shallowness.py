"""Shallow-analysis detector — the per-UC quality signal the failure-driven
self-improvement loop is blind to.

A DAV stage-2 analysis can *succeed* (no 504, valid schema, a verdict) and still
be thin: it asserts components/capabilities without grounding them in spec IDs
("generic labels"), commits after only a couple of tool calls, and surfaces few
gaps. The 2026-05-30 72B eval isolated exactly this as the real quality lever —
on the same UCs the larger model committed earlier and cited roughly half the
distinct spec IDs the 32B did. `results.get_run_exploration()` measures this at
the run-aggregate level for A/B comparison; this module scores it *per UC* so a
single thin analysis is detectable the moment it lands, not only in hindsight.

Pure functions over an analysis dict (the shape written by
`AnalysisResult.to_dict()`: claim sections + `gaps_identified` + `summary` +
`analysis_metadata` at the top level). No I/O, so scoring is unit-testable
without a workspace. **Advisory by design** — raw counts can be inflated by
hallucination, so this FLAGS for review/nudge; it never gates or mutates.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Claim sections that carry `spec_refs` (the grounding handle). `gaps_identified`
# carries `spec_refs_consulted` instead and is counted separately.
_GROUNDED_SECTIONS = (
    "components_required",
    "data_model_touched",
    "capabilities_invoked",
    "policy_modes_required",
)

# Only a definitive verdict that actually asserted something can be "shallow".
# A not_supported / empty analysis that found nothing isn't thinly-grounded —
# it's a different signal (and flagging it here would be noise).
_DEFINITIVE_VERDICTS = {"supported", "partially_supported"}


def _norm_ref(ref) -> str:
    return " ".join(str(ref).strip().lower().split())


@dataclass(frozen=True)
class ShallowThresholds:
    """Advisory thresholds. Defaults anchored to the 72B eval reference points:
    well-grounded 32B UCs tied ~8 components to spec IDs and cited many distinct
    spec refs; the shallow ones were a single generic label / ~2 tool calls."""
    min_distinct_spec_refs: int = 3     # citing <3 distinct spec IDs = thin grounding
    max_ungrounded_ratio: float = 0.5   # >half the claims with no spec_refs = generic
    min_tool_calls: int = 4             # committing after <4 tool calls = barely explored
    min_claims_for_ratio: int = 3       # don't judge the ungrounded ratio of a tiny analysis

    def to_dict(self) -> dict:
        return {
            "min_distinct_spec_refs": self.min_distinct_spec_refs,
            "max_ungrounded_ratio": self.max_ungrounded_ratio,
            "min_tool_calls": self.min_tool_calls,
            "min_claims_for_ratio": self.min_claims_for_ratio,
        }


def score_analysis(analysis: dict) -> dict:
    """Grounding-density metrics for ONE analysis sample dict. Pure, no I/O."""
    spec_ids: set[str] = set()
    n_claims = grounded = ungrounded = 0
    counts: dict[str, int] = {}
    for section in _GROUNDED_SECTIONS:
        items = analysis.get(section) or []
        counts[section] = len(items)
        for it in items:
            if not isinstance(it, dict):
                continue
            n_claims += 1
            refs = [r for r in (it.get("spec_refs") or []) if r]
            if refs:
                grounded += 1
                spec_ids.update(_norm_ref(r) for r in refs)
            else:
                ungrounded += 1
    gaps = analysis.get("gaps_identified") or []
    for g in gaps:
        if isinstance(g, dict):
            for r in (g.get("spec_refs_consulted") or []):
                if r:
                    spec_ids.add(_norm_ref(r))
    meta = analysis.get("analysis_metadata") or {}
    tc = meta.get("tool_call_count")
    summary = analysis.get("summary") or {}
    return {
        "n_components": counts.get("components_required", 0),
        "n_data_model": counts.get("data_model_touched", 0),
        "n_capabilities": counts.get("capabilities_invoked", 0),
        "n_policy_modes": counts.get("policy_modes_required", 0),
        "n_gaps": len(gaps),
        "n_claims": n_claims,
        "grounded_claims": grounded,
        "ungrounded_claims": ungrounded,
        "ungrounded_ratio": round(ungrounded / n_claims, 3) if n_claims else None,
        "distinct_spec_refs": len(spec_ids),
        "tool_calls": int(tc) if isinstance(tc, (int, float)) else None,
        "verdict": summary.get("verdict"),
    }


def flag(metrics: dict, thresholds: Optional[ShallowThresholds] = None) -> dict:
    """Apply thresholds to one analysis's metrics → {shallow, eligible, reasons}.

    `eligible` is False (and shallow False) for analyses that aren't grounding
    candidates (non-definitive verdict, or zero claims). `reasons` is the
    human-readable, auditable why — keep it terse; it feeds an operator review
    and, later, a grounding-nudge proposal."""
    t = thresholds or ShallowThresholds()
    if metrics.get("verdict") not in _DEFINITIVE_VERDICTS or metrics.get("n_claims", 0) < 1:
        return {"shallow": False, "eligible": False, "reasons": []}
    reasons: list[str] = []
    dsr = metrics.get("distinct_spec_refs", 0)
    if dsr < t.min_distinct_spec_refs:
        reasons.append(f"only {dsr} distinct spec refs (< {t.min_distinct_spec_refs})")
    ur = metrics.get("ungrounded_ratio")
    if (ur is not None and ur > t.max_ungrounded_ratio
            and metrics.get("n_claims", 0) >= t.min_claims_for_ratio):
        reasons.append(
            f"{round(ur * 100)}% of {metrics['n_claims']} claims cite no spec_refs")
    tc = metrics.get("tool_calls")
    if tc is not None and tc < t.min_tool_calls:
        reasons.append(f"committed after {tc} tool calls (< {t.min_tool_calls})")
    return {"shallow": bool(reasons), "eligible": True, "reasons": reasons}


def score_and_flag(analysis: dict, thresholds: Optional[ShallowThresholds] = None) -> dict:
    """Convenience: metrics + flag for a single analysis sample."""
    m = score_analysis(analysis)
    return {**m, **flag(m, thresholds)}


def aggregate_samples(scored: list[dict]) -> dict:
    """Roll up per-sample scores for one UC (verification = 1 sample; explore =
    many). Numeric fields are averaged; a UC is `shallow` when the MAJORITY of
    its eligible samples are shallow (a single thin sample among consistent rich
    ones isn't the UC's character). `reasons` are taken from a representative
    shallow sample."""
    if not scored:
        return {"eligible": False, "shallow": False, "reasons": [], "n_samples": 0}
    eligible = [s for s in scored if s.get("eligible")]
    n = len(scored)
    if not eligible:
        return {"eligible": False, "shallow": False, "reasons": [], "n_samples": n}

    def _mean(key):
        vals = [s[key] for s in eligible if isinstance(s.get(key), (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else None

    shallow_samples = [s for s in eligible if s.get("shallow")]
    is_shallow = len(shallow_samples) * 2 > len(eligible)  # strict majority
    reasons = shallow_samples[0]["reasons"] if shallow_samples else []
    return {
        "eligible": True,
        "shallow": is_shallow,
        "reasons": reasons,
        "n_samples": n,
        "n_shallow_samples": len(shallow_samples),
        "verdict": eligible[0].get("verdict"),
        "distinct_spec_refs": _mean("distinct_spec_refs"),
        "n_components": _mean("n_components"),
        "n_gaps": _mean("n_gaps"),
        "n_claims": _mean("n_claims"),
        "ungrounded_ratio": _mean("ungrounded_ratio"),
        "tool_calls": _mean("tool_calls"),
    }
