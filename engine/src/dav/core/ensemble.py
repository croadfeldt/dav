"""
Verification-mode ensemble merger for DAV.

Takes N Analysis objects (samples) produced from the same UseCase under
verification mode (low-temp sampling, distinct seeds) and returns a single
merged Analysis with `sample_annotations` populated to record per-sample
provenance and consensus.

Design (spec 07 §9):

  Verdict
    Majority vote across samples. Ties resolve conservatively toward
    not_supported. Tied verdicts cap overall_confidence at medium.

  Findings (components, capabilities, data_model, providers, policy modes)
    Union across samples. ID canonicalization (reusing the comparator's
    _canonicalize) collapses morphological variants. Per-finding confidence
    on the merged output is the LOWEST confidence across samples that
    contributed (: lowest confidence wins).

  Gaps
    Union by canonicalized description. Severity on merge is the HIGHEST
    severity any sample assigned (conservative — surface concern). Confidence
    is the lowest. Consensus annotation records the vote count.

  tool_call_trace
    Taken from the representative sample whose verdict matches consensus
    (first such sample if multiple). The trace is illustrative, not
    authoritative; the merged analysis's narrative-truth is the merger's
    output, not any single sample.

  AnalysisMetadata
    Aggregated. Fields like tool_call_count and total_tokens are summed.
    Fields like model and endpoint_url are taken from sample 0 (assumed
    consistent across samples in verification mode). wall_time_seconds is
    the maximum (we ran in parallel; we paid the slowest sample's cost).

  sample_annotations (spec 07 §9)
    Always populated when this merger runs. Records sample_count,
    sample_seeds, verdict_votes, verdict_tied, per_sample (SampleRecord per
    input), and consensus dicts per finding type ("entity_id" → "N/M").

The merger is a pure function. No I/O, no logging, no global state. Test
exhaustively in isolation; that's the whole point of pulling it out as
a separate module.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from typing import Any, Iterable

from dav.core.use_case_schema import (
    Analysis, AnalysisMetadata, AnalysisSummary,
    ComponentRequired, DataModelTouched, CapabilityInvoked,
    ProviderTypeInvolved, PolicyModeRequired, GapIdentified,
    SampleRecord, SampleAnnotations,
    SeverityDescriptor, ConfidenceDescriptor,
    Verdict, normalize_confidence, normalize_severity,
)

# --- Canonicalization (extracted for reuse; mirrors compare.py's logic) ---
# We keep this aligned with compare.py's _canonicalize. If compare.py's
# canonicalization rules change, mirror them here. The two implementations
# exist because we want compare.py to remain importable even if the engine
# schema isn't (per its module docstring), so we don't import from it.

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_PLURAL_PATTERNS = [
    (re.compile(r"\bpolicies\b"), "policy"),
    (re.compile(r"\bentities\b"), "entity"),
    (re.compile(r"\bcapabilities\b"), "capability"),
    (re.compile(r"\bvms\b"), "vm"),
]

def canonicalize(s: str) -> str:
    """Normalize a string for equality comparison.

    Lowercase, collapse known plurals to singular, replace non-alphanumeric
    runs with underscore. Mirrors compare.py's _canonicalize so that gap
    deduplication in the merger uses the same rules as gap fingerprinting
    in the comparator.

    If you change this, change it there too.
    """
    if not s:
        return ""
    lowered = s.lower().strip()
    for pattern, repl in _PLURAL_PATTERNS:
        lowered = pattern.sub(repl, lowered)
    return _NON_ALNUM_RE.sub("_", lowered).strip("_")

# --- Confidence / severity ordering ---
# Used to pick "lowest" or "highest" descriptor across samples.

_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
_SEVERITY_ORDER = {
    "advisory": 0, "minor": 1, "moderate": 2, "major": 3, "critical": 4,
}

def _lowest_confidence(descriptors: Iterable[ConfidenceDescriptor]) -> ConfidenceDescriptor:
    """Return the lowest confidence among descriptors. On ties, returns the
    one with the lowest score (most conservative)."""
    descriptors = list(descriptors)
    if not descriptors:
        return normalize_confidence("medium")
    return min(
        descriptors,
        key=lambda d: (_CONFIDENCE_ORDER.get(d.label, 1), d.score),
    )

def _highest_severity(descriptors: Iterable[SeverityDescriptor]) -> SeverityDescriptor:
    """Return the highest severity among descriptors. On ties, returns the
    one with the highest score."""
    descriptors = list(descriptors)
    if not descriptors:
        return normalize_severity("minor")
    return max(
        descriptors,
        key=lambda d: (_SEVERITY_ORDER.get(d.label, 1), d.score),
    )

# --- Verdict voting ---

_VERDICT_CONSERVATISM = {
    Verdict.SUPPORTED.value: 2,
    Verdict.PARTIALLY_SUPPORTED.value: 1,
    Verdict.NOT_SUPPORTED.value: 0,
}

def _resolve_verdict(verdicts: list[str]) -> tuple[str, dict[str, int], bool]:
    """Return (verdict, vote_distribution, tied).

    Voting rule:
      1. Count votes by verdict label.
      2. If a single verdict has strict majority, it wins.
      3. On a tie, pick the most conservative (lowest _VERDICT_CONSERVATISM
         rank). Tie example: 1×supported, 1×partially_supported, 1×not_supported
         → not_supported wins. Or 2×supported, 2×not_supported (4-sample run)
         → not_supported wins.
      4. `tied` is True iff no single verdict had strict majority.
    """
    if not verdicts:
        return Verdict.NOT_SUPPORTED.value, {}, True
    votes = Counter(verdicts)
    distribution = dict(votes)
    n = len(verdicts)
    most_common = votes.most_common()
    top_count = most_common[0][1]

    # Strict majority?
    if top_count > n / 2:
        return most_common[0][0], distribution, False

    # Tie among the top vote-getters; pick the most conservative
    leaders = [v for v, c in most_common if c == top_count]
    leaders.sort(key=lambda v: _VERDICT_CONSERVATISM.get(v, 999))
    return leaders[0], distribution, True

# --- Finding-list mergers ---
# Each list-of-X merger follows the same pattern:
#   1. Canonicalize each item's identity (id, entity, type, mode, or
#      canonicalized description for gaps).
#   2. Group items across samples by canonical key.
#   3. Build a representative item from the group (preserve first sample's
#      original wording for human readability; pick lowest/highest
#      confidence/severity per merge rule).
#   4. Track group_size for consensus annotation.
# Two-stage helper: _group_by_key collects, _consolidate_X builds the merged
# representative.

def _group_by_key(samples: list[Analysis], list_attr: str, key_fn) -> dict[str, list[Any]]:
    """Group items from samples[i].<list_attr> by key_fn(item).

    Returns a dict mapping canonical_key → list of items (ordered by sample index,
    then by within-sample order).
    """
    groups: dict[str, list[Any]] = {}
    for analysis in samples:
        items = getattr(analysis, list_attr) or []
        for item in items:
            key = key_fn(item)
            if not key:
                continue
            groups.setdefault(key, []).append(item)
    return groups

def _consolidate_components(
    groups: dict[str, list[ComponentRequired]],
) -> tuple[list[ComponentRequired], dict[str, str], int]:
    """Merge component groups into a single list. Returns (merged_components,
    consensus_dict, total_samples)."""
    n_samples = max(
        (max(_count_per_sample_appearances(group) for group in groups.values())),
        1,
    ) if groups else 1
    merged = []
    consensus: dict[str, str] = {}
    for key, items in sorted(groups.items()):
        # First occurrence wins for human-readable wording (id, role, rationale, spec_refs)
        first = items[0]
        # Lowest confidence across samples that mentioned it
        lowest_conf = _lowest_confidence([it.confidence for it in items])
        merged.append(ComponentRequired(
            id=first.id,
            role=first.role,
            rationale=first.rationale,
            spec_refs=list(first.spec_refs),
            confidence=lowest_conf,
        ))
        consensus[first.id] = f"{len(items)}/{n_samples}"
    return merged, consensus, n_samples

def _count_per_sample_appearances(group: list[Any]) -> int:
    """Group may contain duplicates if a sample lists the same key twice;
    return the count (used to compute n_samples robustly)."""
    return len(group)

def _consolidate_data_model(
    groups: dict[str, list[DataModelTouched]], n_samples: int,
) -> tuple[list[DataModelTouched], dict[str, str]]:
    merged = []
    consensus: dict[str, str] = {}
    for key, items in sorted(groups.items()):
        first = items[0]
        lowest_conf = _lowest_confidence([it.confidence for it in items])
        # Union of operations and fields_accessed across samples
        ops_set: set[str] = set()
        fields_set: set[str] = set()
        for it in items:
            ops_set.update(it.operations)
            fields_set.update(it.fields_accessed)
        merged.append(DataModelTouched(
            entity=first.entity,
            fields_accessed=sorted(fields_set),
            operations=sorted(ops_set),
            rationale=first.rationale,
            spec_refs=list(first.spec_refs),
            confidence=lowest_conf,
        ))
        consensus[first.entity] = f"{len(items)}/{n_samples}"
    return merged, consensus

def _consolidate_capabilities(
    groups: dict[str, list[CapabilityInvoked]], n_samples: int,
) -> tuple[list[CapabilityInvoked], dict[str, str]]:
    merged = []
    consensus: dict[str, str] = {}
    for key, items in sorted(groups.items()):
        first = items[0]
        lowest_conf = _lowest_confidence([it.confidence for it in items])
        merged.append(CapabilityInvoked(
            id=first.id,
            usage=first.usage,
            rationale=first.rationale,
            spec_refs=list(first.spec_refs),
            confidence=lowest_conf,
        ))
        consensus[first.id] = f"{len(items)}/{n_samples}"
    return merged, consensus

def _consolidate_provider_types(
    groups: dict[str, list[ProviderTypeInvolved]], n_samples: int,
) -> tuple[list[ProviderTypeInvolved], dict[str, str]]:
    merged = []
    consensus: dict[str, str] = {}
    for key, items in sorted(groups.items()):
        first = items[0]
        lowest_conf = _lowest_confidence([it.confidence for it in items])
        merged.append(ProviderTypeInvolved(
            type=first.type,
            role=first.role,
            confidence=lowest_conf,
        ))
        consensus[first.type] = f"{len(items)}/{n_samples}"
    return merged, consensus

def _consolidate_policy_modes(
    groups: dict[str, list[PolicyModeRequired]], n_samples: int,
) -> tuple[list[PolicyModeRequired], dict[str, str]]:
    merged = []
    consensus: dict[str, str] = {}
    for key, items in sorted(groups.items()):
        first = items[0]
        lowest_conf = _lowest_confidence([it.confidence for it in items])
        merged.append(PolicyModeRequired(
            mode=first.mode,
            rationale=first.rationale,
            spec_refs=list(first.spec_refs),
            confidence=lowest_conf,
        ))
        consensus[first.mode] = f"{len(items)}/{n_samples}"
    return merged, consensus

def _consolidate_gaps(
    samples: list[Analysis], n_samples: int,
) -> tuple[list[GapIdentified], dict[str, str]]:
    """Merge gaps across samples by canonicalized description.

    Gap identity is (severity_label, canonical_description) at compare time —
    but here we want to also collapse the same description with different
    severity (one sample says "major" another says "moderate"). So the merger
    uses canonical_description alone as the key, and the merged severity is
    the HIGHEST among contributing samples.
    """
    groups: dict[str, list[GapIdentified]] = {}
    for analysis in samples:
        for gap in analysis.gaps_identified or []:
            key = canonicalize(gap.description)
            if not key:
                continue
            groups.setdefault(key, []).append(gap)

    merged = []
    consensus: dict[str, str] = {}
    for key, items in sorted(groups.items()):
        first = items[0]
        # Severity: highest (most concerning) among samples
        highest_sev = _highest_severity([it.severity for it in items])
        # Confidence: lowest (most conservative) among samples
        lowest_conf = _lowest_confidence([it.confidence for it in items])
        merged.append(GapIdentified(
            description=first.description,
            severity=highest_sev,
            confidence=lowest_conf,
            rationale=first.rationale,
            recommendation=first.recommendation,
            spec_refs_consulted=list(first.spec_refs_consulted),
            spec_refs_missing=first.spec_refs_missing,
        ))
        consensus[key] = f"{len(items)}/{n_samples}"
    return merged, consensus

# --- Tool call trace selection ---

def _representative_trace(samples: list[Analysis], target_verdict: str) -> list:
    """Pick the tool_call_trace from the first sample whose verdict matches
    the target. Falls back to sample 0's trace if none match."""
    for s in samples:
        if s.summary.verdict == target_verdict:
            return list(s.tool_call_trace)
    return list(samples[0].tool_call_trace) if samples else []

# --- Sample record extraction ---

def _build_sample_record(sample: Analysis, seed: int | None) -> SampleRecord:
    meta = sample.analysis_metadata
    # The seed argument lets the caller pass the seed used for this sample;
    # if None, fall back to whatever the sample's metadata recorded.
    actual_seed = seed
    if actual_seed is None:
        if meta.sample_seeds:
            actual_seed = meta.sample_seeds[0] if len(meta.sample_seeds) == 1 else 0
        else:
            actual_seed = 0
    return SampleRecord(
        seed=actual_seed,
        tool_call_count=meta.tool_call_count,
        total_tokens=meta.total_tokens,
        wall_time_seconds=meta.wall_time_seconds,
        verdict=sample.summary.verdict,
        confidence=sample.summary.overall_confidence,
    )

# --- Metadata aggregation ---

def _aggregate_metadata(samples: list[Analysis], seeds: list[int]) -> AnalysisMetadata:
    """Aggregate per-sample metadata into a single merged AnalysisMetadata.

    Aggregation rules:
      tool_call_count, total_tokens — summed across samples
      wall_time_seconds            — maximum (parallel execution; we paid the
                                     slowest sample's cost). For serial
                                     execution callers should sum externally
                                     and overwrite if needed.
      sample_count, sample_seeds   — set from inputs
      mode                         — "verification"
      Other string fields          — taken from sample 0 (assumed identical)
    """
    if not samples:
        return AnalysisMetadata(mode="verification", sample_count=0, sample_seeds=[])
    base = samples[0].analysis_metadata
    merged = replace(base)
    merged.mode = "verification"
    merged.sample_count = len(samples)
    merged.sample_seeds = list(seeds) if seeds else None
    merged.tool_call_count = sum(s.analysis_metadata.tool_call_count for s in samples)
    merged.total_tokens = sum(s.analysis_metadata.total_tokens for s in samples)
    merged.wall_time_seconds = max(
        (s.analysis_metadata.wall_time_seconds for s in samples), default=0.0
    )
    return merged

# --- Public entry point ---

def merge_analyses(
    samples: list[Analysis],
    *,
    sample_seeds: list[int] | None = None,
) -> Analysis:
    """Merge N sample Analyses into a single verification-mode Analysis.

    Args:
        samples: Non-empty list of Analysis objects from the same UseCase.
            All samples must share use_case_uuid (validated).
        sample_seeds: Per-sample seeds, in same order as samples. If None,
            uses [0, 1, ..., N-1] as the seed labels (just for the
            sample_annotations record).

    Returns:
        A merged Analysis with sample_annotations populated.

    Raises:
        ValueError: if samples is empty or use_case_uuids disagree.
    """
    if not samples:
        raise ValueError("merge_analyses requires at least one sample")

    # All samples must share the same UC uuid
    uuids = {s.use_case_uuid for s in samples}
    if len(uuids) != 1:
        raise ValueError(
            f"all samples must share use_case_uuid; got {sorted(uuids)}"
        )
    uc_uuid = uuids.pop()

    n = len(samples)
    if sample_seeds is None:
        sample_seeds = list(range(n))
    if len(sample_seeds) != n:
        raise ValueError(
            f"sample_seeds length {len(sample_seeds)} != samples length {n}"
        )

    # --- Verdict voting ---
    verdicts = [s.summary.verdict for s in samples]
    merged_verdict, vote_distribution, tied = _resolve_verdict(verdicts)

    # --- Overall confidence: lowest among samples; tied verdict caps at medium ---
    overall_confidence = _lowest_confidence(
        [s.summary.overall_confidence for s in samples]
    )
    if tied:
        # Per spec 07 §9.2: tied verdict caps overall_confidence at medium
        if _CONFIDENCE_ORDER.get(overall_confidence.label, 1) > _CONFIDENCE_ORDER["medium"]:
            overall_confidence = normalize_confidence({
                "label": "medium",
                "score": 50,
                "factors": {
                    "base_from_label": 50,
                    "override_rationale": "capped to medium due to tied verdict",
                },
            })

    # --- Finding-list mergers ---
    comp_groups = _group_by_key(samples, "components_required", lambda c: canonicalize(c.id))
    components_merged, comp_consensus, _ = _consolidate_components(comp_groups)
    # Use n (sample count) for consensus, not the recomputed value from groups
    comp_consensus = {k: f"{v.split('/')[0]}/{n}" for k, v in comp_consensus.items()}

    dm_groups = _group_by_key(samples, "data_model_touched", lambda d: canonicalize(d.entity))
    data_model_merged, dm_consensus = _consolidate_data_model(dm_groups, n)

    cap_groups = _group_by_key(samples, "capabilities_invoked", lambda c: canonicalize(c.id))
    capabilities_merged, cap_consensus = _consolidate_capabilities(cap_groups, n)

    pt_groups = _group_by_key(samples, "provider_types_involved", lambda p: canonicalize(p.type))
    provider_types_merged, pt_consensus = _consolidate_provider_types(pt_groups, n)

    pm_groups = _group_by_key(samples, "policy_modes_required", lambda m: canonicalize(m.mode))
    policy_modes_merged, pm_consensus = _consolidate_policy_modes(pm_groups, n)

    gaps_merged, gap_consensus = _consolidate_gaps(samples, n)

    # --- Per-sample records ---
    per_sample = [_build_sample_record(s, sample_seeds[i]) for i, s in enumerate(samples)]

    # --- Metadata aggregation ---
    merged_metadata = _aggregate_metadata(samples, sample_seeds)

    # --- Tool call trace from representative sample ---
    representative_trace = _representative_trace(samples, merged_verdict)

    # --- Build sample_annotations ---
    sample_annotations = SampleAnnotations(
        sample_count=n,
        sample_seeds=list(sample_seeds),
        verdict_votes=vote_distribution,
        verdict_tied=tied,
        per_sample=per_sample,
        component_consensus=comp_consensus,
        capability_consensus=cap_consensus,
        data_model_consensus=dm_consensus,
        provider_type_consensus=pt_consensus,
        policy_mode_consensus=pm_consensus,
        gap_consensus=gap_consensus,
    )

    # --- Build summary ---
    # Notes: brief description of consensus state; reviewer-readable.
    if tied:
        notes = (
            f"Verification merge of {n} samples; tied verdict resolved "
            f"conservatively to {merged_verdict} (votes: {vote_distribution}); "
            f"overall_confidence capped at medium due to tie."
        )
    else:
        notes = (
            f"Verification merge of {n} samples; verdict {merged_verdict} "
            f"by majority (votes: {vote_distribution})."
        )

    summary = AnalysisSummary(
        verdict=merged_verdict,
        overall_confidence=overall_confidence,
        notes=notes,
    )

    return Analysis(
        use_case_uuid=uc_uuid,
        analysis_metadata=merged_metadata,
        summary=summary,
        components_required=components_merged,
        data_model_touched=data_model_merged,
        capabilities_invoked=capabilities_merged,
        provider_types_involved=provider_types_merged,
        policy_modes_required=policy_modes_merged,
        gaps_identified=gaps_merged,
        tool_call_trace=representative_trace,
        sample_annotations=sample_annotations,
        assertion_result=None,
    )
