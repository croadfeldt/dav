"""
Explore-mode variance reporting for DAV.

Explore mode runs N samples at a higher temperature without merging them.
Its purpose is adversarial: surface where the model's behavior on a UC is
unstable, so authors can identify ambiguous architectural questions or
weakly-supported claims.

This module produces a `VarianceReport` summarizing how samples diverged.
It does NOT merge samples into a unified Analysis (that's verification's
job). Instead it emits a digest the author can read alongside the per-sample
YAMLs.

Spec 04 §3 describes explore mode's authorial use case; this module
implements the variance signal that authors look at to decide whether a UC
needs sharpening or whether the architecture itself is genuinely under-
specified at that point.

Output shape (when serialized to YAML):

    use_case_uuid: uc-...
    sample_count: 10
    sample_seeds: [0, 1, ..., 9]
    verdict_distribution:
      supported: 6
      partially_supported: 4
    verdict_stability: 0.6        # = max_count / N
    confidence_distribution:
      high: 5
      medium: 5
    component_appearance:
      tenant_boundary: 10/10      # appears in all samples (stable)
      gatekeeper_policy: 7/10     # appears in 7 (somewhat stable)
      orphan_widget: 1/10         # appears in 1 (unstable, possibly noise)
    capability_appearance: ...
    data_model_appearance: ...
    provider_type_appearance: ...
    policy_mode_appearance: ...
    gap_appearance:
      atomic_onboarding_gap: 9/10
      lifecycle_phase_unclear: 3/10   # weakly supported
    gap_severity_distribution:
      atomic_onboarding_gap:
        major: 7
        moderate: 2
    unstable_findings:
      - Component 'orphan_widget' appears in only 1/10 samples
      - Gap 'lifecycle_phase_unclear' appears in 3/10 samples
    notes: |
      Variance summary across 10 samples at temperature 0.7.

The merger and the variance reporter share `canonicalize` from `ensemble.py`
to keep their dedup keys identical.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from dav.core.use_case_schema import Analysis
from dav.core.ensemble import canonicalize

# Threshold below which a finding is flagged as "unstable" in the report.
# A finding appearing in fewer than 50% of samples is unstable. This is
# a heuristic; tune via UNSTABLE_THRESHOLD if false positives are too noisy.
UNSTABLE_THRESHOLD = 0.5

@dataclass
class VarianceReport:
    """Summary of how N explore-mode samples diverged on the same UC."""
    use_case_uuid: str
    sample_count: int
    sample_seeds: list[int]
    verdict_distribution: dict[str, int]
    verdict_stability: float                     # max_count / N
    confidence_distribution: dict[str, int]
    component_appearance: dict[str, str]         # canonical_id → "N/M"
    capability_appearance: dict[str, str]
    data_model_appearance: dict[str, str]
    provider_type_appearance: dict[str, str]
    policy_mode_appearance: dict[str, str]
    gap_appearance: dict[str, str]
    gap_severity_distribution: dict[str, dict[str, int]]
    unstable_findings: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "use_case_uuid": self.use_case_uuid,
            "sample_count": self.sample_count,
            "sample_seeds": list(self.sample_seeds),
            "verdict_distribution": dict(self.verdict_distribution),
            "verdict_stability": round(self.verdict_stability, 3),
            "confidence_distribution": dict(self.confidence_distribution),
            "component_appearance": dict(self.component_appearance),
            "capability_appearance": dict(self.capability_appearance),
            "data_model_appearance": dict(self.data_model_appearance),
            "provider_type_appearance": dict(self.provider_type_appearance),
            "policy_mode_appearance": dict(self.policy_mode_appearance),
            "gap_appearance": dict(self.gap_appearance),
            "gap_severity_distribution": {
                k: dict(v) for k, v in self.gap_severity_distribution.items()
            },
            "unstable_findings": list(self.unstable_findings),
            "notes": self.notes,
        }

def _appearance_dict(samples: list[Analysis], list_attr: str, key_fn) -> dict[str, str]:
    """Count how many samples contain each finding (by canonical key).

    Returns a dict mapping canonical_key → "K/N" string where K is the count
    of samples containing at least one item with that key, N is total samples.
    """
    n = len(samples)
    counts: Counter[str] = Counter()
    seen_in_sample: dict[int, set[str]] = {}
    for i, analysis in enumerate(samples):
        items = getattr(analysis, list_attr) or []
        keys: set[str] = set()
        for item in items:
            k = key_fn(item)
            if k:
                keys.add(k)
        seen_in_sample[i] = keys
        for k in keys:
            counts[k] += 1
    return {k: f"{c}/{n}" for k, c in counts.most_common()}

def _gap_appearance(samples: list[Analysis]) -> dict[str, str]:
    """Same logic as _appearance_dict but specialized for gaps which use
    canonicalized description as the key."""
    n = len(samples)
    counts: Counter[str] = Counter()
    for analysis in samples:
        seen: set[str] = set()
        for gap in analysis.gaps_identified or []:
            k = canonicalize(gap.description)
            if k:
                seen.add(k)
        for k in seen:
            counts[k] += 1
    return {k: f"{c}/{n}" for k, c in counts.most_common()}

def _gap_severity_distribution(samples: list[Analysis]) -> dict[str, dict[str, int]]:
    """For each gap (by canonical description), distribution of severity labels
    across samples that mentioned it."""
    dist: dict[str, Counter[str]] = {}
    for analysis in samples:
        seen_in_sample: dict[str, str] = {}
        for gap in analysis.gaps_identified or []:
            k = canonicalize(gap.description)
            if not k:
                continue
            # If the same sample mentions the gap twice (rare), take the first
            if k in seen_in_sample:
                continue
            seen_in_sample[k] = gap.severity.label
        for k, label in seen_in_sample.items():
            dist.setdefault(k, Counter())[label] += 1
    return {k: dict(v) for k, v in dist.items()}

def _flag_unstable_findings(report: VarianceReport, n: int) -> list[str]:
    """Produce human-readable warnings for findings below UNSTABLE_THRESHOLD."""
    threshold_count = max(1, int(n * UNSTABLE_THRESHOLD))
    flags: list[str] = []

    def scan(label: str, mapping: dict[str, str]) -> None:
        for canon_key, ratio in mapping.items():
            count = int(ratio.split("/")[0])
            if count < threshold_count:
                flags.append(f"{label} '{canon_key}' appears in only {ratio} samples")

    scan("Component", report.component_appearance)
    scan("Capability", report.capability_appearance)
    scan("Data model entity", report.data_model_appearance)
    scan("Provider type", report.provider_type_appearance)
    scan("Policy mode", report.policy_mode_appearance)
    scan("Gap", report.gap_appearance)

    if report.verdict_stability < UNSTABLE_THRESHOLD:
        top = max(report.verdict_distribution, key=report.verdict_distribution.get)
        flags.append(
            f"Verdict unstable: top verdict '{top}' supported by only "
            f"{report.verdict_distribution[top]}/{n} samples "
            f"(stability {report.verdict_stability:.2f})"
        )

    return flags

def build_variance_report(
    samples: list[Analysis],
    *,
    sample_seeds: list[int] | None = None,
) -> VarianceReport:
    """Build a VarianceReport from N explore-mode samples.

    Args:
        samples: Non-empty list of Analyses from the same UC.
        sample_seeds: Per-sample seeds; defaults to range(N) if None.

    Returns:
        A VarianceReport summarizing divergence across samples.

    Raises:
        ValueError: empty samples or UUID mismatch.
    """
    if not samples:
        raise ValueError("build_variance_report requires at least one sample")
    uuids = {s.use_case_uuid for s in samples}
    if len(uuids) != 1:
        raise ValueError(f"all samples must share use_case_uuid; got {sorted(uuids)}")
    uc_uuid = uuids.pop()

    n = len(samples)
    if sample_seeds is None:
        sample_seeds = list(range(n))
    if len(sample_seeds) != n:
        raise ValueError(f"sample_seeds length {len(sample_seeds)} != samples length {n}")

    # Verdict distribution
    verdicts = [s.summary.verdict for s in samples]
    verdict_dist = dict(Counter(verdicts))
    top_count = max(verdict_dist.values()) if verdict_dist else 0
    verdict_stability = top_count / n if n else 0.0

    # Confidence distribution (overall_confidence label across samples)
    conf_dist = dict(Counter(s.summary.overall_confidence.label for s in samples))

    # Per-finding-type appearance counts
    component_app = _appearance_dict(
        samples, "components_required", lambda c: canonicalize(c.id)
    )
    capability_app = _appearance_dict(
        samples, "capabilities_invoked", lambda c: canonicalize(c.id)
    )
    data_model_app = _appearance_dict(
        samples, "data_model_touched", lambda d: canonicalize(d.entity)
    )
    provider_type_app = _appearance_dict(
        samples, "provider_types_involved", lambda p: canonicalize(p.type)
    )
    policy_mode_app = _appearance_dict(
        samples, "policy_modes_required", lambda m: canonicalize(m.mode)
    )

    gap_app = _gap_appearance(samples)
    gap_sev_dist = _gap_severity_distribution(samples)

    notes = (
        f"Variance summary across {n} explore-mode samples. "
        f"Verdict stability {verdict_stability:.2f} "
        f"({top_count}/{n} agreement on top verdict). "
        f"Findings below {int(UNSTABLE_THRESHOLD*100)}% appearance are flagged "
        f"as unstable."
    )

    report = VarianceReport(
        use_case_uuid=uc_uuid,
        sample_count=n,
        sample_seeds=list(sample_seeds),
        verdict_distribution=verdict_dist,
        verdict_stability=verdict_stability,
        confidence_distribution=conf_dist,
        component_appearance=component_app,
        capability_appearance=capability_app,
        data_model_appearance=data_model_app,
        provider_type_appearance=provider_type_app,
        policy_mode_appearance=policy_mode_app,
        gap_appearance=gap_app,
        gap_severity_distribution=gap_sev_dist,
        notes=notes,
    )
    report.unstable_findings = _flag_unstable_findings(report, n)
    return report
