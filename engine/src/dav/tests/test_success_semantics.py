"""Success semantics (realize vs refuse) — schema inference + prompt contract.

Whole corpus families succeed only if the system REFUSES (must-reject/*, the
`-refused` class-versioning cases). These tests pin the two behaviors that keep
such cases from being scored backwards: the UC resolves to `refuse`, and the
stage-2 prompt states the inverted contract.
"""
from dav.ai.prompts import build_stage2_user_prompt
from dav.core.use_case_schema import (
    Actor, Dimensions, GeneratedBy, Scenario, SuccessSemantics, UseCase,
    infer_success_semantics,
)


def _uc(handle: str, tags=None, success_semantics: str = "") -> UseCase:
    return UseCase(
        uuid="uc-000000000001",
        handle=handle,
        scenario=Scenario(
            description="A tenant references another tenant's resource.",
            actor=Actor(persona="application-team-member", profile="standard"),
            intent="Declare a dependency on a foreign tenant's resource",
            success_criteria=["The request is refused at validation"],
            dimensions=Dimensions(
                lifecycle_phase="new_request",
                resource_complexity="hard_dependencies",
                policy_complexity="cross_domain_constraint",
                provider_landscape="single_eligible",
                governance_context="standard_governance",
                failure_mode="policy_violation",
            ),
            profile="standard",
        ),
        generated_by=GeneratedBy(mode="authoring", source="human-authored"),
        tags=tags or [],
        success_semantics=success_semantics,
    )


# ── inference from corpus naming ─────────────────────────────────────────────

def test_must_reject_handle_prefix_infers_refuse():
    assert infer_success_semantics("must-reject/cross-tenant-reference-refused") == "refuse"


def test_refused_handle_suffix_infers_refuse():
    # the class-versioning family uses the suffix without the must-reject prefix
    assert infer_success_semantics(
        "class-versioning/breaking-base-underdeclared-bump-refused") == "refuse"


def test_refusal_tags_infer_refuse():
    assert infer_success_semantics("some/handle", ["multi-tenancy", "refusal-contract"]) == "refuse"
    assert infer_success_semantics("some/handle", ["must-reject"]) == "refuse"


def test_ordinary_use_case_infers_realize():
    assert infer_success_semantics("compute/vm-intent-osac-placement", ["vm"]) == "realize"
    assert _uc("compute/vm-provisioning").effective_success_semantics == "realize"
    assert _uc("compute/vm-provisioning").is_refusal_case is False


def test_explicit_field_wins_over_inference():
    # author states realize on a must-reject-looking handle -> realize
    uc = _uc("must-reject/looks-like-refusal", success_semantics="realize")
    assert uc.effective_success_semantics == "realize"
    assert uc.is_refusal_case is False
    # and the converse: explicit refuse on an ordinary handle
    assert _uc("compute/ordinary", success_semantics="refuse").is_refusal_case is True


def test_unrecognized_explicit_value_falls_back_to_inference():
    uc = _uc("must-reject/cross-tenant-reference-refused", success_semantics="banana")
    assert uc.effective_success_semantics == SuccessSemantics.REFUSE.value


def test_from_dict_round_trips_explicit_semantics():
    data = _uc("compute/x", success_semantics="refuse").to_dict()
    assert UseCase.from_dict(data).effective_success_semantics == "refuse"


# ── the prompt contract ──────────────────────────────────────────────────────

def test_refusal_prompt_states_inverted_contract():
    p = build_stage2_user_prompt(_uc("must-reject/cross-tenant-reference-refused"))
    assert "SUCCESS SEMANTICS: REFUSE" in p
    # the scored surface is the refusal's quality, and realizing is the failure
    assert "ONLY IF THE SYSTEM REFUSES" in p
    # the key anti-inversion instruction
    assert "Do NOT report the" in p and "inability" in p
    # verdict vocabulary is redefined for this case
    assert '"not_supported"' in p
    # closing task asks the refusal question, not the support question
    assert "correctly REFUSES this intent" in p


def test_ordinary_prompt_is_unchanged():
    p = build_stage2_user_prompt(_uc("compute/vm-intent-osac-placement"))
    assert "SUCCESS SEMANTICS" not in p
    assert "supports this use case." in p
