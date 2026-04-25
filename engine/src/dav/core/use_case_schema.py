"""
Canonical schemas for DAV — UseCase (stage 2 input) and Analysis (stage 2 output).

These schemas align to DAV v1.0 specifications:
  - Severity: 5 labels (adds MODERATE)
  - Severity and Confidence use descriptor-primary form (label + score + band + factors)
    aligned with DCM's Doc 21 scoring conventions (see spec 05 §6 and §1.1)
  - Input accepts shorthand ("major") or nested dict; output is always nested
  - New AssertionResult dataclass for assertion-type UCs
  - New SampleAnnotations for verification-mode ensemble provenance

Design: LLMs emit shorthand strings (constrained by ANALYSIS_JSON_SCHEMA); DAV
normalizes to descriptor form at parse time via each dataclass's from_dict.
By the time an object exists in memory, its severity/confidence fields are
always SeverityDescriptor / ConfidenceDescriptor instances.

See specs/05-use-case-schema.md and specs/07-analysis-output-schema.md for the
authoritative prose. Validation rules here mirror §9.4 (scoring) of spec 05.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict, fields as dc_fields
from datetime import datetime, timezone
from typing import Any
from enum import Enum

# --- Controlled vocabularies ---
# Pre-ε.1 these were hardcoded Python lists (LIFECYCLE_PHASES, PROVIDER_TYPES,
# etc.) baked into this module. moved them to ConsumerProfile —
# loaded at runtime from a YAML file the consumer ships, with MCP fallback
# and a built-in DCM reference profile for backward compatibility.
# Validators on Actor / Dimensions / Scenario accept a ConsumerProfile
# argument (defaulting to the module-level default profile when not passed)
# and read allowed values from it. The build_analysis_json_schema(profile)
# function constructs the LLM-facing JSON schema with the consumer's
# provider_types and policy_modes filled in.
# See engine/src/dav/core/consumer_profile.py for the profile shape and
# loader; specs/05-use-case-schema.md (post-ε.1) for the user-facing model.

class GenerationMode(str, Enum):
    """Pre-existing A/B/C modes.

    Distinct from the verification/reproduce/explore runtime modes added — those belong on AnalysisMetadata.mode.
    """
    REGRESSION = "regression"
    PR_TARGETED = "pr-targeted"
    AUTHORING = "authoring"

class GenerationSource(str, Enum):
    CORPUS = "corpus"
    LLM_UNGUIDED = "llm-unguided"
    LLM_GUIDED = "llm-guided"
    HUMAN_AUTHORED = "human-authored"

class Severity(str, Enum):
    """Five severity labels per spec 05 §6.2. MODERATE added."""
    CRITICAL = "critical"
    MAJOR = "major"
    MODERATE = "moderate"
    MINOR = "minor"
    ADVISORY = "advisory"

class Confidence(str, Enum):
    """Three confidence labels per spec 05 §6.3."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class Band(str, Enum):
    """Five-band DCM taxonomy (spec 05 §1.1).

    Bands are score-derived; authors never set them directly.
    """
    VERY_LOW = "very_low"    # 0-20
    LOW = "low"              # 21-40
    MEDIUM = "medium"        # 41-60
    HIGH = "high"            # 61-80
    VERY_HIGH = "very_high"  # 81-100

class Verdict(str, Enum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    NOT_SUPPORTED = "not_supported"

# --- Scoring tables (spec 05 §6.2, §6.3) ---
# Default scores are at band midpoints per the decision.

_SEVERITY_DEFAULTS: dict[str, int] = {
    "advisory": 10,
    "minor": 30,
    "moderate": 50,
    "major": 70,
    "critical": 90,
}

_CONFIDENCE_DEFAULTS: dict[str, int] = {
    "low": 30,
    "medium": 50,
    "high": 85,
}

# Valid score range per label. Score must fall within these bounds (spec 05 §9.4).
_SEVERITY_BAND_RANGES: dict[str, tuple[int, int]] = {
    "advisory": (0, 20),
    "minor": (21, 40),
    "moderate": (41, 60),
    "major": (61, 80),
    "critical": (81, 100),
}

_CONFIDENCE_BAND_RANGES: dict[str, tuple[int, int]] = {
    "low": (21, 40),
    "medium": (41, 60),
    "high": (81, 100),
}

def score_to_band(score: int) -> str:
    """Return the band name for a 0-100 score.

    Used to derive the `band` field of descriptors. Authors never call this
    directly; it's populated automatically by normalize_severity / normalize_confidence.
    """
    if not isinstance(score, int):
        raise ValueError(f"score must be int, got {type(score).__name__}: {score!r}")
    if 0 <= score <= 20:
        return Band.VERY_LOW.value
    if 21 <= score <= 40:
        return Band.LOW.value
    if 41 <= score <= 60:
        return Band.MEDIUM.value
    if 61 <= score <= 80:
        return Band.HIGH.value
    if 81 <= score <= 100:
        return Band.VERY_HIGH.value
    raise ValueError(f"score {score} out of range 0-100")

@dataclass
class SeverityDescriptor:
    """Descriptor form for severity per spec 05 §6.1.

    Always carries label + score + band. `factors` records provenance
    (base score from label, override rationale if the score was changed).
    """
    label: str
    score: int
    band: str
    factors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "score": self.score,
            "band": self.band,
            "factors": dict(self.factors),
        }

@dataclass
class ConfidenceDescriptor:
    """Descriptor form for confidence per spec 05 §6.1."""
    label: str
    score: int
    band: str
    factors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "score": self.score,
            "band": self.band,
            "factors": dict(self.factors),
        }

def normalize_severity(value: Any) -> SeverityDescriptor:
    """Coerce a severity value into SeverityDescriptor.

    Accepts:
      - str: shorthand form ("major"). Score set to label default (band midpoint).
      - dict: nested form {"label": "major", "score": 75, ...}.
      - SeverityDescriptor: passed through unchanged.

    Raises ValueError on invalid label or out-of-band score.
    Per spec 05 §9.4: score must fall within the label's band range.
    """
    if isinstance(value, SeverityDescriptor):
        return value

    if isinstance(value, str):
        label = value.strip().lower()
        if label not in _SEVERITY_DEFAULTS:
            raise ValueError(
                f"invalid severity label '{value}'; expected one of {sorted(_SEVERITY_DEFAULTS)}"
            )
        score = _SEVERITY_DEFAULTS[label]
        return SeverityDescriptor(
            label=label,
            score=score,
            band=score_to_band(score),
            factors={"base_from_label": score, "override_rationale": None},
        )

    if isinstance(value, dict):
        label_raw = value.get("label")
        if not isinstance(label_raw, str):
            raise ValueError(f"severity dict missing 'label' or not a string: {value!r}")
        label = label_raw.strip().lower()
        if label not in _SEVERITY_DEFAULTS:
            raise ValueError(
                f"invalid severity label '{label_raw}'; expected one of {sorted(_SEVERITY_DEFAULTS)}"
            )
        score = value.get("score", _SEVERITY_DEFAULTS[label])
        if not isinstance(score, int):
            raise ValueError(f"severity score must be int, got {type(score).__name__}: {score!r}")
        lo, hi = _SEVERITY_BAND_RANGES[label]
        if not (lo <= score <= hi):
            raise ValueError(
                f"severity score {score} outside band for label '{label}' (expected {lo}-{hi})"
            )
        factors = dict(value.get("factors") or {})
        factors.setdefault("base_from_label", _SEVERITY_DEFAULTS[label])
        factors.setdefault("override_rationale", None)
        return SeverityDescriptor(
            label=label,
            score=score,
            band=score_to_band(score),
            factors=factors,
        )

    raise ValueError(
        f"cannot normalize severity from {type(value).__name__}: {value!r}"
    )

def normalize_confidence(value: Any) -> ConfidenceDescriptor:
    """Coerce a confidence value into ConfidenceDescriptor.

    Accepts the same forms as normalize_severity (string, dict, or descriptor).
    Default scores per spec 05 §6.3: low=30, medium=50, high=85.
    """
    if isinstance(value, ConfidenceDescriptor):
        return value

    if isinstance(value, str):
        label = value.strip().lower()
        if label not in _CONFIDENCE_DEFAULTS:
            raise ValueError(
                f"invalid confidence label '{value}'; expected one of {sorted(_CONFIDENCE_DEFAULTS)}"
            )
        score = _CONFIDENCE_DEFAULTS[label]
        return ConfidenceDescriptor(
            label=label,
            score=score,
            band=score_to_band(score),
            factors={"base_from_label": score, "override_rationale": None},
        )

    if isinstance(value, dict):
        label_raw = value.get("label")
        if not isinstance(label_raw, str):
            raise ValueError(f"confidence dict missing 'label' or not a string: {value!r}")
        label = label_raw.strip().lower()
        if label not in _CONFIDENCE_DEFAULTS:
            raise ValueError(
                f"invalid confidence label '{label_raw}'; expected one of {sorted(_CONFIDENCE_DEFAULTS)}"
            )
        score = value.get("score", _CONFIDENCE_DEFAULTS[label])
        if not isinstance(score, int):
            raise ValueError(f"confidence score must be int, got {type(score).__name__}: {score!r}")
        lo, hi = _CONFIDENCE_BAND_RANGES[label]
        if not (lo <= score <= hi):
            raise ValueError(
                f"confidence score {score} outside band for label '{label}' (expected {lo}-{hi})"
            )
        factors = dict(value.get("factors") or {})
        factors.setdefault("base_from_label", _CONFIDENCE_DEFAULTS[label])
        factors.setdefault("override_rationale", None)
        return ConfidenceDescriptor(
            label=label,
            score=score,
            band=score_to_band(score),
            factors=factors,
        )

    raise ValueError(
        f"cannot normalize confidence from {type(value).__name__}: {value!r}"
    )

def _descriptor_to_dict(d: Any) -> Any:
    """Serialize a descriptor (or passthrough non-descriptors).

    Used by to_dict methods that need to emit nested-form severity/confidence.
    """
    if isinstance(d, (SeverityDescriptor, ConfidenceDescriptor)):
        return d.to_dict()
    return d

# --- Use Case schema ---

@dataclass
class Actor:
    persona: str
    profile: str

    def validate(self, consumer_profile=None) -> list[str]:
        # consumer_profile is optional; falls back to the
        # module-level default (which is the DCM reference profile if
        # nothing else was set).
        if consumer_profile is None:
            from dav.core.consumer_profile import get_default_profile
            consumer_profile = get_default_profile()
        errors = []
        if self.profile not in consumer_profile.profiles:
            errors.append(
                f"actor.profile '{self.profile}' not in {consumer_profile.profiles}"
            )
        if not self.persona.strip():
            errors.append("actor.persona must not be empty")
        return errors

@dataclass
class Dimensions:
    lifecycle_phase: str
    resource_complexity: str
    policy_complexity: str
    provider_landscape: str
    governance_context: str
    failure_mode: str

    def validate(self, consumer_profile=None) -> list[str]:
        if consumer_profile is None:
            from dav.core.consumer_profile import get_default_profile
            consumer_profile = get_default_profile()
        errors = []
        checks = [
            ("lifecycle_phase", self.lifecycle_phase, consumer_profile.lifecycle_phases),
            ("resource_complexity", self.resource_complexity, consumer_profile.resource_complexities),
            ("policy_complexity", self.policy_complexity, consumer_profile.policy_complexities),
            ("provider_landscape", self.provider_landscape, consumer_profile.provider_landscapes),
            ("governance_context", self.governance_context, consumer_profile.governance_contexts),
            ("failure_mode", self.failure_mode, consumer_profile.failure_modes),
        ]
        for name, value, allowed in checks:
            if value not in allowed:
                errors.append(f"dimensions.{name} '{value}' not in {allowed}")
        return errors

@dataclass
class DomainInteraction:
    domain: str
    interaction: str

@dataclass
class Scenario:
    description: str
    actor: Actor
    intent: str
    success_criteria: list[str]
    dimensions: Dimensions
    profile: str
    expected_domain_interactions: list[DomainInteraction] = field(default_factory=list)

    def validate(self, consumer_profile=None) -> list[str]:
        if consumer_profile is None:
            from dav.core.consumer_profile import get_default_profile
            consumer_profile = get_default_profile()
        errors = []
        if not self.description.strip():
            errors.append("scenario.description must not be empty")
        if not self.intent.strip():
            errors.append("scenario.intent must not be empty")
        if not self.success_criteria:
            errors.append("scenario.success_criteria must have at least one item")
        if self.profile not in consumer_profile.profiles:
            errors.append(
                f"scenario.profile '{self.profile}' not in {consumer_profile.profiles}"
            )
        errors.extend(self.actor.validate(consumer_profile))
        errors.extend(self.dimensions.validate(consumer_profile))
        return errors

@dataclass
class GeneratedBy:
    mode: str
    source: str
    model: str | None = None
    prompt_version: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

@dataclass
class UseCaseMetadata:
    admitted_at: str | None = None
    admitted_dcm_version: str | None = None
    promoted_from_run: str | None = None
    initial_baseline_path: str | None = None
    author: str | None = None

@dataclass
class UseCase:
    uuid: str
    handle: str
    scenario: Scenario
    generated_by: GeneratedBy
    tags: list[str] = field(default_factory=list)
    version: str = "1.0.0"
    metadata: UseCaseMetadata = field(default_factory=UseCaseMetadata)

    @classmethod
    def new(cls, handle: str, scenario: Scenario, generated_by: GeneratedBy,
            tags: list[str] | None = None) -> "UseCase":
        return cls(
            uuid=f"uc-{uuid.uuid4().hex[:12]}",
            handle=handle,
            scenario=scenario,
            generated_by=generated_by,
            tags=tags or [],
        )

    def validate(self, consumer_profile=None) -> list[str]:
        errors = []
        if not self.uuid.startswith("uc-"):
            errors.append(f"uuid '{self.uuid}' must start with 'uc-'")
        if "/" not in self.handle:
            errors.append(f"handle '{self.handle}' must be 'category/descriptor'")
        try:
            GenerationMode(self.generated_by.mode)
        except ValueError:
            errors.append(f"generated_by.mode '{self.generated_by.mode}' not valid")
        try:
            GenerationSource(self.generated_by.source)
        except ValueError:
            errors.append(f"generated_by.source '{self.generated_by.source}' not valid")
        errors.extend(self.scenario.validate(consumer_profile))
        return errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UseCase":
        scenario_data = data["scenario"]
        actor = Actor(**scenario_data["actor"])
        dimensions = Dimensions(**scenario_data["dimensions"])
        expected = [DomainInteraction(**x) for x in scenario_data.get("expected_domain_interactions", [])]
        scenario = Scenario(
            description=scenario_data["description"],
            actor=actor,
            intent=scenario_data["intent"],
            success_criteria=scenario_data["success_criteria"],
            dimensions=dimensions,
            profile=scenario_data["profile"],
            expected_domain_interactions=expected,
        )
        generated_by = GeneratedBy(**data["generated_by"])
        metadata_data = data.get("metadata") or {}
        metadata = UseCaseMetadata(**metadata_data)
        return cls(
            uuid=data["uuid"],
            handle=data["handle"],
            scenario=scenario,
            generated_by=generated_by,
            tags=data.get("tags", []),
            version=data.get("version", "1.0.0"),
            metadata=metadata,
        )

# --- Analysis schema (stage 2 output with descriptor-form severity/confidence) ---

@dataclass
class ComponentRequired:
    id: str
    role: str
    rationale: str
    spec_refs: list[str]
    confidence: ConfidenceDescriptor

    def validate(self) -> list[str]:
        errors = []
        if not self.id.strip():
            errors.append("component.id required")
        if not self.rationale.strip():
            errors.append(f"component '{self.id}' missing rationale")
        if not isinstance(self.confidence, ConfidenceDescriptor):
            errors.append(f"component '{self.id}' confidence is not a descriptor")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "rationale": self.rationale,
            "spec_refs": list(self.spec_refs),
            "confidence": self.confidence.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ComponentRequired":
        return cls(
            id=data["id"],
            role=data.get("role", ""),
            rationale=data.get("rationale", ""),
            spec_refs=list(data.get("spec_refs", [])),
            confidence=normalize_confidence(data.get("confidence", "medium")),
        )

@dataclass
class DataModelTouched:
    entity: str
    fields_accessed: list[str]
    operations: list[str]          # read, write, mutate
    rationale: str
    spec_refs: list[str]
    confidence: ConfidenceDescriptor

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "fields_accessed": list(self.fields_accessed),
            "operations": list(self.operations),
            "rationale": self.rationale,
            "spec_refs": list(self.spec_refs),
            "confidence": self.confidence.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DataModelTouched":
        return cls(
            entity=data["entity"],
            fields_accessed=list(data.get("fields_accessed", [])),
            operations=list(data.get("operations", [])),
            rationale=data.get("rationale", ""),
            spec_refs=list(data.get("spec_refs", [])),
            confidence=normalize_confidence(data.get("confidence", "medium")),
        )

@dataclass
class CapabilityInvoked:
    id: str
    usage: str
    rationale: str
    spec_refs: list[str]
    confidence: ConfidenceDescriptor

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "usage": self.usage,
            "rationale": self.rationale,
            "spec_refs": list(self.spec_refs),
            "confidence": self.confidence.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapabilityInvoked":
        return cls(
            id=data["id"],
            usage=data.get("usage", ""),
            rationale=data.get("rationale", ""),
            spec_refs=list(data.get("spec_refs", [])),
            confidence=normalize_confidence(data.get("confidence", "medium")),
        )

@dataclass
class ProviderTypeInvolved:
    type: str
    role: str
    confidence: ConfidenceDescriptor

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "role": self.role,
            "confidence": self.confidence.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderTypeInvolved":
        return cls(
            type=data["type"],
            role=data.get("role", ""),
            confidence=normalize_confidence(data.get("confidence", "medium")),
        )

@dataclass
class PolicyModeRequired:
    mode: str
    rationale: str
    spec_refs: list[str]
    confidence: ConfidenceDescriptor = field(
        default_factory=lambda: normalize_confidence("medium")
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "rationale": self.rationale,
            "spec_refs": list(self.spec_refs),
            "confidence": self.confidence.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyModeRequired":
        return cls(
            mode=data["mode"],
            rationale=data.get("rationale", ""),
            spec_refs=list(data.get("spec_refs", [])),
            confidence=normalize_confidence(data.get("confidence", "medium")),
        )

@dataclass
class GapIdentified:
    description: str
    severity: SeverityDescriptor
    confidence: ConfidenceDescriptor
    rationale: str
    recommendation: str
    spec_refs_consulted: list[str]
    spec_refs_missing: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "severity": self.severity.to_dict(),
            "confidence": self.confidence.to_dict(),
            "rationale": self.rationale,
            "recommendation": self.recommendation,
            "spec_refs_consulted": list(self.spec_refs_consulted),
            "spec_refs_missing": self.spec_refs_missing,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GapIdentified":
        return cls(
            description=data.get("description", ""),
            severity=normalize_severity(data.get("severity", "minor")),
            confidence=normalize_confidence(data.get("confidence", "medium")),
            rationale=data.get("rationale", ""),
            recommendation=data.get("recommendation", ""),
            spec_refs_consulted=list(data.get("spec_refs_consulted", [])),
            spec_refs_missing=data.get("spec_refs_missing"),
        )

@dataclass
class ToolCall:
    tool: str
    args: dict[str, Any]
    result_summary: str
    purpose: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCall":
        return cls(
            tool=data.get("tool", ""),
            args=dict(data.get("args") or {}),
            result_summary=data.get("result_summary", ""),
            purpose=data.get("purpose", ""),
        )

@dataclass
class AnalysisMetadata:
    """Run metadata per spec 07 §4.

    Most fields added have sensible defaults so existing call-sites
    that only populate (model, timestamp, tool_call_count, total_tokens,
    stage2_run_id) keep working. Fields will be populated more completely as
    wires in the 3-mode runtime.
    """
    model: str = ""
    endpoint_url: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    engine_version: str = ""
    engine_commit: str = ""
    consumer_version: str = ""
    mode: str = "reproduce"           # verification | reproduce | explore
    run_id: str = ""
    tool_call_count: int = 0
    total_tokens: int = 0
    wall_time_seconds: float = 0.0
    sample_count: int = 1
    sample_seeds: list[int] | None = None

    # Optional
    stage: str = ""
    parent_run_id: str = ""
    inference_topology: str = ""

    # Legacy field name preserved for back-compat with current agent output.
    # will deprecate this in favor of run_id.
    stage2_run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass
class AnalysisSummary:
    verdict: str
    overall_confidence: ConfidenceDescriptor
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "overall_confidence": self.overall_confidence.to_dict(),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnalysisSummary":
        return cls(
            verdict=data["verdict"],
            overall_confidence=normalize_confidence(data.get("overall_confidence", "medium")),
            notes=data.get("notes", ""),
        )

@dataclass
class SampleRecord:
    """Per-sample metadata for verification-mode ensemble runs (spec 07 §9)."""
    seed: int
    tool_call_count: int
    total_tokens: int
    wall_time_seconds: float
    verdict: str
    confidence: ConfidenceDescriptor

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "tool_call_count": self.tool_call_count,
            "total_tokens": self.total_tokens,
            "wall_time_seconds": self.wall_time_seconds,
            "verdict": self.verdict,
            "confidence": self.confidence.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SampleRecord":
        return cls(
            seed=int(data["seed"]),
            tool_call_count=int(data.get("tool_call_count", 0)),
            total_tokens=int(data.get("total_tokens", 0)),
            wall_time_seconds=float(data.get("wall_time_seconds", 0.0)),
            verdict=data.get("verdict", ""),
            confidence=normalize_confidence(data.get("confidence", "medium")),
        )

@dataclass
class SampleAnnotations:
    """Ensemble provenance for verification-mode Analyses (spec 07 §9).

    Null/None on reproduce and explore runs; populated on verification runs
    with sample_count >= 2.
    """
    sample_count: int
    sample_seeds: list[int]
    verdict_votes: dict[str, int]
    verdict_tied: bool = False
    per_sample: list[SampleRecord] = field(default_factory=list)
    component_consensus: dict[str, str] = field(default_factory=dict)
    capability_consensus: dict[str, str] = field(default_factory=dict)
    data_model_consensus: dict[str, str] = field(default_factory=dict)
    provider_type_consensus: dict[str, str] = field(default_factory=dict)
    policy_mode_consensus: dict[str, str] = field(default_factory=dict)
    gap_consensus: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "sample_seeds": list(self.sample_seeds),
            "verdict_votes": dict(self.verdict_votes),
            "verdict_tied": self.verdict_tied,
            "per_sample": [s.to_dict() for s in self.per_sample],
            "component_consensus": dict(self.component_consensus),
            "capability_consensus": dict(self.capability_consensus),
            "data_model_consensus": dict(self.data_model_consensus),
            "provider_type_consensus": dict(self.provider_type_consensus),
            "policy_mode_consensus": dict(self.policy_mode_consensus),
            "gap_consensus": dict(self.gap_consensus),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SampleAnnotations":
        return cls(
            sample_count=int(data["sample_count"]),
            sample_seeds=list(data.get("sample_seeds", [])),
            verdict_votes=dict(data.get("verdict_votes") or {}),
            verdict_tied=bool(data.get("verdict_tied", False)),
            per_sample=[SampleRecord.from_dict(s) for s in data.get("per_sample", [])],
            component_consensus=dict(data.get("component_consensus") or {}),
            capability_consensus=dict(data.get("capability_consensus") or {}),
            data_model_consensus=dict(data.get("data_model_consensus") or {}),
            provider_type_consensus=dict(data.get("provider_type_consensus") or {}),
            policy_mode_consensus=dict(data.get("policy_mode_consensus") or {}),
            gap_consensus=dict(data.get("gap_consensus") or {}),
        )

@dataclass
class AssertionResult:
    """Result of an assertion-type UC per spec 07 §10.

    Populated on Analyses from assertion UCs. For analytical UCs this is None.
    """
    passed: bool
    diagnostic: str
    assertion_module: str
    assertion_function: str
    wall_time_seconds: float
    confidence: ConfidenceDescriptor
    severity: SeverityDescriptor | None = None  # Set when passed=False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {
            "passed": self.passed,
            "diagnostic": self.diagnostic,
            "assertion_module": self.assertion_module,
            "assertion_function": self.assertion_function,
            "wall_time_seconds": self.wall_time_seconds,
            "confidence": self.confidence.to_dict(),
            "details": dict(self.details),
        }
        if self.severity is not None:
            out["severity"] = self.severity.to_dict()
        else:
            out["severity"] = None
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AssertionResult":
        sev_raw = data.get("severity")
        severity = normalize_severity(sev_raw) if sev_raw is not None else None
        return cls(
            passed=bool(data["passed"]),
            diagnostic=data.get("diagnostic", ""),
            assertion_module=data.get("assertion_module", ""),
            assertion_function=data.get("assertion_function", ""),
            wall_time_seconds=float(data.get("wall_time_seconds", 0.0)),
            confidence=normalize_confidence(data.get("confidence", "high")),
            severity=severity,
            details=dict(data.get("details") or {}),
        )

@dataclass
class Analysis:
    use_case_uuid: str
    analysis_metadata: AnalysisMetadata
    summary: AnalysisSummary
    components_required: list[ComponentRequired] = field(default_factory=list)
    data_model_touched: list[DataModelTouched] = field(default_factory=list)
    capabilities_invoked: list[CapabilityInvoked] = field(default_factory=list)
    provider_types_involved: list[ProviderTypeInvolved] = field(default_factory=list)
    policy_modes_required: list[PolicyModeRequired] = field(default_factory=list)
    gaps_identified: list[GapIdentified] = field(default_factory=list)
    tool_call_trace: list[ToolCall] = field(default_factory=list)
    sample_annotations: SampleAnnotations | None = None
    assertion_result: AssertionResult | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "use_case_uuid": self.use_case_uuid,
            "analysis_metadata": self.analysis_metadata.to_dict(),
            "components_required": [c.to_dict() for c in self.components_required],
            "data_model_touched": [d.to_dict() for d in self.data_model_touched],
            "capabilities_invoked": [c.to_dict() for c in self.capabilities_invoked],
            "provider_types_involved": [p.to_dict() for p in self.provider_types_involved],
            "policy_modes_required": [p.to_dict() for p in self.policy_modes_required],
            "gaps_identified": [g.to_dict() for g in self.gaps_identified],
            "summary": self.summary.to_dict(),
            "tool_call_trace": [t.to_dict() for t in self.tool_call_trace],
            "sample_annotations": (
                self.sample_annotations.to_dict() if self.sample_annotations else None
            ),
            "assertion_result": (
                self.assertion_result.to_dict() if self.assertion_result else None
            ),
        }
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Analysis":
        meta_raw = data.get("analysis_metadata") or {}
        # Filter to known AnalysisMetadata fields (tolerant of extra keys)
        known_meta = {f.name for f in dc_fields(AnalysisMetadata)}
        meta = AnalysisMetadata(**{k: v for k, v in meta_raw.items() if k in known_meta})

        sample_ann_raw = data.get("sample_annotations")
        sample_ann = (
            SampleAnnotations.from_dict(sample_ann_raw)
            if sample_ann_raw else None
        )

        assertion_raw = data.get("assertion_result")
        assertion = (
            AssertionResult.from_dict(assertion_raw)
            if assertion_raw else None
        )

        return cls(
            use_case_uuid=data["use_case_uuid"],
            analysis_metadata=meta,
            summary=AnalysisSummary.from_dict(data["summary"]),
            components_required=[ComponentRequired.from_dict(x) for x in data.get("components_required", [])],
            data_model_touched=[DataModelTouched.from_dict(x) for x in data.get("data_model_touched", [])],
            capabilities_invoked=[CapabilityInvoked.from_dict(x) for x in data.get("capabilities_invoked", [])],
            provider_types_involved=[ProviderTypeInvolved.from_dict(x) for x in data.get("provider_types_involved", [])],
            policy_modes_required=[PolicyModeRequired.from_dict(x) for x in data.get("policy_modes_required", [])],
            gaps_identified=[GapIdentified.from_dict(x) for x in data.get("gaps_identified", [])],
            tool_call_trace=[ToolCall.from_dict(x) for x in data.get("tool_call_trace", [])],
            sample_annotations=sample_ann,
            assertion_result=assertion,
        )

# --- JSON Schema for LLM guided decoding ---
# The LLM emits severity/confidence as shorthand strings. DAV normalizes to
# nested descriptor form at parse time via each class's from_dict. This keeps
# the LLM prompt simple and avoids the model having to reason about band math.
# See spec 07 §6.1.2 for the shorthand/nested duality.
# the schema is now built from a ConsumerProfile so the
# provider_types and policy_modes enums reflect the consumer's vocabulary
# rather than DCM's hardcoded values. Use build_analysis_json_schema(profile)
# for new code; the module-level ANALYSIS_JSON_SCHEMA constant remains as a
# backward-compat lazy property that uses the default profile.

def build_analysis_json_schema(consumer_profile=None) -> dict[str, Any]:
    """Build the JSON schema vLLM uses for guided_json output.

    The provider_types_involved.type and policy_modes_required.mode enums
    are populated from the supplied ConsumerProfile (or the default profile
    if none is given). All other enums (Confidence, Severity, Verdict) are
    framework-level and don't depend on the consumer.
    """
    if consumer_profile is None:
        from dav.core.consumer_profile import get_default_profile
        consumer_profile = get_default_profile()
    return {
        "type": "object",
        "required": [
            "components_required", "data_model_touched", "capabilities_invoked",
            "provider_types_involved", "policy_modes_required",
            "gaps_identified", "summary",
        ],
        "properties": {
            "components_required": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "role", "rationale", "spec_refs", "confidence"],
                    "properties": {
                        "id": {"type": "string"},
                        "role": {"type": "string"},
                        "rationale": {"type": "string"},
                        "spec_refs": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"enum": [c.value for c in Confidence]},
                    },
                },
            },
            "data_model_touched": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["entity", "fields_accessed", "operations",
                                 "rationale", "spec_refs", "confidence"],
                    "properties": {
                        "entity": {"type": "string"},
                        "fields_accessed": {"type": "array", "items": {"type": "string"}},
                        "operations": {"type": "array", "items": {"type": "string",
                                       "enum": ["read", "write", "mutate"]}},
                        "rationale": {"type": "string"},
                        "spec_refs": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"enum": [c.value for c in Confidence]},
                    },
                },
            },
            "capabilities_invoked": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "usage", "rationale", "spec_refs", "confidence"],
                    "properties": {
                        "id": {"type": "string"},
                        "usage": {"type": "string"},
                        "rationale": {"type": "string"},
                        "spec_refs": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"enum": [c.value for c in Confidence]},
                    },
                },
            },
            "provider_types_involved": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["type", "role", "confidence"],
                    "properties": {
                        # enum from consumer profile, not hardcoded.
                        "type": {"enum": list(consumer_profile.provider_types)},
                        "role": {"type": "string"},
                        "confidence": {"enum": [c.value for c in Confidence]},
                    },
                },
            },
            "policy_modes_required": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["mode", "rationale", "spec_refs", "confidence"],
                    "properties": {
                        # enum from consumer profile.
                        "mode": {"enum": list(consumer_profile.policy_modes)},
                        "rationale": {"type": "string"},
                        "spec_refs": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"enum": [c.value for c in Confidence]},
                    },
                },
            },
            "gaps_identified": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["severity", "description", "rationale",
                                 "spec_refs_consulted", "spec_refs_missing",
                                 "recommendation", "confidence"],
                    "properties": {
                        # Five-label severity enum — MODERATE added.
                        "severity": {"enum": [s.value for s in Severity]},
                        "description": {"type": "string"},
                        "rationale": {"type": "string"},
                        "spec_refs_consulted": {"type": "array", "items": {"type": "string"}},
                        "spec_refs_missing": {"type": "string"},
                        "recommendation": {"type": "string"},
                        "confidence": {"enum": [c.value for c in Confidence]},
                    },
                },
            },
            "summary": {
                "type": "object",
                "required": ["verdict", "overall_confidence", "notes"],
                "properties": {
                    "verdict": {"enum": [v.value for v in Verdict]},
                    "overall_confidence": {"enum": [c.value for c in Confidence]},
                    "notes": {"type": "string"},
                },
            },
        },
    }

# Lazy module attribute. The schema is built from the active default
# consumer profile, which may be set by callers via set_default_profile()
# after import. Reading ANALYSIS_JSON_SCHEMA at access time (rather than
# module import time) ensures the schema reflects the active profile.

def __getattr__(name: str):
    if name == "ANALYSIS_JSON_SCHEMA":
        return build_analysis_json_schema()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if __name__ == "__main__":
    # Smoke test
    print(json.dumps(build_analysis_json_schema(), indent=2))
