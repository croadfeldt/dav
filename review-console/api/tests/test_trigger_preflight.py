"""Trigger-time preflight: fail loud at launch, not silent three stages later.

Friction inventory items 5 and 6b, both observed as incidents:
  - a namespace typo produced "0 source(s) cloned, N skipped by filter" and only
    surfaced as an engine error ("no UC YAMLs") after the pipeline had started;
  - an empty capability catalog made gap id-tagging structurally zero while the
    run scored as if the analysis had simply found nothing identifiable.
"""
from app.main import _unknown_namespaces


def test_typo_is_rejected_with_the_unknown_name():
    assert _unknown_namespaces(["fixture"], {"fixtures", "udlm", "dcm"}) == ["fixture"]


def test_registered_names_pass():
    assert _unknown_namespaces(["fixtures", "udlm"], {"fixtures", "udlm", "dcm"}) == []


def test_no_filter_means_nothing_to_validate():
    """Unfiltered runs (whole source plane) must not be affected."""
    assert _unknown_namespaces(None, {"udlm"}) == []
    assert _unknown_namespaces([], {"udlm"}) == []


def test_empty_registry_rejects_everything():
    """No registered repos at all: any filter is unknown, and the error carries
    the (empty) registered list rather than silently cloning nothing."""
    assert _unknown_namespaces(["udlm"], set()) == ["udlm"]


def test_matching_is_exact_and_case_sensitive():
    """Namespaces are identifiers; a fuzzy or case-folding match would hide
    exactly the mistake this check exists to reject."""
    assert _unknown_namespaces(["Fixtures"], {"fixtures"}) == ["Fixtures"]


def test_duplicates_report_once_sorted():
    assert _unknown_namespaces(["b", "a", "b"], set()) == ["a", "b"]
