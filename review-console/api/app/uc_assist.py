"""NL-assisted UC authoring.

Configuration resolution (highest priority wins):
  1. Explicit cfg dict passed by the caller (a model_configs row with use_uc_assist=true)
  2. Environment variables (DAV_UC_ASSIST_* — fallback for installs without DB rows):
       DAV_UC_ASSIST_ENDPOINT  — base URL (default: https://api.anthropic.com)
       DAV_UC_ASSIST_API_KEY   — API key
       DAV_UC_ASSIST_MODEL     — model ID (default: claude-opus-4-7-20251001)
"""
from __future__ import annotations

import logging
import os
import re as _re
import uuid as _uuid
from typing import Any, Optional

import httpx


def _stamp_server_uuid(yaml_content: str) -> str:
    """Force a server-generated `uuid: uc-<uuid4>` onto an extracted UC draft, overriding any
    model-emitted id. LLMs produce templated, collision-prone placeholders (uc-0000...-1111...),
    so the server owns UC identity. Replaces an existing `uuid:` line (preserving formatting) or
    inserts one. Idempotent in effect — every call assigns a fresh, real UUID."""
    new_line = f"uuid: uc-{_uuid.uuid4()}"
    m = _re.search(r"(?m)^[ \t]*uuid:[ \t]*.*$", yaml_content)
    if m:
        return yaml_content[:m.start()] + new_line + yaml_content[m.end():]
    return new_line + "\n" + yaml_content

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
A DAV use case (UC) is a YAML document. All enum values below are
the actual values accepted by the engine for the DCM consumer
profile — DO NOT invent other values; the run will fail validation.

Required structure:

  title: <short human-readable name>   # 1-120 chars, shown in lists/headers
                                       # e.g. "VM provisioning — happy path"
  # NOTE: do NOT emit a `uuid` — the server assigns a real uc-<uuid> on save.
  handle: <prefix>/<category>/<descriptor>   # e.g. test/standard/vm-provision-happy
  scenario:
    description: <one-line summary>          # non-empty
    actor:
      persona: <free-text persona>           # any non-empty string
                                             # (e.g. consumer | operator | admin)
      profile: minimal | dev | standard | prod | fsi | sovereign
    intent: <what the actor wants>           # non-empty
    success_criteria:                        # at least one item
      - <observable criterion>
    dimensions:
      lifecycle_phase:      new_request | modification | decommission
                          | drift_detection | brownfield_ingestion
                          | rehydration_faithful | rehydration_provider_portable
                          | rehydration_historical_exact
                          | rehydration_historical_portable
                          | expiry_enforcement
      resource_complexity:  single_no_deps | hard_dependencies | composite_service
                          | conditional_soft_deps | process_resource
                          | cross_dependency_payload
      policy_complexity:    system_defaults_only | single_validation
                          | multi_policy_chain | conflicting_policies
                          | orchestration_flow_static | dynamic_conditional_flow
                          | cross_domain_constraint | human_escalation_required
                          | governance_matrix_enforcement | recovery_policy
      provider_landscape:   single_eligible | multiple_eligible | none_eligible
                          | peer_dcm_required | process_provider | mixed
      governance_context:   no_governance | standard_governance | audit_heavy
                          | compliance_gated | sovereignty_enforced
      failure_mode:         happy_path | provider_failure | policy_violation
                          | peer_dcm_disconnect | data_inconsistency
                          | rollback_required | partial_fulfillment | timeout
                          | resource_exhaustion
    profile: <same enum as actor.profile>
    expected_domain_interactions:
      - domain: <domain-id>
        interaction: <section reference>
  generated_by:
    mode:   regression | pr-targeted | authoring         # NO other values
    source: corpus | llm-unguided | llm-guided | human-authored
  tags: []
  metadata: {}

Validation rules the engine enforces (your YAML must satisfy these):
- `generated_by.mode` must be one of: regression, pr-targeted, authoring
- `generated_by.source` must be one of: corpus, llm-unguided, llm-guided, human-authored
- Every `dimensions.*` value MUST be picked from the lists above exactly.
- `actor.profile` and `scenario.profile` must match the same enum.
- `scenario.description`, `scenario.intent`, and `success_criteria` must be non-empty.

⛔ Common cross-dimension confusions that fail validation — DO NOT make these mistakes:

  * `expiry_enforcement` is a valid LIFECYCLE_PHASE, NOT a failure_mode.
    When a UC describes TTL / deadline / decommission-by-time scenarios:
        dimensions.lifecycle_phase: expiry_enforcement     ← correct
        dimensions.failure_mode:    happy_path             ← (or timeout, etc.)
    NOT:
        dimensions.failure_mode:    expiry_enforcement     ← WRONG, INVALID

  * `decommission`, `drift_detection`, `rehydration_*`, `brownfield_ingestion`
    are all LIFECYCLE_PHASE values, NOT failure_modes.

  * `timeout`, `resource_exhaustion`, `rollback_required`, `partial_fulfillment`
    are FAILURE_MODE values, NOT lifecycle phases.

  * `multi_policy_chain`, `conflicting_policies`, `governance_matrix_enforcement`
    are POLICY_COMPLEXITY values, NOT governance_context values.

  * `audit_heavy`, `compliance_gated`, `sovereignty_enforced`
    are GOVERNANCE_CONTEXT values, NOT failure_modes.

Rule of thumb: before emitting a dimension value, scan the lists above and
confirm the value appears in that specific dimension's list. If a concept
fits multiple dimensions (e.g., "policy enforcement" → both lifecycle and
policy), choose the dimension where it literally appears in the enum list.
"""

_SYSTEM_PROMPT = f"""You are an expert at writing DAV (Document and API Verification) use case YAML documents.
When asked to draft, modify, or improve a use case, respond with:
1. A brief explanation of what you changed and why (2-4 sentences).
2. The complete, valid YAML for the use case — nothing else after that.

The YAML block must start with exactly the line: ```yaml
and end with exactly: ```

If the user's request is ambiguous, make reasonable assumptions and note them in your explanation.

When refining an existing UC YAML the user supplied, **preserve any existing
top-of-file comment block** that starts with `# UC Assist prompts:` exactly as
it appears — this is provenance metadata the console maintains. Don't reformat
those lines or move them; just keep them and add your changes below. If the
existing YAML has no such comment block, don't invent one — the console
stamps it on apply.

{_UC_SCHEMA_HINT}
"""


def is_available() -> bool:
    """True when the env-var fallback is usable (no DB pool available)."""
    return bool(ASSIST_API_KEY and ASSIST_ENDPOINT)


def _env_fallback_config() -> Optional[dict]:
    """Return a config dict built from env vars, or None if unconfigured."""
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
    timeout: float = 300.0,
    cfg: Optional[dict] = None,
    pool: Any = None,
) -> dict:
    """Call the assist model with the user's message and optional existing YAML.

    cfg — a model_configs row dict (use_uc_assist=true).  When None,
          falls back to env-var config.  pool is kept for backward compat
          but is no longer used for config lookup.

    Returns {"explanation": str, "yaml_suggestion": str | None, "raw": str}.
    On error returns {"error": str}.
    """
    effective_cfg = cfg or _env_fallback_config()
    if not effective_cfg:
        return {"error": "UC assist not configured — add a model endpoint with UC assist enabled in Config → Models"}

    parts = []
    if current_yaml and current_yaml.strip():
        parts.append(f"Current use case YAML:\n```yaml\n{current_yaml.strip()}\n```\n")
    if context and context.strip():
        parts.append(f"Additional context:\n{context.strip()}\n")
    parts.append(f"Request: {user_message}")
    full_user = "\n".join(parts)

    try:
        if effective_cfg.get("provider") == "anthropic":
            result = await _call_anthropic(full_user, timeout, effective_cfg)
        else:
            result = await _call_openai_compat(full_user, timeout, effective_cfg)
    except Exception as e:
        log.exception("UC assist API call failed")
        return {"error": str(e)}

    return _parse_response(result)


async def _call_anthropic(user_message: str, timeout: float, cfg: dict) -> str:
    if not cfg.get("api_key"):
        raise RuntimeError("Anthropic endpoint requires an API key — set one on the model endpoint in Config → Models.")
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
    base = cfg["endpoint_url"].rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    url = f"{base}/v1/chat/completions"
    headers: dict[str, str] = {"content-type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
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


# ────────────────────────── Bulk extract (M12a) ──────────────────────────

_BULK_SYSTEM_PROMPT = f"""You are an expert at extracting distinct DAV use cases from unstructured text.

The user will paste free-form text — typically a conversation transcript, meeting
notes, a design discussion, or a customer requirements doc. Your job is to
identify each *distinct* use case latent in that text and emit one valid DAV
UC YAML document per discovered use case.

OUTPUT FORMAT (strict — the console parses this):

For each extracted UC, emit:

  ----UC-START----
  rationale: <1-2 sentences explaining what in the text led you to extract this UC>
  source_excerpt: <a short verbatim quote from the input that motivated this UC, ≤200 chars>
  ```yaml
  <complete valid UC YAML matching the schema below>
  ```
  ----UC-END----

Rules:
- Each extracted UC must be DISTINCT — don't emit the same scenario twice in
  different words. If two snippets of the transcript describe the same UC,
  merge them.
- If the text contains nothing UC-shaped, emit zero UCs and a single line:
  ----NO-UCS---- followed by a one-line explanation.
- Cap output at 12 UCs even if you see more — surface the most distinct ones.
- `generated_by.source` MUST be `llm-guided` for every extracted UC.
- `generated_by.mode` SHOULD be `authoring` (these are drafts for review).
- Set `lifecycle_state` is the console's concern, not yours — don't emit it.

{_UC_SCHEMA_HINT}
"""


async def extract_bulk(
    text: str,
    context: Optional[str] = None,
    timeout: float = 600.0,
    cfg: Optional[dict] = None,
) -> dict:
    """Extract N distinct UC drafts from a free-form text blob.

    Returns {"items": [{"rationale", "source_excerpt", "yaml_content"}, ...],
             "raw": str} on success, or {"error": str} on failure.

    Caller is responsible for persisting each item via the normal
    POST /api/use-cases path — extract_bulk only proposes, never writes.
    """
    effective_cfg = cfg or _env_fallback_config()
    if not effective_cfg:
        return {"error": "UC assist not configured — add a model endpoint with UC assist enabled in Config → Models"}

    parts = [f"Source text to extract UCs from:\n```\n{text.strip()}\n```\n"]
    if context and context.strip():
        parts.append(f"Additional context:\n{context.strip()}\n")
    parts.append("Extract every distinct UC you find, one block per UC, using the format above.")
    full_user = "\n".join(parts)

    try:
        if effective_cfg.get("provider") == "anthropic":
            raw = await _call_anthropic_with_system(full_user, _BULK_SYSTEM_PROMPT, timeout, effective_cfg)
        else:
            raw = await _call_openai_compat_with_system(full_user, _BULK_SYSTEM_PROMPT, timeout, effective_cfg)
    except Exception as e:
        log.exception("UC bulk extract API call failed")
        return {"error": str(e)}

    result = _parse_bulk_response(raw)
    # The server owns UC identity — stamp a real uc-<uuid4> on every draft, ignoring any
    # model-emitted uuid (LLMs emit templated, colliding placeholders). See #199/#182.
    for item in result.get("items", []):
        if item.get("yaml_content"):
            item["yaml_content"] = _stamp_server_uuid(item["yaml_content"])
    return result


async def _call_anthropic_with_system(user_message: str, system: str, timeout: float, cfg: dict) -> str:
    if not cfg.get("api_key"):
        raise RuntimeError("Anthropic endpoint requires an API key.")
    url = f"{cfg['endpoint_url'].rstrip('/')}/v1/messages"
    headers = {
        "x-api-key": cfg["api_key"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": cfg["model_id"],
        "max_tokens": 8192,
        "system": system,
        "messages": [{"role": "user", "content": user_message}],
    }
    async with httpx.AsyncClient(timeout=timeout) as cx:
        resp = await cx.post(url, headers=headers, json=body)
    if resp.status_code != 200:
        raise RuntimeError(f"Anthropic API error {resp.status_code}: {resp.text[:400]}")
    data = resp.json()
    content = data.get("content") or []
    return "".join(c.get("text", "") for c in content if c.get("type") == "text")


async def _call_openai_compat_with_system(user_message: str, system: str, timeout: float, cfg: dict) -> str:
    base = cfg["endpoint_url"].rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    url = f"{base}/v1/chat/completions"
    headers: dict[str, str] = {"content-type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    body = {
        "model": cfg["model_id"],
        "max_tokens": 8192,
        "messages": [
            {"role": "system", "content": system},
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


def _parse_bulk_response(raw: str) -> dict:
    """Parse the model's multi-UC output. Tolerates extra prose between blocks.

    Each UC block looks like:
        ----UC-START----
        rationale: <text>
        source_excerpt: <text>
        ```yaml
        <yaml>
        ```
        ----UC-END----
    """
    import re
    if "----NO-UCS----" in raw:
        msg = raw.split("----NO-UCS----", 1)[1].strip()
        return {"items": [], "raw": raw, "no_ucs_reason": msg}

    items = []
    block_re = re.compile(
        r"----UC-START----(.*?)----UC-END----",
        re.DOTALL,
    )
    yaml_re = re.compile(r"```yaml\s*\n(.*?)```", re.DOTALL)
    rationale_re = re.compile(r"^rationale:\s*(.+)$", re.MULTILINE)
    excerpt_re = re.compile(r"^source_excerpt:\s*(.+)$", re.MULTILINE)

    for m in block_re.finditer(raw):
        block = m.group(1)
        ym = yaml_re.search(block)
        if not ym:
            continue
        yaml_content = ym.group(1).strip()
        rm = rationale_re.search(block)
        em = excerpt_re.search(block)
        items.append({
            "yaml_content": yaml_content,
            "rationale": rm.group(1).strip() if rm else "",
            "source_excerpt": em.group(1).strip() if em else "",
        })

    return {"items": items, "raw": raw}
