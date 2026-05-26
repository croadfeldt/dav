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
        # Anti-fishing state — models routinely ignore "section not found"
        # results and just try the same section_title in another document, or
        # re-fetch a document already returned as "too large". When patterns
        # recur, we PREPEND a directive to the tool response so the model sees
        # reinforced guidance fresh on its next turn. Reset per agent run.
        self._section_title_misses: dict[str, int] = {}   # section_title -> miss count
        self._too_large_handles: set[str] = set()
        # wall-time tracking for AnalysisMetadata.wall_time_seconds
        self._wall_time_start: float = 0.0
        # per-sample seed override. When None, falls back to
        # config.seed. The runner sets this for each sample of a multi-sample
        # run so each sample uses a distinct seed.
        self._sample_seed: int | None = None

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
                if count >= 3:
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

    def _emit_turn(self, turn: int, kind: str, **fields) -> None:
        """Append a single structured-turn record to turns_log_path (JSONL).

        Errors are swallowed — emission must never disrupt the run itself.
        `kind` values: 'start', 'response', 'tool', 'final'.

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
                **capped,
            }
            self.turns_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.turns_log_path.open("a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except Exception as e:
            log.warning("turns emit failed: %s", e)

    def analyze(self, use_case: UseCase) -> Analysis:
        """
        Run the agent loop on a use case. Returns a validated Analysis.

        Raises AgentError on unrecoverable failures (exhausted budget
        with no final response, malformed JSON, schema validation fails).
        """
        run_id = str(uuid.uuid4())
        log.info("stage2 run %s started for use case %s", run_id, use_case.uuid)
        # measure wall time so AnalysisMetadata.wall_time_seconds
        # is populated for ensemble merging and explore-mode cost reporting.
        import time as _time
        self._wall_time_start = _time.monotonic()
        # Reset anti-fishing state for this run
        self._section_title_misses = {}
        self._too_large_handles = set()

        tool_defs = get_tool_definitions()
        sys_prompt = build_stage2_system_prompt(self.consumer_profile)
        user_prompt = build_stage2_user_prompt(use_case, self.consumer_profile)
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

            log.info(
                "turn %d/%d: %d messages in context, %d tokens used so far%s",
                turn, self.config.max_tool_calls,
                len(messages), self._total_tokens,
                " (budget-hit: tools disabled, forcing final emit)" if at_budget else "",
            )

            try:
                response = self.inference.chat(
                    messages=messages,
                    tools=tools_arg,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                    guided_json_schema=guided,
                    seed=self._sample_seed if self._sample_seed is not None else self.config.seed,
                )
            except InferenceError as e:
                raise AgentError(f"inference failed at turn {turn}: {e}") from e

            usage = response.usage or {}
            self._total_tokens += usage.get("total_tokens", 0)

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

                for tc in response.tool_calls:
                    tool_name = tc["function"]["name"]
                    try:
                        raw_args = tc["function"].get("arguments") or "{}"
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except json.JSONDecodeError:
                        args = {}
                        log.warning("tool %s had malformed arg JSON: %r", tool_name, raw_args)
                    log.info("turn %d: mcp call %s args=%s", turn, tool_name, args)

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
                # Parse its content as the final analysis.
                return self._parse_final(response.content, use_case, run_id)

            # Hit the budget limit on this turn — it should be emitting final
            return self._parse_final(response.content, use_case, run_id)

        # Shouldn't reach here, but keep mypy happy
        raise AgentError("agent loop terminated without final response")

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
