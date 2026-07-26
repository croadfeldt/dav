"""Rule-based verdict derivation (ADR-010).

The LLM *asserts* a verdict; DAV *derives* the reported verdict from the evidence
by applying an ordered set of deterministic, conservative rules. This separates
"what the model claimed" from "what the findings support" — the sweep's core
critique was that DAV lets the model assert verdicts with only a single post-hoc
gate.

Design invariants:

* **Downgrade-only.** A rule may only lower the verdict along
  supported → partially_supported → not_supported. The asserted verdict is the
  ceiling: evidence can withdraw support the model over-claimed, but never
  manufacture support it under-claimed. This makes the derivation safe to apply
  unconditionally — a well-calibrated model is untouched.
* **Transparent.** `derive_verdict` returns the derived verdict *and* the list of
  rules that fired (each with from/to/why), so both values and the reasoning are
  preserved rather than silently overwritten.
* **Grown rule-by-rule.** The first rule (GATE-001) is the original ensemble
  verdict gate, lifted verbatim. New rules append to `_RULES`; each is a small
  pure predicate over the asserted verdict + the merged gaps.
"""

from __future__ import annotations

from typing import Any

from .use_case_schema import Verdict

# Lower rank = less support. Used to guarantee rules only ever downgrade.
_SUPPORT_RANK = {
    Verdict.NOT_SUPPORTED.value: 0,
    Verdict.PARTIALLY_SUPPORTED.value: 1,
    Verdict.SUPPORTED.value: 2,
}

# Gap severities that block an unqualified "supported".
_BLOCKING_SEVERITIES = {"major", "critical"}


def _sev_label(gap: Any) -> str:
    """Severity label from a GapIdentified (SeverityDescriptor) or a bare string."""
    sev = getattr(gap, "severity", gap)
    label = getattr(sev, "label", sev)
    return (label or "minor").strip().lower() if isinstance(label, str) else "minor"


def _rule_gate_001(verdict: str, gaps: list) -> "str | None":
    """GATE-001 — a `supported` verdict can't stand alongside a major/critical gap.

    This is the original ensemble verdict gate (ensemble.py), now a named rule.
    """
    if verdict == Verdict.SUPPORTED.value and any(
        _sev_label(g) in _BLOCKING_SEVERITIES for g in (gaps or [])
    ):
        return Verdict.PARTIALLY_SUPPORTED.value
    return None


# Ordered rule registry. Each entry: (rule_id, why, fn(verdict, gaps) -> target|None).
# Append new rules here; keep shipped ids/order stable.
_RULES: list[tuple[str, str, Any]] = [
    ("GATE-001",
     "supported asserted but a major/critical gap is present",
     _rule_gate_001),
]


def derive_verdict(asserted: str, gaps: list) -> tuple[str, list[dict]]:
    """Derive the reported verdict from the asserted verdict + merged gaps.

    Returns (derived_verdict, applied_rules) where applied_rules is a list of
    {"rule", "from", "to", "why"} in the order the rules fired. `applied_rules`
    is empty when the asserted verdict already reflects the evidence (the common
    case for a well-calibrated model), in which case derived == asserted.
    """
    verdict = asserted
    applied: list[dict] = []
    for rule_id, why, fn in _RULES:
        target = fn(verdict, gaps)
        # Downgrade-only guard: ignore any rule that would (mis)raise support.
        if target is not None and _SUPPORT_RANK.get(target, 99) < _SUPPORT_RANK.get(verdict, 99):
            applied.append({"rule": rule_id, "from": verdict, "to": target, "why": why})
            verdict = target
    return verdict, applied
