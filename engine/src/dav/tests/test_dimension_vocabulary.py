"""The dimension vocabulary must come from the corpus, not a private engine copy.

The engine carried its own list of legal `scenario.dimensions.*` values while the
corpus grew legitimate new ones (`day2_operations`, `single_with_deps`,
`internal_audit`, ...). Any UC using one was silently quarantined. Measured
independently against the repos at origin/main:

    udlm      62 / 73  quarantined by the engine's old list   (85%)
    dcm       73 / 489                                        (15%)
    combined 135 / 562                                        (24%)

The model side published `DIMENSION-VOCABULARY.yaml` as a single source and gates
every UC against it in CI. These tests pin that the engine READS it rather than
forking a second copy that can drift again.
"""
import textwrap

import pytest

from dav.core.consumer_profile import (
    apply_dimension_vocabulary,
    find_dimension_vocabulary,
    get_generic_reference_profile,
)

VOCAB = textwrap.dedent("""
    version: "1.0.0"
    dimensions:
      lifecycle_phase: [new_request, day2_operations]
      resource_complexity: [single_no_deps, single_with_deps]
      governance_context: [standard_governance, internal_audit]
    folded_aliases:
      lifecycle_phase: {decommissioning: decommission}
    """)


@pytest.fixture
def vocab_file(tmp_path):
    d = tmp_path / "use-cases"
    d.mkdir()
    f = d / "DIMENSION-VOCABULARY.yaml"
    f.write_text(VOCAB)
    return f


def test_found_by_recursive_search(tmp_path, vocab_file):
    """The corpus is cloned to a workspace whose layout differs per repo (udlm
    publishes at use-cases/, dcm at dav/use-cases/), so locating it must not
    depend on a fixed relative path."""
    assert find_dimension_vocabulary(tmp_path) == vocab_file


def test_missing_vocabulary_returns_none_rather_than_raising(tmp_path):
    """Absence must degrade to the built-in list, not crash a run — but the caller
    warns, because that fallback is the fork that caused the quarantine."""
    assert find_dimension_vocabulary(tmp_path / "nope") is None


def test_published_values_replace_the_private_list(vocab_file):
    profile, report = apply_dimension_vocabulary(get_generic_reference_profile(), vocab_file)
    assert "day2_operations" in profile.lifecycle_phases
    assert "single_with_deps" in profile.resource_complexities
    assert "internal_audit" in profile.governance_contexts
    assert report["version"] == "1.0.0"


def test_report_names_what_was_being_quarantined(vocab_file):
    """The added values ARE the quarantine cause; a silent swap would hide the very
    thing this change exists to reveal."""
    _, report = apply_dimension_vocabulary(get_generic_reference_profile(), vocab_file)
    assert "day2_operations" in report["dimensions"]["lifecycle_phase"]["added"]
    assert "internal_audit" in report["dimensions"]["governance_context"]["added"]


def test_report_names_engine_only_values_that_will_now_quarantine(vocab_file):
    """Replacement is not purely additive. A value the engine accepted but the
    published file omits starts quarantining, so it must be surfaced, not silently
    dropped — this is what let me check both corpora before adopting (neither uses
    any of them)."""
    _, report = apply_dimension_vocabulary(get_generic_reference_profile(), vocab_file)
    removed = report["dimensions"]["lifecycle_phase"]["removed"]
    assert "modification" in removed, "engine-only values must be reported"


def test_dimensions_absent_from_the_file_are_left_alone(vocab_file):
    """The file lists three dimensions; the other three keep the engine defaults
    rather than becoming empty (which would quarantine everything)."""
    profile, _ = apply_dimension_vocabulary(get_generic_reference_profile(), vocab_file)
    assert profile.failure_modes, "unlisted dimension was emptied"
    assert "policy_violation" in profile.failure_modes


def test_folded_aliases_are_recorded_but_not_applied(vocab_file):
    """Accepting both spellings is how one concept silently becomes two categories
    and corrupts every count — the corpus is already folded, so the engine requires
    the canonical form."""
    profile, report = apply_dimension_vocabulary(get_generic_reference_profile(), vocab_file)
    assert report["folded_aliases"]["lifecycle_phase"] == {"decommissioning": "decommission"}
    assert "decommissioning" not in profile.lifecycle_phases


def test_a_uc_using_a_grown_value_validates_after_adoption(vocab_file):
    """End to end: this exact shape was being quarantined."""
    from dav.core.use_case_schema import Dimensions

    dims = Dimensions(
        lifecycle_phase="day2_operations", resource_complexity="single_with_deps",
        policy_complexity="single_validation", provider_landscape="single_eligible",
        governance_context="internal_audit", failure_mode="policy_violation")

    before = get_generic_reference_profile()
    assert dims.validate(before), "expected the old list to reject this UC"

    after, _ = apply_dimension_vocabulary(before, vocab_file)
    # policy_complexity/provider_landscape/failure_mode are not in the fixture file,
    # so they still validate against the engine defaults — which accept these.
    assert not dims.validate(after), "UC should validate once the vocabulary is adopted"
