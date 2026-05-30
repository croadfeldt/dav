"""Diagnose & propose — Phase 1 of the self-improvement loop.

Given a run's failure taxonomy (failure_taxonomy.build_taxonomy), produce a
ranked list of *typed proposals* — `{kind, target, rationale, proposed_change,
predicted_effect, confidence, ...}`. Proposals are FILED FOR REVIEW, never
applied (applying is Phase 2). See docs/dav-self-improvement-vision.md.

Two layers:
  * `diagnose_rules()` — deterministic, high-confidence proposals for known
    signature classes. These ENCODE the OSAC 2026-05-29/30 fixes: each bug was
    root-caused by hand, and the mapping below is that knowledge made reusable.
  * `diagnose_llm()` — an LLM "second opinion" for unknown signatures and
    enrichment. The system prompt carries the hard guardrails (classify before
    editing prompts; don't over-harden; respect throughput×timeout). Optional:
    degrades to rules-only if no model is wired or the call fails.

Pure except for `diagnose_llm`, which takes an injected async `call_fn` so the
module has no httpx/DB dependency and stays unit-testable.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Awaitable, Callable, Optional

from .failure_taxonomy import SEVERITY_VOCAB, CONFIDENCE_VOCAB

log = logging.getLogger("dav-review-api.diagnose")

# Proposal `kind` vocabulary — the change's blast radius, which gates how it may
# be applied later (Phase 2): prompt/profile = auto-with-revert; code/route/
# tool/infra = human-gated.
KINDS = {"prompt", "profile", "route", "tool", "code", "infra", "data"}

# The 3-bucket scale the model borrows from the confidence axis → the middle of
# the 5-level severity scale (the mapping shipped in engine commit 10fe660).
_LMH_TO_SEVERITY = {"low": "minor", "medium": "moderate", "high": "major"}


def _proposal(signature_class, kind, target, rationale, proposed_change,
              predicted_effect, confidence, *, source="rule", evidence=None,
              change_spec=None) -> dict:
    return {
        "signature_class": signature_class,
        "kind": kind,
        "target": target,
        "rationale": rationale,
        "proposed_change": proposed_change,
        "predicted_effect": predicted_effect,
        "confidence": confidence,      # high | medium | low
        "source": source,              # rule | llm
        "evidence": evidence or {},
        # Structured, A/B-testable delta (Phase 2) when the change is mechanical;
        # None for prose-only proposals. {type:'max_tokens', direction, current}.
        "change_spec": change_spec,
    }


def _nearest_severity(label: str) -> Optional[str]:
    lab = (label or "").strip().lower()
    if lab in _LMH_TO_SEVERITY:
        return _LMH_TO_SEVERITY[lab]
    if lab in SEVERITY_VOCAB:
        return lab
    return None


# ---------------------------------------------------------------------------
# Rules layer — one builder per known signature class.
# Each takes (signature_dict, config_dict) and returns 0+ proposals.
# ---------------------------------------------------------------------------

def _rule_route_504(sig, cfg):
    mt = cfg.get("max_tokens")
    return [_proposal(
        "route_504", "profile", "dav_stage2_max_tokens / route timeout",
        rationale=(
            "Generations are exceeding the inference route timeout: a single "
            "call's `max_tokens / throughput(tok/s)` is larger than the HAProxy "
            "route timeout. The honest output ceiling is `throughput × timeout`."
        ),
        proposed_change=(
            f"Lower `dav_stage2_max_tokens` (currently {mt}) so the worst-case "
            f"generation finishes under the route timeout at this model's "
            f"throughput, OR raise the ISVC `haproxy.router.openshift.io/timeout` "
            f"annotation in lock-step. Prefer lowering max_tokens unless real "
            f"analyses are being truncated."
        ),
        predicted_effect="Eliminates 504 gateway timeouts on long generations.",
        confidence="high",
        evidence={"count": sig["count"], "exemplars": sig["exemplars"], "max_tokens": mt},
        change_spec={"type": "max_tokens", "direction": "lower", "current": mt},
    )]


def _rule_output_truncation(sig, cfg):
    mt = cfg.get("max_tokens")
    return [_proposal(
        "output_truncation", "profile", "dav_stage2_max_tokens (vs route timeout)",
        rationale=(
            "The final analysis was cut mid-JSON (unbalanced braces / extra "
            "data). Usually the model's structured output exceeded `max_tokens`; "
            "less often a tool-parser bug corrupts the stream."
        ),
        proposed_change=(
            f"Raise `dav_stage2_max_tokens` (currently {mt}) IF the route timeout "
            f"still allows `max_tokens/throughput < timeout` — otherwise this "
            f"trades a truncation for a 504. If raising isn't possible at this "
            f"throughput, reduce analysis verbosity via the stage-2 prompt, or "
            f"move to a faster model. If multi-function tool calls are in play, "
            f"suspect the `--tool-call-parser` (cf. vllm#43713 for qwen3_xml)."
        ),
        predicted_effect="Final analyses parse cleanly instead of dying on truncated JSON.",
        confidence="medium",
        evidence={"count": sig["count"], "exemplars": sig["exemplars"], "max_tokens": mt},
        change_spec={"type": "max_tokens", "direction": "raise", "current": mt},
    )]


def _rule_vocab_reject(vocab_name, vocab, sig, cfg):
    captured = sig.get("captured") or []
    suggestions = {}
    for lab in captured:
        near = _nearest_severity(lab) if vocab_name == "severity" else (
            lab.lower() if lab.lower() in vocab else None)
        suggestions[lab] = near
    mapping = ", ".join(
        f"'{lab}' → '{near or '<nearest canonical, human-decide>'}'"
        for lab, near in suggestions.items()
    ) or "(no captured labels)"
    return [_proposal(
        f"{vocab_name}_reject", "code", f"engine `_{vocab_name.upper()}_ALIASES`",
        rationale=(
            f"The model emitted {vocab_name} label(s) outside the canonical "
            f"vocabulary {vocab}, and the schema hard-rejected an otherwise "
            f"complete analysis. Models reuse adjacent scales (e.g. the "
            f"confidence axis's low/medium/high). Map the whole alternate scale "
            f"at once — aliasing one label just defers the next failure."
        ),
        proposed_change=(
            f"Add alias(es) to `_{vocab_name.upper()}_ALIASES` in "
            f"`engine/src/dav/core/use_case_schema.py`: {mapping}. When an "
            f"aliased label arrives as a dict with a non-canonical score, use the "
            f"canonical default score. Keep raising on genuinely unknown labels."
        ),
        predicted_effect="Completed analyses stop dying on a one-word vocabulary mismatch.",
        confidence="high",
        evidence={"count": sig["count"], "captured": captured, "exemplars": sig["exemplars"]},
    )]


def _rule_score_out_of_band(sig, cfg):
    return [_proposal(
        "score_out_of_band", "code", "engine `normalize_severity` dict path",
        rationale=(
            "An aliased label carried a score from the model's own (non-canonical) "
            "scale that fell outside the resolved label's band."
        ),
        proposed_change=(
            "When aliasing changes the label, discard the model-supplied score and "
            "use the canonical default for the resolved label (don't validate the "
            "stale score against the new band)."
        ),
        predicted_effect="Aliased labels with off-scale scores stop raising.",
        confidence="medium",
        evidence={"count": sig["count"], "exemplars": sig["exemplars"]},
    )]


def _rule_context_overflow(sig, cfg):
    return [_proposal(
        "context_overflow", "profile", "--max-model-len / MCP section cap",
        rationale=(
            "Prompt + requested output overflowed the model context window. On a "
            "fixed VRAM budget, bigger models leave less KV → smaller usable "
            "context; large tool results compound it."
        ),
        proposed_change=(
            "Raise `--max-model-len` if KV headroom allows; otherwise cap large "
            "tool outputs (cf. the MCP `get_document_section` size cap that "
            "redirects oversized sections to targeted lookups), or reduce "
            "retrieved breadth via the prompt."
        ),
        predicted_effect="Agent stops hitting the context ceiling mid-run.",
        confidence="medium",
        evidence={"count": sig["count"], "exemplars": sig["exemplars"]},
    )]


def _rule_budget_exhausted(sig, cfg):
    return [_proposal(
        "budget_exhausted", "tool", "MCP tools / retrieval resolution (NOT prompt hardening)",
        rationale=(
            "The agent burned its tool-call budget without converging — typically "
            "'fishing': repeated lookups that miss (a too-strict resolver, or a "
            "missing targeted tool), not laziness."
        ),
        proposed_change=(
            "Find WHY lookups miss: (a) MCP handle/section resolution too strict "
            "(add shortcut fallbacks), (b) the model needs a targeted tool the "
            "corpus lacks (cf. adding `get_capability` for matrix-row IDs). "
            "DO NOT just harden the prompt to 'stop fishing' — that backfired "
            "(v1.9 made misses worse on every model). Add a *tool/resolver*, or "
            "add ONE terse prompt pointer, then A/B."
        ),
        predicted_effect="Agent converges in fewer, hitting tool calls.",
        confidence="medium",
        evidence={"count": sig["count"], "exemplars": sig["exemplars"]},
    )]


def _rule_tool_parse_error(sig, cfg):
    return [_proposal(
        "tool_parse_error", "infra", "--tool-call-parser",
        rationale=(
            "The model's tool calls could not be parsed — usually a parser/model "
            "lineage mismatch."
        ),
        proposed_change=(
            "Verify `--tool-call-parser` matches the model: `hermes` (Qwen2.5/3 "
            "dense), `qwen3_coder` (Coder MoE), etc. Known upstream bug: "
            "qwen3_xml mangles multi-function tool_call arguments (vllm#43713)."
        ),
        predicted_effect="Tool calls are parsed and the agent loop engages.",
        confidence="medium",
        evidence={"count": sig["count"], "exemplars": sig["exemplars"]},
    )]


def _rule_inference_error(sig, cfg):
    return [_proposal(
        "inference_error", "infra", "serving endpoint health",
        rationale="Inference endpoint connection / non-504 5xx error.",
        proposed_change=(
            "Check the serving pod (Ready? OOM? autotune still running?), the "
            "route, and endpoint reachability. Needs an operator — not a "
            "prompt/config change."
        ),
        predicted_effect="Restores a reachable, healthy endpoint.",
        confidence="low",
        evidence={"count": sig["count"], "exemplars": sig["exemplars"]},
    )]


_RULES: dict[str, Callable] = {
    "route_504": _rule_route_504,
    "output_truncation": _rule_output_truncation,
    "severity_reject": lambda s, c: _rule_vocab_reject("severity", SEVERITY_VOCAB, s, c),
    "confidence_reject": lambda s, c: _rule_vocab_reject("confidence", CONFIDENCE_VOCAB, s, c),
    "score_out_of_band": _rule_score_out_of_band,
    "context_overflow": _rule_context_overflow,
    "budget_exhausted": _rule_budget_exhausted,
    "tool_parse_error": _rule_tool_parse_error,
    "inference_error": _rule_inference_error,
}


def diagnose_rules(taxonomy: dict) -> list[dict]:
    """Deterministic proposals for the taxonomy's known signature classes."""
    cfg = taxonomy.get("config") or {}
    out: list[dict] = []
    for sig in taxonomy.get("signatures") or []:
        builder = _RULES.get(sig["signature_class"])
        if builder:
            out.extend(builder(sig, cfg))
    return out


# ---------------------------------------------------------------------------
# LLM layer — second opinion for unknowns + enrichment. Optional.
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = """You are the DAV self-improvement diagnostician. Given a \
failed pipeline run's failure taxonomy (signature histogram + run config), \
propose specific, typed changes that would fix the failures. You are FILING \
PROPOSALS FOR HUMAN REVIEW — never assume anything is applied.

Output ONLY a JSON array. Each element:
{"kind": "prompt|profile|route|tool|code|infra|data", "target": "<the knob/file>", \
"rationale": "<why, tied to evidence>", "proposed_change": "<concrete change>", \
"predicted_effect": "<what it fixes>", "confidence": "high|medium|low"}

Hard rules (these are scar tissue from real incidents — follow them):
1. Classify the failure CLASS before proposing prompt edits. Most failures are \
config/parser/throughput, NOT prompt problems. Do not propose a prompt edit for \
a 504 or a schema reject.
2. Never propose "harden the prompt to stop <behavior>". Repeating a prohibition \
makes the model do it MORE (a real regression we shipped and reverted). Prefer a \
tool/resolver fix, or removing prompt text, then an A/B.
3. Respect the constraints: output budget is bounded by throughput × route_timeout; \
context is bounded by VRAM minus weights. Don't propose raising max_tokens past \
what the route timeout allows, or context past KV capacity.
4. Prefer the smallest, most reversible change. If a failure needs code or infra, \
say so plainly (it'll be human-gated).
Only add proposals the rules layer would miss; don't restate obvious ones."""


async def diagnose_llm(
    taxonomy: dict,
    call_fn: Callable[[str, str], Awaitable[str]],
) -> list[dict]:
    """Ask an LLM for additional proposals. `call_fn(system, user) -> text`.

    Returns [] on any failure (the loop degrades to rules-only).
    """
    try:
        user = (
            "Failure taxonomy for review:\n```json\n"
            + json.dumps({
                "run_id": taxonomy.get("run_id"),
                "totals": {k: taxonomy.get(k) for k in ("total_ucs", "succeeded", "failed")},
                "config": taxonomy.get("config"),
                "signatures": taxonomy.get("signatures"),
            }, indent=2)
            + "\n```\nReturn the JSON array of additional proposals."
        )
        log.info("diagnose_llm: requesting second opinion for run %s", taxonomy.get("run_id"))
        raw = await call_fn(_LLM_SYSTEM_PROMPT, user)
        items = _extract_json_array(raw)
        out = []
        for it in items:
            if not isinstance(it, dict) or "kind" not in it:
                continue
            out.append(_proposal(
                it.get("signature_class", "llm"),
                it.get("kind", "data"),
                it.get("target", ""),
                it.get("rationale", ""),
                it.get("proposed_change", ""),
                it.get("predicted_effect", ""),
                it.get("confidence", "low"),
                source="llm",
                evidence={"from_llm": True},
            ))
        log.info("diagnose_llm: %d proposal(s) from %d parsed item(s) (raw %d chars)",
                 len(out), len(items), len(raw or ""))
        return out
    except Exception as e:
        # Degrade to rules-only, but never silently — a diagnostic tool that
        # hides its own failures is the anti-pattern this whole loop exists to fix.
        log.warning("diagnose_llm failed (%s: %s) — degrading to rules-only",
                    type(e).__name__, e)
        return []


def _extract_json_array(text: str) -> list:
    """Pull the first JSON array out of an LLM response (handles ```json fences)."""
    if not text:
        return []
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    blob = m.group(1) if m else None
    if blob is None:
        s, e = text.find("["), text.rfind("]")
        blob = text[s:e + 1] if (s != -1 and e > s) else None
    if not blob:
        return []
    try:
        v = json.loads(blob)
        return v if isinstance(v, list) else []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

_CONF_RANK = {"high": 0, "medium": 1, "low": 2}


def merge_and_rank(rules_props: list[dict], llm_props: list[dict]) -> list[dict]:
    """Combine rule + LLM proposals, drop LLM dupes of a rule's (kind,target),
    and rank by confidence then source (rules first)."""
    seen = {(p["kind"], (p["target"] or "").lower()) for p in rules_props}
    merged = list(rules_props)
    for p in llm_props:
        key = (p["kind"], (p["target"] or "").lower())
        if key not in seen:
            merged.append(p)
            seen.add(key)
    merged.sort(key=lambda p: (
        _CONF_RANK.get(p["confidence"], 3),
        0 if p["source"] == "rule" else 1,
    ))
    return merged


async def diagnose(
    taxonomy: dict,
    call_fn: Optional[Callable[[str, str], Awaitable[str]]] = None,
) -> dict:
    """Full diagnosis: rules + (optional) LLM, merged and ranked.

    Returns {run_id, generated_signatures, proposals, used_llm}.
    """
    rules_props = diagnose_rules(taxonomy)
    llm_props: list[dict] = []
    if call_fn is not None:
        llm_props = await diagnose_llm(taxonomy, call_fn)
    proposals = merge_and_rank(rules_props, llm_props)
    return {
        "run_id": taxonomy.get("run_id"),
        "signature_classes": [s["signature_class"] for s in (taxonomy.get("signatures") or [])],
        "proposals": proposals,
        "llm_attempted": call_fn is not None,   # vs used_llm: did it CONTRIBUTE
        "used_llm": bool(llm_props),
        "rule_count": len(rules_props),
        "llm_count": len(llm_props),
    }
