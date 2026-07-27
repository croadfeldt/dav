"""The corpus index: parse + validate at sync, count everything, guess nothing.

P1 of the scope plan. The incidents this guards against, by name:
  - 7 of 8 fixture UCs silently quarantined for off-vocabulary dimensions,
    reported only as "running 1 UC(s)" (parse/validate must happen BEFORE launch);
  - the engine's private vocabulary fork (validation must use the corpus's
    published file or be honestly skipped — never a built-in list);
  - vacuous passes (absent vocabulary => valid=None "unvalidated", not True).
"""
from app.corpus_index import (
    VOCABULARY_FILENAME,
    build_index_rows,
    load_vocabulary,
    parse_uc,
    validate_dimensions,
)

VOCAB_YAML = """
version: "1.0.0"
dimensions:
  lifecycle_phase: [new_request, day2_operations]
  resource_complexity: [single_no_deps, composite_service]
  failure_mode: [happy_path, policy_violation]
"""

UC_OK = """
uuid: uc-1
handle: must-reject/fx-a
scenario:
  description: d
  dimensions: {lifecycle_phase: new_request, failure_mode: policy_violation}
"""

UC_OFF_VOCAB = """
uuid: uc-2
handle: must-realize/fx-b
scenario:
  description: d
  dimensions: {lifecycle_phase: provisioning}
"""


def _entries(*pairs):
    return [{"path": p, "content": c} for p, c in pairs]


def test_vocabulary_found_anywhere_in_the_sweep():
    ents = _entries(("udlm/use-cases/" + VOCABULARY_FILENAME, VOCAB_YAML))
    assert load_vocabulary(ents)["lifecycle_phase"] == ["new_request", "day2_operations"]


def test_missing_vocabulary_is_none_not_a_guess():
    assert load_vocabulary(_entries(("x/a.yaml", UC_OK))) is None


def test_parse_uc_extracts_fields_and_infers_semantics():
    uc = parse_uc(UC_OK)
    assert uc["family"] == "must-reject"
    assert uc["success_semantics"] == "refuse"      # inferred from the family
    uc2 = parse_uc(UC_OFF_VOCAB)
    assert uc2["success_semantics"] == "realize"


def test_explicit_semantics_wins_over_inference():
    uc = parse_uc(UC_OK.replace("scenario:", "success_semantics: realize\nscenario:"))
    assert uc["success_semantics"] == "realize"


def test_non_uc_files_parse_to_none():
    for content in ("uc: x\nexpected_verdict: supported", "just: [a, list]", "::: not yaml :::"):
        assert parse_uc(content) is None


def test_off_vocabulary_dimension_is_named_in_the_reason():
    vocab = load_vocabulary(_entries((VOCABULARY_FILENAME, VOCAB_YAML)))
    reasons = validate_dimensions({"lifecycle_phase": "provisioning"}, vocab)
    assert reasons and "lifecycle_phase='provisioning'" in reasons[0]


def test_keys_absent_from_vocabulary_are_not_checked():
    vocab = {"lifecycle_phase": ["new_request"]}
    assert validate_dimensions({"governance_context": "anything"}, vocab) == []


def test_build_rows_counts_everything_and_validates():
    ents = _entries(("f/" + VOCABULARY_FILENAME, VOCAB_YAML),
                    ("f/uc-ok.yaml", UC_OK),
                    ("f/uc-bad.yaml", UC_OFF_VOCAB),
                    ("f/expected.yaml", "uc: x\nexpected_verdict: supported"))
    vocab = load_vocabulary(ents)
    rows, stats = build_index_rows("f", ents, vocab, "abc123")
    assert stats == {"ucs": 2, "non_uc": 2, "invalid": 1}
    by = {r["handle"]: r for r in rows}
    assert by["must-reject/fx-a"]["valid"] is True
    assert by["must-realize/fx-b"]["valid"] is False
    assert "provisioning" in by["must-realize/fx-b"]["invalid_reason"]
    assert all(r["repo_sha"] == "abc123" for r in rows)


def test_no_vocabulary_means_unvalidated_not_passing():
    """valid=None must be distinguishable from valid=True — rendering NULL as
    green would be the vacuous pass again."""
    rows, stats = build_index_rows("f", _entries(("f/uc.yaml", UC_OFF_VOCAB)), None, None)
    assert rows[0]["valid"] is None
    assert rows[0]["invalid_reason"] is None
    assert stats["invalid"] == 0
