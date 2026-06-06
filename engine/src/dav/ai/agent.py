"""
Stage 2 analysis agent.

The stage 2 agent analyzes a single use case against the current
DCM spec corpus. It runs a tool-use loop: the LLM gets the use case
+ a system prompt, makes tool calls against dav-docs-mcp to retrieve
spec content, reasons iteratively, and emits a structured Analysis
with rationales for every assertion.

Loop termination:
  - LLM indicates it's done (finish_reason='stop' on a message with
    no tool_calls) — normal case
  - Max tool-call budget reached — fail-safe
  - Repeated errors from the MCP server — abort

Output contract: the final LLM response must be parseable as JSON
matching ANALYSIS_JSON_SCHEMA. This is enforced via vLLM's guided
decoding, but we also parse + validate on our side to catch drift
from endpoints that don't support guided_json.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from dav.core.use_case_schema import (
    UseCase, Analysis, AnalysisMetadata, AnalysisSummary,
    ComponentRequired, DataModelTouched, CapabilityInvoked,
    ProviderTypeInvolved, PolicyModeRequired, GapIdentified,
    ToolCall, build_analysis_json_schema,
)
from .client import (
    InferenceClient, ChatMessage, InferenceError,
)
from .mcp_tools import McpClient, get_tool_definitions
from .prompts import build_stage2_system_prompt, build_stage2_user_prompt

def _extract_json_object(text: str) -> str:
    """Extract the outermost JSON object from text that may contain
    surrounding prose or markdown fences.

    Handles three drift patterns observed in LLM final responses:
      1. Leading prose before the object ("Here is the analysis: {...}")
      2. Markdown fences anywhere in the text ("```json\\n{...}\\n```")
      3. Trailing prose after the object ("{...}\\n\\nLet me know if...")

    Uses string-aware brace counting so rationale fields containing '{' or
    '}' in their text do not confuse the parser.

    Raises ValueError if no balanced JSON object can be extracted.
    """
    # Strip Qwen3 thinking-mode blocks if present. Even with /no_think in the
    # system prompt, the model occasionally leaks <think>...</think>. The
    # blocks are free-form prose that may contain braces and would confuse
    # the downstream brace counter, so strip them first.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # Strip markdown fences if present, anywhere in the text
    fence_match = re.search(
        r"```(?:json|JSON)?\s*\n?(.*?)\n?```",
        text,
        re.DOTALL,
    )
    if fence_match:
        text = fence_match.group(1)

    # Find the first '{'
    start = text.find("{")
    if start == -1:
        raise ValueError("no '{' found in response")

    # Walk the string, counting braces, respecting string literals
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    raise ValueError(
        f"unbalanced braces: object starting at position {start} never closed"
    )

log = logging.getLogger(__name__)

# Known field aliases from model output → dataclass field name.
# When the schema has semantically-overlapping fields with different names
# across dataclasses (role vs usage vs description), the model will drift
# between them. Map aliases to their canonical field names rather than
# dropping as unknown.
_FIELD_ALIASES: dict[str, dict[str, str]] = {
    "CapabilityInvoked": {"role": "usage", "description": "usage"},
    "ComponentRequired": {"usage": "role", "description": "role"},
    "ProviderTypeInvolved": {"usage": "role", "description": "role"},
}

def _from_dict(cls, data: dict):
    """Construct a dataclass from a dict, with field-alias remapping and
    unknown-field dropping.

    Model output drifts from the strict schema — fields like 'role' appear
    on CapabilityInvoked where 'usage' is expected. This helper:
      1. Remaps known aliases (role → usage on CapabilityInvoked, etc.)
      2. Drops remaining unknown fields with a WARNING log
      3. Constructs the dataclass from the filtered dict

    This makes the parser resilient to model-schema drift without hiding
    the drift — all remapping and dropping is logged so that patterns
    can be detected and the schema/prompt brought back into sync.
    """
    import dataclasses as _dc
    known = {f.name for f in _dc.fields(cls)}
    aliases = _FIELD_ALIASES.get(cls.__name__, {})

    remapped = {}
    aliased = []
    for k, v in data.items():
        if k in known:
            remapped[k] = v
        elif k in aliases and aliases[k] in known:
            canonical = aliases[k]
            # Prefer the canonical name if both are present
            if canonical not in remapped:
                remapped[canonical] = v
                aliased.append(f"{k}→{canonical}")
        # else: drop (recorded below)

    if aliased:
        log.info("%s: remapped aliased fields: %s", cls.__name__, aliased)

    dropped = set(data.keys()) - known - set(aliases.keys())
    if dropped:
        log.warning(
            "%s: dropped unknown fields from model output: %s (keeping: %s)",
            cls.__name__,
            sorted(dropped),
            sorted(remapped.keys()),
        )
    # Prefer the schema's from_dict when available. This ensures
    # severity/confidence shorthand strings get normalized to descriptor form
    # at ingest time rather than being stored as bare strings.
    from_dict_method = getattr(cls, "from_dict", None)
    if callable(from_dict_method):
        return from_dict_method(remapped)
    return cls(**remapped)

DEFAULT_MAX_TOOL_CALLS = 30
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 6144

class AgentError(Exception):
    pass

@dataclass
class AgentConfig:
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    seed: int | None = 42                        # for reproducibility in bake-off
    use_guided_json: bool = True
    # per-stage sample iteration controls.
    # sample_count and sample_concurrency are advisory at this layer —
    # Stage2Agent.analyze() runs ONE sample. The orchestration that runs
    # N samples and merges them lives in stage2_analyze.run_samples()
    # (which reads these fields from the AgentConfig it gets handed).
    # They live on AgentConfig so programmatic API users can set them
    # without going through the CLI; the CLI overrides via flags.
    sample_count: int = 1
    sample_concurrency: int = 1

class Stage2Agent:
    """
    One stage 2 agent = one analysis run for one use case.

    Constructed once per use case; not reusable (holds per-run state
    like tool-call trace, token counts).
    """

    def __init__(
        self,
        inference: InferenceClient,
        mcp: McpClient,
        config: AgentConfig | None = None,
        consumer_profile=None,
        consumer_content_path=None,
        turns_log_path: "Path | None" = None,
    ):
        self.inference = inference
        self.mcp = mcp
        self.config = config or AgentConfig()
        # ConsumerProfile parameterizes prompts and JSON schema.
        # If not passed, falls back to the module-level default profile (the
        # DCM reference profile unless explicitly set otherwise). This keeps
        # pre-ε.1 callers working without modification.
        if consumer_profile is None:
            from dav.core.consumer_profile import get_default_profile
            consumer_profile = get_default_profile()
        self.consumer_profile = consumer_profile
        # optional path to the consumer's content tree, read for
        # consumer_version_string() at AnalysisMetadata population time.
        # When None, AnalysisMetadata.consumer_version stays empty.
        self.consumer_content_path = consumer_content_path
        # Optional structured per-turn JSONL log. When set, every model
        # response + tool call/result is appended as one JSON line. Consumed
        # by the DAV review-console run-detail drawer for the live
        # prompts/responses tail. None disables emission (cost = 0).
        self.turns_log_path = turns_log_path
        self._tool_trace: list[ToolCall] = []
        self._total_tokens: int = 0
        # Last response's prompt_tokens — used as a lower-bound estimate for
        # the NEXT call's input size so we can compute how much output budget
        # actually fits inside the model's context window. The vLLM serving
        # config exposes a hard ceiling (currently 86016 = 84K via YaRN) and
        # rejects requests where input + max_tokens > ceiling. With
        # --max-tokens 16384 (post-2026-05-28 bump for verbose analyses),
        # any UC whose tool-call exploration accumulates >~69.6K input
        # tokens overflowed and produced a 400 BadRequestError. Now we
        # adaptively shrink max_tokens per turn and force a final emission
        # (tools_arg=None) when room is tight. Also catches and retries on
        # the explicit 400 message as a belt-and-suspenders.
        self._last_prompt_tokens: int = 0
        # Operator-tunable. Default tracks the deployed vLLM --max-model-len.
        self._model_context_limit: int = int(os.environ.get("DAV_MODEL_CONTEXT_LIMIT", "86016"))
        self._context_safety_buffer: int = int(os.environ.get("DAV_MODEL_CONTEXT_SAFETY", "256"))
        self._budget_capped_turn_count: int = 0
        self._context_overflow_retry_count: int = 0
        # Two-pass orchestration state (M12 "information-preservation" pass).
        # When DAV_STAGE2_TWO_PASS != "0" (default), analyze() runs in two
        # passes: pass 1 explores the spec via MCP and emits a verbose
        # structured findings JSON; pass 2 starts in a fresh context with
        # the findings + UC + MCP access and emits the canonical Analysis.
        # _two_pass_active gates the dispatch: when True, analyze() runs
        # the single-pass body directly (used INSIDE _analyze_two_pass to
        # avoid recursion).
        self._two_pass_active: bool = False
        self._pass_label: Optional[str] = None
        # Per-pass prompt + emit overrides. _analyze_two_pass populates
        # these between pass 1 and pass 2; single-pass analyze leaves them
        # None and builds prompts normally.
        self._sys_prompt_override: Optional[str] = None
        self._user_prompt_override: Optional[str] = None
        self._emit_findings_only: bool = False
        # Anti-fishing state — models routinely ignore "section not found"
        # results and just try the same section_title in another document, or
        # re-fetch a document already returned as "too large". When patterns
        # recur, we PREPEND a directive to the tool response so the model sees
        # reinforced guidance fresh on its next turn. Reset per agent run.
        self._section_title_misses: dict[str, int] = {}   # section_title -> miss count
        self._too_large_handles: set[str] = set()
        # Cross-turn dedup state (post-M12 follow-up). Records every distinct
        # (tool_name, args_json) pair the agent has actually executed this run,
        # plus the turn it first ran on and a preview of the result. When the
        # model emits the same call again across a turn boundary, we short-
        # circuit to a DUPLICATE-CROSS-TURN marker with a pointer to the
        # original — saves the MCP round trip AND tells the model to pivot.
        # Reset per analyze() like the other anti-fishing state.
        self._call_history: dict[tuple, dict] = {}
        # Counter surfaced via _emit_turn(kind="summary") at end of analyze()
        # so operators can spot UCs where the model kept asking for the same
        # thing without having to eyeball every turn.
        self._cross_turn_dup_count: int = 0
        # wall-time tracking for AnalysisMetadata.wall_time_seconds
        self._wall_time_start: float = 0.0
        # per-sample seed override. When None, falls back to
        # config.seed. The runner sets this for each sample of a multi-sample
        # run so each sample uses a distinct seed.
        self._sample_seed: int | None = None
        # Per-UC spec namespace scope (M12 "C" pass). Populated from
        # UseCase.spec_namespaces at the start of analyze(). When non-empty,
        # the agent (1) appends a tighter focus paragraph to the system
        # prompt that takes precedence over the run-wide
        # DAV_SPEC_NAMESPACES_FILTER hint, and (2) hard-rejects
        # get_document / get_document_section MCP calls whose handle
        # namespace is outside the list — returning an OUT-OF-SCOPE marker
        # that the model sees on its next turn. Counted via
        # self._out_of_scope_blocked_count and surfaced on the summary
        # record. search_docs results aren't filtered (the result format
        # varies per MCP backend); the system-prompt focus + the per-call
        # reject combine to keep grounding in-scope in practice.
        self._uc_spec_namespaces: list[str] = []
        self._out_of_scope_blocked_count: int = 0

    # Safety cap on any single field's stored length. Prevents a runaway
    # prompt or tool result from making the JSONL file pathologically large.
    # Default 256 KB per field — easily enough for typical DCM analysis
    # prompts (30-50 KB) and tool results (1-5 KB). Override via env var
    # DAV_TURNS_MAX_FIELD_BYTES for stress tests.
    _TURNS_MAX_FIELD_BYTES = int(os.environ.get("DAV_TURNS_MAX_FIELD_BYTES", "262144"))

    def _anti_fishing_wrap(
        self, tool_name: str, args: dict, result: str, ok: bool,
    ) -> str:
        """Detect recurring tool-call mistakes and prepend a forceful directive
        to the result so the model sees reinforced guidance on its next turn.

        Two patterns handled today:

        1. **Repeated section_title not-found across documents** — model is
           "fishing" for a section name in every document instead of picking
           from the "Available sections" list the MCP returns. After the 3rd
           attempt with the same section_title, prepend a STOP directive.
        2. **Re-fetching a document already returned as too-large** — model
           tries `get_document(handle)` after being told it was over the
           response budget. On the 2nd attempt for the same handle, prepend a
           STOP directive forcing a section call.
        """
        if not ok:
            return result
        # Pattern 1: section_title misses
        if tool_name == "get_document_section" and "not found" in result.lower():
            st = (args.get("section_title") or "").strip()
            if st:
                self._section_title_misses[st] = self._section_title_misses.get(st, 0) + 1
                count = self._section_title_misses[st]
                if count >= 2:
                    return (
                        f"⛔ ANTI-FISHING STOP: you have tried "
                        f"section_title='{st}' {count} times across different "
                        f"documents and it was not found in any of them. "
                        f"This section title does not exist with that wording. "
                        f"Your NEXT action MUST be `search_docs(query=<DIFFERENT "
                        f"keywords>)` — pick alternative terms. Do not retry "
                        f"this section_title in another document.\n\n"
                        f"--- Original tool response below for context ---\n\n"
                        f"{result}"
                    )
        # Pattern 2: re-fetching too-large documents
        if tool_name == "get_document":
            handle = (args.get("handle") or "").strip()
            if "too large" in result.lower() or "document too large" in result.lower():
                already_seen = handle in self._too_large_handles
                self._too_large_handles.add(handle)
                if already_seen:
                    return (
                        f"⛔ ANTI-FISHING STOP: you already fetched "
                        f"get_document('{handle}') and were told it was too "
                        f"large. Calling it again returns the same outline. "
                        f"Your NEXT action MUST be "
                        f"`get_document_section(handle='{handle}', "
                        f"section_title='<a title from the outline>')`.\n\n"
                        f"--- Original tool response below for context ---\n\n"
                        f"{result}"
                    )
        return result

    def _retrieval_memo_msg(self) -> Optional[ChatMessage]:
        """A compact, always-current memo of what's already been retrieved this
        run — pinned at the message TAIL (never the cached system prefix) right
        before each model call. DAV never evicts tool results, so everything
        below is still in context; the model just loses track in a long window
        and re-fetches. This is PREVENTION (the model sees what it has before it
        acts), complementing the reactive cross-turn dedup. Toggle with
        DAV_RETRIEVAL_MEMO=0. Sourced from _call_history (only successful calls)."""
        if os.environ.get("DAV_RETRIEVAL_MEMO", "1") == "0":
            return None
        items = []
        for v in self._call_history.values():
            tool, a, t = v.get("tool"), v.get("args") or {}, v.get("turn")
            if tool == "get_document_section":
                items.append((t, f"{a.get('handle', '?')} § {a.get('section_title', '?')}"))
            elif tool == "get_document":
                items.append((t, f"{a.get('handle', '?')} (full document)"))
            elif tool == "search_docs":
                items.append((t, f"search_docs(\"{a.get('query', '?')}\")"))
        if not items:
            return None
        items.sort(key=lambda x: (x[0] if x[0] is not None else 0))
        listed = "\n".join(f"  • {label}   — already retrieved on turn {t}"
                           for t, label in items)
        body = (
            "📓 ENGINE MEMO — RETRIEVAL LEDGER. You have ALREADY retrieved the "
            "following this session and their results are still in the context "
            "above. Re-reading them is free; RE-FETCHING any of them wastes your "
            "tool-call budget and will be blocked. Before calling a tool, check "
            "this list — only fetch something NEW, or stop fetching and write "
            "your final JSON analysis:\n" + listed
        )
        return ChatMessage(role="system", content=body)

    def _check_namespace_scope(self, tool_name: str, args: dict) -> Optional[str]:
        """Hard MCP-call scope guard (M12 "C" pass).

        When the active use case declares `spec_namespaces`, calls to
        `get_document` / `get_document_section` with a handle whose
        namespace prefix is outside that list are blocked. Returns a
        formatted OUT-OF-SCOPE marker the agent loop returns to the
        model in lieu of the real MCP result; returns None when the
        call is in-scope (proceed normally).
        """
        if not self._uc_spec_namespaces:
            return None
        if tool_name not in ("get_document_section", "get_document"):
            return None
        handle = (args.get("handle") or "").strip()
        if not handle or "/" not in handle:
            return None
        ns = handle.split("/", 1)[0]
        if ns in self._uc_spec_namespaces:
            return None
        self._out_of_scope_blocked_count += 1
        return (
            f"⛔ OUT-OF-SCOPE: this use case's spec_namespaces is "
            f"{self._uc_spec_namespaces}; the handle {handle!r} is in "
            f"namespace {ns!r}, which is NOT in scope for this UC. "
            f"The engine refused the call without contacting the MCP. "
            f"Your next action MUST query an in-scope handle (one whose "
            f"prefix is in {self._uc_spec_namespaces}). If you genuinely "
            f"need cross-namespace grounding, document that in your "
            f"analysis under `dependencies` instead of fetching the doc."
        )

    def _emit_turn(self, turn: int, kind: str, **fields) -> None:
        """Append a single structured-turn record to turns_log_path (JSONL).

        Errors are swallowed — emission must never disrupt the run itself.
        `kind` values: 'start', 'response', 'tool', 'final', 'summary'.
        Records include the active two-pass label ('pass1' / 'pass2' /
        None) so the UI prompts panel can render per-pass timelines.

        Fields are written in full (no preview truncation) up to a per-field
        safety cap (DAV_TURNS_MAX_FIELD_BYTES, default 256 KB). Length is
        recorded alongside each capped string so the UI can show "+N bytes
        not stored" if the cap kicked in.
        """
        if self.turns_log_path is None:
            return
        try:
            import datetime as _dt
            # Cap any oversize string fields and tag with _truncated:true so
            # the UI can be honest about the elision.
            capped: dict = {}
            cap = self._TURNS_MAX_FIELD_BYTES
            for k, v in fields.items():
                if isinstance(v, str) and len(v.encode("utf-8")) > cap:
                    # Truncate by byte budget (safe re: utf-8 boundaries via
                    # the errors='ignore' decode)
                    truncated = v.encode("utf-8")[:cap].decode("utf-8", errors="ignore")
                    capped[k] = truncated
                    capped[k + "_truncated"] = True
                else:
                    capped[k] = v
            rec = {
                "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "turn": turn,
                "kind": kind,
                **({"pass": self._pass_label} if self._pass_label else {}),
                **capped,
            }
            self.turns_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.turns_log_path.open("a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except Exception as e:
            log.warning("turns emit failed: %s", e)

    def analyze(self, use_case: UseCase):
        """
        Run the agent loop on a use case. Returns a validated Analysis
        (single-pass) or the raw findings string (when _emit_findings_only).

        Default behavior: two-pass — pass 1 explores + emits findings JSON,
        pass 2 receives findings + has MCP for re-fetch + emits Analysis.
        Set DAV_STAGE2_TWO_PASS=0 to force the legacy single-pass.

        Raises AgentError on unrecoverable failures (exhausted budget
        with no final response, malformed JSON, schema validation fails).
        """
        # Dispatch BEFORE setup so two-pass can drive the body twice.
        # _two_pass_active=True means we're already INSIDE _analyze_two_pass
        # calling this method for one pass — proceed with the single body
        # below using the override hooks.
        if not self._two_pass_active and os.environ.get("DAV_STAGE2_TWO_PASS", "1") != "0":
            return self._analyze_two_pass(use_case)
        run_id = str(uuid.uuid4())
        log.info("stage2 %s run %s started for use case %s",
                 self._pass_label or "single-pass", run_id, use_case.uuid)
        # measure wall time so AnalysisMetadata.wall_time_seconds
        # is populated for ensemble merging and explore-mode cost reporting.
        import time as _time
        self._wall_time_start = _time.monotonic()
        # Reset anti-fishing state for this run
        self._section_title_misses = {}
        self._too_large_handles = set()
        self._call_history = {}
        self._cross_turn_dup_count = 0
        self._last_prompt_tokens = 0
        self._budget_capped_turn_count = 0
        self._context_overflow_retry_count = 0
        # M12 "C" pass: pick up per-UC spec scope, if any.
        self._uc_spec_namespaces = list(getattr(use_case, "spec_namespaces", None) or [])
        self._out_of_scope_blocked_count = 0

        tool_defs = get_tool_definitions()
        # Honor two-pass overrides when set; otherwise build prompts as usual.
        sys_prompt = self._sys_prompt_override or build_stage2_system_prompt(self.consumer_profile)
        if self._sys_prompt_override is None and self._uc_spec_namespaces:
            sys_prompt += (
                "\n\n## Per-UC spec source scope (HARD)\n"
                f"This use case declares `spec_namespaces: "
                f"{self._uc_spec_namespaces}`. This OVERRIDES any run-wide "
                f"spec focus. Restrict MCP grounding to documents whose "
                f"handle prefix is one of those namespaces. The engine "
                f"hard-rejects `get_document` and `get_document_section` "
                f"calls for out-of-scope handles with an "
                f"`⛔ OUT-OF-SCOPE` marker — pre-empt that by checking "
                f"each handle's prefix before calling. For `search_docs`, "
                f"prefer in-scope results and ignore the rest."
            )
        user_prompt = self._user_prompt_override or build_stage2_user_prompt(use_case, self.consumer_profile)
        messages: list[ChatMessage] = [
            ChatMessage(role="system",  content=sys_prompt),
            ChatMessage(role="user",    content=user_prompt),
        ]
        self._emit_turn(
            turn=0, kind="start",
            uc_uuid=use_case.uuid,
            sample_seed=self._sample_seed,
            system_prompt=sys_prompt,
            system_prompt_length=len(sys_prompt),
            user_prompt=user_prompt,
            user_prompt_length=len(user_prompt),
            max_tool_calls=self.config.max_tool_calls,
        )

        # Tool-use loop
        for turn in range(self.config.max_tool_calls + 1):
            at_budget = (turn == self.config.max_tool_calls)
            guided = build_analysis_json_schema(self.consumer_profile) if (
                self.config.use_guided_json and at_budget
            ) else None
            # On the budget-hit turn, remove tools from the request so the
            # model cannot keep tool-calling past the budget. This forces a
            # text response (parsed as final analysis) even when guided_json
            # is disabled. Without this, --no-guided-json runs that exhaust
            # the budget hit `raise AgentError("agent loop terminated without
            # final response")` and produce no analysis.
            tools_arg = None if at_budget else tool_defs

            # Dynamic max_tokens — when the running prompt size is large
            # enough that requesting the full config.max_tokens would push
            # past the model's context ceiling, shrink the request and
            # force a final emission (drop tools) so the model commits with
            # whatever room is left. Estimate next-prompt size from the
            # last response's prompt_tokens plus a growth pad — the next
            # turn adds the prior assistant message + new tool result(s).
            growth_pad = 1500   # rough: assistant content + one tool result
            estimated_prompt = (self._last_prompt_tokens or 0) + growth_pad
            ceiling = self._model_context_limit - self._context_safety_buffer
            requested_max = self.config.max_tokens
            available_for_output = ceiling - estimated_prompt
            if available_for_output < requested_max:
                # Force final emission when room is tight: drop tools and
                # cap max_tokens to what actually fits.
                if available_for_output < 256:
                    # Catastrophic — almost no room left. Reserve a minimum
                    # to let the model say SOMETHING; the wrap-up nudge
                    # already in the last tool result tells it to commit.
                    available_for_output = max(256, ceiling - estimated_prompt - 8)
                requested_max = max(256, min(self.config.max_tokens, available_for_output))
                tools_arg = None   # force final emit
                self._budget_capped_turn_count += 1
                log.info(
                    "turn %d/%d: context-tight (last_prompt=%d, ceiling=%d, "
                    "available_for_output=%d) — dropping tools, "
                    "max_tokens=%d, forcing final emit",
                    turn, self.config.max_tool_calls,
                    self._last_prompt_tokens, ceiling, available_for_output,
                    requested_max,
                )

            log.info(
                "turn %d/%d: %d messages in context, %d tokens used so far%s",
                turn, self.config.max_tool_calls,
                len(messages), self._total_tokens,
                " (budget-hit: tools disabled, forcing final emit)" if at_budget else "",
            )

            # Pin the retrieval ledger at the tail so the model sees what it
            # already has before deciding what to call. Transient — popped in the
            # finally so it never accumulates and never touches the cached prefix.
            _memo = self._retrieval_memo_msg()
            if _memo is not None:
                messages.append(_memo)
            try:
                response = self.inference.chat(
                    messages=messages,
                    tools=tools_arg,
                    temperature=self.config.temperature,
                    max_tokens=requested_max,
                    guided_json_schema=guided,
                    seed=self._sample_seed if self._sample_seed is not None else self.config.seed,
                )
            except InferenceError as e:
                # Last-ditch belt-and-suspenders: if vLLM rejected the
                # request because input + max_tokens overflowed the
                # context ceiling, parse the reported input size from the
                # error message and retry once with tools off + a
                # max_tokens that actually fits. The error message format
                # is the vLLM OpenAI-compat one:
                #   "...maximum context length is N tokens. However, you
                #    requested X output tokens and your prompt contains
                #    at least Y input tokens..."
                em = str(e)
                if ("maximum context length" in em and
                        ("input_tokens" in em or "prompt contains" in em)):
                    import re as _re
                    mIn = _re.search(r"prompt contains at least (\d+) input tokens", em)
                    if mIn:
                        actual_input = int(mIn.group(1))
                        retry_max = max(256, ceiling - actual_input - 8)
                        log.warning(
                            "turn %d: context overflow (input=%d, ceiling=%d); "
                            "retrying once with max_tokens=%d, tools off",
                            turn, actual_input, ceiling, retry_max,
                        )
                        self._context_overflow_retry_count += 1
                        try:
                            response = self.inference.chat(
                                messages=messages,
                                tools=None,
                                temperature=self.config.temperature,
                                max_tokens=retry_max,
                                guided_json_schema=guided,
                                seed=self._sample_seed if self._sample_seed is not None else self.config.seed,
                            )
                        except InferenceError as e2:
                            raise AgentError(f"inference failed at turn {turn} (post-overflow retry): {e2}") from e2
                    else:
                        raise AgentError(f"inference failed at turn {turn}: {e}") from e
                else:
                    raise AgentError(f"inference failed at turn {turn}: {e}") from e
            finally:
                # Drop the transient memo; re-added fresh (and current) next turn.
                if _memo is not None:
                    messages.pop()

            usage = response.usage or {}
            self._total_tokens += usage.get("total_tokens", 0)
            self._last_prompt_tokens = usage.get("prompt_tokens", self._last_prompt_tokens)

            self._emit_turn(
                turn=turn, kind="response",
                content=response.content or "",
                content_length=len(response.content or ""),
                tool_call_count=len(response.tool_calls or []),
                tokens_used=usage.get("total_tokens", 0),
                tokens_total=self._total_tokens,
                messages_in_context=len(messages),
            )

            # If the model wants to call tools, execute them and loop
            if response.tool_calls:
                assistant_msg = ChatMessage(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                )
                messages.append(assistant_msg)

                # In-turn dedup: models sometimes emit the same tool call 4-8
                # times in parallel within a single response. Each duplicate
                # eats context (tool responses are 1-3 KB each) and pushes us
                # toward the context limit. Execute each (tool_name, args)
                # pair ONCE; for duplicates, return a short "[duplicate]"
                # marker referencing the first tool_call_id so the model
                # still gets the required response per tool_call_id but
                # without re-running the call or eating real context.
                # Result cache: (tool_name, args_json) -> (first_tc_id, content)
                _exec_cache: dict[tuple, tuple[str, str]] = {}
                for tc in response.tool_calls:
                    tool_name = tc["function"]["name"]
                    try:
                        raw_args = tc["function"].get("arguments") or "{}"
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except json.JSONDecodeError:
                        args = {}
                        log.warning("tool %s had malformed arg JSON: %r", tool_name, raw_args)
                    dedup_key = (tool_name, json.dumps(args, sort_keys=True, default=str))

                    if dedup_key in _exec_cache:
                        first_id, first_content = _exec_cache[dedup_key]
                        dup_content = (
                            f"⛔ DUPLICATE-IN-TURN: you emitted this exact "
                            f"tool call {tool_name}({json.dumps(args)}) multiple "
                            f"times in a single response. The engine executed it "
                            f"once (see tool_call_id={first_id}); this response "
                            f"is a no-op marker. STOP emitting duplicate tool "
                            f"calls in one response — wait for the result of "
                            f"one call before deciding what to call next."
                        )
                        log.info(
                            "turn %d: dedup duplicate %s args=%s (first_id=%s)",
                            turn, tool_name, args, first_id,
                        )
                        self._emit_turn(
                            turn=turn, kind="tool",
                            tool_name=tool_name, args=args, ok=True,
                            result=dup_content, result_length=len(dup_content),
                            dedup_of=first_id,
                        )
                        messages.append(ChatMessage(
                            role="tool", content=dup_content,
                            tool_call_id=tc["id"], name=tool_name,
                        ))
                        continue

                    # Cross-turn dedup (post-M12 follow-up): same (tool, args)
                    # already executed on an earlier turn this run? Skip the
                    # MCP round trip and return a STOP marker with a pointer
                    # back to the original turn + a preview of its result.
                    # Models (especially Qwen3-class) lose track of their own
                    # prior calls as context grows; without this guard they
                    # re-issue identical searches/section fetches across
                    # turns 7, 9, 11, ... and burn the budget.
                    if dedup_key in self._call_history:
                        prior = self._call_history[dedup_key]
                        preview = (prior["result"] or "")[:400]
                        if len(prior["result"] or "") > 400:
                            preview += f"… [{len(prior['result']) - 400} more chars in original]"
                        repeat_content = (
                            f"⛔ DUPLICATE-CROSS-TURN: you already called "
                            f"{tool_name}({json.dumps(args)}) on turn "
                            f"{prior['turn']} (tool_call_id={prior['tool_call_id']}). "
                            f"The result is unchanged. Re-running would waste "
                            f"the budget. Either pivot to a DIFFERENT query / "
                            f"section / handle, or stop fetching and write your "
                            f"final JSON analysis on your NEXT response.\n\n"
                            f"--- Original result preview ---\n{preview}"
                        )
                        self._cross_turn_dup_count += 1
                        log.info(
                            "turn %d: cross-turn dedup blocked %s args=%s "
                            "(first run on turn %d)",
                            turn, tool_name, args, prior["turn"],
                        )
                        self._emit_turn(
                            turn=turn, kind="tool",
                            tool_name=tool_name, args=args, ok=True,
                            result=repeat_content, result_length=len(repeat_content),
                            dedup_of=prior["tool_call_id"],
                            cross_turn_dedup=True,
                            first_seen_turn=prior["turn"],
                        )
                        _exec_cache[dedup_key] = (tc["id"], repeat_content)
                        messages.append(ChatMessage(
                            role="tool", content=repeat_content,
                            tool_call_id=tc["id"], name=tool_name,
                        ))
                        continue

                    log.info("turn %d: mcp call %s args=%s", turn, tool_name, args)

                    # M12 "C" pass: hard namespace scope check before the
                    # MCP round trip. When the UC declared spec_namespaces
                    # and the requested handle is out of scope, return the
                    # OUT-OF-SCOPE marker; the agent loop emits a tool turn
                    # record with the marker as the result, and the model
                    # sees it on the next response. Mirrors the dedup +
                    # anti-fishing short-circuit pattern.
                    scope_block = self._check_namespace_scope(tool_name, args)
                    if scope_block is not None:
                        log.info(
                            "turn %d: out-of-scope block %s args=%s",
                            turn, tool_name, args,
                        )
                        self._emit_turn(
                            turn=turn, kind="tool",
                            tool_name=tool_name, args=args, ok=True,
                            result=scope_block, result_length=len(scope_block),
                            out_of_scope=True,
                        )
                        messages.append(ChatMessage(
                            role="tool", content=scope_block,
                            tool_call_id=tc["id"], name=tool_name,
                        ))
                        _exec_cache[dedup_key] = (tc["id"], scope_block)
                        continue

                    mcp_result = self.mcp.call(tool_name, args)
                    full_result = (
                        mcp_result.result if mcp_result.ok
                        else f"ERROR: {mcp_result.error}"
                    )
                    # Anti-fishing pattern detection — prepend reinforcement to
                    # the tool response when the model keeps making the same
                    # mistake. The model reads the tool result fresh each turn,
                    # so prepending here is more reliable than relying on the
                    # buried system-prompt directive.
                    full_result = self._anti_fishing_wrap(tool_name, args, full_result, mcp_result.ok)
                    # Wrap-up nudge — when nearing the tool-call budget, prepend
                    # a directive telling the model to stop fetching and write
                    # its final JSON. Last 3 turns get the warning; the
                    # budget-hit turn already strips tools (handled above).
                    remaining = self.config.max_tool_calls - turn
                    if remaining <= 3 and mcp_result.ok:
                        full_result = (
                            f"⚠ WRAP-UP: only {remaining} tool-call turn(s) "
                            f"left before the budget closes. STOP fetching new "
                            f"sections and synthesize your final JSON analysis "
                            f"on your NEXT response using whatever you already "
                            f"have. The engine will refuse further tool calls "
                            f"after turn {self.config.max_tool_calls}.\n\n"
                            f"--- Original tool response below ---\n\n"
                            f"{full_result}"
                        )
                    _exec_cache[dedup_key] = (tc["id"], full_result)
                    # Record this call for cross-turn dedup on future turns.
                    # Only record successful MCP results — error responses
                    # might be transient and worth retrying. The WRAP-UP
                    # prepended directive is included in `full_result` here,
                    # but its preview is only used as a model-facing hint, so
                    # that's fine.
                    if mcp_result.ok and dedup_key not in self._call_history:
                        self._call_history[dedup_key] = {
                            "turn": turn,
                            "tool_call_id": tc["id"],
                            "result": full_result,
                            "tool": tool_name,
                            "args": args,
                        }

                    self._tool_trace.append(ToolCall(
                        tool=tool_name,
                        args=args,
                        result_summary=full_result[:500],   # ToolTrace stays compact
                        purpose=f"turn {turn}",
                    ))
                    self._emit_turn(
                        turn=turn, kind="tool",
                        tool_name=tool_name,
                        args=args,
                        ok=mcp_result.ok,
                        result=full_result,
                        result_length=len(full_result),
                    )

                    messages.append(ChatMessage(
                        role="tool",
                        content=full_result if mcp_result.ok
                                 else f"Tool error: {mcp_result.error}",
                        tool_call_id=tc["id"],
                        name=tool_name,
                    ))
                continue

            # No tool calls → the model is emitting a final answer
            if turn < self.config.max_tool_calls:
                # Model stopped early. This is normal if it has enough info.
                self._emit_run_summary(final_turn=turn)
                if self._emit_findings_only:
                    # Pass 1: return raw content string (findings JSON) for
                    # pass 2 to consume. No Analysis validation here —
                    # pass 1's emit doesn't conform to the Analysis schema.
                    return response.content or ""
                return self._parse_final(response.content, use_case, run_id)

            # Hit the budget limit on this turn — it should be emitting final
            self._emit_run_summary(final_turn=turn)
            if self._emit_findings_only:
                return response.content or ""
            return self._parse_final(response.content, use_case, run_id)

        # Shouldn't reach here, but keep mypy happy
        raise AgentError("agent loop terminated without final response")

    def _analyze_two_pass(self, use_case: UseCase) -> Analysis:
        """Run the two-pass exploration + synthesis flow.

        Pass 1: explore via MCP, emit a verbose structured FINDINGS JSON.
                The agent loop runs normally but with the pass-1 system
                prompt and returns raw content (no Analysis validation).
        Pass 2: receive the findings as part of the user prompt, with
                MCP still available for re-fetch, and emit the canonical
                Analysis JSON.

        Information preservation is the design goal: anything pass 1
        compressed too aggressively in its findings can be re-pulled by
        pass 2 via the same MCP tools — never blindly summarized away.
        """
        from .prompts import (
            build_pass1_findings_system_prompt,
            build_pass2_analysis_system_prompt,
            build_pass2_user_prompt,
        )

        log.info("stage2 two-pass beginning for use case %s", use_case.uuid)
        self._two_pass_active = True
        try:
            # ── Pass 1: exploration + findings ──
            self._pass_label = "pass1"
            self._sys_prompt_override = build_pass1_findings_system_prompt(self.consumer_profile)
            if self._uc_spec_namespaces:
                self._sys_prompt_override += (
                    "\n\n## Per-UC spec source scope (HARD)\n"
                    f"This use case declares `spec_namespaces: "
                    f"{self._uc_spec_namespaces}`. The engine hard-rejects "
                    f"out-of-scope `get_document` / `get_document_section` "
                    f"calls with an `⛔ OUT-OF-SCOPE` marker."
                )
            self._user_prompt_override = None
            self._emit_findings_only = True
            findings_str = self.analyze(use_case)
            log.info(
                "stage2 pass-1 emitted %d chars of findings", len(findings_str or "")
            )

            # ── Reset per-pass state for pass 2 (preserve session-level counters) ──
            self._reset_between_passes()

            # ── Pass 2: synthesis + analysis ──
            self._pass_label = "pass2"
            self._sys_prompt_override = build_pass2_analysis_system_prompt(self.consumer_profile)
            if self._uc_spec_namespaces:
                self._sys_prompt_override += (
                    "\n\n## Per-UC spec source scope (HARD)\n"
                    f"Same constraint as pass 1: spec_namespaces="
                    f"{self._uc_spec_namespaces}."
                )
            self._user_prompt_override = build_pass2_user_prompt(
                use_case, findings_str, self.consumer_profile,
            )
            self._emit_findings_only = False
            return self.analyze(use_case)
        finally:
            self._two_pass_active = False
            self._pass_label = None
            self._sys_prompt_override = None
            self._user_prompt_override = None
            self._emit_findings_only = False

    def _reset_between_passes(self) -> None:
        """Reset per-pass agent state between pass 1 and pass 2.

        Resets dedup + anti-fishing + scope counters so pass 2 starts
        with a fresh exploration budget. Preserves session-cumulative
        counters (wall time, total tokens, budget caps, overflow retries)
        because the operator wants those as totals for the whole UC.
        """
        self._section_title_misses = {}
        self._too_large_handles = set()
        self._call_history = {}
        # NOTE: cross_turn_dup_count + out_of_scope_blocked are cumulative
        # across passes — they represent agent-loop hygiene, not per-pass
        # exploration depth. _last_prompt_tokens resets so pass 2's dynamic
        # max_tokens calculation starts from zero estimate.
        self._last_prompt_tokens = 0

    def _compute_infrastructure_confidence(self) -> dict:
        """Synthesize the per-UC infrastructure-induced quality assessment
        from the same counters surfaced on the summary turn record.

        Distinct from the model's analytical confidence (the per-component
        / per-analysis confidence scores already in the Analysis schema).
        This answers "did infrastructure constrain grounding?" rather than
        "is the answer right?" — a UC can be analytically high-confidence
        while infrastructure-compromised (model committed early due to
        budget pressure without enough exploration) and vice versa.

        Score deductions:
          * context_overflow_retries     × 30 (major — vLLM rejected a call)
          * budget_capped_turns          × 10 (major-ish — forced commit)
          * cross_turn_dedup ratio > 20% × 10 (moderate — model lost track)
          * section_title_misses > 5     × 5  (moderate — fishing)
          * out_of_scope_blocked > 3     × 5  (soft — namespace boundary hits)
        """
        score = 100
        signals: list[str] = []
        recommendations: list[str] = []

        if self._context_overflow_retry_count > 0:
            n = self._context_overflow_retry_count
            score -= 30 * n
            signals.append(f"context_overflow_retries={n}")
            recommendations.append(
                "Switch to a long-context model (Sonnet 4.6 / Opus 4.7, 200K) "
                "for this UC via the New Run model selector."
            )

        if self._budget_capped_turn_count > 0:
            n = self._budget_capped_turn_count
            score -= 10 * n
            signals.append(f"budget_capped_turns={n}")
            if "long-context" not in " ".join(recommendations):
                recommendations.append(
                    "Several turns committed early due to context pressure; "
                    "consider a long-context model or narrowing spec_namespaces."
                )

        distinct = len(self._call_history)
        if distinct > 0 and self._cross_turn_dup_count / distinct > 0.2:
            score -= 10
            signals.append(
                f"cross_turn_dedup_rate={self._cross_turn_dup_count}/{distinct}"
            )
            recommendations.append(
                "Model attempted the same tool call across turns at a high rate "
                "— possible context-window memory pressure."
            )

        misses_total = sum(self._section_title_misses.values())
        if misses_total > 5:
            score -= 5
            signals.append(f"section_title_misses={misses_total}")
            recommendations.append(
                "Model fished for section titles repeatedly — UC may probe "
                "areas the spec doesn't cover, or spec headings are inconsistent."
            )

        if self._out_of_scope_blocked_count > 3:
            score -= 5
            signals.append(f"out_of_scope_blocked={self._out_of_scope_blocked_count}")
            recommendations.append(
                "Model frequently attempted out-of-scope handles — verify "
                "the UC's spec_namespaces field matches its actual needs."
            )

        score = max(0, score)
        if score >= 85:
            label = "high"
            explanation = "Analysis ran cleanly within infrastructure limits."
        elif score >= 65:
            label = "medium"
            explanation = (
                "Mild infrastructure pressure during exploration; conclusions "
                "are likely sound but verify spot-checks of cited spec sections."
            )
        elif score >= 40:
            label = "low"
            explanation = (
                "Significant infrastructure constraints affected grounding; "
                "the model may have committed before fully exploring the spec. "
                "Consider re-running with a long-context model."
            )
        else:
            label = "compromised"
            explanation = (
                "Severe infrastructure constraints — analysis should not be "
                "trusted without a re-run on a long-context model."
            )
        return {
            "label": label,
            "score": score,
            "signals": signals,
            "explanation": explanation,
            "recommendations": recommendations,
        }

    def _emit_run_summary(self, final_turn: int) -> None:
        """End-of-run summary record. Surfaces per-sample stats the operator
        wants to spot quickly — most importantly the cross-turn duplicate
        count so UCs where the model kept re-asking become visible without
        eyeballing every turn. The UI's prompts panel renders kind='summary'
        records inline; run-summary.yaml aggregation across samples is a
        follow-up that touches stage2_analyze + run_corpus."""
        self._emit_turn(
            turn=final_turn + 1,
            kind="summary",
            cross_turn_duplicates_blocked=self._cross_turn_dup_count,
            distinct_calls=len(self._call_history),
            section_title_misses=sum(self._section_title_misses.values()),
            too_large_handles=len(self._too_large_handles),
            out_of_scope_blocked=self._out_of_scope_blocked_count,
            uc_spec_namespaces=list(self._uc_spec_namespaces),
            budget_capped_turns=self._budget_capped_turn_count,
            context_overflow_retries=self._context_overflow_retry_count,
            total_tokens=self._total_tokens,
        )

    def _parse_final(self, content: str, use_case: UseCase, run_id: str) -> Analysis:
        """
        Parse the LLM's final message content as JSON and construct an Analysis.
        """
        if not content.strip():
            raise AgentError("final response had no content")

        # Extract the JSON object, tolerating prose preamble/postamble and
        # markdown fences anywhere in the response.
        text = content.strip()

        try:
            json_str = _extract_json_object(text)
            data = json.loads(json_str)
        except (ValueError, json.JSONDecodeError) as e:
            log.error("final content was not valid JSON: %s", text[:500])
            raise AgentError(f"could not parse final analysis as JSON: {e}") from e

        try:
            import time as _time
            wall_time = max(0.0, _time.monotonic() - self._wall_time_start)
            effective_seed = (
                self._sample_seed if self._sample_seed is not None else self.config.seed
            )
            # populate version provenance fields
            from dav.core.version import (
                engine_version_string, engine_commit_string,
                consumer_version_string,
            )
            # Per-UC denotation of effective sampling — mirrors what's at
            # the top of run-summary.yaml so every analysis is self-
            # describing without needing to cross-reference its run.
            try:
                from dav.ai.client import effective_sampling as _eff_sampling
                _eff_block = _eff_sampling(self.inference.primary)
            except Exception:
                _eff_block = {}
            analysis = Analysis(
                use_case_uuid=use_case.uuid,
                analysis_metadata=AnalysisMetadata(
                    model=self.inference.primary.model,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    tool_call_count=len(self._tool_trace),
                    total_tokens=self._total_tokens,
                    stage2_run_id=run_id,
                    wall_time_seconds=wall_time,
                    sample_seeds=[effective_seed] if effective_seed is not None else None,
                    engine_version=engine_version_string(),
                    engine_commit=engine_commit_string(),
                    consumer_version=consumer_version_string(self.consumer_content_path),
                    infrastructure_confidence=self._compute_infrastructure_confidence(),
                    effective_sampling=_eff_block,
                ),
                components_required=[_from_dict(ComponentRequired, x) for x in data.get("components_required", [])],
                data_model_touched=[_from_dict(DataModelTouched, x) for x in data.get("data_model_touched", [])],
                capabilities_invoked=[_from_dict(CapabilityInvoked, x) for x in data.get("capabilities_invoked", [])],
                provider_types_involved=[_from_dict(ProviderTypeInvolved, x) for x in data.get("provider_types_involved", [])],
                policy_modes_required=[_from_dict(PolicyModeRequired, x) for x in data.get("policy_modes_required", [])],
                gaps_identified=[_from_dict(GapIdentified, x) for x in data.get("gaps_identified", [])],
                summary=_from_dict(AnalysisSummary, data["summary"]),
                tool_call_trace=self._tool_trace,
            )
        except (KeyError, TypeError) as e:
            raise AgentError(f"final analysis missing required fields: {e}") from e

        # Validate rationale coverage (§5.1 of requirements)
        self._warn_on_empty_rationales(analysis)
        return analysis

    def _warn_on_empty_rationales(self, analysis: Analysis) -> None:
        """Log a warning if any assertion is missing its rationale."""
        empty = []
        for c in analysis.components_required:
            if not c.rationale.strip():
                empty.append(f"component/{c.id}")
        for c in analysis.capabilities_invoked:
            if not c.rationale.strip():
                empty.append(f"capability/{c.id}")
        for g in analysis.gaps_identified:
            if not g.rationale.strip():
                empty.append(f"gap/{g.description[:30]}")
        if empty:
            log.warning("analysis %s has %d empty rationales: %s",
                        analysis.analysis_metadata.stage2_run_id,
                        len(empty), empty[:5])
