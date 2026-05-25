"""NL-assisted UC authoring.

Configuration priority (highest wins):
  1. DB row in uc_assist_config (managed via Config → UC Assist in the UI)
  2. Environment variables:
       DAV_UC_ASSIST_ENDPOINT  — base URL (default: https://api.anthropic.com)
       DAV_UC_ASSIST_API_KEY   — API key
       DAV_UC_ASSIST_MODEL     — model ID (default: claude-opus-4-7-20251001)

Pass the asyncpg pool to chat() so it can resolve DB config at call time.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

log = logging.getLogger("dav-review-api.uc_assist")

ASSIST_ENDPOINT = os.environ.get(
    "DAV_UC_ASSIST_ENDPOINT", "https://api.anthropic.com"
).rstrip("/")
ASSIST_API_KEY = os.environ.get("DAV_UC_ASSIST_API_KEY", "")
ASSIST_MODEL = os.environ.get(
    "DAV_UC_ASSIST_MODEL", "claude-opus-4-7-20251001"
)

# Injected at startup — UC YAML schema for grounding the model.
_UC_SCHEMA_HINT = """
A DAV use case (UC) is a YAML document with this top-level structure:

  uuid: <uuid-string>               # globally unique, kebab-case
  handle: <prefix>/<category>/<descriptor>   # e.g. test/standard/vm-provision-happy
  scenario:
    description: <one-line summary>
    actor:
      persona: consumer | operator | admin
      profile: standard | fsi | sovereign | minimal | prod
    intent: <what the actor wants>
    success_criteria:
      - <criterion>
    dimensions:
      lifecycle_phase: new_request | in_flight | post_completion
      resource_complexity: single_no_deps | single_with_deps | multi_resource
      policy_complexity: system_defaults_only | custom_policies | multi_policy
      provider_landscape: single_eligible | multi_eligible | no_eligible
      governance_context: standard_governance | elevated_governance
      failure_mode: happy_path | partial_failure | total_failure
    profile: <same as actor.profile>
    expected_domain_interactions:
      - domain: <domain-id>
        interaction: <section reference>
  generated_by:
    mode: regression | human-authored | nl-assisted
    source: <author or tool>
  tags: []
  metadata: {}
"""

_SYSTEM_PROMPT = f"""You are an expert at writing DAV (Document and API Verification) use case YAML documents.
When asked to draft, modify, or improve a use case, respond with:
1. A brief explanation of what you changed and why (2-4 sentences).
2. The complete, valid YAML for the use case — nothing else after that.

The YAML block must start with exactly the line: ```yaml
and end with exactly: ```

If the user's request is ambiguous, make reasonable assumptions and note them in your explanation.

{_UC_SCHEMA_HINT}
"""


def is_available() -> bool:
    """True when the env-var fallback is usable (no DB pool available)."""
    return bool(ASSIST_API_KEY and ASSIST_ENDPOINT)


async def get_db_config(pool) -> Optional[dict]:
    """Return the DB-stored config dict, or None if absent/disabled."""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM uc_assist_config WHERE id=1")
        if row and row["enabled"] and row["api_key"]:
            return dict(row)
    except Exception:
        log.exception("Failed to read uc_assist_config from DB")
    return None


def _resolve_config(db_cfg: Optional[dict]) -> Optional[dict]:
    """Return the effective config (DB preferred, env-var fallback)."""
    if db_cfg:
        return db_cfg
    if ASSIST_API_KEY and ASSIST_ENDPOINT:
        return {
            "provider": "anthropic" if "anthropic.com" in ASSIST_ENDPOINT else "openai",
            "endpoint_url": ASSIST_ENDPOINT,
            "model_id": ASSIST_MODEL,
            "api_key": ASSIST_API_KEY,
        }
    return None


async def chat(
    user_message: str,
    current_yaml: Optional[str] = None,
    context: Optional[str] = None,
    timeout: float = 60.0,
    pool: Any = None,
) -> dict:
    """Call the assist model with the user's message and optional existing YAML.

    Returns {"explanation": str, "yaml_suggestion": str | None, "raw": str}.
    On error returns {"error": str}.
    """
    db_cfg = await get_db_config(pool) if pool is not None else None
    cfg = _resolve_config(db_cfg)
    if not cfg:
        return {"error": "UC assist not configured — add credentials in Config → UC Assist"}

    parts = []
    if current_yaml and current_yaml.strip():
        parts.append(f"Current use case YAML:\n```yaml\n{current_yaml.strip()}\n```\n")
    if context and context.strip():
        parts.append(f"Additional context:\n{context.strip()}\n")
    parts.append(f"Request: {user_message}")
    full_user = "\n".join(parts)

    try:
        if cfg.get("provider") == "anthropic":
            result = await _call_anthropic(full_user, timeout, cfg)
        else:
            result = await _call_openai_compat(full_user, timeout, cfg)
    except Exception as e:
        log.exception("UC assist API call failed")
        return {"error": str(e)}

    return _parse_response(result)


async def _call_anthropic(user_message: str, timeout: float, cfg: dict) -> str:
    url = f"{cfg['endpoint_url'].rstrip('/')}/v1/messages"
    headers = {
        "x-api-key": cfg["api_key"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": cfg["model_id"],
        "max_tokens": 4096,
        "system": _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_message}],
    }
    async with httpx.AsyncClient(timeout=timeout) as cx:
        resp = await cx.post(url, headers=headers, json=body)
    if resp.status_code != 200:
        raise RuntimeError(f"Anthropic API error {resp.status_code}: {resp.text[:400]}")
    data = resp.json()
    content = data.get("content") or []
    return "".join(c.get("text", "") for c in content if c.get("type") == "text")


async def _call_openai_compat(user_message: str, timeout: float, cfg: dict) -> str:
    url = f"{cfg['endpoint_url'].rstrip('/')}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "content-type": "application/json",
    }
    body = {
        "model": cfg["model_id"],
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    }
    async with httpx.AsyncClient(timeout=timeout) as cx:
        resp = await cx.post(url, headers=headers, json=body)
    if resp.status_code != 200:
        raise RuntimeError(f"API error {resp.status_code}: {resp.text[:400]}")
    data = resp.json()
    choices = data.get("choices") or []
    return (choices[0].get("message") or {}).get("content", "") if choices else ""


def _parse_response(raw: str) -> dict:
    """Extract explanation + YAML block from the model's response."""
    import re
    yaml_match = re.search(r"```yaml\s*\n(.*?)```", raw, re.DOTALL)
    yaml_suggestion = yaml_match.group(1).strip() if yaml_match else None
    if yaml_match:
        explanation = raw[:yaml_match.start()].strip()
    else:
        explanation = raw.strip()
    return {
        "explanation": explanation,
        "yaml_suggestion": yaml_suggestion,
        "raw": raw,
    }
