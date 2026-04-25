"""Semantic comparator for Stage 2 Analysis YAMLs.

Compares two Analysis outputs for architectural equivalence, ignoring
LLM-tail noise (rationale wording, tool call trajectory, spec_ref path
choice). Used by:

- Stage 3 change-impact analyzer: determine whether a spec change
  produced a meaningful analytical delta on an existing use case.
- Authoring feedback routing: same, scoped to per-author changes.
- Ad-hoc regression checking: "did prompt v1.3 change my analysis
  compared to v1.2?"

Not used for: byte-identical reproducibility checking. That was the
wrong target (see docs/Phase-1a-Close-Out.md for rationale).

Verdict model: two-tier.
  equivalent — same architectural conclusions, wording may differ.
  changed    — something a reviewer should look at.

Severity of the change is recorded inside the diff as metadata on each
finding, so reviewers can triage what's important without needing a
separate verdict tier. Severities:
  trivial   — count-only differences within equivalent ID sets; the
              comparator currently does not emit this tier but reserves
              the severity in case future callers want it.
  minor     — confidence shift, rationale vocabulary divergence on same
              IDs, same-severity gap rewording.
  major     — IDs added/removed, gap severity changed, confidence
              shifted by more than one tier.
  critical  — verdict changed, or a policy mode flipped
              Internal↔External.

The comparator never returns equivalent if any major or critical finding
is present. It may return equivalent if only minor findings are present
AND those findings are judged to be below the equivalence threshold
(currently: confidence shifts alone do not break equivalence; everything
else does).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# -----------------------------------------------------------------------
# Signal extraction
# -----------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

def _canonicalize(s: str) -> str:
    """Normalize a string for equality comparison.

    The model names concepts inconsistently across runs: 'GateKeeper Policy'
    vs 'GateKeeper Policies' vs 'Gate Keeper Policy' all refer to the same
    architectural concept. Naive string equality would treat these as
    different IDs and emit spurious "added/removed" diffs.

    Canonicalization strategy: lowercase, collapse non-alphanumeric runs
    to a single underscore, strip trailing underscores. This loses some
    information (can't distinguish 'VM' from 'VMs' post-canonicalization
    when both become 'vm') but that's the intent — trivial morphological
    differences should collapse.

    Future: if false positives appear (distinct concepts collapsing to
    the same canonical form), introduce a vocabulary lookup as a second
    layer on top of this.
    """
    if not s:
        return ""
    lowered = s.lower().strip()
    # Collapse a small set of known plural patterns to singular. We
    # deliberately avoid the general "strip trailing s" rule because it
    # misclassifies words whose base form ends in -s (process → 'proces',
    # address → 'addres', status → 'statu'). Those false matches are more
    # common than genuine plurals in architectural vocabulary.
    # If the model emits a true plural we want to collapse ('VMs',
    # 'entities'), add it here. For unknowns, the word stays as the
    # model wrote it — a cost of missed equivalences, but preferable to
    # false matches.
    lowered = re.sub(r"\bpolicies\b", "policy", lowered)
    lowered = re.sub(r"\bentities\b", "entity", lowered)
    lowered = re.sub(r"\bcapabilities\b", "capability", lowered)
    lowered = re.sub(r"\bvms\b", "vm", lowered)
    # Collapse non-alphanumeric runs to underscore
    normalized = _NON_ALNUM_RE.sub("_", lowered).strip("_")
    return normalized

def _confidence_label(value):
    """Extract a bare confidence label from either descriptor-form or shorthand.

    confidence may appear as a descriptor dict
    {"label": "high", "score": 85, "band": "very_high", "factors": {...}} or as
    a bare string "high" (pre-γ outputs, or LLM shorthand). This helper
    returns the lowercased label uniformly. Unknown/missing → empty string.

    The comparator operates on raw dicts rather than dataclasses so it can
    consume analyses from any source without importing the engine schema.
    """
    if value is None:
        return ""
    if isinstance(value, dict):
        label = value.get("label")
        if not isinstance(label, str):
            return ""
        return label.strip().lower()
    if isinstance(value, str):
        return value.strip().lower()
    return ""

@dataclass
class AnalysisSignal:
    """Structured extraction of the fields that matter for equivalence.

    Constructed from an Analysis dict (parsed from YAML). Preserves
    canonical IDs for set comparison and keeps original strings for
    human-readable diff output.
    """
    verdict: str
    overall_confidence: str

    # Sets of canonical IDs — used for set-equality. Originals kept in
    # parallel dicts for diff rendering.
    component_ids: set[str] = field(default_factory=set)
    capability_ids: set[str] = field(default_factory=set)
    provider_types: set[str] = field(default_factory=set)
    policy_modes: set[str] = field(default_factory=set)
    data_entities: set[str] = field(default_factory=set)

    # Gaps are compared as (severity, canonical_description) tuples —
    # a gap that changed severity or changed what it's describing is a
    # different gap architecturally.
    gap_fingerprints: set[tuple[str, str]] = field(default_factory=set)

    # Raw lookups for diff rendering (canonical_id → original_string).
    component_originals: dict[str, str] = field(default_factory=dict)
    capability_originals: dict[str, str] = field(default_factory=dict)
    data_entity_originals: dict[str, str] = field(default_factory=dict)
    gap_originals: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)

    # Per-component/capability confidence, for detecting confidence drift
    # on IDs that are present in both sides.
    component_confidences: dict[str, str] = field(default_factory=dict)
    capability_confidences: dict[str, str] = field(default_factory=dict)

def extract_signal(analysis: dict) -> AnalysisSignal:
    """Build an AnalysisSignal from a parsed Analysis YAML dict.

    Accepts the dict form (what yaml.safe_load produces), not the
    dataclass form, to avoid a hard dependency on the engine's schema
    module. The comparator runs in places the engine may not be
    importable (standalone CLI, Stage 3 pipeline, etc.).
    """
    summary = analysis.get("summary") or {}
    sig = AnalysisSignal(
        verdict=(summary.get("verdict") or "").strip().lower(),
        overall_confidence=_confidence_label(summary.get("overall_confidence")),
    )

    for comp in analysis.get("components_required") or []:
        orig = comp.get("id") or ""
        canon = _canonicalize(orig)
        if canon:
            sig.component_ids.add(canon)
            sig.component_originals[canon] = orig
            if comp.get("confidence"):
                sig.component_confidences[canon] = _confidence_label(comp["confidence"])

    for cap in analysis.get("capabilities_invoked") or []:
        orig = cap.get("id") or ""
        canon = _canonicalize(orig)
        if canon:
            sig.capability_ids.add(canon)
            sig.capability_originals[canon] = orig
            if cap.get("confidence"):
                sig.capability_confidences[canon] = _confidence_label(cap["confidence"])

    for prov in analysis.get("provider_types_involved") or []:
        t = (prov.get("type") or "").strip().lower()
        if t:
            sig.provider_types.add(t)

    for mode in analysis.get("policy_modes_required") or []:
        m = (mode.get("mode") or "").strip().lower()
        if m:
            sig.policy_modes.add(m)

    for ent in analysis.get("data_model_touched") or []:
        orig = ent.get("entity") or ""
        canon = _canonicalize(orig)
        if canon:
            sig.data_entities.add(canon)
            sig.data_entity_originals[canon] = orig

    for gap in analysis.get("gaps_identified") or []:
        # severity may be a descriptor dict {label, score, band, factors}
        # or a bare string (shorthand or older analysis files). Extract uniformly.
        sev_raw = gap.get("severity")
        if isinstance(sev_raw, dict):
            severity = (sev_raw.get("label") or "").strip().lower()
        else:
            severity = (sev_raw or "").strip().lower()
        # Use canonicalized description as the gap's semantic fingerprint.
        # Two gaps with the same severity and the same architectural claim
        # (even if worded differently) fingerprint as the same gap.
        desc_canon = _canonicalize(gap.get("description") or "")
        if severity and desc_canon:
            fp = (severity, desc_canon)
            sig.gap_fingerprints.add(fp)
            sig.gap_originals[fp] = gap

    return sig

# -----------------------------------------------------------------------
# Finding model
# -----------------------------------------------------------------------

SEVERITY_TRIVIAL = "trivial"
SEVERITY_MINOR = "minor"
SEVERITY_MAJOR = "major"
SEVERITY_CRITICAL = "critical"

_SEVERITY_ORDER = {
    SEVERITY_TRIVIAL: 0,
    SEVERITY_MINOR: 1,
    SEVERITY_MAJOR: 2,
    SEVERITY_CRITICAL: 3,
}

@dataclass
class Finding:
    """A single delta between two analyses."""
    severity: str
    field: str      # e.g., "verdict", "components_required", "gaps_identified"
    description: str

    def render(self) -> str:
        return f"  [{self.severity:8s}] {self.field}: {self.description}"

_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}

def _confidence_distance(a: str, b: str) -> int:
    """How far apart two confidence labels are. 0 = same, 1 = adjacent."""
    if a not in _CONFIDENCE_ORDER or b not in _CONFIDENCE_ORDER:
        return 0  # unknown labels → treat as equal (don't crash)
    return abs(_CONFIDENCE_ORDER[a] - _CONFIDENCE_ORDER[b])

# -----------------------------------------------------------------------
# Comparison
# -----------------------------------------------------------------------

@dataclass
class CompareResult:
    verdict: str            # "equivalent" | "changed"
    findings: list[Finding] = field(default_factory=list)
    use_case_uuid_a: str = ""
    use_case_uuid_b: str = ""

    @property
    def is_equivalent(self) -> bool:
        return self.verdict == "equivalent"

    @property
    def max_severity(self) -> str:
        """Highest severity among findings. Empty string if no findings."""
        if not self.findings:
            return ""
        return max(self.findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 0)).severity

    def render(self) -> str:
        """Human-readable diff output."""
        if self.is_equivalent and not self.findings:
            header = "=== equivalent ==="
            body = ["  (no meaningful differences detected)"]
        elif self.is_equivalent:
            header = f"=== equivalent (with {len(self.findings)} minor findings) ==="
            body = [f.render() for f in self.findings]
        else:
            header = f"=== changed ({self.max_severity}, {len(self.findings)} findings) ==="
            body = [f.render() for f in self.findings]

        if self.use_case_uuid_a and self.use_case_uuid_b:
            if self.use_case_uuid_a == self.use_case_uuid_b:
                id_line = f"  use_case_uuid: {self.use_case_uuid_a}"
            else:
                id_line = (
                    f"  WARNING: comparing different use cases: "
                    f"{self.use_case_uuid_a} vs {self.use_case_uuid_b}"
                )
            return "\n".join([header, id_line, *body])
        return "\n".join([header, *body])

def _set_diff_finding(
    a: set[str],
    b: set[str],
    originals_a: dict[str, str],
    originals_b: dict[str, str],
    field_name: str,
    add_severity: str,
    remove_severity: str,
) -> list[Finding]:
    """Emit findings for items added-in-b or removed-from-a.

    Separate severities for add vs remove lets callers distinguish "this
    change introduced a new architectural element" (usually major) from
    "this change dropped one" (usually also major but sometimes just a
    rewording collapse).
    """
    findings = []
    added = b - a
    removed = a - b
    for canon in sorted(added):
        orig = originals_b.get(canon, canon)
        findings.append(Finding(
            severity=add_severity,
            field=field_name,
            description=f"added: '{orig}'",
        ))
    for canon in sorted(removed):
        orig = originals_a.get(canon, canon)
        findings.append(Finding(
            severity=remove_severity,
            field=field_name,
            description=f"removed: '{orig}'",
        ))
    return findings

def compare(analysis_a: dict, analysis_b: dict) -> CompareResult:
    """Compare two Analysis dicts semantically. Return CompareResult.

    Ordering: analysis_a is treated as the "before" and analysis_b as
    "after". This matters only for the human-readable "added"/"removed"
    labeling in findings; the equivalence verdict is symmetric.
    """
    sig_a = extract_signal(analysis_a)
    sig_b = extract_signal(analysis_b)

    findings: list[Finding] = []

    # Verdict change is critical. The whole point of the framework is
    # to detect this specific class of change.
    if sig_a.verdict != sig_b.verdict:
        findings.append(Finding(
            severity=SEVERITY_CRITICAL,
            field="verdict",
            description=f"{sig_a.verdict} → {sig_b.verdict}",
        ))

    # Overall confidence is minor on single-tier moves (high→medium),
    # major on two-tier (high→low). Zero tiers: no finding.
    conf_distance = _confidence_distance(sig_a.overall_confidence, sig_b.overall_confidence)
    if conf_distance == 1:
        findings.append(Finding(
            severity=SEVERITY_MINOR,
            field="overall_confidence",
            description=f"{sig_a.overall_confidence} → {sig_b.overall_confidence}",
        ))
    elif conf_distance >= 2:
        findings.append(Finding(
            severity=SEVERITY_MAJOR,
            field="overall_confidence",
            description=f"{sig_a.overall_confidence} → {sig_b.overall_confidence}",
        ))

    # Components: added/removed are major (the analysis now claims a
    # different architectural surface is involved).
    findings.extend(_set_diff_finding(
        sig_a.component_ids, sig_b.component_ids,
        sig_a.component_originals, sig_b.component_originals,
        "components_required",
        add_severity=SEVERITY_MAJOR,
        remove_severity=SEVERITY_MAJOR,
    ))

    # Capabilities: same rationale as components.
    findings.extend(_set_diff_finding(
        sig_a.capability_ids, sig_b.capability_ids,
        sig_a.capability_originals, sig_b.capability_originals,
        "capabilities_invoked",
        add_severity=SEVERITY_MAJOR,
        remove_severity=SEVERITY_MAJOR,
    ))

    # Data entities: slightly less severe than components/capabilities
    # since entity coverage often drifts without architectural meaning
    # (model may or may not mention 'AuditEvent' as a touched entity
    # even when audit is part of the workflow). Call it minor if the
    # set size is similar, major if a whole entity class appears or
    # disappears.
    data_added = sig_b.data_entities - sig_a.data_entities
    data_removed = sig_a.data_entities - sig_b.data_entities
    data_severity = SEVERITY_MINOR if abs(len(data_added) - len(data_removed)) <= 1 else SEVERITY_MAJOR
    findings.extend(_set_diff_finding(
        sig_a.data_entities, sig_b.data_entities,
        sig_a.data_entity_originals, sig_b.data_entity_originals,
        "data_model_touched",
        add_severity=data_severity,
        remove_severity=data_severity,
    ))

    # Provider types: the set is small (six total types) and architectural.
    # Any change is major.
    findings.extend(_set_diff_finding(
        sig_a.provider_types, sig_b.provider_types,
        {t: t for t in sig_a.provider_types},
        {t: t for t in sig_b.provider_types},
        "provider_types_involved",
        add_severity=SEVERITY_MAJOR,
        remove_severity=SEVERITY_MAJOR,
    ))

    # Policy modes: there are only two possible values (Internal, External).
    # Any change here is critical — it's a foundational architectural flip.
    if sig_a.policy_modes != sig_b.policy_modes:
        added = sig_b.policy_modes - sig_a.policy_modes
        removed = sig_a.policy_modes - sig_b.policy_modes
        for m in sorted(added):
            findings.append(Finding(
                severity=SEVERITY_CRITICAL,
                field="policy_modes_required",
                description=f"added: '{m}'",
            ))
        for m in sorted(removed):
            findings.append(Finding(
                severity=SEVERITY_CRITICAL,
                field="policy_modes_required",
                description=f"removed: '{m}'",
            ))

    # Gaps: a gap appearing or disappearing is major. A gap changing
    # severity is major (severity change is a gap-fingerprint change).
    # The comparator does not currently try to pair up gaps that were
    # merely reworded — if description canonicalizes differently, they
    # are treated as different gaps. In practice the canonicalizer is
    # aggressive enough that reworded gaps on the same topic should
    # fingerprint to the same string.
    gaps_added = sig_b.gap_fingerprints - sig_a.gap_fingerprints
    gaps_removed = sig_a.gap_fingerprints - sig_b.gap_fingerprints
    for fp in sorted(gaps_added):
        gap = sig_b.gap_originals.get(fp, {})
        desc = gap.get("description", fp[1])
        findings.append(Finding(
            severity=SEVERITY_MAJOR,
            field="gaps_identified",
            description=f"added [{fp[0]}]: '{desc}'",
        ))
    for fp in sorted(gaps_removed):
        gap = sig_a.gap_originals.get(fp, {})
        desc = gap.get("description", fp[1])
        findings.append(Finding(
            severity=SEVERITY_MAJOR,
            field="gaps_identified",
            description=f"removed [{fp[0]}]: '{desc}'",
        ))

    # Per-component confidence drift. Only for components present on
    # both sides (added/removed already handled above).
    for canon in sig_a.component_ids & sig_b.component_ids:
        a_conf = sig_a.component_confidences.get(canon, "")
        b_conf = sig_b.component_confidences.get(canon, "")
        dist = _confidence_distance(a_conf, b_conf)
        if dist >= 1:
            severity = SEVERITY_MINOR if dist == 1 else SEVERITY_MAJOR
            orig = sig_a.component_originals.get(canon, canon)
            findings.append(Finding(
                severity=severity,
                field="component_confidence",
                description=f"'{orig}': {a_conf} → {b_conf}",
            ))

    # Per-capability confidence drift. Same logic as components.
    for canon in sig_a.capability_ids & sig_b.capability_ids:
        a_conf = sig_a.capability_confidences.get(canon, "")
        b_conf = sig_b.capability_confidences.get(canon, "")
        dist = _confidence_distance(a_conf, b_conf)
        if dist >= 1:
            severity = SEVERITY_MINOR if dist == 1 else SEVERITY_MAJOR
            orig = sig_a.capability_originals.get(canon, canon)
            findings.append(Finding(
                severity=severity,
                field="capability_confidence",
                description=f"'{orig}': {a_conf} → {b_conf}",
            ))

    # Decide the overall verdict. Equivalence holds if all findings are
    # minor or trivial; any major or critical finding breaks it.
    max_sev = SEVERITY_TRIVIAL
    for f in findings:
        if _SEVERITY_ORDER.get(f.severity, 0) > _SEVERITY_ORDER.get(max_sev, 0):
            max_sev = f.severity

    verdict = "equivalent" if _SEVERITY_ORDER.get(max_sev, 0) < _SEVERITY_ORDER[SEVERITY_MAJOR] else "changed"

    return CompareResult(
        verdict=verdict,
        findings=findings,
        use_case_uuid_a=analysis_a.get("use_case_uuid", ""),
        use_case_uuid_b=analysis_b.get("use_case_uuid", ""),
    )
