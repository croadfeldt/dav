"""
Tests for ConsumerProfile loading and the default-profile mechanism.

Run:  python -m dav.tests.test_consumer_profile
Or:   pytest engine/src/dav/tests/test_consumer_profile.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

import yaml

from dav.core.consumer_profile import (
    ConsumerProfile,
    ConsumerProfileError,
    load_profile,
    load_profile_from_file,
    load_profile_from_mcp,
    get_dcm_reference_profile,
    get_default_profile,
    set_default_profile,
    reset_default_profile,
)

_failures: list[str] = []

def assert_eq(actual, expected, label: str) -> None:
    if actual != expected:
        _failures.append(f"{label}: got {actual!r}, expected {expected!r}")

def assert_true(cond: bool, label: str) -> None:
    if not cond:
        _failures.append(f"{label}: expected truthy")

def assert_raises(fn, exc, label: str) -> None:
    try:
        fn()
    except exc:
        return
    except Exception as e:
        _failures.append(f"{label}: expected {exc.__name__}, got {type(e).__name__}: {e}")
        return
    _failures.append(f"{label}: expected {exc.__name__}, no exception raised")

# --- Helpers ---

def _minimal_valid_dict():
    return {
        "framework_name": "TestFramework",
        "framework_short": "TF",
        "consumer_id": "test",
        "schema_version": "1.0",
        "lifecycle_phases": ["a", "b"],
        "resource_complexities": ["x"],
        "policy_complexities": ["x"],
        "provider_landscapes": ["x"],
        "governance_contexts": ["x"],
        "failure_modes": ["x"],
        "profiles": ["dev"],
        "provider_types": ["t1"],
        "policy_modes": ["m1"],
    }

# --- ConsumerProfile.validate tests ---

def test_validate_accepts_valid_profile():
    p = ConsumerProfile(**_minimal_valid_dict())
    assert_eq(p.validate(), [], "valid profile produces no errors")

def test_validate_rejects_empty_framework_name():
    d = _minimal_valid_dict()
    d["framework_name"] = ""
    p = ConsumerProfile(**d)
    errs = p.validate()
    assert_true(any("framework_name" in e for e in errs), "framework_name flagged")

def test_validate_rejects_empty_consumer_id():
    d = _minimal_valid_dict()
    d["consumer_id"] = "  "
    p = ConsumerProfile(**d)
    errs = p.validate()
    assert_true(any("consumer_id" in e for e in errs), "consumer_id flagged")

def test_validate_rejects_empty_vocabulary():
    d = _minimal_valid_dict()
    d["lifecycle_phases"] = []
    p = ConsumerProfile(**d)
    errs = p.validate()
    assert_true(any("lifecycle_phases" in e for e in errs), "empty vocab flagged")

def test_validate_rejects_non_string_in_vocabulary():
    d = _minimal_valid_dict()
    d["provider_types"] = ["valid", 42, "another"]
    p = ConsumerProfile(**d)
    errs = p.validate()
    assert_true(any("provider_types" in e for e in errs),
                f"non-string item flagged; got errs={errs}")

# --- ConsumerProfile.from_dict tests ---

def test_from_dict_preserves_known_fields():
    d = _minimal_valid_dict()
    d["abstractions_summary"] = "X, Y, Z"
    p = ConsumerProfile.from_dict(d)
    assert_eq(p.consumer_id, "test", "consumer_id round-trip")
    assert_eq(p.abstractions_summary, "X, Y, Z", "abstractions_summary round-trip")

def test_from_dict_tolerates_extra_fields():
    d = _minimal_valid_dict()
    d["future_field"] = "ignored"
    d["another_extra"] = [1, 2, 3]
    p = ConsumerProfile.from_dict(d)
    assert_eq(p.consumer_id, "test", "extra fields don't crash")

def test_to_dict_round_trip():
    d = _minimal_valid_dict()
    p1 = ConsumerProfile.from_dict(d)
    p2 = ConsumerProfile.from_dict(p1.to_dict())
    assert_eq(p1.to_dict(), p2.to_dict(), "round-trip identity")

# --- DCM reference profile tests ---

def test_dcm_reference_is_valid():
    p = get_dcm_reference_profile()
    assert_eq(p.validate(), [], "DCM reference validates clean")

def test_dcm_reference_has_expected_provider_types():
    p = get_dcm_reference_profile()
    # Current DCM spec defines 5 provider types. Compound services are a
    # Data concept (compound resource type specifications) orchestrated by
    # the Control Plane (Request Processor / Orchestrator), not a provider
    # type. Earlier corpus + earlier reference profile listed 6 types
    # including 'meta'; that was retired in DCM commit ecc11e9 (2026-04).
    # See spec/architecture/ai/DCM-AI-PROMPT.md "meta_provider removed".
    assert_eq(set(p.provider_types),
              {"service", "information", "auth", "peer_dcm", "process"},
              "DCM provider_types match current spec (5 types, no meta)")

def test_dcm_reference_has_expected_profiles():
    p = get_dcm_reference_profile()
    assert_eq(set(p.profiles),
              {"minimal", "dev", "standard", "prod", "fsi", "sovereign"},
              "DCM profiles match historical hardcoded values")

def test_dcm_reference_returns_fresh_copies():
    p1 = get_dcm_reference_profile()
    p2 = get_dcm_reference_profile()
    p1.provider_types.append("mutated")
    # Mutating one copy should not affect the other
    assert_true("mutated" not in p2.provider_types,
                "fresh copy not affected by mutation")

# --- File loader tests ---

def test_load_from_file_happy_path():
    d = _minimal_valid_dict()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(d, f)
        path = Path(f.name)
    try:
        p = load_profile_from_file(path)
        assert_eq(p.consumer_id, "test", "loaded consumer_id")
    finally:
        path.unlink()

def test_load_from_file_missing_file():
    assert_raises(
        lambda: load_profile_from_file("/nonexistent/path/profile.yaml"),
        FileNotFoundError, "missing file"
    )

def test_load_from_file_non_mapping():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(["not", "a", "mapping"], f)
        path = Path(f.name)
    try:
        assert_raises(
            lambda: load_profile_from_file(path),
            ValueError, "non-mapping YAML"
        )
    finally:
        path.unlink()

def test_load_from_file_invalid_profile():
    """File loads but profile.validate() fails → ValueError."""
    d = _minimal_valid_dict()
    d["lifecycle_phases"] = []   # validation will fail
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(d, f)
        path = Path(f.name)
    try:
        assert_raises(
            lambda: load_profile_from_file(path),
            ValueError, "validation failure surfaces as ValueError"
        )
    finally:
        path.unlink()

# --- MCP loader tests (mocked) ---

def test_load_from_mcp_direct_dict():
    """MCP returns a profile dict directly."""
    d = _minimal_valid_dict()
    fake_client = mock.MagicMock()
    fake_client.call.return_value = d
    with mock.patch("dav.ai.mcp_tools.McpClient", return_value=fake_client):
        p = load_profile_from_mcp("http://fake-mcp:8080")
    assert_eq(p.consumer_id, "test", "MCP direct dict load")

def test_load_from_mcp_wrapped_response():
    """MCP returns the standard {content: [{type:text, text:json}]} shape."""
    import json as _json
    d = _minimal_valid_dict()
    fake_client = mock.MagicMock()
    fake_client.call.return_value = {
        "content": [{"type": "text", "text": _json.dumps(d)}]
    }
    with mock.patch("dav.ai.mcp_tools.McpClient", return_value=fake_client):
        p = load_profile_from_mcp("http://fake-mcp:8080")
    assert_eq(p.consumer_id, "test", "MCP wrapped response load")

def test_load_from_mcp_call_failure():
    """MCP call raises → ConsumerProfileError."""
    fake_client = mock.MagicMock()
    fake_client.call.side_effect = RuntimeError("MCP unreachable")
    with mock.patch("dav.ai.mcp_tools.McpClient", return_value=fake_client):
        assert_raises(
            lambda: load_profile_from_mcp("http://fake-mcp:8080"),
            ConsumerProfileError, "MCP failure becomes ConsumerProfileError"
        )

def test_load_from_mcp_invalid_profile():
    """MCP returns a profile dict that fails validation."""
    d = _minimal_valid_dict()
    d["consumer_id"] = ""   # invalid
    fake_client = mock.MagicMock()
    fake_client.call.return_value = d
    with mock.patch("dav.ai.mcp_tools.McpClient", return_value=fake_client):
        assert_raises(
            lambda: load_profile_from_mcp("http://fake-mcp:8080"),
            ConsumerProfileError, "MCP returns invalid profile"
        )

# --- load_profile (high-level) tests ---

def test_load_profile_path_takes_precedence():
    d = _minimal_valid_dict()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(d, f)
        path = Path(f.name)
    try:
        # MCP should be ignored when path is given
        with mock.patch("dav.ai.mcp_tools.McpClient") as mc:
            mc.side_effect = AssertionError("MCP should not be called")
            p = load_profile(path=path, mcp_url="http://should-be-ignored:8080")
        assert_eq(p.consumer_id, "test", "path precedence")
    finally:
        path.unlink()

def test_load_profile_mcp_fallback_to_dcm_on_failure():
    """MCP failure + fall_back_to_dcm=True → DCM reference."""
    fake_client = mock.MagicMock()
    fake_client.call.side_effect = RuntimeError("MCP down")
    with mock.patch("dav.ai.mcp_tools.McpClient", return_value=fake_client):
        p = load_profile(mcp_url="http://fake:8080", fall_back_to_dcm=True)
    assert_eq(p.consumer_id, "dcm", "fallback to DCM on MCP failure")

def test_load_profile_mcp_failure_no_fallback_raises():
    fake_client = mock.MagicMock()
    fake_client.call.side_effect = RuntimeError("MCP down")
    with mock.patch("dav.ai.mcp_tools.McpClient", return_value=fake_client):
        assert_raises(
            lambda: load_profile(mcp_url="http://fake:8080", fall_back_to_dcm=False),
            ConsumerProfileError, "MCP failure raises with fallback disabled"
        )

def test_load_profile_no_args_returns_dcm():
    p = load_profile()
    assert_eq(p.consumer_id, "dcm", "default returns DCM reference")

def test_load_profile_no_args_no_fallback_raises():
    assert_raises(
        lambda: load_profile(fall_back_to_dcm=False),
        ConsumerProfileError, "no args + no fallback raises"
    )

# --- Default profile mechanism tests ---

def test_default_profile_starts_as_dcm():
    reset_default_profile()
    p = get_default_profile()
    assert_eq(p.consumer_id, "dcm", "default starts as DCM")

def test_set_and_get_default_profile():
    reset_default_profile()
    custom = ConsumerProfile(**_minimal_valid_dict())
    set_default_profile(custom)
    p = get_default_profile()
    assert_eq(p.consumer_id, "test", "custom default returned")

def test_reset_default_profile():
    custom = ConsumerProfile(**_minimal_valid_dict())
    set_default_profile(custom)
    reset_default_profile()
    p = get_default_profile()
    assert_eq(p.consumer_id, "dcm", "reset returns to DCM")

def test_replacing_default_profile_logs_but_works():
    reset_default_profile()
    p1 = ConsumerProfile(**_minimal_valid_dict())
    p2_dict = _minimal_valid_dict()
    p2_dict["consumer_id"] = "second"
    p2 = ConsumerProfile(**p2_dict)
    set_default_profile(p1)
    set_default_profile(p2)   # replace; should not raise
    p = get_default_profile()
    assert_eq(p.consumer_id, "second", "replacement honored")

# --- Integration with use_case_schema validators ---

def test_validation_uses_explicit_profile():
    """UseCase.validate(profile) uses the supplied profile, not the default."""
    from dav.core.use_case_schema import (
        Actor, Dimensions, Scenario, UseCase, GeneratedBy, UseCaseMetadata,
    )
    reset_default_profile()  # default = DCM

    # Build a UC with TinyURL-style vocab
    custom = ConsumerProfile(**{**_minimal_valid_dict(),
                                "lifecycle_phases": ["create_link", "resolve_link"],
                                "profiles": ["dev", "prod"]})
    uc = UseCase(
        uuid="uc-tinyurl-001",
        handle="link/create",
        scenario=Scenario(
            description="d", intent="i", success_criteria=["c"],
            actor=Actor(persona="p", profile="dev"),
            dimensions=Dimensions(
                lifecycle_phase="create_link",
                resource_complexity="x",
                policy_complexity="x",
                provider_landscape="x",
                governance_context="x",
                failure_mode="x",
            ),
            profile="dev",
        ),
        generated_by=GeneratedBy(mode="regression", source="human-authored"),
        metadata=UseCaseMetadata(),
    )
    # Validates against custom (TinyURL) profile
    assert_eq(uc.validate(custom), [], "validates against custom profile")
    # Fails against default (DCM) profile because 'dev' lifecycle isn't in DCM
    errs = uc.validate()  # default = DCM
    assert_true(any("create_link" in e for e in errs),
                f"DCM default rejects custom-vocab UC; got errs={errs}")

def test_validation_uses_default_when_no_profile_passed():
    """When no profile is passed and a non-DCM default is set, validators use that default."""
    from dav.core.use_case_schema import (
        Actor, Dimensions, Scenario, UseCase, GeneratedBy, UseCaseMetadata,
    )
    custom = ConsumerProfile(**{**_minimal_valid_dict(),
                                "lifecycle_phases": ["create_link", "resolve_link"],
                                "profiles": ["dev", "prod"]})
    set_default_profile(custom)
    try:
        uc = UseCase(
            uuid="uc-tinyurl-002",
            handle="link/resolve",
            scenario=Scenario(
                description="d", intent="i", success_criteria=["c"],
                actor=Actor(persona="p", profile="dev"),
                dimensions=Dimensions(
                    lifecycle_phase="resolve_link",
                    resource_complexity="x",
                    policy_complexity="x",
                    provider_landscape="x",
                    governance_context="x",
                    failure_mode="x",
                ),
                profile="dev",
            ),
            generated_by=GeneratedBy(mode="regression", source="human-authored"),
            metadata=UseCaseMetadata(),
        )
        assert_eq(uc.validate(), [], "validates against module-level default")
    finally:
        reset_default_profile()

# --- Integration with build_analysis_json_schema ---

def test_json_schema_uses_profile_provider_types():
    from dav.core.use_case_schema import build_analysis_json_schema
    custom = ConsumerProfile(**{**_minimal_valid_dict(),
                                "provider_types": ["pulumi_provider", "tf_provider"],
                                "policy_modes": ["sync", "async"]})
    schema = build_analysis_json_schema(custom)
    pt_enum = schema["properties"]["provider_types_involved"]["items"]["properties"]["type"]["enum"]
    pm_enum = schema["properties"]["policy_modes_required"]["items"]["properties"]["mode"]["enum"]
    assert_eq(pt_enum, ["pulumi_provider", "tf_provider"], "provider_types from profile")
    assert_eq(pm_enum, ["sync", "async"], "policy_modes from profile")

def test_json_schema_default_uses_dcm():
    from dav.core.use_case_schema import build_analysis_json_schema
    reset_default_profile()
    schema = build_analysis_json_schema()
    pt_enum = schema["properties"]["provider_types_involved"]["items"]["properties"]["type"]["enum"]
    assert_true("peer_dcm" in pt_enum, "DCM default has peer_dcm")

def test_module_level_constant_via_getattr():
    """`from dav.core.use_case_schema import ANALYSIS_JSON_SCHEMA` works as a lazy module attribute."""
    reset_default_profile()
    from dav.core.use_case_schema import ANALYSIS_JSON_SCHEMA
    pt_enum = ANALYSIS_JSON_SCHEMA["properties"]["provider_types_involved"]["items"]["properties"]["type"]["enum"]
    assert_true("peer_dcm" in pt_enum, "module-level constant returns DCM-default schema")

# --- Run ---

def main():
    tests = [
        test_validate_accepts_valid_profile,
        test_validate_rejects_empty_framework_name,
        test_validate_rejects_empty_consumer_id,
        test_validate_rejects_empty_vocabulary,
        test_validate_rejects_non_string_in_vocabulary,
        test_from_dict_preserves_known_fields,
        test_from_dict_tolerates_extra_fields,
        test_to_dict_round_trip,
        test_dcm_reference_is_valid,
        test_dcm_reference_has_expected_provider_types,
        test_dcm_reference_has_expected_profiles,
        test_dcm_reference_returns_fresh_copies,
        test_load_from_file_happy_path,
        test_load_from_file_missing_file,
        test_load_from_file_non_mapping,
        test_load_from_file_invalid_profile,
        test_load_from_mcp_direct_dict,
        test_load_from_mcp_wrapped_response,
        test_load_from_mcp_call_failure,
        test_load_from_mcp_invalid_profile,
        test_load_profile_path_takes_precedence,
        test_load_profile_mcp_fallback_to_dcm_on_failure,
        test_load_profile_mcp_failure_no_fallback_raises,
        test_load_profile_no_args_returns_dcm,
        test_load_profile_no_args_no_fallback_raises,
        test_default_profile_starts_as_dcm,
        test_set_and_get_default_profile,
        test_reset_default_profile,
        test_replacing_default_profile_logs_but_works,
        test_validation_uses_explicit_profile,
        test_validation_uses_default_when_no_profile_passed,
        test_json_schema_uses_profile_provider_types,
        test_json_schema_default_uses_dcm,
        test_module_level_constant_via_getattr,
    ]
    for t in tests:
        try:
            t()
        except Exception as e:
            _failures.append(f"{t.__name__} threw: {type(e).__name__}: {e}")
    if _failures:
        print(f"FAIL: {len(_failures)} assertion(s)/error(s):")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"OK: {len(tests)} tests passed")

if __name__ == "__main__":
    main()
