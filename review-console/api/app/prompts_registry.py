"""Prompt registry + assembler (F8 — per-project prompt management).

Source of truth for the DAV pipeline STAGES and the named SECTIONS of each stage's
base prompt. The UI reads this read-only so editors see what they're customizing; the
assembler renders base + per-project customization for live preview (and, as stages are
wired, for the actual run).

Customization model (stored in project_stage_context):
  • content (TEXT)            — additional context, appended as a trailing section
  • section_overrides (JSONB) — { section_name: replacement_text } replacing a base section

Status per stage (honest about what's enforced today):
  • 'append-live'  — additional context is injected at runtime now (console stages)
  • 'stored-held'  — customization is stored + previewable but NOT yet enforced at runtime
                     (the stage-2 engine prompt is the eval verdict — a real override is a
                     prompt-quality change that must be A/B'd before it's trusted)

Design: docs/prompt-management-design.md. See rbac.py P_PROMPT_MANAGE, main.py endpoints.
"""
from __future__ import annotations

from typing import Optional

# A short, clearly-labelled reference snapshot of the engine stage-2 system prompt
# opening (canonical source: engine/src/dav/ai/prompts.py _STAGE2_SYSTEM_PROMPT_TEMPLATE).
# Used only for the editor/preview — NOT the live prompt. Kept brief on purpose.
_STAGE2_SYSTEM_SNAPSHOT = (
    "You are a principal architect reviewing the {framework_name} architecture "
    "specification. Your job is to analyze whether the architecture, as currently "
    "documented, supports a specific use case.\n\n"
    "Use the spec-retrieval tools iteratively: search by keyword, then fetch specific "
    "sections. Ground every claim in retrieved spec content; do not invent capabilities. "
    "Emit the final verdict as the required Analysis JSON.\n\n"
    "[reference snapshot — canonical text lives in the engine; overriding this section is "
    "stored but held pending an A/B]"
)

# Ordered stage registry. `sections` are overridable named base sections; `append` is the
# always-available trailing additional-context lever.
STAGES = [
    {
        "key": "stage2-analysis",
        "label": "Stage 2 — Analysis (eval verdict)",
        "surface": "engine",
        "status": "stored-held",
        "description": "The core use-case-vs-spec evaluation. Engine-owned; the eval "
                       "verdict prompt. Overrides are stored and previewable but held "
                       "until A/B-validated (prompt-quality sensitive).",
        "append": {"live": False, "label": "Additional grounding context for evaluation"},
        "sections": [
            {"name": "system", "label": "System prompt",
             "description": "The principal-architect system instruction the model runs under.",
             "base": _STAGE2_SYSTEM_SNAPSHOT},
        ],
    },
    {
        "key": "arch_review",
        "label": "Architecture review & enhancement",
        "surface": "console",
        "status": "append-live",
        "description": "Narrative architecture review and enhancement planning (post-eval). "
                       "Additional context is injected at runtime today.",
        "append": {"live": True, "label": "Additional context & instructions for review/enhancement"},
        "sections": [],
    },
]

_BY_KEY = {s["key"]: s for s in STAGES}


def stage(key: str) -> Optional[dict]:
    return _BY_KEY.get(key)


def registry() -> list[dict]:
    """The full stage/section catalog for the UI (read-only)."""
    return STAGES


def assemble(stage_key: str, *, content: str = "", section_overrides: Optional[dict] = None) -> dict:
    """Render the assembled prompt for a stage given a project's customization.

    Returns {sections: [{name, label, text, overridden}], append, text} where `text` is
    the final assembled prompt (base sections with overrides applied, then the appended
    additional-context section). Pure — used for preview and (later) runtime assembly.
    """
    s = _BY_KEY.get(stage_key)
    section_overrides = section_overrides or {}
    if not s:
        return {"sections": [], "append": (content or "").strip(), "text": (content or "").strip()}
    out_sections, parts = [], []
    for sec in s["sections"]:
        ov = section_overrides.get(sec["name"])
        text = ov if (ov is not None and ov.strip() != "") else sec.get("base", "")
        out_sections.append({
            "name": sec["name"], "label": sec["label"],
            "text": text, "overridden": bool(ov and ov.strip()),
        })
        if text:
            parts.append(text)
    append = (content or "").strip()
    if append:
        parts.append(f"## Project context & instructions (set by the architect — honor these)\n{append}")
    return {"sections": out_sections, "append": append, "text": "\n\n".join(parts).strip()}
