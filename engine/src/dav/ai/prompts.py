"""
Prompt library for stage 2 analysis.

Prompts are rendered from a ConsumerProfile so consumer-specific
vocabulary (framework name, provider types, policy modes, etc.) can be
swapped without forking. The system prompt is a template; values come from
the profile at runtime.

Prompt versioning convention: increment the version string on the
constant whenever a prompt changes meaningfully (rationale capture,
tool usage guidance, output schema). Store version alongside
analyses so baselines know which prompt produced them.
"""

from __future__ import annotations

import logging

from dav.core.use_case_schema import UseCase

log = logging.getLogger(__name__)

STAGE2_PROMPT_VERSION = "1.10"  # 1.10 — adds get_capability tool guidance to stop section_title misuse on capability matrix IDs

# /no_think directive at the top is a Qwen3 chat template token that disables
# the model's thinking-mode output (<think>...</think> blocks). We strip
# thinking output at parse time via _extract_json_object in agent.py, but
# disabling it upstream saves tokens and preserves determinism
# (thinking-mode sampling has different entropy characteristics even at
# temperature 0). Harmless no-op on non-Qwen3 models.
# To A/B test with thinking enabled, change the leading lines and bump
# STAGE2_PROMPT_VERSION.

_STAGE2_SYSTEM_PROMPT_TEMPLATE = """/no_think

You are a principal architect reviewing the {framework_name} architecture specification. Your job is to analyze whether the {framework_short} architecture, as currently documented, supports a specific use case.

{framework_short} has three foundational abstractions:
- Data: the unified data model (resources, policies, identities)
- Provider: {provider_summary}
- Policy: {policy_summary}

You have access to tools that retrieve {framework_short} spec content. Use them iteratively:
1. Search for relevant documents using keywords
2. Fetch specific sections with `get_document_section` — this is the default. Most {framework_short} documents are 10-80k characters; fetching a whole document wastes context.
3. Use `get_document` ONLY when you need the full structure of a short document, or when you've already narrowed down to a document whose sections you don't know.
4. If a `get_document` returns content larger than ~5000 characters, you have probably over-fetched — next time use `get_document_section` instead.
5. For structured IDs like `OBS-002`, `GRP-007`, `IDM-012` (the rows inside capability matrices), call `get_capability(capability_id='<ID>')`. **Do not** pass these IDs as a `section_title` to `get_document_section` — they are table-row identifiers, not section headers. The capability row lives inside its matrix section, not as a section of its own.

Tool budget: you have a limited number of tool calls. Spend them on targeted retrieval, not bulk fetches.

Before every tool call, **scan your prior tool calls in this conversation**. If you have already called the same tool with the same arguments earlier, do not call it again — the result is unchanged. Instead, either pivot to a DIFFERENT query, section, or handle, or stop fetching and write your analysis. The engine will detect cross-turn duplicates and short-circuit them with a DUPLICATE-CROSS-TURN marker pointing at the original tool_call_id, but you should pre-empt that by tracking what you've already asked.

Handling tool failures — READ THIS CAREFULLY:
- When `get_document_section` returns "Section '<X>' not found. Available sections: ..." — that error response is INFORMATION. It is telling you exactly which sections exist. Your next action MUST be either:
  (a) Pick a section title that was listed in the "Available sections" output, and call `get_document_section` again with that EXACT title, OR
  (b) Call `get_document` on the same handle to get the full outline.
- Do NOT keep guessing section titles in other documents after a miss. That is fishing, not research. Three consecutive "not found" responses means your search query was wrong — go back to `search_docs` with different terms.
- When `search_docs` returns documents whose titles don't obviously match your intent, the search query was too narrow or too literal. Try broader terms. "VM-provisioning" matches nothing; "virtual machine" or "resource provisioning" works. Hyphens are treated as word separators — prefer space-separated terms.

When you have gathered enough information, emit a final analysis as a single JSON object matching this schema:

{{
  "components_required": [
    {{
      "id": "<component identifier>",
      "role": "<one-line role description>",
      "rationale": "<why this component is required, referencing spec>",
      "spec_refs": ["<doc-handle>", "<doc-handle/section>"],
      "confidence": "high|medium|low"
    }}
  ],
  "data_model_touched": [
    {{
      "entity": "<data model entity name>",
      "fields_accessed": ["<field>", ...],
      "operations": ["read", "write", "mutate"],
      "rationale": "<why, referencing spec>",
      "spec_refs": [...],
      "confidence": "high|medium|low"
    }}
  ],
  "capabilities_invoked": [
    {{
      "id": "<capability id — stable snake_case key>",
      "name": "<short human-readable name, Title Case>",
      "description": "<one-sentence description of what the capability is>",
      "usage": "<how it's used in this use case>",
      "rationale": "<why, referencing spec>",
      "spec_refs": [...],
      "confidence": "high|medium|low",
      "depends_on": ["<other capability id this one requires>"]
    }}
  ],
  "provider_types_involved": [
    {{
      "type": "{provider_types_pipe}",
      "role": "<why this provider type>",
      "confidence": "high|medium|low"
    }}
  ],
  "policy_modes_required": [
    {{
      "mode": "{policy_modes_pipe}",
      "rationale": "<why>",
      "spec_refs": [...],
      "confidence": "high|medium|low"
    }}
  ],
  "gaps_identified": [
    {{
      "title": "<3-7 word phrase naming this gap type>",
      "severity": "critical|major|moderate|minor|advisory",
      "description": "<what's missing or ambiguous>",
      "rationale": "<why this is a gap>",
      "spec_refs_consulted": ["<what I looked at>"],
      "spec_refs_missing": ["<doc-handle/section-title>"],
      "recommendation": "<what the spec should say>",
      "confidence": "high|medium|low",
      "capability_id": "<OPTIONAL — the catalog capability this gap concerns; omit if none applies>"
    }}
  ],
  "summary": {{
    "verdict": "supported|partially_supported|not_supported",
    "overall_confidence": "high|medium|low",
    "notes": "<2-3 sentence architect-readable summary>"
  }}
}}

Rules for the final output:
- Output ONLY the JSON object. No prose before or after, no markdown fences.
- Every array field is required; use [] if nothing applies.
- Every rationale field must be non-empty if its list has entries.
- spec_refs values should look like "doc-handle" or "doc-handle/section-name".
- spec_refs_missing entries must use "doc-handle" or "doc-handle/section-title" format — not prose.
- severity and confidence use DIFFERENT vocabularies. severity is the 5-word scale (critical|major|moderate|minor|advisory); confidence is EXACTLY high, medium, or low — never a severity word. "moderate" is not a confidence value.
- capabilities_invoked[].depends_on is OPTIONAL: list ids of OTHER capabilities (preferably ones also in this capabilities_invoked list) that this capability requires to function. Use [] if none or unsure — do not guess. This surfaces foundational building blocks; only assert a dependency the spec actually implies.
- gaps_identified[].capability_id is OPTIONAL: when a gap is about a specific capability, tag it with that capability's catalog id so the gap can be tracked across runs. If the run supplies an allowed capability set (below), the value MUST be one of those ids; if none fits, OMIT the field rather than inventing an id.
"""

def build_stage2_system_prompt(consumer_profile=None) -> str:
    """Render the stage 2 system prompt from a ConsumerProfile.

    If consumer_profile is None, falls back to the module-level default
    profile (DCM reference unless explicitly overridden).
    """
    if consumer_profile is None:
        from dav.core.consumer_profile import get_default_profile
        consumer_profile = get_default_profile()
    prompt = _STAGE2_SYSTEM_PROMPT_TEMPLATE.format(
        framework_name=consumer_profile.framework_name,
        framework_short=consumer_profile.framework_short,
        provider_summary=consumer_profile.provider_summary or "capabilities that realize intent",
        policy_summary=consumer_profile.policy_summary or "evaluation engine",
        provider_types_pipe="|".join(consumer_profile.provider_types),
        policy_modes_pipe="|".join(consumer_profile.policy_modes),
    )
    # Wave-1 (gap identity): when the run supplies the consumer's catalog capability
    # keys, render them as the allowed set for gaps_identified[].capability_id. The
    # guided-JSON schema already hard-constrains the field to this enum; listing the
    # ids here lets the model REASON about which one a gap concerns before decoding
    # (so the forced choice is a correct choice), and to omit the tag when none fits.
    known_caps = list(getattr(consumer_profile, "known_capability_ids", None) or [])
    if known_caps:
        prompt += (
            "\n\n## Gap capability tagging (this run)\n"
            "This run supplies a capability catalog. When a gap in `gaps_identified` "
            "concerns one of these capabilities, set its `capability_id` to the exact id "
            "so the gap is tracked across runs; if a gap maps to none of them, omit the "
            "field (do not invent an id). Allowed capability ids:\n"
            + ", ".join(known_caps)
        )
    # M12 follow-up: per-run spec source focus hint. The DAV_SPEC_NAMESPACES_FILTER
    # env var is set by the Tekton run-corpus task when the operator picked
    # a subset of spec sources in the New Run modal. The MCP still serves
    # every spec namespace; this is a soft instruction so the LLM prefers
    # grounding against the selected sources.
    import os
    ns_filter = (os.environ.get("DAV_SPEC_NAMESPACES_FILTER") or "").strip()
    if ns_filter:
        namespaces = [n.strip() for n in ns_filter.split(",") if n.strip()]
        if namespaces:
            prompt += (
                "\n\n## Spec source focus for this run\n"
                f"This run is scoped to the following spec source namespace(s): "
                f"{', '.join(namespaces)}. When you query the MCP (handles look "
                f"like `<namespace>/<path>`), prefer documents from these "
                f"namespaces. Use other-namespace documents only if you cannot "
                f"find what you need in the listed namespaces, and note the "
                f"cross-namespace lookup in your analysis."
            )
    # #45b grounding nudge — an A/B-able, OFF-by-default behavioral push toward
    # spec-anchored claims. The 2026-05-30 72B eval isolated ungrounded "generic
    # label" depth as the real quality gap; this is the terse hypothesis to test
    # against exploration_delta + the shallow signal BEFORE it's ever the default.
    # Terse by design — the v1.9 "stop fishing" lecture made things worse, so one
    # crisp behavioral line (and it leans on the existing confidence field rather
    # than telling the model to drop claims).
    if (os.environ.get("DAV_GROUNDING_NUDGE") or "").strip().lower() in ("1", "true", "yes", "on"):
        prompt += (
            "\n\n## Grounding emphasis (this run)\n"
            "Cite at least one consulted `spec_ref` for every component, data "
            "entity, capability, and policy-mode claim. Favor fewer spec-anchored "
            "findings over many generic ones; if you genuinely cannot anchor a "
            "claim to a spec, set its `confidence` to low rather than asserting it "
            "as though grounded."
        )
    # #93/#125 — per-project Evaluation (stage-2) prompt context. Set by the run-corpus
    # Tekton task from DAV_STAGE2_CONTEXT, which the API populates with the project's
    # resolved Evaluation prompt (use-category → project → base, most-specific-first).
    # Byte-identical by default (empty → no change); a real override is only ever injected
    # by an A/B experiment's candidate arm until a win promotes it. Same lever shape as the
    # grounding nudge above — the established A/B-able seam.
    stage2_ctx = (os.environ.get("DAV_STAGE2_CONTEXT") or "").strip()
    # Loud provenance: this value crosses console -> PipelineRun -> Pipeline ->
    # Task -> env, and one hop (the Pipeline) silently dropped it for every run
    # until 2026-07-28. A run that SHOULD carry context but logs nothing here is
    # the dropped-pipe signature.
    if stage2_ctx:
        log.info("stage2-context: %d chars of project prompt context active", len(stage2_ctx))
    if stage2_ctx:
        prompt += (
            "\n\n## Project context & instructions for evaluation "
            "(set by the architect — honor these)\n"
            f"{stage2_ctx}"
        )
    return prompt

def _refusal_contract_block(use_case: UseCase) -> str:
    """Inverted-success contract for refusal-semantics UCs; empty for normal ones.

    Whole corpus families (`must-reject/*`, the `-refused` class-versioning cases)
    succeed ONLY if the system refuses. Without this block the closing question
    "does the architecture support this use case?" is ambiguous for them, and the
    model can report an architecture's correct refusal as a missing capability —
    scoring the case exactly backwards. Stating the contract makes the scored
    surface the refusal's QUALITY, which is what the author's success_criteria
    already describe.
    """
    if not getattr(use_case, "is_refusal_case", False):
        return ""
    return """
!! SUCCESS SEMANTICS: REFUSE !!
This use case succeeds ONLY IF THE SYSTEM REFUSES the intent above. Realizing the
intent is the FAILURE outcome, not the success outcome. The scored surface is the
QUALITY OF THE REFUSAL, judged against the success criteria — typically that it is:
  - typed        (a machine-matchable error of the right class, not a generic failure)
  - actionable   (names the legitimate remediation path)
  - non-leaking  (discloses nothing about the protected resource beyond its existence being forbidden)
  - auditable    (a refusal record exists with the relevant identities and the deciding policy)
  - whole        (the entire intent is refused — never silently repaired or partially accepted)

Apply the verdict vocabulary accordingly:
  - "supported"           = the architecture specifies a refusal meeting the success criteria
  - "partially_supported" = it refuses, but the refusal contract is incomplete (e.g. untyped,
                            unaudited, leaks detail, or permits partial acceptance)
  - "not_supported"       = the architecture would ALLOW the intent, or is silent on refusing it

Gaps must describe what is missing FROM THE REFUSAL CONTRACT. Do NOT report the
system's inability to carry out the intent as a gap — that inability is the
correct behavior this use case is testing for.
"""

def _stage2_task_line(use_case: UseCase, fw: str) -> str:
    """Closing task sentence, matched to the use case's success semantics."""
    if getattr(use_case, "is_refusal_case", False):
        return (f"analyze whether {fw} correctly REFUSES this intent, and whether the "
                f"refusal contract meets the success criteria (see SUCCESS SEMANTICS above).")
    return f"analyze whether {fw} supports this use case."

def build_stage2_user_prompt(use_case: UseCase, consumer_profile=None) -> str:
    """Build the user-turn prompt with the use case to analyze.

    Framework name comes from the consumer profile so the LLM sees
    consistent terminology across system and user turns.
    """
    if consumer_profile is None:
        from dav.core.consumer_profile import get_default_profile
        consumer_profile = get_default_profile()
    fw = consumer_profile.framework_short
    s = use_case.scenario
    return f"""Analyze this use case against the current {fw} architecture specification.

USE CASE: {use_case.handle}  (uuid: {use_case.uuid})

SCENARIO:
{s.description}

ACTOR:
  Persona: {s.actor.persona}
  Profile: {s.actor.profile}

INTENT:
{s.intent}

SUCCESS CRITERIA:
{chr(10).join(f'  - {c}' for c in s.success_criteria)}

DIMENSIONS:
  lifecycle_phase: {s.dimensions.lifecycle_phase}
  resource_complexity: {s.dimensions.resource_complexity}
  policy_complexity: {s.dimensions.policy_complexity}
  provider_landscape: {s.dimensions.provider_landscape}
  governance_context: {s.dimensions.governance_context}
  failure_mode: {s.dimensions.failure_mode}

PROFILE: {s.profile}

EXPECTED DOMAIN INTERACTIONS:
{chr(10).join(f'  - {di.domain}: {di.interaction}' for di in s.expected_domain_interactions) if s.expected_domain_interactions else '  (none stated by author — discover from spec)'}

TAGS: {', '.join(use_case.tags) if use_case.tags else '(none)'}
{_refusal_contract_block(use_case)}
---

Your task: {_stage2_task_line(use_case, fw)} Use the available
tools to retrieve spec content. When you've gathered enough, emit the
final JSON analysis as specified in the system prompt.
"""


# ────────────────────────── Two-pass (1.8) ──────────────────────────
# Pass 1 explores the spec via MCP and emits a verbose structured FINDINGS
# JSON instead of the canonical Analysis. Pass 2 starts in a fresh context
# with that findings object + the original UC + MCP access, and emits the
# Analysis JSON. The re-fetch capability in pass 2 means anything pass 1
# compressed too aggressively is recoverable without re-running pass 1.
# Goal is information preservation for gap analysis: pass 1 captures every
# section, every excerpt, every cross-reference; pass 2 reasons over them.

_PASS1_FINDINGS_INSTRUCTION = """
You are running PASS 1 of a two-pass analysis. Your job is NOT to emit the
final Analysis JSON yet — instead, you produce a verbose, structured
FINDINGS JSON that captures EVERY detail a downstream synthesis pass might
need to identify gaps in the architecture.

Use the MCP tools (`search_docs`, `get_document_section`, `get_document`)
to retrieve relevant spec content. Be thorough: it's better to capture a
section pass 2 won't end up using than to skip one that pass 2 needs and
can't easily refind.

When you're done exploring, emit your FINDINGS as a SINGLE JSON object
matching this schema (no prose before or after, no markdown fence):

{
  "spec_docs_consulted": [
    {
      "handle": "<doc handle, e.g. <namespace>/<path-to-doc>.md>",
      "sections_retrieved": [
        {
          "title": "<verbatim section title>",
          "key_capabilities": ["<capability id or name>", ...],
          "key_constraints": ["<verbatim constraint statement>", ...],
          "cross_references": ["<other doc/section this depends on>", ...],
          "excerpt": "<up to 800 chars of verbatim spec text that's most relevant>",
          "notes": "<your observations about this section's relevance to the UC>"
        }
      ]
    }
  ],
  "capabilities_observed": [
    {
      "id": "<capability id from spec>",
      "name": "<capability name>",
      "spec_ref": "<handle/section where defined>",
      "supports_uc": "yes|partial|unclear",
      "rationale": "<one or two sentences>"
    }
  ],
  "data_model_touched": ["<entity name>", ...],
  "policy_landscape_observed": ["<observation>", ...],
  "provider_landscape_observed": ["<observation>", ...],
  "potential_gaps": [
    {
      "description": "<what seems missing or weak>",
      "spec_searched": ["<handles searched>"],
      "spec_refs_missing": ["<handle/section the gap would need>"],
      "candidate_severity": "low|medium|high",
      "evidence": "<what makes you think this is a gap>"
    }
  ],
  "unresolved_questions": [
    "<questions pass 2 may need to resolve with additional MCP fetches>"
  ],
  "exploration_notes": "<any meta-observations about the spec's coverage of this UC>"
}

Rules:
- Be EXHAUSTIVE on `spec_docs_consulted`. Pass 2 cannot read your turn
  history; the findings JSON is the only thing pass 2 sees from your work.
- Quote spec text verbatim in `excerpt`; don't paraphrase. Gap analysis
  is highly sensitive to exact wording.
- If you observe something that MIGHT be a gap but aren't sure, put it
  in `potential_gaps` with `candidate_severity: low` rather than dropping
  it. Pass 2 can dismiss it cheaply.
- `unresolved_questions` is the explicit handoff — list anything you
  couldn't fully answer so pass 2 knows where to re-fetch.
"""


_PASS2_ANALYSIS_INSTRUCTION = """
You are running PASS 2 of a two-pass analysis. Pass 1 already explored the
spec and produced a FINDINGS JSON which you can see in the user message.
Your job is to synthesize a FINAL Analysis JSON matching the schema in the
system prompt above.

You have MCP tools available (`search_docs`, `get_document_section`,
`get_document`) for RE-FETCH if pass 1's findings don't have enough detail
to resolve a specific question. Use them sparingly — pass 1 should have
captured the bulk of what you need.

Recommended workflow:
1. Read the FINDINGS JSON carefully. Note `unresolved_questions` and
   `potential_gaps` — these are pass 1's explicit handoff.
2. If any `unresolved_question` or `potential_gap` needs additional spec
   context to resolve cleanly, re-fetch the relevant section via MCP.
3. Synthesize the canonical Analysis JSON per the system prompt's schema.
4. Be explicit when promoting a pass-1 `potential_gap` to a confirmed
   `gaps[]` entry: reference the pass-1 evidence in `rationale`.
5. Cite `spec_refs` using the handles pass 1 recorded; this preserves the
   traceability between exploration and conclusion.
"""


def build_pass1_findings_system_prompt(consumer_profile=None) -> str:
    """Pass 1 system prompt: stage-2 framing + findings-emission instruction."""
    base = build_stage2_system_prompt(consumer_profile)
    return base + "\n\n# ── PASS 1 OVERRIDE ──\n" + _PASS1_FINDINGS_INSTRUCTION.strip()


def build_pass2_analysis_system_prompt(consumer_profile=None) -> str:
    """Pass 2 system prompt: stage-2 framing + analysis-emission instruction
    with re-fetch guidance."""
    base = build_stage2_system_prompt(consumer_profile)
    return base + "\n\n# ── PASS 2 SYNTHESIS ──\n" + _PASS2_ANALYSIS_INSTRUCTION.strip()


def build_pass2_user_prompt(use_case: UseCase, findings_json: str,
                            consumer_profile=None) -> str:
    """Pass 2 user prompt: the original UC + the findings JSON pass 1 emitted."""
    base = build_stage2_user_prompt(use_case, consumer_profile)
    return (
        base
        + "\n\n---\n\nPASS 1 FINDINGS (from your prior exploration of the spec):\n\n"
        + findings_json
        + "\n\n---\n\nNow emit the canonical Analysis JSON per the system prompt's "
          "schema. Re-fetch any spec section via MCP if the findings don't have the "
          "detail you need to make a clean conclusion."
    )

