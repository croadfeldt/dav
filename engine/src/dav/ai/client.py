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
from dataclasses import dataclass
from typing import Any, Callable
import requests

log = logging.getLogger(__name__)

class InferenceError(Exception):
    pass

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

    def __init__(self, primary: EndpointConfig,
                 fallback: EndpointConfig | None = None):
        self.primary = primary
        self.fallback = fallback

    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        guided_json_schema: dict[str, Any] | None = None,
        seed: int | None = None,
    ) -> ChatResponse:
        body = self._build_body(self.primary, messages, tools, temperature,
                                 max_tokens, guided_json_schema, seed)
        try:
            return self._post(self.primary, body)
        except InferenceError as e:
            if self.fallback is None:
                raise
            log.warning("primary endpoint %s failed: %s; trying fallback %s",
                        self.primary.label, e, self.fallback.label)
            # Rebuild for fallback: model and chat_template_kwargs may differ.
            body = self._build_body(self.fallback, messages, tools, temperature,
                                     max_tokens, guided_json_schema, seed)
            return self._post(self.fallback, body)

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
        if guided_json_schema:
            # vLLM-specific extension, ignored by vanilla OpenAI endpoints
            body["extra_body"] = {"guided_json": guided_json_schema}
        if seed is not None:
            body["seed"] = seed
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

        try:
            r = requests.post(url, headers=headers, json=body,
                              timeout=endpoint.timeout_seconds)
        except requests.RequestException as e:
            raise InferenceError(f"request to {endpoint.label} failed: {e}") from e

        if r.status_code != 200:
            raise InferenceError(
                f"{endpoint.label} returned {r.status_code}: {r.text[:500]}"
            )

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
            content=content,
            tool_calls=tool_calls,
            finish_reason=choices[0].get("finish_reason", ""),
            usage=data.get("usage", {}),
            endpoint_used=endpoint.label,
        )
