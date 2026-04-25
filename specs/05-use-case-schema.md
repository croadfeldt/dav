# DAV Specification 05 — Use Case Schema

**Status:** Proposed (v1.0)
**Audience:** Consumer authoring use cases; anyone integrating with DAV
**Depends on:** None (this is foundational)
**Referenced by:** `02-stage-model.md`, `06-prompt-contract.md`, `07-analysis-output-schema.md`, `08-consumer-integration.md`, `10-calibration-and-correctness.md`

---

## 1. Purpose

A use case (UC) is DAV's unit of architectural question. "Does this architecture support onboarding a new tenant atomically?" is a use case. "Are all referenced document handles valid?" is a use case. UCs are supplied by consumers in YAML files conforming to the schema defined here.

This spec defines:

- The YAML structure of a use case file
- Required and optional fields
- The three UC types (analytical, assertion, hybrid) and their respective fields
- Controlled vocabularies for enumerable fields
- The severity and confidence scoring model (0-100, aligned with DCM's scoring conventions)
- Validation rules DAV applies before a UC is accepted
- File layout conventions for a consumer's UC corpus

This spec is a **consumer contract**. Consumers conform to it; DAV validates conformance and rejects non-conforming UCs with diagnostic errors. Breaking changes to this schema require a DAV major version bump.

### 1.1 Relationship to DCM scoring

DAV's severity and confidence scoring is intentionally aligned with DCM's confidence and trust scoring conventions (DCM Doc 21, Information Providers — Advanced). Both systems use:

- **0-100 score range** — allows mathematical composition, sorting, and threshold comparisons
- **Descriptor-primary representation** — a nested object carrying the label (human-handle), derived score (for math), derived band (for policy thresholds), and factor breakdown (for audit)
- **Five-band vocabulary** — `very_low` (0-20), `low` (21-40), `medium` (41-60), `high` (61-80), `very_high` (81-100)

Aligning on representation lets DAV findings compose cleanly with DCM's confidence data when DAV is integrated as a DCM capability (see ADR-002). Policies that gate on severity thresholds use the same numeric language as policies that gate on confidence or trust thresholds.

## 2. File layout

Consumer UCs live in a flat or domain-organized directory under the consumer's `dav/use-cases/` path:

```
<consumer-repo>/dav/use-cases/
├── README.md                       Required — describes consumer's UC organization
├── cross_domain/
│   ├── tenant-onboarding.yaml
│   └── federation-peering.yaml
├── data/
│   └── persistent-volume-provision.yaml
├── spec_integrity/
│   ├── all-handles-resolve.yaml           (assertion UC)
│   └── vocabulary-compliance.yaml         (assertion UC)
└── ...
```

Directory structure is consumer-chosen. DAV does not prescribe domain categorization. The only rules:

1. `README.md` at the UC corpus root is required and must describe:
   - The consumer's UC organization strategy
   - The consumer's controlled vocabulary for the `domain` field
   - Consumer-specific conventions (if any) that depart from or refine this spec
2. UC files must have `.yaml` extension (`.yml` is rejected for consistency)
3. UC file names should match the UC's short handle, kebab-case
4. Nested subdirectories are permitted for logical grouping

## 3. The universal UC structure

Every UC — regardless of type — has this top-level structure:

```yaml
use_case_uuid: <string>                     # Required — unique identifier
uc_type: analytical | assertion | hybrid    # Required — determines which other fields apply
domain: <string>                            # Required — consumer-defined controlled vocabulary
description: <string>                       # Required — one-sentence plain English
gate_class: hard | advisory | mixed         # Required — CI gating class

metadata:                                   # Optional block
  author: <string>
  created: <ISO-8601 date>
  last_modified: <ISO-8601 date>
  tags: [<string>, ...]
  references: [<string>, ...]               # URLs or doc handles this UC relates to

# Type-specific fields follow; see §5, §6, §7
```

### 3.1 `use_case_uuid`

Unique identifier for the UC. Two forms are permitted:

- **Seed UUID**: `uc-seed-NNN[a-z]?` for human-authored UCs (e.g., `uc-seed-008a`). Letter suffix distinguishes refined variants of a seed concept.
- **Assertion UUID**: `uc-assert-NNN` for assertion-type UCs (e.g., `uc-assert-001`).
- **Generic UUID**: any RFC 4122 UUID for machine-generated UCs.

UUIDs are immutable once assigned. Renaming a UC (changing its description or scope substantially) requires a new UUID.

### 3.2 `uc_type`

One of three values:

- **`analytical`** — LLM-assisted architectural analysis. The UC describes a scenario; DAV's Stage 2 runs an LLM agent against the spec corpus and produces structured findings.
- **`assertion`** — Deterministic Python check. The UC points at a consumer-supplied Python function that returns pass/fail with diagnostic. No LLM involvement.
- **`hybrid`** — Combination. An assertion runs first (structural validation); if it passes, an analytical pass runs. If the assertion fails, the analytical pass is skipped and the assertion failure is the final result.

See §5, §6, §7 for type-specific fields.

### 3.3 `domain`

Consumer-defined category label. The consumer's `README.md` must document the controlled vocabulary. DAV validates that UCs use values from that vocabulary but does not interpret them.

Example DCM vocabulary (from current DCM UCs):

```
cross_domain        # UCs involving multiple subsystems
data                # UCs about data model operations
spec_integrity      # Assertion UCs validating corpus structure
compute             # UCs about compute resource provisioning
network             # UCs about network resource provisioning
governance          # UCs about policy, audit, compliance
federation          # UCs about cross-instance coordination
```

A consumer can have any vocabulary. DAV treats `domain` as an opaque filtering label.

### 3.4 `description`

One-sentence human-readable description. Should complete the phrase "This use case checks whether..." without that phrase appearing literally.

Good: `"Onboarding a new tenant results in either full provisioning or no persistent state."`

Bad: `"This use case checks tenant onboarding."` (too vague)
Bad: `"DCM's tenant onboarding flow per Doc 49 §9.1 must provision seven entities in dependency order while enforcing compensation semantics defined in Doc 07 §8, as invoked by FSI profile policies, during which..."` (too long; belongs in `scenario`)

### 3.5 `gate_class`

Governs how the UC's results participate in CI gating. One of:

- **`hard`** — Failure blocks workflows. Only `assertion` UCs can use `gate_class: hard`. DAV validates this and rejects analytical UCs declared as hard gates.
- **`advisory`** — Failure is reported but does not block. Default for `analytical` UCs. Informs human reviewers.
- **`mixed`** — Only valid for `hybrid` UCs. The assertion portion is a hard gate; the analytical portion is advisory.

Rationale for the distinction is documented in `03-determinism-invariants.md` and `02-stage-model.md`.

## 4. Scope

A UC may optionally declare a `scope` field specifying what the UC applies to:

```yaml
scope:
  type: global | tenant_profile | environment | component | capability
  value: <string>                            # Interpretation depends on type
```

Scope types:

- **`global`** — UC applies to the whole consumer architecture (default if `scope` is omitted).
- **`tenant_profile`** — UC applies to a specific tenant profile. Example: `{type: tenant_profile, value: fsi}`. Relevant for multi-tenant architectures like DCM.
- **`environment`** — UC applies to a deployment environment. Example: `{type: environment, value: production}`.
- **`component`** — UC applies to a specific architectural component. Example: `{type: component, value: policy-engine}`.
- **`capability`** — UC applies to a specific capability. Example: `{type: capability, value: architecture-validation}`.

Consumers define what legal `value` strings are for each scope type in their `README.md`. DAV treats scope as opaque filtering and audit metadata.

## 5. Analytical UC fields

Analytical UCs describe scenarios and success criteria. DAV's Stage 2 reads the spec corpus, runs an LLM agent, and produces an Analysis.

```yaml
use_case_uuid: uc-seed-008a
uc_type: analytical
domain: cross_domain
description: Tenant onboarding produces atomic state transitions.
gate_class: advisory

analytical:
  scenario: |
    <free-form narrative describing the architectural scenario to be analyzed,
     typically 1-5 paragraphs>

  success_criteria:
    - <structured criterion>
    - <structured criterion>

  expected_components: [<string>, ...]       # Optional hints — not authoritative
  expected_capabilities: [<string>, ...]
  expected_gaps: [<string>, ...]             # Known gaps; helps calibration

  focus_areas:                               # Optional — biases Stage 2 attention
    - <string>
    - <string>
```

### 5.1 `analytical.scenario`

Free-form narrative. This becomes part of the Stage 2 prompt. Keep it factual and scenario-focused; avoid prescribing what DAV should find.

Good: `"A new tenant 'Payments Platform' is onboarded via POST /api/v1/admin/tenants with display_name, handle, group_class, initial_quota_profile, billing_contact, data_classifications_permitted, and sovereignty_zones. The onboarding process provisions seven entities: tenant, resource_group, quota, auth_provider, audit_stream, policy binding, and tenant admin."`

Bad: `"Tenant onboarding should be atomic. DAV should find that the spec supports atomicity. If it doesn't, that's a gap."`

### 5.2 `analytical.success_criteria`

Structured criteria the analysis will be evaluated against. Each criterion is one of:

- **`all_or_nothing: <entity-list>`** — The entities listed must all be provisioned or none should persist.
- **`eventually_consistent_with: <invariant>`** — An invariant that must hold eventually.
- **`bounded_by: {resource: <name>, limit: <value>}`** — A resource bound.
- **`visible_to: [<actor-role>, ...]`** — Auditability/visibility requirement.
- **`free_form: <string>`** — When the criterion doesn't fit structured forms. Use sparingly.

Example:

```yaml
success_criteria:
  - all_or_nothing:
      entities: [tenant, resource_group, quota, auth_provider, audit_stream, policy_binding, actor]
  - visible_to:
      actors: [platform_admin, tenant_admin, compliance_officer]
  - free_form: >
      The audit record for the onboarding event must be a single atomic record
      tied to the onboarding transaction, not multiple uncorrelated events.
```

### 5.3 `analytical.expected_*` (optional hints)

Consumers can optionally declare what they expect DAV to find. These are not authoritative — DAV's analysis may legitimately find more or different items — but they:

- Help calibration scoring (see `10-calibration-and-correctness.md`)
- Flag missing items in the analysis (if `expected_components` includes `audit_stream` but Stage 2 doesn't find it, that's a signal)
- Document UC author intent for human reviewers

Leave these empty if you genuinely don't want to pre-commit to expectations.

### 5.4 `analytical.focus_areas`

Optional. Free-form strings that bias Stage 2's attention. The engine incorporates these into the system prompt as "particularly consider: X, Y, Z."

Example: `focus_areas: [atomicity, compensation, multi-actor-visibility]`

Use sparingly. Excessive focus areas push the LLM toward confirmation bias.

## 6. Severity and Confidence Scoring

UC findings carry severity (how bad is it?) and confidence (how sure are we?). Both use a common scoring model aligned with DCM's Doc 21 conventions.

### 6.1 Representation form

Both severity and confidence use the **descriptor-primary nested form**: a label is authoritative (what humans set and discuss), a score is derived (for math and sorting), a band is derived from the score (for policy thresholds), and factors optionally record what went into the score (for audit).

```yaml
severity:
  label: major                       # Required — the human handle
  score: 70                          # Optional override; defaults to label center
  band: high                         # Derived from score; not author-set
  factors:                           # Optional; records score computation
    base_from_label: 70
    override_rationale: null         # Set when score is explicitly overridden
```

```yaml
confidence:
  label: high                        # Required — the human handle
  score: 85                          # Optional override; defaults to label center
  band: very_high                    # Derived from score; not author-set
  factors:
    base_from_label: 85
    override_rationale: null
```

Rules:

1. `label` is required. It's what authors set.
2. `score` is optional. If omitted, DAV computes it from the label using the default center-of-band value.
3. If `score` is set by the author, it must fall within the label's declared range (see §6.2 and §6.3). An author-supplied score outside the range is a validation error.
4. `band` is derived from `score` using the DCM five-band taxonomy. Authors never set it directly; DAV populates it.
5. `factors` is optional metadata for audit. Authors who override `score` should set `factors.override_rationale` to explain why.

### 6.2 Severity labels and bands

Severity has five labels aligned with DCM's five bands:

| Label | Default score | Band | Band name | When to use |
|-------|--------------|------|-----------|-------------|
| `advisory` | 10 | 0-20 | very_low | A suggestion; noticeable but not blocking. Documentation improvements, nice-to-haves, stylistic observations. |
| `minor` | 30 | 21-40 | low | A real issue but not urgent. Could affect operations under specific conditions; has workarounds. |
| `moderate` | 50 | 41-60 | medium | A genuine architectural concern. Should be addressed in ordinary work but does not block. |
| `major` | 70 | 61-80 | high | A significant gap or defect. Should be addressed promptly. May block certain categories of work. |
| `critical` | 90 | 81-100 | very_high | A severe issue. Blocks related work; may compromise security, correctness, or compliance. |

The `moderate` label fills the middle band (41-60). It's the default choice when a finding is neither "probably fine for now" (minor) nor "warrants urgent attention" (major).

Examples of severity assignment:

- An LLM-identified gap where a small section is missing from a spec doc → `advisory`
- A corpus spellcheck assertion failure → `advisory`
- A UC references a doc handle that has been renamed → `minor`
- A capability is declared but has no backing provider → `moderate`
- A spec section contradicts another spec section → `major`
- A referenced doc handle does not resolve at all (broken corpus) → `critical` (if the corpus is unparseable, nothing works)

### 6.3 Confidence labels and bands

Confidence has three labels, also mapped to DCM's five-band taxonomy (occupying the low, medium, and very_high bands):

| Label | Default score | Band | Band name | When to use |
|-------|--------------|------|-----------|-------------|
| `low` | 30 | 21-40 | low | Evidence is weak or contested. Multiple plausible interpretations; reviewer judgment required. |
| `medium` | 50 | 41-60 | medium | Reasonable evidence supports the finding; some uncertainty remains. |
| `high` | 85 | 81-100 | very_high | Strong evidence; finding is well-supported by direct spec references or deterministic computation. |

Note that confidence labels do not occupy the `very_low` or `high` bands by default. Authors who want more granularity — e.g., declaring a finding at "high" confidence but sitting at the low end (score 65) — can do so via the `score` override within the label's band range. The score override is bounded by the label's band, not by the gaps between labels.

Alternatively, the v1.1 schema may introduce additional confidence labels if author feedback shows the three-label set is insufficient. This is an open question (see §12.5).

### 6.4 Overriding the score

Authors override the score when the default label-center value does not match their judgment. A `major` finding can legitimately sit at 62 (near the bottom of the band) or 79 (near the top). The override is a within-band refinement, not a label reassignment.

Validation rule: if `score` is author-set, it must fall within the label's band range. Setting `severity.label: major` with `severity.score: 45` is a validation error — if the finding scores 45, its label should be `moderate`, not `major`.

If an author wants to override `score` across band boundaries, they must also change `label`. This keeps label and band consistent.

### 6.5 Derived fields

DAV populates these automatically; authors do not set them:

- **`band`**: computed from `score` using fixed thresholds (0-20 → very_low, 21-40 → low, 41-60 → medium, 61-80 → high, 81-100 → very_high)
- **`factors.base_from_label`**: the default score for the declared label (unused arithmetically in v1.0 but recorded for audit)

### 6.6 Shorthand form

For UCs that don't need overrides, a shorthand form is permitted:

```yaml
severity: major                      # Shorthand — equivalent to nested form with defaults
```

DAV expands this at load time to:

```yaml
severity:
  label: major
  score: 70
  band: high
  factors:
    base_from_label: 70
    override_rationale: null
```

The shorthand is only permitted when no override is present. As soon as the author wants to set a non-default score or rationale, they must use the nested form.

### 6.7 Rationale for this model

The descriptor-primary representation is adopted from DCM Doc 21's confidence scoring. The benefit is cross-system consistency: a DAV severity and a DCM confidence score are structurally identical objects carrying different semantic meaning. Policies, audit queries, and UI components can treat them uniformly.

The 0-100 range supports mathematical composition. Under future DCM-DAV integration (ADR-002), actionability scores that combine severity and confidence (e.g., `actionability = severity.score × (confidence.score / 100)`) are computable without unit conversion. The score is the common language; the labels are the human handles.

Full discussion of the composition rules — actionability, aggregation across findings, regression gate thresholds — lives in `10-calibration-and-correctness.md`. This section defines the representation; that spec defines how to use it.

## 7. Assertion UC fields

Assertion UCs point at a consumer-supplied Python function. DAV invokes the function; the function returns a result.

```yaml
use_case_uuid: uc-assert-001
uc_type: assertion
domain: spec_integrity
description: All document handles referenced in the UC corpus resolve to actual documents.
gate_class: hard

assertion:
  module: dcm.dav.assertions.handle_resolution
  function: check_all_uc_handles_resolve
  args:                                      # Optional — passed as kwargs to function
    corpus_root: "/corpus"
    use_case_dir: "/corpus/use_cases"
  timeout_seconds: 60                        # Optional — default 300
```

### 7.1 `assertion.module` and `assertion.function`

Python module path and function name. The module must be importable from the DAV engine's Python path at runtime. Consumers register their assertion modules via the DAV engine's consumer-extension mechanism (see `08-consumer-integration.md`).

Function signature:

```python
def check_all_uc_handles_resolve(
    corpus_root: str,
    use_case_dir: str,
    **kwargs,                                # DAV may pass additional context
) -> AssertionResult:
    """Return AssertionResult describing pass/fail plus diagnostic."""
```

Where `AssertionResult` is defined by DAV and has the shape:

```python
@dataclass
class SeverityDescriptor:
    label: str                               # advisory | minor | moderate | major | critical
    score: int | None = None                 # 0-100; None = use label default
    band: str | None = None                  # Populated by DAV; not set by caller
    factors: dict | None = None              # Audit trail of score computation


@dataclass
class ConfidenceDescriptor:
    label: str                               # low | medium | high
    score: int | None = None                 # 0-100; None = use label default
    band: str | None = None                  # Populated by DAV; not set by caller
    factors: dict | None = None              # Audit trail of score computation


@dataclass
class AssertionResult:
    passed: bool
    diagnostic: str                          # Human-readable
    details: dict | None = None              # Structured findings
    severity: SeverityDescriptor = field(
        default_factory=lambda: SeverityDescriptor(label="major")
    )
    confidence: ConfidenceDescriptor = field(
        default_factory=lambda: ConfidenceDescriptor(label="high")
    )
```

Assertion functions may construct these descriptors directly or use the shorthand-accepting helpers DAV provides:

```python
# Shorthand form — DAV expands to nested form automatically
return AssertionResult(
    passed=False,
    diagnostic="3 doc handles failed to resolve: ...",
    severity="major",                        # String; DAV wraps in SeverityDescriptor
    confidence="high",
)

# Nested form — when score override or factors are needed
return AssertionResult(
    passed=False,
    diagnostic="Handle '49-implementation-specifications' resolves but section missing",
    severity=SeverityDescriptor(
        label="major",
        score=75,                            # Within high band (61-80); near the top
        factors={"override_rationale": "Missing critical section, but not broken corpus"},
    ),
    confidence=ConfidenceDescriptor(label="high"),
)
```

The DAV engine normalizes shorthand strings into `SeverityDescriptor` / `ConfidenceDescriptor` instances at result ingestion time. Authors of assertion functions can use whichever form is convenient.

### 7.2 `assertion.args`

Keyword arguments passed to the function at invocation time. DAV augments these with a standard set of context kwargs (current spec revision, run ID, mode, etc.) documented in the engine reference.

### 7.3 `assertion.timeout_seconds`

Maximum wall time for the assertion. Exceeding the timeout is treated as a failure. Default 300 seconds. Assertions should generally run in under 10 seconds; anything slower is a code smell.

## 8. Hybrid UC fields

Hybrid UCs combine both. Assertion runs first as a structural gate; on pass, analytical runs.

```yaml
use_case_uuid: uc-hybrid-001
uc_type: hybrid
domain: cross_domain
description: Tenant onboarding UC passes structural checks and architectural analysis.
gate_class: mixed

assertion:
  module: dcm.dav.assertions.onboarding_prereqs
  function: check_onboarding_prereqs
  args:
    corpus_root: "/corpus"
  timeout_seconds: 30

analytical:
  scenario: |
    <as per §5.1>
  success_criteria:
    - <as per §5.2>
```

Semantics:

- If `assertion` fails, the UC result is the assertion failure. `gate_class: mixed` means this portion is a hard gate.
- If `assertion` passes, `analytical` runs. The analytical result is reported as advisory.
- Both sections use the same schemas as their pure-type counterparts.

Hybrid UCs are valuable when an architectural analysis only makes sense if structural preconditions hold. For example, analyzing "does tenant onboarding work atomically" assumes the onboarding spec exists at all — if Doc 49 §9.1 is missing (assertion check), the analytical question is moot.

## 9. Validation rules

DAV validates UC files before accepting them. A UC that fails validation is rejected with a diagnostic. Validation rules:

### 9.1 Structural rules

1. File parses as valid YAML
2. Top-level keys match the universal structure (§3) — unknown top-level keys are rejected
3. `use_case_uuid` is present and non-empty
4. `uc_type` is one of the three valid values
5. `domain` is present and non-empty
6. `description` is present, non-empty, ≤ 200 characters
7. `gate_class` is present and valid for the UC type:
   - `analytical` → `advisory` (only)
   - `assertion` → `hard` (only — assertions are always hard gates)
   - `hybrid` → `mixed` (only)

### 9.2 Type-specific rules

**Analytical UCs:**
- `analytical` block present
- `analytical.scenario` present and ≥ 50 characters
- `analytical.success_criteria` present with ≥ 1 item

**Assertion UCs:**
- `assertion` block present
- `assertion.module` resolves (importable) at consumer-content-load time
- `assertion.function` exists in the module
- `assertion.function` has a compatible signature (accepts kwargs; returns `AssertionResult`)

**Hybrid UCs:**
- Both `assertion` and `analytical` blocks present
- Each block validates per its type-specific rules

### 9.3 Vocabulary rules

- `domain` value is in the consumer's declared vocabulary (from `README.md`)
- `scope.type` (if present) is one of the five legal values
- `scope.value` (if present) is valid for the declared `scope.type` per consumer conventions

### 9.4 Scoring rules

Rules governing severity and confidence descriptors wherever they appear (assertion results, analytical `expected_gaps` annotations, output analyses, etc.):

- `severity.label` is one of `advisory | minor | moderate | major | critical`
- `confidence.label` is one of `low | medium | high`
- `severity.score` (if set) is an integer in 0-100
- `confidence.score` (if set) is an integer in 0-100
- `severity.score` falls within the label's band range:
  - `advisory` → 0-20
  - `minor` → 21-40
  - `moderate` → 41-60
  - `major` → 61-80
  - `critical` → 81-100
- `confidence.score` falls within the label's band range:
  - `low` → 21-40
  - `medium` → 41-60
  - `high` → 81-100
- `band` field is populated by DAV; any author-set `band` value is rejected with a warning (use `score` to refine within a label; use `label` to move between bands)
- `factors.base_from_label` matches the declared label's default center value; if an author sets a different value, warning issued

### 9.5 Referential integrity rules

- Documents referenced in `metadata.references` resolve to real documents in the consumer's spec corpus (if they look like spec handles)
- `analytical.expected_components`, `expected_capabilities`, `expected_gaps` are flagged for review if they use values that have never appeared in any Analysis output, but not rejected (authors may intentionally name items they expect to discover don't exist)

### 9.6 Warning-only rules (non-fatal)

- `analytical.scenario` > 2000 characters (usually means too much prescription)
- `analytical.focus_areas` > 5 items (usually confirmation bias risk)
- `gate_class: hard` on UCs whose assertions take > 30 seconds on average (CI cost concern)
- UC has no `metadata` block (hygiene)
- Author-set `severity.score` differs from label default by ≥ 10 without `factors.override_rationale` set (override without explanation is permitted but flagged for review)

## 10. Example UCs

### 10.1 Minimal analytical UC

```yaml
use_case_uuid: uc-seed-001
uc_type: analytical
domain: data
description: Provisioning a persistent volume attaches it to a valid storage provider.
gate_class: advisory

analytical:
  scenario: |
    A user requests a persistent volume of size 100 GiB with StorageClass "standard-ssd".
    The request is handled by the storage provider subsystem. The provisioned volume
    must appear in the tenant's resource_group inventory and be readable/writable.

  success_criteria:
    - visible_to:
        actors: [tenant_user, tenant_admin]
    - bounded_by:
        resource: tenant_storage_quota
        limit: "declared in tenant quota profile"
```

### 10.2 Assertion UC

```yaml
use_case_uuid: uc-assert-001
uc_type: assertion
domain: spec_integrity
description: All referenced document handles in the UC corpus resolve to real documents.
gate_class: hard

metadata:
  author: cr
  created: 2026-04-24
  tags: [corpus-hygiene, pre-migration-ready]
  references:
    - "dav/specs/05-use-case-schema.md"

assertion:
  module: dcm.dav.assertions.handle_resolution
  function: check_all_uc_handles_resolve
  args:
    corpus_root: "/corpus"
    use_case_dir: "/corpus/use_cases"
  timeout_seconds: 30
```

Companion Python implementation (in `dcm/dav/assertions/handle_resolution.py`):

```python
from dav.core.schema import AssertionResult, SeverityDescriptor

def check_all_uc_handles_resolve(
    corpus_root: str,
    use_case_dir: str,
    **kwargs,
) -> AssertionResult:
    """Walk all UC files; verify every referenced doc handle resolves."""
    unresolved = []
    for uc_file in iter_uc_files(use_case_dir):
        for handle in extract_doc_handles(uc_file):
            if not handle_resolves(handle, corpus_root):
                unresolved.append({"uc": str(uc_file), "handle": handle})

    if not unresolved:
        return AssertionResult(
            passed=True,
            diagnostic="All UC-referenced doc handles resolve.",
            confidence="high",
        )

    # Failed — severity depends on how many handles broke
    if len(unresolved) <= 3:
        severity = "major"                   # Shorthand; DAV expands at ingestion
    else:
        severity = SeverityDescriptor(
            label="critical",
            score=88,
            factors={"override_rationale": f"{len(unresolved)} broken handles — corpus integrity compromised"},
        )

    return AssertionResult(
        passed=False,
        diagnostic=f"{len(unresolved)} doc handle(s) failed to resolve.",
        details={"unresolved": unresolved},
        severity=severity,
        confidence="high",
    )
```

### 10.3 Hybrid UC

```yaml
use_case_uuid: uc-hybrid-tenant-onboarding
uc_type: hybrid
domain: cross_domain
description: Tenant onboarding UC passes structural checks and architectural analysis.
gate_class: mixed

metadata:
  author: cr
  tags: [onboarding, tenant, policy-binding]

scope:
  type: tenant_profile
  value: fsi

assertion:
  module: dcm.dav.assertions.onboarding_prereqs
  function: check_onboarding_prereqs
  args:
    corpus_root: "/corpus"
    required_sections:
      - "49-implementation-specifications/9.1 New Tenant Onboarding Flow"
      - "14-policy-profiles/3.4 Profile Activation"

analytical:
  scenario: |
    A new tenant is onboarded through the standard onboarding flow with FSI profile
    active. Compensation semantics must be specified for every provisioning step,
    and audit records must compose atomically.
  success_criteria:
    - all_or_nothing:
        entities: [tenant, resource_group, quota, auth_provider, audit_stream,
                   policy_binding, actor]
    - visible_to:
        actors: [platform_admin, tenant_admin, compliance_officer]
  expected_components: [tenant_boundary, quota_profile, auth_provider, audit_stream,
                         policy_profile]
  expected_gaps: ["atomic onboarding composition"]
```

## 11. Evolution and versioning

This schema is versioned at the DAV framework level via semantic versioning:

- **Minor version bumps** (e.g., 1.0 → 1.1): additive changes. New optional fields, new `uc_type` values, new `gate_class` values. Existing UCs remain valid.
- **Major version bumps** (e.g., 1.0 → 2.0): breaking changes. Field removals, field semantics changes, required-field additions. Migration path must be documented.

Consumers declare their target schema version in their `dav-version.yaml` file. DAV validates UCs against the declared version, not its own current version. This permits consumers to upgrade on their own schedule.

## 12. Non-goals

This schema does not prescribe:

- UC naming conventions beyond UUID format (consumers choose)
- UC directory layout beyond requiring `README.md` at root (consumers choose)
- Domain vocabulary (consumers define)
- Scope value spaces (consumers define)
- Assertion function implementation details (consumers write these)
- UC authoring style or tone (consumers develop their own voice)

## 13. Open questions

Items expected to be refined in future versions:

### 13.1 Parameterized UCs

Today, each UC is a distinct YAML file. For cases where the same scenario applies to many entities (e.g., "every storage class supports multi-tenant provisioning"), an explicit parameterization mechanism would be useful. Not yet specified.

### 13.2 UC dependencies

A UC may logically depend on another (e.g., uc-hybrid-tenant-onboarding implicitly requires that uc-assert-onboarding-prereqs passes). Today this is expressed through the hybrid type. A more general dependency declaration might be useful if dependency chains grow beyond pairs.

### 13.3 Multi-language assertions

Assertion UCs today reference Python modules. Supporting non-Python assertions (shell scripts, Go binaries, external HTTP endpoints) might be useful for consumers in other language ecosystems. Not yet specified.

### 13.4 UC refinement workflow

Stage 1 (Seed) currently produces a refined UC from a skeletal one. The seed-to-refined workflow and how multiple refinements of the same seed UC relate is not yet formally specified here. Pending `02-stage-model.md` authoring.

### 13.5 Confidence label granularity

Confidence has three labels (`low | medium | high`) mapped onto three of the five DCM bands (`low | medium | very_high`). The `very_low` and `high` bands are not occupied by any default confidence label. Authors can reach those bands via score override, but the absence of a direct label feels asymmetric compared to severity's five-label coverage.

Options for v1.1:

1. Keep three labels; document that `very_low` and `high` bands are reachable only via override
2. Add two more labels: `very_low` (center 10) and `high` (center 70), giving confidence full five-label symmetry with severity
3. Rename the labels to match severity's spacing: `very_low | low | medium | high | very_high`

This schema ships as v1.0 with three labels. The decision to expand is pending real-world consumer feedback.

### 13.6 Cross-system score composition

When DAV is integrated with DCM (ADR-002), DAV findings with severity/confidence scores may participate in DCM policy decisions alongside DCM confidence scores. The exact composition rules — e.g., "actionability = severity × confidence" vs. more elaborate DCM-style multiplier formulas — are not yet defined. Spec 10 (Calibration and Correctness) is the expected home for these rules.

## 14. Changelog

- **2026-04-24** — v1.0 initial. Three UC types (analytical, assertion, hybrid); structured success criteria; scope field; gate_class discipline; explicit validation rules. **Severity and confidence scoring adopted from DCM Doc 21: descriptor-primary nested form, 0-100 score range, five-band taxonomy, five severity labels (advisory/minor/moderate/major/critical), three confidence labels (low/medium/high), shorthand string form permitted when no overrides.**
