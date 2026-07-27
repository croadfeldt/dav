"""
Inference client wrapper.

Thin layer over OpenAI-compatible HTTP (vLLM, Ollama, upstream OpenAI).
Supports:
  - Chat completions with tool calling
  - Guided JSON decoding via vLLM's `extra_body` extension
  - Primary + fallback endpoint with automatic failover

Endpoint config comes from caller (not from env) so the engine can
be tested with any endpoint — 14B fallback during dev, R9700 70B
later, or upstream OpenAI in CI.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable
import requests

log = logging.getLogger(__name__)

class InferenceError(Exception):
    pass

# Retry policy for transient inference failures (production incident
# 2026-07: a 503 window at run start turned every request into an
# immediate InferenceError and burned the whole corpus loop in seconds).
# Classification:
#   retryable — 429, any 5xx, connection errors, connect timeouts
#               (the endpoint is down/overloaded but may recover)
#   fatal     — other 4xx (the request itself is wrong; retrying can't
#               help), read timeouts (the server already consumed a full
#               timeout_seconds budget; retrying doubles the damage)
# Exponential backoff 2s → 4s → 8s → 16s → give up (30s cap keeps any
# single sleep bounded); ~5 attempts and ≤ ~2 min total added latency.
_RETRY_MAX_ATTEMPTS = 5
_RETRY_BACKOFF_INITIAL_S = 2.0
_RETRY_BACKOFF_CAP_S = 30.0
_RETRY_TOTAL_BUDGET_S = 120.0


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500

@dataclass
class EndpointConfig:
    url: str                                   # e.g. http://vllm-tier3.../v1
    model: str                                 # served-model-name
    api_key: str = "no-key-needed"             # vLLM ignores; OpenAI requires
    timeout_seconds: int = 900
    label: str = "primary"                     # for logging
    chat_template_kwargs: dict[str, Any] | None = None
    # Optional dict forwarded to the server as OpenAI-extension
    # `chat_template_kwargs`, which the server passes into Jinja2 chat
    # template rendering. Used for Qwen3's `enable_thinking` flag in
    # particular: `{"enable_thinking": False}` disables <think>...</think>
    # reasoning blocks, which is almost always right for tool-use loops.
    # Templates without matching kwargs ignore the field.
    temperature: float | None = None
    # Sampling temperature actually used for this run. Stored on the
    # endpoint so effective_sampling() can surface it alongside the
    # other params even though the HTTP body sets temperature
    # separately (via the request, not the endpoint defaults).
    max_tokens: int | None = None
    # Same rationale — surfaced via effective_sampling for output
    # denotation. The actual cap is enforced per-call in AgentConfig.
    cache_prompt: bool = False
    # Whether to allow llama.cpp's cross-request KV cache reuse. The
    # field default here is conservatively False, but per-mode defaults
    # in dav.stages.stage2_analyze and dav.stages.run_corpus override
    # this for verification and explore modes (where True is correct).
    # Reproduce mode keeps False to preserve byte-identical reruns.
    #
    # Background: prompt caching reuses KV values from a prior request
    # in a specific FP trajectory; that trajectory depends on what the
    # prior request was; so "same prompt twice" can produce different
    # final logits at argmax-tie boundaries. See llama.cpp discussion
    # #10311. The cost in correctness is tiny logit-level variance;
    # the win is 5-10x speedup on agentic workloads where each turn
    # extends the previous request's prompt by a small delta.
    #
    # DAV's framing of "predictable correctness" via N-sample ensemble
    # absorbs this kind of variance. The locked default for verification
    # is True since CI/regression at production scale needs the speedup
    # and the ensemble already handles variance. Reproduce mode keeps
    # False because byte-identical reruns are its explicit purpose.
    deterministic: bool = True
    # Whether the endpoint is configured for deterministic decoding. Affects
    # default temperature and seed handling at the client.chat() layer.
    # When False, callers may pass higher temperatures and skip the seed.

    top_k: int | None = None
    top_p: float | None = None
    min_p: float | None = None
    # Sampler params. When None, the field is omitted from the request body
    # and the server's CLI default applies. When set, the per-request value
    # overrides whatever the server has defaulted.
    capabilities: dict = field(default_factory=dict)
    # Static facts about what the server side of this endpoint supports.
    # Populated from model_configs.capabilities (DAV migration 014) and
    # threaded through the PipelineRun → Tekton task → engine CLI as a
    # JSON blob (--capabilities-json). Recognized keys:
    #   speculative_decoding: bool  — when true, drops min_p+logit_bias
    #   supports_min_p: bool        — defaults true; false forces drop
    #   supports_logit_bias: bool   — defaults true; false forces drop
    #   max_tokens_default: int     — applied when nothing else sets it
    # Engine treats these as authoritative: a flag set False here drops
    # the param regardless of whether a use_profile or CLI override set
    # it. Negation-only: if a key is absent the engine assumes "supported".
    use_key: str | None = None
    # Identifies the calling context: evaluation_verification,
    # evaluation_explore, evaluation_reproduce, arch_review, uc_assist,
    # enhancement. Used for the effective-sampling log line and the
    # output denotation; doesn't change body construction directly.
    #
    # Why this matters: we saw an llama.cpp inference server launched
    # with --top-k 1, which makes the sampler strictly greedy
    # regardless of temperature or seed. Per-request overrides only apply
    # to the fields you send; unsent fields keep the CLI default. So a body
    # of {temperature: 0.2, seed: <varying>} produces identical output across
    # seeds because top_k=1 (the CLI default) overrides any sampling that
    # temperature/seed would otherwise drive.
    #
    # The fix is per-request explicit sampler params in modes that want
    # variance:
    #   verification, explore: top_k=40, top_p=0.95, min_p=0.05 (or similar)
    #   reproduce:             top_k=1 (explicit greedy, portable to other
    #                          servers that don't ship --top-k 1 as default)
    #
    # Per-mode defaults are populated in dav.stages.run_corpus and
    # dav.stages.stage2_analyze; this dataclass just stores them.


def effective_sampling(endpoint: "EndpointConfig") -> dict:
    """Return the per-request sampling shape the engine WILL send, after
    capabilities filtering. Sources of truth for output denotation in
    run-summary.yaml and per-UC AnalysisMetadata. Read at run start when
    the endpoint is constructed; mirrors the logic in _build_body. Keep
    in sync if _build_body's drop conditions change.
    """
    caps = endpoint.capabilities or {}
    sent: dict = {}
    dropped: dict = {}
    if endpoint.temperature is not None:
        sent["temperature"] = endpoint.temperature
    if endpoint.top_k is not None:
        sent["top_k"] = endpoint.top_k
    if endpoint.top_p is not None:
        sent["top_p"] = endpoint.top_p
    if endpoint.min_p is not None:
        if caps.get("supports_min_p") is False or caps.get("speculative_decoding") is True:
            dropped["min_p"] = endpoint.min_p
        else:
            sent["min_p"] = endpoint.min_p
    if endpoint.max_tokens is not None:
        sent["max_tokens"] = endpoint.max_tokens
    if endpoint.chat_template_kwargs:
        sent["chat_template_kwargs"] = dict(endpoint.chat_template_kwargs)
    return {
        "use_key": endpoint.use_key,
        "model": endpoint.model,
        "endpoint_url": endpoint.url,
        "sent": sent,
        "dropped": dropped,
        "capabilities": dict(caps),
    }


@dataclass
class ChatMessage:
    role: str                                  # system | user | assistant | tool
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None

    def to_openai(self) -> dict[str, Any]:
        # Qwen3's chat template is stricter than OpenAI's spec. Observed
        # failure modes on llama.cpp b6652 with Qwen3-32B:
        #   1. Null or missing content crashes message.content[:N] slicing
        #      with "Value is not an array or object: null". OpenAI's spec
        #      permits null content when tool_calls is set; Qwen3's template
        #      does not. Fix: always serialize content as a string.
        #   2. Tool-role messages are expected to be wrapped in
        #      <tool_response>...</tool_response>. Without the wrapper the
        #      binding between tool call and response is lost.
        #   3. Assistant messages with tool_calls need their tool_calls
        #      rendered INSIDE the content field as Qwen3-native XML:
        #        <tool_call>{"name": "...", "arguments": {...}}</tool_call>
        #      Qwen3's template scans content for this marker to reconstruct
        #      the call. Empty-string content with a separate tool_calls
        #      array (OpenAI convention) crashes on the slicing logic.
        content = self.content if isinstance(self.content, str) else ""

        if self.role == "tool":
            # Wrap tool responses in Qwen3's expected markers.
            if not content.startswith("<tool_response>"):
                content = f"<tool_response>\n{content}\n</tool_response>"

        if self.role == "assistant" and self.tool_calls and "<tool_call>" not in content:
            # Render tool_calls as Qwen3-native XML. The OpenAI tool_calls
            # field stays alongside (harmless on backends that use it).
            tc_blocks = []
            for tc in self.tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                # arguments may be a JSON string or an already-parsed dict
                if isinstance(raw_args, str):
                    args_str = raw_args
                else:
                    args_str = json.dumps(raw_args)
                tc_blocks.append(
                    f'<tool_call>\n{{"name": "{name}", "arguments": {args_str}}}\n</tool_call>'
                )
            # Prepend any existing content (usually empty) with the tool_call blocks
            rendered = "\n".join(tc_blocks)
            content = f"{content}\n{rendered}" if content else rendered

        m: dict[str, Any] = {"role": self.role, "content": content}
        if self.name:
            m["name"] = self.name
        if self.tool_call_id:
            m["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            m["tool_calls"] = self.tool_calls
        return m

@dataclass
class ToolDefinition:
    """OpenAI-style tool definition (the `type=function` variant)."""
    name: str
    description: str
    parameters: dict[str, Any]                 # JSON Schema

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

@dataclass
class ChatResponse:
    content: str
    tool_calls: list[dict[str, Any]]           # raw openai tool_calls array
    finish_reason: str
    usage: dict[str, int]                      # {prompt_tokens, completion_tokens, ...}
    endpoint_used: str                         # which endpoint served the response
    # Chain-of-thought from reasoning models (gpt-oss harmony, Qwen thinking, ...).
    # Previously parsed and discarded, which made "the model reasoned instead of
    # answering" indistinguishable from "the model returned nothing" one layer up.
    # Carrying it is what lets the client widen the budget and retry instead of
    # failing the use case.
    reasoning_content: str = ""

def _is_anthropic(endpoint: "EndpointConfig") -> bool:
    """Route to the native Anthropic Messages API (for prompt caching + the
    different request/response shape) when the endpoint is Claude. Detected by
    the host or a claude-* model id, so a model_config pointing at
    https://api.anthropic.com/v1 with model 'claude-*' just works."""
    return ("anthropic" in (endpoint.url or "").lower()
            or (endpoint.model or "").lower().startswith("claude"))


class InferenceClient:
    """
    OpenAI-compatible chat client with optional fallback.

    Usage:
        client = InferenceClient(
            primary=EndpointConfig(url="http://vllm...", model="qwen", label="primary"),
            fallback=EndpointConfig(url="http://vllm-tier3...", model="qwen-14b", label="fallback"),
        )
        resp = client.chat(messages=[...], tools=[...], temperature=0.0)
    """

    # Multiplier applied to max_tokens once an endpoint is observed to reason.
    # A reasoning model spends its budget on reasoning_content BEFORE emitting
    # content, so a budget sized for the answer alone yields an empty answer.
    REASONING_HEADROOM = 4
    # Never ask a reasoning model for less than this. The agent narrows max_tokens
    # as context fills; below this floor a reasoning model cannot finish thinking
    # AND still emit an analysis, so the turn is wasted no matter how good it is.
    REASONING_MIN_TOKENS = 2048

    def __init__(self, primary: EndpointConfig,
                 fallback: EndpointConfig | None = None):
        self.primary = primary
        self.fallback = fallback
        # Set the first time an endpoint returns reasoning_content. Detected rather
        # than configured: whether a model reasons is a property of the model and
        # its chat template, not something an operator should have to declare, and
        # getting it wrong silently costs whole use cases.
        self._reasoning_observed = False


    def _reasoning_budget(self, max_tokens: int) -> int:
        """Widen a token budget for an endpoint known to emit reasoning_content.

        No-op until reasoning has actually been observed, so non-reasoning models
        (and the sampling recorded in run provenance) are completely unaffected.
        """
        if not self._reasoning_observed:
            return max_tokens
        return max(self.REASONING_MIN_TOKENS, max_tokens * self.REASONING_HEADROOM)

    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        guided_json_schema: dict[str, Any] | None = None,
        seed: int | None = None,
    ) -> ChatResponse:
        if _is_anthropic(self.primary):
            return self._chat_anthropic(self.primary, messages, tools,
                                        temperature, max_tokens, seed)
        # Once we know this endpoint reasons, pre-emptively widen the budget so the
        # answer is not starved by the thinking that precedes it.
        effective_max_tokens = self._reasoning_budget(max_tokens)
        body = self._build_body(self.primary, messages, tools, temperature,
                                 effective_max_tokens, guided_json_schema, seed)
        try:
            resp = self._post(self.primary, body)
            # Empty content + reasoning + no tool calls = the model thought until it
            # ran out of room. Retry ONCE with a widened budget rather than handing
            # the agent an empty response it can only fail on. Bounded: the retry
            # flips _reasoning_observed first, so the second attempt already carries
            # headroom and cannot recurse.
            if (not resp.content and not resp.tool_calls
                    and getattr(resp, "reasoning_content", "")
                    and not self._reasoning_observed):
                self._reasoning_observed = True
                widened = self._reasoning_budget(max_tokens)
                log.warning(
                    "%s: reasoning model detected — retrying once with "
                    "max_tokens %d -> %d",
                    self.primary.label, effective_max_tokens, widened,
                )
                retry_body = self._build_body(self.primary, messages, tools,
                                              temperature, widened,
                                              guided_json_schema, seed)
                resp = self._post(self.primary, retry_body)
            return resp
        except InferenceError as e:
            if self.fallback is None:
                raise
            log.warning("primary endpoint %s failed: %s; trying fallback %s",
                        self.primary.label, e, self.fallback.label)
            # Rebuild for fallback: model and chat_template_kwargs may differ.
            body = self._build_body(self.fallback, messages, tools, temperature,
                                     max_tokens, guided_json_schema, seed)
            return self._post(self.fallback, body)

    # --- Anthropic native path (prompt caching + Messages-API format) ---
    # NOTE: built model-agnostically; NEEDS end-to-end validation against the live
    # API once a key is configured (untested without one). guided_json is not
    # supported by Anthropic — Claude produces the final JSON from the prompt
    # instruction instead (validate final-JSON reliability on the first run).

    @staticmethod
    def _to_anthropic(messages: list[ChatMessage]) -> tuple[Any, list[dict[str, Any]]]:
        """OpenAI-format ChatMessage list -> (system_blocks, anthropic_messages).
        The FIRST system message becomes the top-level system (cache_control'd);
        any later system message (the retrieval memo) is appended as a text block
        to the trailing user message. Tool results become tool_result blocks
        batched into user messages; assistant tool_calls become tool_use blocks.
        Consecutive same-role messages are merged (Anthropic requires alternation).
        A rolling cache breakpoint is pinned on the last tool_result so the
        conversation-so-far re-bills at ~0.1x like the static system prefix."""
        system_text = None
        a_msgs: list[dict[str, Any]] = []

        def _user(blocks):
            if a_msgs and a_msgs[-1]["role"] == "user":
                a_msgs[-1]["content"].extend(blocks)
            else:
                a_msgs.append({"role": "user", "content": list(blocks)})

        for m in messages:
            content = m.content if isinstance(m.content, str) else ""
            if m.role == "system":
                if system_text is None:
                    system_text = content
                else:
                    _user([{"type": "text", "text": content}])          # retrieval memo
            elif m.role == "user":
                _user([{"type": "text", "text": content}])
            elif m.role == "tool":
                _user([{"type": "tool_result", "tool_use_id": m.tool_call_id,
                        "content": content}])
            elif m.role == "assistant":
                blocks: list[dict[str, Any]] = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for tc in (m.tool_calls or []):
                    fn = tc.get("function", {})
                    raw = fn.get("arguments", "{}")
                    try:
                        inp = json.loads(raw) if isinstance(raw, str) else raw
                    except json.JSONDecodeError:
                        inp = {}
                    blocks.append({"type": "tool_use", "id": tc.get("id"),
                                   "name": fn.get("name", ""), "input": inp})
                if not blocks:
                    blocks = [{"type": "text", "text": ""}]
                if a_msgs and a_msgs[-1]["role"] == "assistant":
                    a_msgs[-1]["content"].extend(blocks)
                else:
                    a_msgs.append({"role": "assistant", "content": blocks})

        system_blocks = None
        if system_text is not None:
            system_blocks = [{"type": "text", "text": system_text,
                              "cache_control": {"type": "ephemeral"}}]
        for msg in reversed(a_msgs):                                    # rolling breakpoint
            done = False
            for blk in reversed(msg["content"]):
                if blk.get("type") == "tool_result":
                    blk["cache_control"] = {"type": "ephemeral"}
                    done = True
                    break
            if done:
                break
        return system_blocks, a_msgs

    def _chat_anthropic(self, endpoint, messages, tools, temperature, max_tokens, seed):
        system_blocks, a_msgs = self._to_anthropic(messages)
        body: dict[str, Any] = {"model": endpoint.model, "max_tokens": max_tokens,
                                "messages": a_msgs}
        # Opus 4.7+ removed sampling params entirely — sending temperature
        # returns a 400. Earlier Claude models (Sonnet 4.6 etc.) still accept it.
        # (seed is never sent: the Anthropic API has no seed param; ensemble
        # variance comes from sampling. v1 also omits `thinking` — adaptive
        # thinking would require round-tripping thinking blocks through the
        # tool-use loop, which ChatMessage doesn't carry yet; follow-up.)
        if not endpoint.model.startswith(("claude-opus-4-7", "claude-opus-4-8")):
            body["temperature"] = temperature
        if system_blocks:
            body["system"] = system_blocks
        if tools:
            body["tools"] = [{"name": t.name, "description": t.description,
                              "input_schema": t.parameters} for t in tools]
        url = f"{endpoint.url.rstrip('/')}/messages"
        headers = {"x-api-key": endpoint.api_key, "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        try:
            r = requests.post(url, headers=headers, json=body,
                              timeout=endpoint.timeout_seconds)
        except requests.RequestException as e:
            raise InferenceError(f"request to {endpoint.label} (anthropic) failed: {e}") from e
        if r.status_code != 200:
            raise InferenceError(f"{endpoint.label} (anthropic) returned {r.status_code}: {r.text[:500]}")
        try:
            data = r.json()
        except json.JSONDecodeError as e:
            raise InferenceError(f"{endpoint.label} returned non-JSON: {r.text[:500]}") from e
        text_parts, tool_calls = [], []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append({"id": block.get("id"), "type": "function",
                                   "function": {"name": block.get("name"),
                                                "arguments": json.dumps(block.get("input") or {})}})
        u = data.get("usage", {}) or {}
        # Anthropic's input_tokens is the UNCACHED remainder only; the true
        # prompt size = input + cache_creation + cache_read. The agent budgets
        # max_tokens off prompt_tokens, so report the sum or the context-ceiling
        # math silently breaks the moment caching starts hitting.
        cache_w = u.get("cache_creation_input_tokens", 0) or 0
        cache_r = u.get("cache_read_input_tokens", 0) or 0
        in_tok = (u.get("input_tokens", 0) or 0) + cache_w + cache_r
        out_tok = u.get("output_tokens", 0) or 0
        usage = {"prompt_tokens": in_tok, "completion_tokens": out_tok,
                 "total_tokens": in_tok + out_tok,
                 "cache_creation_input_tokens": cache_w,
                 "cache_read_input_tokens": cache_r}
        return ChatResponse(content="".join(text_parts), tool_calls=tool_calls,
                            finish_reason=data.get("stop_reason") or "stop",
                            usage=usage, endpoint_used=endpoint.label)

    def list_models(self, endpoint: EndpointConfig | None = None) -> list[str]:
        """For health checks."""
        endpoint = endpoint or self.primary
        try:
            r = requests.get(
                f"{endpoint.url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {endpoint.api_key}"},
                timeout=10,
            )
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", [])]
        except Exception as e:
            raise InferenceError(f"list_models failed at {endpoint.url}: {e}") from e

    # --- internals ---

    def _build_body(
        self,
        endpoint: EndpointConfig,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None,
        temperature: float,
        max_tokens: int,
        guided_json_schema: dict[str, Any] | None,
        seed: int | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": endpoint.model,
            "messages": [m.to_openai() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            # llama.cpp's cross-request KV cache reuse. When enabled (the
            # llama.cpp default), the server keeps the prior request's KV
            # cache and reuses the longest common prefix against the current
            # llama.cpp-specific extension: reuse KV-cache from prior
            # request when the prefix matches. 5-10x speedup on agentic
            # workloads. Introduces tiny logit-level variance vs cold
            # prefill at argmax-tie boundaries (see llama.cpp #10311);
            # DAV's predictable-correctness framing absorbs this via
            # N-sample ensemble in verification mode. Reproduce mode
            # forces False since byte-identical reruns are its purpose.
            # configurable via endpoint.cache_prompt; per-mode defaults
            # are set at the stage layer (verification=True, reproduce=False,
            # explore=True). The field is a no-op on OpenAI/vLLM backends,
            # so it costs nothing there.
            "cache_prompt": endpoint.cache_prompt,
        }
        if tools:
            body["tools"] = [t.to_openai() for t in tools]
            body["tool_choice"] = "auto"
            # Disable parallel tool calls so the model emits ONE tool_call
            # per response and waits for the result before deciding what to
            # call next. This prevents pathological decoder states where the
            # model emits the same tool_call 4-16 times in parallel
            # (observed in Qwen3-32B at temperature 0.2). vLLM honors this
            # OpenAI-compatible flag; backends that ignore it are no worse
            # off than today.
            body["parallel_tool_calls"] = False
        if guided_json_schema:
            # `extra_body` is an OpenAI *client-library* concept — the Python SDK
            # lifts its contents into the top level of the request. We build the
            # request body by hand, so sending a literal "extra_body" key put the
            # schema somewhere no server reads. Both vLLM and llama.cpp accepted
            # the request and returned unconstrained prose, which means structured
            # output has been inert on every backend — including the "re-emit once
            # with guided schema" recovery path, which was re-asking with no
            # constraint at all.
            #
            # response_format/json_schema is the OpenAI-standard form and is
            # honored by both backends we serve (verified live against vLLM and
            # llama.cpp; see test_structured_output.py for the exact shapes).
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "dav_analysis", "schema": guided_json_schema},
            }
        if seed is not None:
            body["seed"] = seed
        # Per-request sampler params. Diagnosed bug 2026-04-26: relying on
        # server CLI defaults for top_k/top_p/min_p produced silent greedy
        # decoding (CLI --top-k 1) regardless of temperature or seed. Explicit
        # per-mode values fix it. None means "let server default apply" — only
        # use that path when the server is known to default sensibly OR when
        # the caller is explicit about wanting greedy.
        #
        # endpoint.capabilities (DAV migration 014) is the authoritative
        # negative filter: a flag set False drops the param even when the
        # caller set a non-None value. Keys absent from capabilities default
        # to "supported". Today this handles speculative-decoding's
        # min_p/logit_bias prohibition; new capability keys plug in here.
        caps = endpoint.capabilities or {}
        if endpoint.top_k is not None:
            body["top_k"] = endpoint.top_k
        if endpoint.top_p is not None:
            body["top_p"] = endpoint.top_p
        # Capabilities-driven drop: speculative_decoding=True OR
        # supports_min_p=False both suppress min_p (vLLM rejects min_p with
        # speculative decoding; first surfaced 2026-05-29 on Qwen3.6-27B MTP).
        if endpoint.min_p is not None:
            if caps.get("supports_min_p") is False or caps.get("speculative_decoding") is True:
                pass  # dropped — see endpoint.capabilities
            else:
                body["min_p"] = endpoint.min_p
        if endpoint.chat_template_kwargs:
            body["chat_template_kwargs"] = endpoint.chat_template_kwargs
        return body

    def _post(self, endpoint: EndpointConfig, body: dict[str, Any]) -> ChatResponse:
        url = f"{endpoint.url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {endpoint.api_key}",
            "Content-Type": "application/json",
        }
        # Move extra_body out for vLLM — some vLLM versions accept it at the
        # top level; others want it inlined. Try inlined first.
        extra_body = body.pop("extra_body", None)
        if extra_body:
            body.update(extra_body)

        # Diagnostic: dump message roles + content lengths. Surfaces the
        # Qwen3 template failure mode (null/missing content crashing
        # message.content[:N] slicing) without leaking corpus content.
        if log.isEnabledFor(logging.DEBUG):
            summary = [
                {
                    "role": m.get("role"),
                    "content_type": type(m.get("content")).__name__,
                    "content_len": len(m["content"]) if isinstance(m.get("content"), str) else None,
                    "has_tool_calls": bool(m.get("tool_calls")),
                    "tool_call_id": m.get("tool_call_id"),
                }
                for m in body.get("messages", [])
            ]
            log.debug("%s outgoing messages: %s", endpoint.label, summary)

        # Retry loop for transient failures (see _RETRY_* constants above).
        # Fatal errors raise InferenceError immediately, which preserves the
        # fallback-endpoint behavior in chat(): the fallback is tried only
        # after the primary's retries are exhausted (or a fatal error).
        attempt = 0
        backoff = _RETRY_BACKOFF_INITIAL_S
        retry_started = time.monotonic()
        while True:
            attempt += 1
            retryable = False
            r = None
            try:
                r = requests.post(url, headers=headers, json=body,
                                  timeout=endpoint.timeout_seconds)
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.ConnectTimeout) as e:
                # Endpoint unreachable / connect timed out — transient.
                retryable = True
                err = f"request to {endpoint.label} failed: {e}"
                cause = e
            except requests.RequestException as e:
                # Read timeouts and everything else: fatal. A read timeout
                # means the server already burned timeout_seconds on this
                # request; retrying would double the damage.
                raise InferenceError(f"request to {endpoint.label} failed: {e}") from e
            else:
                if r.status_code == 200:
                    break
                err = f"{endpoint.label} returned {r.status_code}: {r.text[:500]}"
                cause = None
                retryable = _is_retryable_status(r.status_code)
                if not retryable:
                    # Other 4xx — the request itself is wrong; retrying can't help.
                    raise InferenceError(err)

            elapsed = time.monotonic() - retry_started
            if (attempt >= _RETRY_MAX_ATTEMPTS
                    or elapsed + backoff > _RETRY_TOTAL_BUDGET_S):
                final = (f"{err} (giving up after {attempt} attempt(s), "
                         f"{elapsed:.0f}s)")
                if cause is not None:
                    raise InferenceError(final) from cause
                raise InferenceError(final)
            log.warning(
                "transient inference error from %s (attempt %d/%d): %s — "
                "retrying in %.0fs",
                endpoint.label, attempt, _RETRY_MAX_ATTEMPTS, err, backoff,
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, _RETRY_BACKOFF_CAP_S)

        try:
            data = r.json()
        except json.JSONDecodeError as e:
            raise InferenceError(f"{endpoint.label} returned non-JSON: {r.text[:500]}") from e

        choices = data.get("choices", [])
        if not choices:
            raise InferenceError(f"{endpoint.label} returned no choices: {data}")
        msg = choices[0].get("message", {})
        content = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or ""
        tool_calls = msg.get("tool_calls") or []

        # Defensive: if content is empty, reasoning_content is non-empty, and
        # there are no tool_calls, the model emitted thinking-mode output
        # instead of a real response. /no_think should prevent this, but if
        # the chat template ignored the directive or max_tokens was
        # exhausted by the thinking chain, we surface it loudly instead of
        # silently returning empty content and letting the agent fail in
        # mysterious ways downstream.
        if not content and not tool_calls and reasoning:
            log.warning(
                "%s returned empty content with non-empty reasoning_content "
                "(%d chars). /no_think may not be honored by the backend's "
                "chat template, or max_tokens (%d) was consumed by thinking. "
                "reasoning preview: %r",
                endpoint.label,
                len(reasoning),
                data.get("usage", {}).get("completion_tokens", -1),
                reasoning[:200],
            )

        return ChatResponse(
            reasoning_content=reasoning,
            content=content,
            tool_calls=tool_calls,
            finish_reason=choices[0].get("finish_reason", ""),
            usage=data.get("usage", {}),
            endpoint_used=endpoint.label,
        )
