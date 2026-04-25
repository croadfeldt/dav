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

from dav.core.use_case_schema import UseCase

STAGE2_PROMPT_VERSION = "1.4"  # ε.1 — consumer-templated

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

Tool budget: you have a limited number of tool calls. Spend them on targeted retrieval, not bulk fetches.

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
      "id": "<capability id>",
      "usage": "<how it's used in this use case>",
      "rationale": "<why, referencing spec>",
      "spec_refs": [...],
      "confidence": "high|medium|low"
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
      "severity": "critical|major|moderate|minor|advisory",
      "description": "<what's missing or ambiguous>",
      "rationale": "<why this is a gap>",
      "spec_refs_consulted": ["<what I looked at>"],
      "spec_refs_missing": "<what should exist>",
      "recommendation": "<what the spec should say>",
      "confidence": "high|medium|low"
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
"""

def build_stage2_system_prompt(consumer_profile=None) -> str:
    """Render the stage 2 system prompt from a ConsumerProfile.

    If consumer_profile is None, falls back to the module-level default
    profile (DCM reference unless explicitly overridden).
    """
    if consumer_profile is None:
        from dav.core.consumer_profile import get_default_profile
        consumer_profile = get_default_profile()
    return _STAGE2_SYSTEM_PROMPT_TEMPLATE.format(
        framework_name=consumer_profile.framework_name,
        framework_short=consumer_profile.framework_short,
        provider_summary=consumer_profile.provider_summary or "capabilities that realize intent",
        policy_summary=consumer_profile.policy_summary or "evaluation engine",
        provider_types_pipe="|".join(consumer_profile.provider_types),
        policy_modes_pipe="|".join(consumer_profile.policy_modes),
    )

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

---

Your task: analyze whether {fw} supports this use case. Use the available
tools to retrieve spec content. When you've gathered enough, emit the
final JSON analysis as specified in the system prompt.
"""
