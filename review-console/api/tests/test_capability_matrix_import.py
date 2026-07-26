"""Parser tests for the DCM Capabilities Matrix import.

Pins the shape the importer depends on: ids, per-perspective definitions, declared
dependencies, em-dash-as-empty, and duplicate handling.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.capability_matrix_import import parse_matrix  # noqa: E402

MATRIX = """# DCM — Foundational Capabilities Matrix

## 1. Identity and Access Management

| ID | Capability | Consumer | Service Provider | Platform/Admin | Depends On |
|----|-----------|---------|---------|---------------|-----------|
| IAM-001 | Actor Authentication | Authenticate via IdP | — | Register Auth Providers | — |
| IAM-002 | Session Token Management | Receive tokens | — | Configure TTL | IAM-001 |
| IAM-003 | Role-Based Access Control | Role-appropriate responses | provider does thing | Declare mappings | IAM-001, IAM-002 |

## 2. Policy

| ID | Capability | Consumer | Service Provider | Platform/Admin | Depends On |
|----|-----------|---------|---------|---------------|-----------|
| POL-001 | Policy Evaluation | — | Evaluate policy | Author policies | IAM-003 |
"""


def _by_key(rows):
    return {r["cap_key"]: r for r in rows}


def test_parses_every_capability_row():
    rows = parse_matrix(MATRIX)
    assert [r["cap_key"] for r in rows] == ["IAM-001", "IAM-002", "IAM-003", "POL-001"]


def test_name_and_domain_prefix():
    r = _by_key(parse_matrix(MATRIX))["IAM-002"]
    assert r["name"] == "Session Token Management"
    assert r["domain_prefix"] == "IAM"


def test_section_becomes_domain_without_numbering():
    rows = _by_key(parse_matrix(MATRIX))
    assert rows["IAM-001"]["domain"] == "Identity and Access Management"
    assert rows["POL-001"]["domain"] == "Policy"


def test_definition_labels_each_perspective_and_drops_empty_ones():
    d = _by_key(parse_matrix(MATRIX))["IAM-001"]["definition"]
    assert "Consumer: Authenticate via IdP" in d
    assert "Platform/Admin: Register Auth Providers" in d
    # the em-dash Service Provider cell must not become an empty labelled segment
    assert "Service Provider:" not in d


def test_multiple_dependencies_split():
    assert _by_key(parse_matrix(MATRIX))["IAM-003"]["depends_on"] == ["IAM-001", "IAM-002"]


def test_em_dash_dependency_is_no_dependency():
    assert _by_key(parse_matrix(MATRIX))["IAM-001"]["depends_on"] == []


def test_spec_ref_recorded():
    assert parse_matrix(MATRIX)[0]["spec_refs"] == ["DCM-Capabilities-Matrix"]


def test_duplicate_ids_first_wins():
    dupe = MATRIX + "| IAM-001 | Redefined Later | x | — | — | — |\n"
    rows = _by_key(parse_matrix(dupe))
    assert rows["IAM-001"]["name"] == "Actor Authentication"


def test_non_capability_rows_ignored():
    noise = MATRIX + "\n| not-an-id | junk | a | b | c | d |\n| ---- | --- |\n"
    assert len(parse_matrix(noise)) == 4
