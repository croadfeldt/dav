"""The persona set must come from the corpus, not a private engine copy (ADR-011).

Same contract as DIMENSION-VOCABULARY (test_dimension_vocabulary.py): the model
side publishes PERSONAS.yaml (canonical ids + tier + framing + objectives +
folded_aliases, CI-gated by PER-001); the engine READS it. A DAV-private persona
list would be the DIM-001 fork under a different name.

Every assertion reads what a consumer reads: the profile fields after apply,
the lens list the per-lens analysis will iterate, the report the run log prints.
"""
import textwrap

import pytest

from dav.core.consumer_profile import (
    apply_personas,
    find_personas,
    get_generic_reference_profile,
    lens_ids_for_uc,
    resolve_persona,
)

PERSONAS = textwrap.dedent("""
    version: "1.0.0"
    personas:
      - id: solution-architect
        tier: operational
        framing: "an architect decomposing a whole architecture"
        objectives: ["decomposition holds"]
      - id: compliance-auditor
        tier: governance
        framing: "an auditor validating platform behavior after the fact"
        objectives: ["every refusal leaves a tamper-evident record"]
      - id: director
        tier: oversight
        framing: "a director accountable for a portfolio"
        objectives: ["portfolio rollup derived from the graph"]
    folded_aliases:
      auditor: compliance-auditor
""")


@pytest.fixture
def personas_file(tmp_path):
    p = tmp_path / "use-cases" / "PERSONAS.yaml"
    p.parent.mkdir()
    p.write_text(PERSONAS)
    return p


def test_find_locates_recursively(personas_file, tmp_path):
    assert find_personas(tmp_path) == personas_file
    assert find_personas(tmp_path / "nowhere", tmp_path) == personas_file
    assert find_personas(tmp_path / "nowhere") is None
    assert find_personas(None, "") is None


def test_apply_populates_profile_and_report(personas_file):
    profile, report = apply_personas(get_generic_reference_profile(), personas_file)
    assert [p["id"] for p in profile.personas] == [
        "solution-architect", "compliance-auditor", "director"]
    assert profile.persona_aliases == {"auditor": "compliance-auditor"}
    assert report["errors"] == []
    assert report["count"] == 3
    assert report["tiers"] == {"operational": 1, "governance": 1, "oversight": 1}
    assert report["version"] == "1.0.0"


@pytest.mark.parametrize("mutation, expected_error", [
    ("tier: governance", None),  # control: the unmutated file applies clean
    ("tier: management", "not in"),               # invented tier
])
def test_tier_enum_enforced(personas_file, mutation, expected_error):
    text = PERSONAS.replace("tier: governance", mutation, 1)
    personas_file.write_text(text)
    profile, report = apply_personas(get_generic_reference_profile(), personas_file)
    if expected_error:
        assert any(expected_error in e for e in report["errors"])
        # a file that fails validation is NOT applied
        assert profile.personas == []
    elif mutation == "tier: governance":
        assert report["errors"] == [] and len(profile.personas) == 3


def test_duplicate_id_rejected(personas_file):
    dup = PERSONAS.replace("id: director", "id: solution-architect", 1)
    personas_file.write_text(dup)
    profile, report = apply_personas(get_generic_reference_profile(), personas_file)
    assert any("duplicate id" in e for e in report["errors"])
    assert profile.personas == []


def test_alias_to_unknown_canonical_rejected(personas_file):
    bad = PERSONAS.replace("auditor: compliance-auditor", "auditor: nobody-here")
    personas_file.write_text(bad)
    profile, report = apply_personas(get_generic_reference_profile(), personas_file)
    assert any("not a persona id" in e for e in report["errors"])
    assert profile.personas == []


def test_resolve_persona_canonical_alias_unknown(personas_file):
    profile, _ = apply_personas(get_generic_reference_profile(), personas_file)
    assert resolve_persona(profile, "director") == "director"
    assert resolve_persona(profile, "auditor") == "compliance-auditor"
    assert resolve_persona(profile, "astronaut") is None
    # no persona set loaded → nothing resolves (caller stays single-lens)
    assert resolve_persona(get_generic_reference_profile(), "director") is None


def test_lens_union_dedups_after_alias_resolution(personas_file):
    profile, _ = apply_personas(get_generic_reference_profile(), personas_file)
    # 'auditor' (alias) and 'compliance-auditor' (canonical) are ONE lens, not two
    lens, unknown = lens_ids_for_uc(
        profile, "solution-architect", ["auditor", "compliance-auditor", "director"])
    assert lens == ["solution-architect", "compliance-auditor", "director"]
    assert unknown == []


def test_lens_reports_unknown_and_keeps_resolved(personas_file):
    profile, _ = apply_personas(get_generic_reference_profile(), personas_file)
    lens, unknown = lens_ids_for_uc(profile, "astronaut", ["director", ""])
    # the unknown is REPORTED, never silently dropped; resolved lenses survive
    assert unknown == ["astronaut"]
    assert lens == ["director"]


def test_scenario_perspectives_round_trip():
    """scenario.perspectives flows through UC parse (from_dict) — a UC carrying
    the corpus field must not lose it on the way to lens derivation."""
    from dav.core.use_case_schema import UseCase
    uc = UseCase.from_dict({
        "uuid": "u-1", "handle": "fx-demo",
        "scenario": {
            "description": "d", "intent": "i", "success_criteria": ["s"],
            "actor": {"persona": "solution-architect", "profile": "infrastructure"},
            "profile": "infrastructure",
            "dimensions": {
                "lifecycle_phase": "new_request",
                "resource_complexity": "single_no_deps",
                "policy_complexity": "unconstrained",
                "provider_landscape": "single_provider",
                "governance_context": "standard",
                "failure_mode": "none",
            },
            "perspectives": ["compliance-auditor", "director"],
        },
        "generated_by": {"mode": "manual", "source": "human"},
    })
    assert uc.scenario.perspectives == ["compliance-auditor", "director"]
    # absent → empty list, never None
    d = uc.to_dict()
    del d["scenario"]["perspectives"]
    assert UseCase.from_dict(d).scenario.perspectives == []
