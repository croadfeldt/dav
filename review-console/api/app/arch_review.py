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
    "You are a senior software architect documenting concrete enhancements "
    "to address identified coverage gaps in a system.\n\n"
    "For each gap, produce an Enhancement Specification with:\n"
    "1. Enhancement title and objective — one sentence stating what changes and why.\n"
    "2. Proposed change — the specific architectural component, pattern, or mechanism to add or modify.\n"
    "3. Implementation outline — 3–5 concrete steps an engineering team would follow.\n"
    "4. Acceptance criteria — how to verify the gap is closed, tied to the use case's success criteria.\n"
    "5. Dependencies and risks — what must be in place first, and what could go wrong.\n\n"
    "Key each enhancement to its gap ID. Be specific and actionable. "
    "Write in plain prose — no markdown headers, minimal bullet points."
)

_ENHANCEMENT_RUN_SYSTEM = (
    "You are a senior software architect documenting a prioritised enhancement roadmap "
    "to address coverage gaps across multiple use cases.\n\n"
    "Produce:\n"
    "1. Systemic enhancements — gaps appearing across multiple use cases signalling shared deficiencies.\n"
    "2. UC-specific enhancements for high-severity single-UC gaps.\n"
    "3. For each enhancement: title, proposed change, implementation outline (3–5 steps), "
    "acceptance criteria, dependencies and risks.\n"
    "4. A sequenced delivery roadmap identifying which enhancements unblock others.\n\n"
    "Reference gap IDs and UC handles. Be specific and actionable. Write in plain prose."
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
    else:
        parts.append("\nNo gaps identified.")
    parts.append("\nPlease provide the Enhancement Specification for each gap.")
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
            if g.get("recommendation"):
                parts.append(f"      Recommendation: {(g.get('recommendation') or '')[:400]}")
    parts.append("\nPlease provide the Enhancement Roadmap.")
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
