"""
Consumer profile loading and access for DAV.

A ConsumerProfile is the runtime-loadable bundle of consumer-specific
vocabulary that DAV needs to validate use cases, render prompts, and
constrain LLM output. these were hardcoded in
`use_case_schema.py` as DCM-specific Python constants. After ε.1 they
live in a YAML file the consumer ships, and DAV loads them at startup.

The profile defines the set of allowed values for the nine controlled
vocabularies that drive UC validation:

    lifecycle_phases
    resource_complexities
    policy_complexities
    provider_landscapes
    governance_contexts
    failure_modes
    profiles
    provider_types
    policy_modes

Plus framework-level identification:

    framework_name   — e.g. "DCM" — substituted into prompts and logs
    framework_short  — e.g. "DCM" or "MyApp" — used in shorter contexts
    consumer_id      — short slug, e.g. "dcm" — used in metadata fields
    consumer_version — version string, populated from consumer_version.txt
                       in the consumer repo (read separately by version.py)

Loading precedence (per Q2 decision: file with MCP fallback):

  1. If `--consumer-profile PATH` is given on the CLI, load from that file.
  2. Else if `consumer_profile_url` is set in environment or supplied to
     `load_profile()`, fetch from the MCP server.
  3. Else fall back to a built-in DCM reference profile (preserves
     pre-ε.1 behavior — existing call sites that don't pass a profile
     keep working as if DCM is the consumer).

The "default profile" mechanism lets pre-ε.1 callers and tests run
unmodified. New code should prefer explicit profile passing.

See specs/05-use-case-schema.md (post-ε.1 update planned) for the
canonical profile YAML shape.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

@dataclass
class ConsumerProfile:
    """Runtime bundle of consumer-specific vocabulary and identification.

    Constructed by `load_profile()`. Validators, prompt renderers, and
    JSON schema builders accept a ConsumerProfile and parameterize their
    behavior on it.
    """

    # Identification
    framework_name: str                  # e.g. "DCM (Data Center Management)"
    framework_short: str                 # e.g. "DCM"
    consumer_id: str                     # short slug, e.g. "dcm"
    schema_version: str = "1.0"          # profile schema version (forward compat)

    # Vocabularies (drive UC validation; rendered into prompts; enum-constrain
    # LLM JSON output)
    lifecycle_phases: list[str] = field(default_factory=list)
    resource_complexities: list[str] = field(default_factory=list)
    policy_complexities: list[str] = field(default_factory=list)
    provider_landscapes: list[str] = field(default_factory=list)
    governance_contexts: list[str] = field(default_factory=list)
    failure_modes: list[str] = field(default_factory=list)
    profiles: list[str] = field(default_factory=list)
    provider_types: list[str] = field(default_factory=list)
    policy_modes: list[str] = field(default_factory=list)

    # Optional architectural-context strings substituted into the system prompt.
    # Example for DCM:
    #   abstractions_summary: "Data, Provider, Policy"
    #   provider_summary: "capabilities that realize intent"
    #   policy_summary: "evaluation engine with two modes (Internal/External), Evaluation Context, multi-pass convergence"
    # These let the consumer shape the prompt without forking it.
    abstractions_summary: str = ""
    provider_summary: str = ""
    policy_summary: str = ""

    def validate(self) -> list[str]:
        """Sanity-check the profile shape. Returns errors as strings."""
        errors = []
        if not self.framework_name.strip():
            errors.append("framework_name must not be empty")
        if not self.framework_short.strip():
            errors.append("framework_short must not be empty")
        if not self.consumer_id.strip():
            errors.append("consumer_id must not be empty")
        # Vocabularies that drive validation must be non-empty
        for name in ("lifecycle_phases", "resource_complexities",
                     "policy_complexities", "provider_landscapes",
                     "governance_contexts", "failure_modes",
                     "profiles", "provider_types", "policy_modes"):
            v = getattr(self, name)
            if not isinstance(v, list) or not v:
                errors.append(f"{name} must be a non-empty list")
                continue
            for item in v:
                if not isinstance(item, str) or not item.strip():
                    errors.append(f"{name} contains invalid (non-string or empty) item: {item!r}")
                    break
        return errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConsumerProfile":
        """Construct from a parsed YAML dict. Tolerates extra fields."""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

# --- DCM reference profile (built-in default for backward compat) ---
# This mirrors what was hardcoded in use_case_schema.py before ε.1.
# When no profile is explicitly loaded, this becomes the default so
# pre-ε.1 callers and tests keep working unmodified.

_DCM_REFERENCE_PROFILE = ConsumerProfile(
    framework_name="DCM (Data Center Management)",
    framework_short="DCM",
    consumer_id="dcm",
    schema_version="1.0",
    lifecycle_phases=[
        "new_request", "modification", "decommission",
        "drift_detection", "brownfield_ingestion",
        "rehydration_faithful", "rehydration_provider_portable",
        "rehydration_historical_exact", "rehydration_historical_portable",
        "expiry_enforcement",
    ],
    resource_complexities=[
        "single_no_deps", "hard_dependencies", "composite_service",
        "conditional_soft_deps", "process_resource", "cross_dependency_payload",
    ],
    policy_complexities=[
        "system_defaults_only", "single_gatekeeper", "multi_policy_chain",
        "conflicting_policies", "orchestration_flow_static",
        "dynamic_conditional_flow", "cross_domain_constraint",
        "human_escalation_required", "governance_matrix_enforcement",
        "recovery_policy",
    ],
    provider_landscapes=[
        "single_eligible", "multiple_eligible", "none_eligible",
        "peer_dcm_required", "process_provider",
        "mixed",
    ],
    governance_contexts=[
        "no_governance", "standard_governance", "audit_heavy",
        "compliance_gated", "sovereignty_enforced",
    ],
    failure_modes=[
        "happy_path", "provider_failure", "policy_violation",
        "peer_dcm_disconnect", "data_inconsistency", "rollback_required",
        "partial_fulfillment", "timeout", "resource_exhaustion",
    ],
    profiles=["minimal", "dev", "standard", "prod", "fsi", "sovereign"],
    provider_types=["service", "information", "auth", "peer_dcm", "process"],
    policy_modes=["Internal", "External"],
    abstractions_summary="Data, Provider, Policy",
    provider_summary="capabilities that fulfill intent (five types: service, information, auth, peer_dcm, process; compound-service composition is a Data concept orchestrated by the Control Plane, not a provider type)",
    policy_summary="evaluation engine with two modes (Internal/External), Evaluation Context, multi-pass convergence",
)

def get_dcm_reference_profile() -> ConsumerProfile:
    """Return a copy of the built-in DCM reference profile.

    Used as the default when no profile is loaded. Returns a fresh copy
    so callers can mutate it (rare but possible) without affecting the
    canonical baseline.
    """
    # Deep-ish copy via to_dict round-trip (lists are recreated)
    return ConsumerProfile.from_dict(_DCM_REFERENCE_PROFILE.to_dict())

# --- Loaders ---

def load_profile_from_file(path: Path | str) -> ConsumerProfile:
    """Load a ConsumerProfile from a YAML file.

    Raises FileNotFoundError if the file is absent, ValueError if the
    YAML doesn't parse to a mapping or doesn't validate.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"consumer profile not found: {p}")
    with p.open("r") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"consumer profile {p}: top-level YAML must be a mapping")
    profile = ConsumerProfile.from_dict(data)
    errors = profile.validate()
    if errors:
        msg = f"consumer profile {p} failed validation:\n  - " + "\n  - ".join(errors)
        raise ValueError(msg)
    log.info("loaded consumer profile from %s (consumer_id=%s)", p, profile.consumer_id)
    return profile

def load_profile_from_mcp(mcp_url: str, *, timeout_seconds: int = 30) -> ConsumerProfile:
    """Fetch a ConsumerProfile from an MCP server's `get_consumer_profile` tool.

    The MCP server is expected to expose a tool that returns the consumer
    profile as a dict. If the tool is missing or fails, this raises
    `ConsumerProfileError` so callers can fall back to file or built-in.

    does NOT update the MCP server itself to expose this tool
    (that's a separate workstream for the MCP server's repo). This function
    is the engine-side loading code that will work once the MCP serves it.
    Until then, the file loader is the operational path.
    """
    # Lazy import: McpClient pulls in fastmcp which is heavy; only required
    # if the user actually uses MCP fallback.
    try:
        from dav.ai.mcp_tools import McpClient
    except Exception as e:
        raise ConsumerProfileError(f"MCP fallback unavailable: {e}") from e

    client = McpClient(server_url=mcp_url)
    try:
        result = client.call("get_consumer_profile", {})
    except Exception as e:
        raise ConsumerProfileError(
            f"failed to fetch consumer profile from MCP at {mcp_url}: {e}"
        ) from e

    # Normalize: MCP may return a dict directly, or wrap in {"content": [...]}, etc.
    profile_data = _extract_profile_payload(result)
    if not isinstance(profile_data, dict):
        raise ConsumerProfileError(
            f"MCP returned non-mapping consumer profile: {type(profile_data).__name__}"
        )
    profile = ConsumerProfile.from_dict(profile_data)
    errors = profile.validate()
    if errors:
        raise ConsumerProfileError(
            "consumer profile from MCP failed validation:\n  - " + "\n  - ".join(errors)
        )
    log.info("loaded consumer profile from MCP %s (consumer_id=%s)",
             mcp_url, profile.consumer_id)
    return profile

def _extract_profile_payload(mcp_result: Any) -> Any:
    """Pull the profile dict out of various MCP response shapes."""
    if isinstance(mcp_result, dict) and "framework_name" in mcp_result:
        # Already the profile
        return mcp_result
    if isinstance(mcp_result, dict) and "content" in mcp_result:
        # MCP-style {"content": [{"type": "text", "text": "<json>"}]}
        content = mcp_result["content"]
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and "text" in first:
                import json
                try:
                    return json.loads(first["text"])
                except (ValueError, TypeError):
                    return first["text"]
    return mcp_result

class ConsumerProfileError(Exception):
    """Raised when a ConsumerProfile cannot be loaded."""

def load_profile(
    *,
    path: Path | str | None = None,
    mcp_url: str | None = None,
    fall_back_to_dcm: bool = True,
) -> ConsumerProfile:
    """Load a ConsumerProfile with file → MCP → built-in fallback.

    Precedence:
      1. If `path` is given, load from file. No fallback if it fails.
      2. Else if `mcp_url` is given, try MCP. If MCP fails and
         `fall_back_to_dcm=True`, fall back to the DCM reference profile.
      3. Else if `fall_back_to_dcm=True`, return the DCM reference profile.
      4. Else raise ConsumerProfileError.

    Pre-ε.1 callers that don't pass anything get the DCM reference profile,
    which preserves backward compatibility.
    """
    if path is not None:
        return load_profile_from_file(path)

    if mcp_url is not None:
        try:
            return load_profile_from_mcp(mcp_url)
        except ConsumerProfileError as e:
            if fall_back_to_dcm:
                log.warning(
                    "MCP profile load failed (%s); falling back to DCM reference profile",
                    e,
                )
                return get_dcm_reference_profile()
            raise

    if fall_back_to_dcm:
        log.debug("no profile path or MCP URL given; using DCM reference profile")
        return get_dcm_reference_profile()

    raise ConsumerProfileError(
        "no profile path or MCP URL given and fall_back_to_dcm=False"
    )

# --- Default profile mechanism ---
# Module-level default profile. Set once at process startup (typically by
# stage2_analyze.py after parsing CLI args) and read by validators and
# prompt renderers that don't have an explicit profile passed to them.

_DEFAULT_PROFILE: ConsumerProfile | None = None

def set_default_profile(profile: ConsumerProfile) -> None:
    """Set the module-level default profile.

    Called once at process startup. Subsequent calls overwrite (with a
    debug log; no error). Validators and prompt renderers fall back to
    this when no explicit profile is passed.
    """
    global _DEFAULT_PROFILE
    if _DEFAULT_PROFILE is not None:
        log.debug(
            "default profile being replaced (was %s, now %s)",
            _DEFAULT_PROFILE.consumer_id, profile.consumer_id,
        )
    _DEFAULT_PROFILE = profile

def get_default_profile() -> ConsumerProfile:
    """Return the module-level default profile.

    If none has been set, returns the DCM reference profile (preserving
    pre-ε.1 behavior for callers that haven't been updated).
    """
    if _DEFAULT_PROFILE is not None:
        return _DEFAULT_PROFILE
    return get_dcm_reference_profile()

def reset_default_profile() -> None:
    """Clear the module-level default profile (test-only utility)."""
    global _DEFAULT_PROFILE
    _DEFAULT_PROFILE = None
