"""Architectural review of DAV analysis findings.

Streams a review from a configured model (OpenAI-compatible or Anthropic)
given analysis results pulled from the DB by the caller.

Scopes:
  uc   — review gaps for a single use case
  run  — cross-cutting review across all UCs in a run
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx
import yaml as _yaml

log = logging.getLogger("dav-review-api.arch_review")

_UC_SYSTEM = (
    "You are a senior software architect reviewing AI-generated analysis findings "
    "about a system's use case coverage gaps.\n\n"
    "Given a use case definition and its analysis findings (gaps, verdict, severity), provide:\n"
    "1. A 2-3 sentence summary of the key architectural deficiency.\n"
    "2. For each significant gap, a concrete architectural change or enhancement.\n"
    "3. Any cross-cutting concerns (security, scalability, observability).\n"
    "4. Recommendations prioritised by severity and implementation effort.\n\n"
    "Be specific and actionable. Reference gap IDs and severity levels. "
    "Write in plain prose — no markdown headers, minimal bullet points."
)

_RUN_SYSTEM = (
    "You are a senior software architect reviewing AI-generated analysis findings "
    "across multiple use cases for a system.\n\n"
    "Given findings from N use cases (verdicts, gaps, severity), identify:\n"
    "1. Architectural themes — gaps appearing across multiple use cases that signal systemic deficiencies.\n"
    "2. High-severity single-UC gaps requiring immediate attention.\n"
    "3. A prioritised roadmap of architectural changes (3-5 items max).\n"
    "4. Risks if the gaps remain unaddressed.\n\n"
    "Be concise and strategic. Reference use case handles where relevant. "
    "Write in plain prose."
)


def _build_uc_prompt(uc: dict, analysis: dict, gaps: list[dict]) -> str:
    parts: list[str] = []

    handle = uc.get("handle") or uc.get("uuid", "unknown")
    parts.append(f"Use Case: {handle}")

    yaml_content = uc.get("yaml_content", "")
    if yaml_content:
        try:
            parsed = _yaml.safe_load(yaml_content)
            scenario = parsed.get("scenario") or {}
            if scenario.get("description"):
                parts.append(f"Description: {scenario['description']}")
            if scenario.get("intent"):
                parts.append(f"Intent: {scenario['intent']}")
            criteria = scenario.get("success_criteria") or []
            if criteria:
                parts.append("Success criteria:\n" + "\n".join(f"  - {c}" for c in criteria))
        except Exception:
            pass

    verdict = analysis.get("verdict") or "unknown"
    confidence = analysis.get("overall_confidence") or ""
    notes = analysis.get("overall_assessment") or ""
    parts.append(
        f"\nVerdict: {verdict}" + (f" (confidence: {confidence})" if confidence else "")
    )
    if notes:
        parts.append(f"Overall assessment: {notes}")

    if gaps:
        parts.append(f"\nGaps identified ({len(gaps)}):")
        for g in gaps:
            sev = g.get("severity") or {}
            if isinstance(sev, dict):
                sev_label = sev.get("label") or sev.get("band") or "unknown"
            else:
                sev_label = str(sev)
            parts.append(
                f"\n  [{g.get('gap_id', '?')}] {g.get('title', '')} — severity: {sev_label}"
            )
            if g.get("description"):
                parts.append(f"    {g['description']}")
            if g.get("rationale"):
                parts.append(f"    Rationale: {g['rationale']}")
            if g.get("recommendation"):
                parts.append(f"    Recommendation: {g['recommendation']}")
    else:
        parts.append("\nNo gaps identified.")

    parts.append("\nPlease provide your architectural review.")
    return "\n".join(parts)


def _build_run_prompt(run_id: str, uc_analyses: list[dict]) -> str:
    parts: list[str] = [
        f"Run: {run_id}",
        f"Use cases analyzed: {len(uc_analyses)}\n",
    ]

    verdict_counts: dict[str, int] = {}
    total_gaps = 0
    for ua in uc_analyses:
        v = ua.get("verdict") or "unknown"
        verdict_counts[v] = verdict_counts.get(v, 0) + 1
        total_gaps += len(ua.get("gaps") or [])

    parts.append(
        "Verdict summary: "
        + ", ".join(f"{v}: {c}" for v, c in sorted(verdict_counts.items()))
    )
    parts.append(f"Total gaps: {total_gaps}\n")

    for ua in uc_analyses:
        handle = ua.get("uc_handle") or ua.get("uc_uuid", "?")
        verdict = ua.get("verdict") or "unknown"
        gaps = ua.get("gaps") or []
        parts.append(f"  {handle} — {verdict}, {len(gaps)} gap(s)")
        for g in gaps:
            sev = g.get("severity") or {}
            if isinstance(sev, dict):
                sev_label = sev.get("label") or sev.get("band") or "?"
            else:
                sev_label = str(sev)
            desc = (g.get("description") or "")[:400]
            parts.append(
                f"    [{g.get('gap_id','?')}] {g.get('title','')} ({sev_label}): {desc}"
            )

    parts.append("\nPlease provide your cross-cutting architectural review and prioritised roadmap.")
    return "\n".join(parts)


_ENHANCEMENT_UC_SYSTEM = (
    "You are a senior software architect producing CONCRETE SPEC EDITS to close "
    "coverage gaps. Your output is consumed mechanically — a downstream step "
    "applies your patches to the architecture specification repository, so "
    "every patch must be ready to commit without further translation.\n\n"
    "For EACH gap, emit one ENHANCEMENT block in this exact format:\n\n"
    "    ENHANCEMENT <id> (gap: <gap_id>)\n"
    "    target: <spec doc handle, e.g. dcm/components/policy-evaluation.md>\n"
    "    action: add_section | update_section | replace_text | new_document\n"
    "    section_title: <verbatim title to add or modify>\n"
    "    position: <after \"<existing section title>\" | end_of_document | top>\n"
    "    rationale: <one sentence linking this patch to the gap>\n"
    "    ```markdown\n"
    "    <VERBATIM markdown content to insert or that replaces the existing section — \n"
    "     ready to paste, no placeholders, no \"<your text here>\" stubs>\n"
    "    ```\n"
    "    acceptance: <one sentence — how a reviewer confirms the gap is closed>\n\n"
    "Rules:\n"
    "- Prefer `add_section` and `update_section` over `new_document`. Only emit "
    "  `new_document` when no existing target doc fits, and then `target:` is the "
    "  proposed new handle (e.g. `dcm/governance/audit-trail.md`).\n"
    "- The user message tells you which doc handles each gap's analysis flagged "
    "  (`spec_refs_missing`) — prefer those targets unless an obviously-better one "
    "  exists in the user message's spec_refs context.\n"
    "- The markdown block is the LITERAL EDIT — write the actual prose / list / "
    "  table the spec will carry, not a meta-description of what to write.\n"
    "- One ENHANCEMENT block per gap. Multiple gaps may target the same doc; "
    "  group them in DOC ORDER so a human can apply the patches sequentially.\n"
    "- No prose between blocks. No introduction. No summary. The downstream "
    "  parser keys on the `ENHANCEMENT <id>` line."
)

_ENHANCEMENT_RUN_SYSTEM = (
    "You are a senior software architect producing CONCRETE SPEC EDITS to close "
    "coverage gaps across multiple use cases. Your output is consumed "
    "mechanically — a downstream step applies your patches to the architecture "
    "specification repository.\n\n"
    "Group your output in TWO sections:\n\n"
    "1. SYSTEMIC ENHANCEMENTS (one patch may close gaps in multiple UCs)\n"
    "2. UC-SPECIFIC ENHANCEMENTS (only gaps not addressed above)\n\n"
    "For EACH enhancement use this exact block format:\n\n"
    "    ENHANCEMENT <id> (gaps: <gap_id>[, <gap_id>...], UCs: <uc_handle>[, ...])\n"
    "    target: <spec doc handle>\n"
    "    action: add_section | update_section | replace_text | new_document\n"
    "    section_title: <verbatim>\n"
    "    position: <after \"...\" | end_of_document | top>\n"
    "    rationale: <one sentence>\n"
    "    ```markdown\n"
    "    <VERBATIM markdown content — ready to paste, no placeholders>\n"
    "    ```\n"
    "    acceptance: <one sentence>\n\n"
    "After the blocks, emit a final ORDER section listing the enhancement IDs in "
    "the order a human should apply them (dependencies first):\n\n"
    "    ORDER: <id1>, <id2>, <id3>, ...\n\n"
    "Rules:\n"
    "- The markdown block is the LITERAL EDIT — actual content, not meta-prose.\n"
    "- The user message lists each gap's spec_refs_missing — prefer those targets.\n"
    "- No introduction, no summary. The downstream parser keys on the\n"
    "  `ENHANCEMENT <id>` and `ORDER:` lines."
)


def _build_enhancement_prompt(uc: dict, analysis: dict, gaps: list[dict]) -> str:
    parts: list[str] = []
    handle = uc.get("handle") or uc.get("uuid", "unknown")
    parts.append(f"Use Case: {handle}")
    yaml_content = uc.get("yaml_content", "")
    if yaml_content:
        try:
            parsed = _yaml.safe_load(yaml_content)
            scenario = parsed.get("scenario") or {}
            if scenario.get("description"):
                parts.append(f"Description: {scenario['description']}")
            if scenario.get("intent"):
                parts.append(f"Intent: {scenario['intent']}")
            criteria = scenario.get("success_criteria") or []
            if criteria:
                parts.append("Success criteria:\n" + "\n".join(f"  - {c}" for c in criteria))
        except Exception:
            pass
    verdict = analysis.get("verdict") or "unknown"
    parts.append(f"\nVerdict: {verdict}")
    if gaps:
        parts.append(f"\nGaps requiring enhancement ({len(gaps)}):")
        for g in gaps:
            sev = g.get("severity") or {}
            sev_label = sev.get("label") or sev.get("band") or "unknown" if isinstance(sev, dict) else str(sev)
            parts.append(f"\n  [{g.get('gap_id', '?')}] {g.get('title', '')} — severity: {sev_label}")
            if g.get("description"):
                parts.append(f"    {g['description']}")
            if g.get("rationale"):
                parts.append(f"    Rationale: {g['rationale']}")
            if g.get("recommendation"):
                parts.append(f"    Initial recommendation: {g['recommendation']}")
            # Pass through the spec docs the stage-2 analysis flagged as missing
            # the content this gap needs. Lets the model target its patches
            # without guessing which doc/handle to touch.
            refs_missing = g.get("spec_refs_missing") or g.get("spec_refs") or []
            if refs_missing:
                if isinstance(refs_missing, list):
                    parts.append("    spec_refs_missing: " + ", ".join(str(r) for r in refs_missing))
                else:
                    parts.append(f"    spec_refs_missing: {refs_missing}")
    else:
        parts.append("\nNo gaps identified.")
    parts.append(
        "\nProduce ENHANCEMENT blocks per the system instructions. "
        "Each block must include a verbatim markdown patch ready to paste — "
        "no placeholders, no meta-prose."
    )
    return "\n".join(parts)


def _build_enhancement_run_prompt(run_id: str, uc_analyses: list[dict]) -> str:
    parts = [f"Run: {run_id}", f"Use cases analyzed: {len(uc_analyses)}\n"]
    total_gaps = sum(len(ua.get("gaps") or []) for ua in uc_analyses)
    parts.append(f"Total gaps requiring enhancement: {total_gaps}\n")
    for ua in uc_analyses:
        handle = ua.get("uc_handle") or ua.get("uc_uuid", "?")
        verdict = ua.get("verdict") or "unknown"
        gaps = ua.get("gaps") or []
        if not gaps:
            continue
        parts.append(f"  {handle} — {verdict}, {len(gaps)} gap(s):")
        for g in gaps:
            sev = g.get("severity") or {}
            sev_label = sev.get("label") or sev.get("band") or "?" if isinstance(sev, dict) else str(sev)
            parts.append(f"    [{g.get('gap_id','?')}] {g.get('title','')} ({sev_label})")
            if g.get("description"):
                parts.append(f"      Description: {(g.get('description') or '')[:300]}")
            if g.get("recommendation"):
                parts.append(f"      Recommendation: {(g.get('recommendation') or '')[:400]}")
            refs_missing = g.get("spec_refs_missing") or g.get("spec_refs") or []
            if refs_missing:
                if isinstance(refs_missing, list):
                    parts.append(f"      spec_refs_missing: {', '.join(str(r) for r in refs_missing)}")
                else:
                    parts.append(f"      spec_refs_missing: {refs_missing}")
    parts.append(
        "\nProduce ENHANCEMENT blocks per the system instructions, grouped into "
        "SYSTEMIC ENHANCEMENTS then UC-SPECIFIC ENHANCEMENTS, followed by the "
        "final ORDER line. Each block must include a verbatim markdown patch "
        "ready to paste — no placeholders, no meta-prose."
    )
    return "\n".join(parts)


async def _strip_think_blocks(source: AsyncIterator[str]) -> AsyncIterator[str]:
    """Strip <think>...</think> blocks from a streaming text source.

    Handles tags split across chunk boundaries. Safe to apply to any provider;
    models that don't emit think blocks pass through unmodified.
    """
    OPEN, CLOSE = "<think>", "</think>"
    in_think = False
    buf = ""

    async for chunk in source:
        buf += chunk
        while True:
            if not in_think:
                idx = buf.find(OPEN)
                if idx == -1:
                    safe = max(0, len(buf) - (len(OPEN) - 1))
                    if safe:
                        yield buf[:safe]
                        buf = buf[safe:]
                    break
                if idx > 0:
                    yield buf[:idx]
                buf = buf[idx + len(OPEN):]
                in_think = True
            else:
                idx = buf.find(CLOSE)
                if idx == -1:
                    buf = buf[-(len(CLOSE) - 1):] if len(buf) >= len(CLOSE) else buf
                    break
                buf = buf[idx + len(CLOSE):].lstrip("\n")
                in_think = False

    if not in_think and buf:
        yield buf


async def stream_review(
    provider: str,
    endpoint_url: str,
    model_id: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    timeout: float = 120.0,
) -> AsyncIterator[str]:
    endpoint_url = endpoint_url.rstrip("/")
    if provider == "anthropic":
        inner = _stream_anthropic(
            endpoint_url, model_id, api_key, system_prompt, user_prompt, timeout
        )
    else:
        inner = _stream_openai(
            endpoint_url, model_id, api_key, system_prompt, user_prompt, timeout
        )
    async for chunk in inner:
        yield chunk


async def _stream_openai(
    endpoint_url: str,
    model_id: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    timeout: float,
) -> AsyncIterator[str]:
    # Strip /v1 suffix if the user included it in the stored endpoint URL,
    # then always append the full path, avoiding double /v1/v1/... 404s.
    base = endpoint_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    url = f"{base}/v1/chat/completions"
    headers: dict[str, str] = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {
        "model": model_id,
        "stream": True,
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    async with httpx.AsyncClient(timeout=timeout) as cx:
        async with cx.stream("POST", url, headers=headers, json=body) as resp:
            if resp.status_code != 200:
                body_text = await resp.aread()
                raise RuntimeError(f"API error {resp.status_code}: {body_text[:400]}")
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                    delta = ((obj.get("choices") or [{}])[0].get("delta")) or {}
                    text = delta.get("content") or ""
                    if text:
                        yield text
                except Exception:
                    continue


async def _stream_anthropic(
    endpoint_url: str,
    model_id: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    timeout: float,
) -> AsyncIterator[str]:
    url = f"{endpoint_url}/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": model_id,
        "max_tokens": 4096,
        "stream": True,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    async with httpx.AsyncClient(timeout=timeout) as cx:
        async with cx.stream("POST", url, headers=headers, json=body) as resp:
            if resp.status_code != 200:
                body_text = await resp.aread()
                raise RuntimeError(f"Anthropic API error {resp.status_code}: {body_text[:400]}")
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                try:
                    obj = json.loads(data)
                    if obj.get("type") == "content_block_delta":
                        text = (obj.get("delta") or {}).get("text") or ""
                        if text:
                            yield text
                except Exception:
                    continue
