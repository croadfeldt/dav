"""E4 criterion anchor: gap reporting scoped to the UC's own success criteria.

Measured motivation (E1×E3 battery, 2026-07-28): placement scatter — one
seeded hole at 1/3 consensus on four DIFFERENT UCs, so no identity or
sampling mechanism can quorum it. The UC's success_criteria are its declared
scored surface; anchoring to them is the deterministic convergence lever.
"""
import os

import yaml

from dav.ai.prompts import build_stage2_user_prompt
from dav.core.use_case_schema import UseCase


def _uc():
    return UseCase.from_dict({
        "uuid": "u-1", "handle": "must-realize/fx-demo",
        "scenario": {
            "description": "d", "intent": "i",
            "success_criteria": ["The refusal names the root", "All members refused"],
            "actor": {"persona": "platform-engineer", "profile": "infrastructure"},
            "profile": "infrastructure",
            "dimensions": {
                "lifecycle_phase": "new_request",
                "resource_complexity": "single_no_deps",
                "policy_complexity": "unconstrained",
                "provider_landscape": "single_provider",
                "governance_context": "standard",
                "failure_mode": "none",
            },
        },
        "generated_by": {"mode": "manual", "source": "human"},
    })


def test_anchor_off_by_default(monkeypatch):
    monkeypatch.delenv("DAV_CRITERION_ANCHOR", raising=False)
    assert "Scope anchor" not in build_stage2_user_prompt(_uc())


def test_anchor_enumerates_criteria_when_on(monkeypatch):
    monkeypatch.setenv("DAV_CRITERION_ANCHOR", "true")
    p = build_stage2_user_prompt(_uc())
    assert "Scope anchor" in p
    assert "1. The refusal names the root" in p
    assert "2. All members refused" in p
    assert "belongs to a different use case" in p


def test_anchor_survives_pass2(monkeypatch):
    from dav.ai.prompts import build_pass2_user_prompt
    monkeypatch.setenv("DAV_CRITERION_ANCHOR", "1")
    p = build_pass2_user_prompt(_uc(), "{}")
    assert "Scope anchor" in p
