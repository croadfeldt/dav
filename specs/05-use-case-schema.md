# DAV Specification 05 — Use Case Schema

**Status:** Active (v1.1 — synced with engine 2026-06-30)
**Audience:** Consumer authoring use cases; anyone integrating with DAV
**Depends on:** None (this is foundational)
**Referenced by:** `02-stage-model.md`, `06-prompt-contract.md`, `07-analysis-output-schema.md`, `08-consumer-integration.md`, `10-calibration-and-correctness.md`

---

## 1. Purpose

A use case (UC) is DAV's unit of architectural question. "Does this architecture support onboarding a new tenant atomically?" is a use case. "Are all referenced document handles valid?" is a use case. UCs are supplied by consumers in YAML files conforming to the schema defined here.

This spec defines:

- The YAML structure of a use case file
- Required and optional fields
- Controlled vocabularies and their relationship to consumer profiles
- The severity, confidence, and priority scoring model (0-100, aligned with DCM's scoring conventions)
- Validation rules DAV applies before a UC is accepted
- File layout conventions for a consumer's UC corpus

This spec is a **consumer contract**. Consumers conform to it; DAV validates conformance and rejects non-conforming UCs with diagnostic errors. The engine's loader is tolerant of unknown keys (warns, does not reject) to accommodate authoring drift, but the fields defined here are the contract.

### 1.1 Relationship to DCM scoring

DAV's severity and confidence scoring is intentionally aligned with DCM's confidence and trust scoring conventions (DCM Doc 21, Information Providers — Advanced). Both systems use:

- **0-100 score range** — allows mathematical composition, sorting, and threshold comparisons
- **Descriptor-primary representation** — a nested object carrying the label (human-handle), derived score (for math), derived band (for policy thresholds), and factor breakdown (for audit)
- **Five-band vocabulary** — `very_low` (0-20), `low` (21-40), `medium` (41-60), `high` (61-80), `very_high` (81-100)

## 2. File layout

Consumer UCs live in a flat or domain-organized directory under the consumer's `dav/use-cases/` path:

```
<consumer-repo>/dav/use-cases/
├── README.md                       Required — describes consumer's UC organization
├── compute/
│   ├── vm-standard-provision.yaml
│   └── vm-provision-with-provider-failure.yaml
├── governance/
│   ├── audit-merkle-tree-verification.yaml
│   └── policy-override-approval.yaml
├── cross-domain/
│   └── tenant-onboarding.yaml
├── data/
│   └── persistent-volume-provision.yaml
└── ...
```

Directory structure is consumer-chosen. DAV does not prescribe domain categorization. The only rules:

1. `README.md` at the UC corpus root is required and must describe:
   - The consumer's UC organization strategy
   - The consumer's controlled vocabulary for domain categories
   - Consumer-specific conventions (if any) that depart from or refine this spec
2. UC files must have `.yaml` extension (`.yml` is rejected for consistency)
3. UC file names should match the UC's handle descriptor, kebab-case
4. Nested subdirectories are permitted for logical grouping

## 3. The UC structure

Every UC has this top-level structure:

```yaml
uuid: <string>                             # Required — unique identifier
handle: <string>                           # Required — category/descriptor slug
scenario:                                  # Required — the scenario block (§4)
  description: <string>
  actor:
    persona: <string>
    profile: <string>
  intent: <string>
  success_criteria: [<string>, ...]
  dimensions:                              # §4.3
    lifecycle_phase: <string>
    resource_complexity: <string>
    policy_complexity: <string>
    provider_landscape: <string>
    governance_context: <string>
    failure_mode: <string>
  profile: <string>
  expected_domain_interactions:             # Optional
    - domain: <string>
      interaction: <string>
generated_by:                              # Required — authoring provenance (§5)
  mode: <string>
  source: <string>
  model: <string | null>
  prompt_version: <string | null>
  timestamp: <ISO-8601>
tags: [<string>, ...]                      # Optional — free-form labels
version: <string>                          # Optional — UC schema version (default "1.0.0")
metadata:                                  # Optional — process metadata (§6)
  author: <string>
  admitted_at: <ISO-8601>
  admitted_dcm_version: <string>
  promoted_from_run: <string | null>
  initial_baseline_path: <string | null>
  note: <string>
  edited: <any>
spec_namespaces: [<string>, ...]           # Optional — per-UC spec scope filter (§7)
priority:                                  # Optional — roadmap weighting (§8)
  label: <string>
  score: <int>
  rationale: <string>
```

### 3.1 `uuid`

Unique identifier for the UC. Must start with `uc-`. Three forms are common:

- **Seed UUID**: `uc-seed-NNN[a-z]?` for human-authored UCs (e.g., `uc-seed-008a`). Letter suffix distinguishes refined variants.
- **Generated UUID**: `uc-<hex12>` for machine-generated UCs (e.g., `uc-a4a4f8def3ca`).
- **Descriptive UUID**: `uc-<kebab-slug>` for named UCs (e.g., `uc-policy-resolution-capability`).

UUIDs are immutable once assigned. Renaming a UC (changing its description or scope substantially) requires a new UUID.

### 3.2 `handle`

A `category/descriptor` slug that determines file path and serves as the human-readable identifier. Must contain exactly one `/` separating the domain category from the descriptor.

Examples: `compute/vm-standard-provision`, `governance/audit-merkle-tree-verification`, `cross-domain/tenant-onboarding`.

The category portion corresponds to the UC's domain and typically matches the subdirectory name.

## 4. Scenario

The `scenario` block describes the architectural question. It is the core of the UC.

### 4.1 `scenario.description`

Free-form narrative describing the architectural scenario to be analyzed. This becomes part of the Stage 2 prompt. Keep it factual and scenario-focused.

Must be non-empty. Recommended length: 1–5 sentences for simple scenarios, up to several paragraphs for complex ones. Descriptions exceeding 2000 characters trigger a warning (usually means too much prescription).

### 4.2 `scenario.actor`

Who initiates the scenario:

```yaml
actor:
  persona: <string>     # Role name (e.g. "application-team-member", "compliance-auditor")
  profile: <string>     # Deployment profile (from consumer profile vocabulary)
```

`persona` must be non-empty. `profile` must be a value from the consumer's profile vocabulary (see §10).

### 4.3 `scenario.intent`

One-sentence statement of what the actor is trying to accomplish. Must be non-empty.

Good: `"Provision a new VM in the standard profile"`
Bad: `"Test VM provisioning"` (too vague; does not state the actor's goal)

### 4.4 `scenario.success_criteria`

A list of plain-English statements the analysis will be evaluated against. Must contain at least one item.

```yaml
success_criteria:
  - VM is created and reachable for the requesting team
  - Applicable (resolved-profile) policies are evaluated before allocation
  - Provisioning is recorded in the audit trail with actor, intent, and outcome
```

### 4.5 `scenario.dimensions`

Six classification dimensions that characterize the scenario's complexity. All six are required. Values must come from the consumer's profile vocabulary (see §10).

```yaml
dimensions:
  lifecycle_phase: new_request           # What lifecycle operation is exercised
  resource_complexity: single_no_deps    # How complex is the resource graph
  policy_complexity: single_gating       # How complex is the policy evaluation
  provider_landscape: single_eligible    # How many providers are in play
  governance_context: standard_governance # What governance regime applies
  failure_mode: happy_path               # What failure scenario is tested
```

These dimensions serve three purposes:
1. **UC classification** — filter and organize UCs by complexity
2. **Prompt parameterization** — dimensions are rendered into the Stage 2 prompt
3. **Coverage analysis** — identify which dimension combinations lack UC coverage

### 4.6 `scenario.profile`

The deployment profile under which the scenario runs. Must be a value from the consumer's profile vocabulary. This is the profile the *scenario* targets, distinct from `actor.profile` (which is the actor's own profile).

### 4.7 `scenario.expected_domain_interactions`

Optional list of expected architectural domain interactions. Each entry names a domain and describes how the scenario exercises it.

```yaml
expected_domain_interactions:
  - domain: policy
    interaction: resolved-profile gating evaluates the request before allocation
  - domain: provider
    interaction: service provider allocates the VM resource
  - domain: audit
    interaction: provisioning event recorded
```

These are author expectations, not authoritative constraints. DAV's analysis may find more or different interactions.

## 5. Generation provenance (`generated_by`)

Required block recording how the UC was created:

```yaml
generated_by:
  mode: authoring                        # Required — generation mode
  source: human-authored                 # Required — generation source
  model: null                            # Optional — LLM model if generated
  prompt_version: seed-1.0               # Optional — prompt version used
  timestamp: '2026-04-20T18:41:39+00:00' # Auto-populated ISO-8601
```

### 5.1 `generated_by.mode`

One of three values:

- **`regression`** — Generated as part of a regression test suite
- **`pr-targeted`** — Generated to test a specific PR or change
- **`authoring`** — Authored (manually or assisted) as a standing UC

### 5.2 `generated_by.source`

One of four values:

- **`corpus`** — Loaded from the existing UC corpus
- **`llm-unguided`** — Generated by an LLM without human guidance
- **`llm-guided`** — Generated by an LLM with human guidance (e.g., UC Assist)
- **`human-authored`** — Written by a human

## 6. Metadata

Optional block for process metadata. The engine's loader is tolerant — unknown keys are warned, not rejected.

```yaml
metadata:
  author: chris@croadfeldt               # Who wrote/last edited this UC
  admitted_at: '2026-04-20T18:41:39+00:00'  # When DAV admitted this UC
  admitted_dcm_version: preview           # Consumer version at admission
  promoted_from_run: null                 # Run ID if promoted from a generated UC
  initial_baseline_path: null             # Path to initial baseline analysis
  note: "free-form annotation"            # Human note
  edited: '2026-06-29 — description'      # Authoring provenance (free-form)
```

All fields are optional. The `edited` field is intentionally free-form (string, boolean, or timestamp) to accommodate different authoring workflows.

## 7. Spec namespaces (`spec_namespaces`)

Optional list of spec-corpus namespace prefixes. When present, the Stage 2 agent restricts MCP grounding to documents whose handle prefix is in this list.

```yaml
spec_namespaces:
  - "udlm/"
  - "dcm/"
```

Empty or missing means no per-UC restriction; the run-wide `DAV_SPEC_NAMESPACES_FILTER` env var still applies as the soft default. This lets one corpus mix UCs that test DCM-only, UDLM-only, and cross-spec scenarios without forcing the operator to pick at run-trigger time.

## 8. Priority (roadmap weighting)

Optional author-set field for roadmap planning. Priority describes the *use case itself* — how much it matters for roadmap planning — not a finding the engine produces.

Priority uses the same descriptor-primary representation as severity and confidence (label + derived score + derived band + factors), so the same policy, sorting, and UI machinery applies uniformly.

### 8.1 Priority labels

| Label | Default score | Band range |
|-------|---------------|------------|
| `critical` | 90 | 81-100 (very_high) |
| `high` | 70 | 61-80 (high) |
| `medium` | 50 | 41-60 (medium) |
| `low` | 20 | 0-40 (very_low + low) |

The `score` is the roadmap weight: consumers sort UCs by `priority.score` descending (highest first). Score-override and band-validation rules are identical to severity — an author-set score must fall within the label's band range.

### 8.2 Priority forms

Shorthand and nested forms both work:

```yaml
priority: high            # shorthand — expands to score 70, band high
```

```yaml
priority:
  label: critical
  score: 95               # override within the critical band (81-100)
  rationale: "blocks cost-mgmt team onboarding"   # stored in factors.override_rationale
```

Unlike severity, priority has no synonym aliases — labels must be exact. Priority is optional: a UC with no `priority` is treated as unranked and sorts last.

## 9. Severity and Confidence Scoring

UC findings carry severity (how bad is it?) and confidence (how sure are we?). Both use a common scoring model aligned with DCM's Doc 21 conventions. These apply to *analysis output* (Stage 2 findings) and *assertion results*, not to the UC input itself.

### 9.1 Representation form

Both use the **descriptor-primary nested form**: a label is authoritative, a score is derived, a band is derived from the score, and factors record provenance.

```yaml
severity:
  label: major
  score: 70
  band: high
  factors:
    base_from_label: 70
    override_rationale: null
```

### 9.2 Severity labels and bands

| Label | Default score | Band | When to use |
|-------|--------------|------|-------------|
| `advisory` | 10 | very_low (0-20) | A suggestion; not blocking |
| `minor` | 30 | low (21-40) | Real issue, not urgent; has workarounds |
| `moderate` | 50 | medium (41-60) | Genuine concern; should be addressed |
| `major` | 70 | high (61-80) | Significant gap; address promptly |
| `critical` | 90 | very_high (81-100) | Severe; blocks related work |

### 9.3 Severity aliases

The LLM may emit confidence-axis labels (`low`, `medium`, `high`) as severity values. The engine normalizes these to canonical severity labels:

| LLM emits | Normalized to |
|-----------|---------------|
| `low` | `minor` |
| `medium` | `moderate` |
| `high` | `major` |

This keeps `advisory` and `critical` reserved for when the model explicitly uses those words.

### 9.4 Confidence labels and bands

| Label | Default score | Band |
|-------|--------------|------|
| `low` | 30 | low (21-40) |
| `medium` | 50 | medium (41-60) |
| `high` | 85 | very_high (81-100) |

### 9.5 Shorthand form

For values that don't need overrides, a shorthand string is permitted:

```yaml
severity: major       # expands to nested form with defaults
confidence: high      # expands to nested form with defaults
```

The engine normalizes shorthand to nested form at parse time.

## 10. Consumer profiles

Controlled vocabularies for enumerable fields (dimensions, profiles, provider types, policy modes) are defined by the **consumer profile**, not hardcoded in this spec. A consumer profile is a YAML file that ships with the consumer's content:

```yaml
framework_name: "DCM (Data Center Management)"
framework_short: "DCM"
consumer_id: "dcm"
schema_version: "1.0"

lifecycle_phases: [new_request, modification, decommission, ...]
resource_complexities: [single_no_deps, hard_dependencies, ...]
policy_complexities: [system_defaults_only, single_gating, ...]
provider_landscapes: [single_eligible, multiple_eligible, ...]
governance_contexts: [no_governance, standard_governance, ...]
failure_modes: [happy_path, provider_failure, ...]
profiles: [minimal, dev, standard, prod, fsi, sovereign]
provider_types: [service, information, meta, auth, peer_dcm, process]
policy_modes: [Internal, External]
```

DAV validates UC dimension values, actor profiles, and scenario profiles against the active consumer profile. When no explicit profile is loaded, DAV uses a built-in generic reference profile.

The consumer profile also provides architectural-context strings (`abstractions_summary`, `provider_summary`, `policy_summary`) that are substituted into the Stage 2 system prompt.

See `consumer_profile.py` for the `ConsumerProfile` dataclass and `examples/dcm-reference-profile.yaml` for the DCM reference.

## 11. Validation rules

DAV validates UC files before accepting them. The engine's loader is **tolerant by design** (operating-model DR §6): unknown keys produce warnings, not hard failures. This accommodates authoring drift. The rules below are the contract; warnings on extras are the safety valve.

### 11.1 Structural rules

1. File parses as valid YAML
2. `uuid` is present, non-empty, starts with `uc-`
3. `handle` is present, non-empty, contains exactly one `/` (`category/descriptor`)
4. `scenario` block is present with all required sub-fields
5. `generated_by` block is present with `mode` and `source`

### 11.2 Scenario rules

1. `scenario.description` is present and non-empty
2. `scenario.intent` is present and non-empty
3. `scenario.success_criteria` is present with ≥ 1 item
4. `scenario.actor.persona` is non-empty
5. `scenario.actor.profile` is in the consumer profile's `profiles` vocabulary
6. `scenario.profile` is in the consumer profile's `profiles` vocabulary
7. All six `scenario.dimensions` fields are present and their values are in the consumer profile's corresponding vocabulary

### 11.3 Generation provenance rules

1. `generated_by.mode` is one of: `regression`, `pr-targeted`, `authoring`
2. `generated_by.source` is one of: `corpus`, `llm-unguided`, `llm-guided`, `human-authored`

### 11.4 Scoring rules

Rules governing severity, confidence, and priority descriptors wherever they appear:

- `severity.label` is one of `advisory | minor | moderate | major | critical`
- `confidence.label` is one of `low | medium | high`
- `priority.label` (if present) is one of `low | medium | high | critical`
- Score (if set) is an integer in 0-100 within the label's band range
- `band` is derived from score; author-set `band` values produce a warning

### 11.5 Warning-only rules (non-fatal)

- `scenario.description` > 2000 characters
- UC has no `metadata` block
- Unknown top-level keys (tolerant loader warns, does not reject)
- Unknown keys inside `generated_by` or `metadata` blocks (tolerant loader warns)

## 12. Example UCs

### 12.1 Analytical UC (standard provision)

```yaml
uuid: uc-seed-001a
handle: compute/vm-standard-provision
scenario:
  description: An application team requests a new virtual machine in the standard
    profile. The platform must run the applicable policy checks before allocation,
    allocate the VM through an eligible service provider, and produce an auditable
    record of the provisioning.
  actor:
    persona: application-team-member
    profile: standard
  intent: Provision a new VM in the standard profile
  success_criteria:
    - VM is created and reachable for the requesting team
    - Applicable (resolved-profile) policies are evaluated before allocation
    - Provisioning is recorded in the audit trail with actor, intent, and outcome
    - The request is idempotent — repeating it does not create duplicate VMs
  dimensions:
    lifecycle_phase: new_request
    resource_complexity: single_no_deps
    policy_complexity: single_gating
    provider_landscape: single_eligible
    governance_context: standard_governance
    failure_mode: happy_path
  profile: standard
  expected_domain_interactions:
    - domain: policy
      interaction: resolved-profile gating evaluates the request before allocation
    - domain: provider
      interaction: service provider allocates the VM resource
    - domain: data
      interaction: resource record created
    - domain: audit
      interaction: provisioning event recorded
generated_by:
  mode: authoring
  source: human-authored
  model: null
  prompt_version: seed-1.0
  timestamp: '2026-04-20T18:41:39.738396+00:00'
tags:
  - compute
  - vm
  - happy-path
  - standard-profile
version: 1.1.0
metadata:
  admitted_at: '2026-04-20T18:41:39.738428+00:00'
  admitted_dcm_version: preview
  author: chris@croadfeldt
```

### 12.2 UC with priority

```yaml
uuid: uc-seed-008a
handle: cross-domain/tenant-onboarding
scenario:
  description: A new tenant is onboarded through the standard onboarding flow.
  actor:
    persona: platform-admin
    profile: standard
  intent: Onboard a new tenant with atomic state transitions
  success_criteria:
    - All seven entities provisioned or none persist
  dimensions:
    lifecycle_phase: new_request
    resource_complexity: compound_service
    policy_complexity: cross_domain_constraint
    provider_landscape: mixed
    governance_context: compliance_gated
    failure_mode: happy_path
  profile: standard
generated_by:
  mode: authoring
  source: human-authored
  timestamp: '2026-04-23T04:45:00+00:00'
tags: [onboarding, tenant]
priority:
  label: critical
  score: 95
  rationale: "blocks Piotr's team onboarding"
```

### 12.3 UC with spec namespaces

```yaml
uuid: uc-udlm-dep-graph
handle: cross-domain/udlm-dependency-graph
scenario:
  description: UDLM models containment, requirement, and shared-fault-domain
    edges as a first-class queryable structure.
  actor:
    persona: architect
    profile: standard
  intent: Validate UDLM dependency graph data model
  success_criteria:
    - Dependency edges are typed and queryable
  dimensions:
    lifecycle_phase: new_request
    resource_complexity: hard_dependencies
    policy_complexity: system_defaults_only
    provider_landscape: single_eligible
    governance_context: standard_governance
    failure_mode: happy_path
  profile: standard
generated_by:
  mode: authoring
  source: human-authored
spec_namespaces:
  - "udlm/"
```

## 13. Non-goals

This schema does not prescribe:

- Domain vocabulary (consumers define via consumer profile)
- Directory layout beyond requiring `README.md` at root (consumers choose)
- Dimension vocabulary (consumers define via consumer profile)
- UC authoring style or tone (consumers develop their own voice)

## 14. Open questions

### 14.1 UDLM-native UC format

The current dimensions use DAV-invented vocabulary (`lifecycle_phase`, `resource_complexity`, etc.) that partially overlaps UDLM primitives (entity types, lifecycle operations, policy types, provider types). A future version may define UC format using UDLM primitives directly, making UCs a first-class UDLM entity usable at design-time, spec-time, validation-time, and operations-time. Pending design review.

### 14.2 Parameterized UCs

For cases where the same scenario applies to many entities, an explicit parameterization mechanism would be useful. Not yet specified.

### 14.3 UC dependencies

A more general dependency declaration between UCs (beyond what hybrid type provides) might be useful if dependency chains grow beyond pairs.

### 14.4 Confidence label granularity

Confidence has three labels mapped onto three of the five DCM bands. The `very_low` and `high` bands are not occupied by any default confidence label. Pending real-world consumer feedback on whether to expand.

## 15. Changelog

- **2026-06-30** — v1.1 synced with engine. Structure updated to match `use_case_schema.py`: `uuid` (not `use_case_uuid`), `handle`, top-level `scenario` with `actor`/`intent`/`dimensions`/`expected_domain_interactions`, `generated_by`, top-level `tags`, `version`, `metadata` with process fields, `spec_namespaces`, `priority`. Removed aspirational concepts not implemented in engine (`uc_type`, `gate_class`, `domain` as separate field, `analytical:{}` wrapping, assertion/hybrid UC types). Consumer profile vocabulary system documented. Tolerant loader behavior documented (operating-model DR §6). Severity alias normalization documented.
- **2026-04-24** — v1.0 initial. Three UC types (analytical, assertion, hybrid); structured success criteria; scope field; gate_class discipline; explicit validation rules. Severity and confidence scoring adopted from DCM Doc 21.
