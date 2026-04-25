# DAV Specification 07 — Analysis Output Schema

**Status:** Proposed (v1.0)
**Audience:** Anyone consuming DAV output — reviewers, tool integrators, CI systems, consumer developers
**Depends on:** `05-use-case-schema.md` (defines inputs; this spec defines outputs)
**Referenced by:** `02-stage-model.md`, `03-determinism-invariants.md`, `04-three-modes.md`, `08-consumer-integration.md`, `10-calibration-and-correctness.md`

---

## 1. Purpose

Every DAV run produces an Analysis — a structured YAML artifact describing what the analysis found. This spec defines the shape of an Analysis: its fields, their types, their semantics, and the constraints DAV guarantees on its output.

This is the **read-side consumer contract**, complementing the write-side contract in `05-use-case-schema.md`. Together they define DAV's I/O surface. Tools that consume DAV output — CI gates, review UIs, differential analyzers, dashboards — conform to this schema.

Breaking changes to this schema require a DAV major version bump. Consumers declare their target schema version in `dav-version.yaml`; DAV emits output conforming to that version.

### 1.1 Relationship to Use Case Schema

The Analysis is DAV's *response* to a use case. UC fields flow through to the Analysis:

- A UC's `use_case_uuid` appears verbatim in the Analysis
- A UC's `scope` is mirrored in the Analysis
- A UC's `analytical.expected_components` influences (but does not dictate) the Analysis's `components_required`
- Assertion UC results populate a different shape; see §10

Severity and confidence representation is inherited directly from `05-use-case-schema.md` §6. Every field in the Analysis that carries these concepts uses the same descriptor-primary nested form: label, score, band, factors.

### 1.2 One schema, three modes of production

DAV runs in three modes (see `04-three-modes.md`). The Analysis schema is the same across modes; what differs is population:

- **Reproduce mode (N=1)**: Single sample. `sample_annotations` is null. Fields reflect the one sample's output directly.
- **Verification mode (N≥2, merged)**: Multiple samples merged by ensemble consensus. `sample_annotations` is populated with per-field vote counts and consensus evidence.
- **Explore mode (N≥2, unmerged)**: Emits per-sample Analyses (each conforming to this schema with `sample_annotations` null) plus a variance report. The variance report is a separate artifact (not an Analysis) defined elsewhere.

A reader of an Analysis can tell which mode produced it by inspecting `analysis_metadata.mode` and whether `sample_annotations` is populated.

## 2. Top-level structure

An Analysis is a YAML document with this top-level shape:

```yaml
use_case_uuid: <string>                     # From the UC; required
analysis_metadata: <object>                 # Run metadata; required
scope: <object | null>                      # Mirrors UC scope; optional

components_required: <list>                 # Findings; always present (may be empty)
data_model_touched: <list>
capabilities_invoked: <list>
provider_types_involved: <list>
policy_modes_required: <list>
gaps_identified: <list>

summary: <object>                           # Verdict and overall confidence; required

tool_call_trace: <list>                     # Run-level diagnostic; required
sample_annotations: <object | null>         # Ensemble evidence; null outside verification mode
```

All top-level keys are required in the emitted document except where noted. Missing required keys indicate a malformed Analysis and should be rejected by consumers.

## 3. `use_case_uuid`

Exact copy of the UC's `use_case_uuid` field. Immutable across the analysis.

If the run was triggered without a UC (e.g., an ad-hoc query), DAV synthesizes a UUID of the form `adhoc-<short-uuid>` and emits it here. Ad-hoc analyses are valid but cannot participate in calibration workflows.

## 4. `analysis_metadata`

Metadata about the run itself. Required fields:

```yaml
analysis_metadata:
  model: <string>                           # Inference model identifier as reported by endpoint
  endpoint_url: <string>                    # URL of the inference endpoint (for audit)
  timestamp: <ISO-8601 string>              # When the analysis completed
  engine_version: <string>                  # DAV engine version (e.g., "v1.0.0")
  engine_commit: <string>                   # Git SHA or equivalent for reproducibility
  consumer_version: <string>                # Consumer content version (e.g., "dcm@abc1234")
  mode: verification | reproduce | explore  # Which mode produced this
  
  run_id: <string>                          # Unique run identifier
  tool_call_count: <integer>                # Total tool calls across all samples
  total_tokens: <integer>                   # Total tokens used across all samples
  wall_time_seconds: <number>               # End-to-end wall time
  
  sample_count: <integer>                   # N for this run; 1 for reproduce, ≥1 for others
  sample_seeds: <list[integer] | null>      # Seeds used; null if not applicable
```

Optional fields:

```yaml
  stage: <string>                           # Stage that produced this (e.g., "stage2_analyze")
  parent_run_id: <string>                   # If this run was part of a larger workflow
  inference_topology: <string>              # Free-form hint for cross-topology tracking
                                            # (e.g., "layer-split-q8-dual-r9700")
```

### 4.1 Semantics

- **`run_id`** is globally unique. Format: `<mode>-<short-uuid>` where short-uuid is 8 hex chars (e.g., `verification-abc12345`).
- **`tool_call_count`** is the sum across all samples in verification/explore modes, matching the aggregate cost. For per-sample counts, see `sample_annotations.per_sample`.
- **`total_tokens`** follows the same aggregation rule.
- **`wall_time_seconds`** is end-to-end including all samples but excluding setup (pod spin-up, corpus load). It's the "how long did the analysis take" number a user sees.
- **`engine_commit`** is critical for Tier 1 determinism audits. Two runs with the same engine_commit should produce byte-identical output modulo timestamp and run_id.
- **`inference_topology`** is a hint consumers can use to track analyses across topology changes. DAV doesn't interpret it; it's free-form audit metadata.

## 5. `scope`

Mirrors the UC's `scope` field. If the UC declared `scope`, it's copied here verbatim. If the UC did not declare `scope`, this field is `null` (equivalent to `{type: global}`).

```yaml
scope:
  type: global | tenant_profile | environment | component | capability
  value: <string | null>                    # Null for global
```

Consumers can filter analyses by scope when querying historical runs.

## 6. Findings — the six lists

The core of an Analysis is six lists, each a set of structured findings. All are required; any may be empty.

### 6.1 Shared finding structure

Every finding shares a base shape:

```yaml
- id: <string>                              # Domain-specific identifier; stable across runs
  confidence: <ConfidenceDescriptor>        # Per spec 05 §6
  rationale: <string>                       # 1-3 sentences of plain-language justification
  spec_refs: <list[string]>                 # Spec sections supporting this finding
```

Type-specific fields are layered on top per §6.2–§6.7.

#### 6.1.1 `id`

Finding identifier. Stable and descriptive. Examples: `tenant_boundary`, `auth_provider`, `policy_profile_binding`, `audit_stream`.

IDs are consumer-domain-specific. DAV does not validate them against a controlled vocabulary; the consumer's spec corpus and UCs imply the natural namespace. Two different analyses of the same UC on the same spec revision should produce the same IDs for the same underlying concepts (within the limits of LLM non-determinism, which `sample_annotations` surfaces).

#### 6.1.2 `confidence`

ConfidenceDescriptor per `05-use-case-schema.md` §6. Can appear in shorthand (`confidence: high`) or nested form. DAV normalizes to nested form on output:

```yaml
confidence:
  label: high
  score: 85
  band: very_high
  factors:
    base_from_label: 85
    override_rationale: null
```

#### 6.1.3 `rationale`

Plain-language explanation of why this finding applies. 1-3 sentences. References to spec sections should use the `spec_refs` field, not inline prose — rationale is about *why*, spec_refs are about *where*.

Good: `"The tenant entity is created with group_class='tenant_boundary' during onboarding, which establishes the identity scope for all subsequent policy evaluations."`

Bad: `"Doc 49 §9.1 says... and then Doc 14 §3.4 says..."` (put the refs in `spec_refs`)

#### 6.1.4 `spec_refs`

List of spec section references that support this finding. Format is consumer-defined; DAV doesn't validate the format but consumers typically use `<document_handle>/<section_title>` (e.g., `49-implementation-specifications/9.1 New Tenant Onboarding Flow`).

An empty `spec_refs` list is permitted but a warning signal: a finding without spec support is either hallucinated or relies on unstated general knowledge. Reviewers should scrutinize these.

### 6.2 `components_required`

Architectural components the UC's scenario requires.

```yaml
components_required:
- id: tenant_boundary
  role: <string>                            # One-sentence description of the component's role
  confidence: <ConfidenceDescriptor>
  rationale: <string>
  spec_refs: [<string>, ...]
- ...
```

`role` is unique to this finding type. It's a short description of what the component does in the scenario — not a definition of the component generally.

### 6.3 `data_model_touched`

Data model entities the UC's scenario reads from or writes to.

```yaml
data_model_touched:
- entity: <string>                          # Entity name (e.g., "tenant", "resource_group")
  fields_accessed: <list[string]>           # Specific fields touched
  operations: <list[string]>                # Operations performed (e.g., ["read", "write"])
  confidence: <ConfidenceDescriptor>
  rationale: <string>
  spec_refs: [<string>, ...]
- ...
```

Note: `entity` is used instead of `id` for this finding type because data model entities have a canonical name in the spec, which is more natural than a synthetic identifier.

### 6.4 `capabilities_invoked`

Capabilities the UC's scenario requires from the architecture.

```yaml
capabilities_invoked:
- id: tenant_provisioning
  usage: <string>                           # One-sentence description of how the capability is used
  confidence: <ConfidenceDescriptor>
  rationale: <string>
  spec_refs: [<string>, ...]
- ...
```

`usage` is unique to this finding type and parallel to `components_required.role`.

### 6.5 `provider_types_involved`

Types of providers the scenario involves. Provider type vocabulary is consumer-defined (e.g., DCM uses `service | information | auth | peer_dcm | process`).

```yaml
provider_types_involved:
- type: <string>                            # Consumer-defined provider type
  role: <string>                            # One-sentence description of this type's role
  confidence: <ConfidenceDescriptor>
  rationale: <string>                       # Optional for this finding type
  spec_refs: [<string>, ...]                # Optional
- ...
```

`type` replaces `id` because provider types are a fixed vocabulary, not synthetic identifiers. `rationale` and `spec_refs` are optional because provider type involvement is often obvious from component and capability findings and doesn't need independent justification.

### 6.6 `policy_modes_required`

Policy evaluation modes the scenario requires. Vocabulary is consumer-defined; DCM uses `Internal | External`.

```yaml
policy_modes_required:
- mode: <string>                            # Consumer-defined policy mode
  rationale: <string>
  spec_refs: [<string>, ...]
  confidence: <ConfidenceDescriptor>
- ...
```

`mode` replaces `id` for the same reason as §6.5.

### 6.7 `gaps_identified`

Gaps between what the UC expects and what the spec provides. This is the finding type that carries `severity` in addition to `confidence`.

```yaml
gaps_identified:
- description: <string>                     # What's missing or incorrect
  severity: <SeverityDescriptor>            # Per spec 05 §6
  confidence: <ConfidenceDescriptor>
  rationale: <string>
  recommendation: <string>                  # What the consumer should do
  spec_refs_consulted: <list[string]>       # Where DAV looked (but didn't find)
  spec_refs_missing: <string | null>        # What DAV expected to find
- ...
```

Gaps don't use `id` because gap identification is inherently unstable across runs (different LLM samples may describe the same gap with different wording). Dedup happens at ensemble-merge time via description-normalization.

#### 6.7.1 `severity` on gaps

SeverityDescriptor per spec 05 §6. Shorthand or nested form. On output, DAV emits the nested form. Severity labels on gaps typically range `advisory` to `critical`; use the rubric in spec 05 §6.2.

#### 6.7.2 `recommendation`

Concrete, actionable. What should the consumer do? Typically one of:

- "Add a section on X to document Y"
- "Clarify the relationship between A and B"
- "Add an ADR documenting the decision about Z"
- "Reconcile the conflict between section X and section Y"

Bad recommendations are vague ("improve documentation") or prescriptive beyond scope ("rewrite the spec").

#### 6.7.3 `spec_refs_consulted` vs `spec_refs_missing`

When DAV identifies a gap, it usually searched for something and didn't find it. `spec_refs_consulted` lists what it looked at; `spec_refs_missing` describes what it expected to find but didn't.

Example:

```yaml
- description: Atomic onboarding composition is not explicitly addressed in the spec.
  severity:
    label: advisory
    score: 15
    band: very_low
    factors:
      base_from_label: 10
      override_rationale: "Score nudged up slightly — the gap affects audit semantics"
  confidence: medium
  rationale: >
    The onboarding flow in Doc 49 §9.1 describes 7 provisioning steps but does not
    specify whether partial-completion rollback is guaranteed.
  recommendation: >
    Add a section to Doc 49 describing atomic onboarding composition, including
    compensation semantics if step N fails after steps 1..N-1 have committed.
  spec_refs_consulted:
    - "49-implementation-specifications/9.1 New Tenant Onboarding Flow"
    - "07-service-dependencies/§8 Compound Service Compensation"
  spec_refs_missing: "Atomic onboarding composition in 49-implementation-specifications"
```

## 7. `summary`

The overall verdict and confidence. Required.

```yaml
summary:
  verdict: supported | partially_supported | not_supported
  overall_confidence: <ConfidenceDescriptor>
  notes: <string>                           # 1-5 sentences summarizing findings
```

### 7.1 `verdict`

Three values, categorical (not scored):

- **`supported`**: The architecture supports the UC's scenario. No critical or major gaps.
- **`partially_supported`**: The architecture supports the core scenario but has gaps that a reviewer should consider. Typically one or more `major`/`moderate` gaps.
- **`not_supported`**: The architecture does not support the UC's scenario. Typically one or more `critical` gaps, or the UC's expected components/capabilities are largely absent.

Verdict is NOT derived automatically from severity scores — it's the model's (or ensemble's) judgment informed by the full analysis. Two analyses can have similar severity scores but different verdicts based on which components or capabilities are present.

See `10-calibration-and-correctness.md` for the relationship between verdict buckets and severity aggregates.

### 7.2 `overall_confidence`

ConfidenceDescriptor per spec 05 §6. How confident is DAV in the verdict?

In verification mode, this is the minimum confidence across samples (most conservative).

### 7.3 `notes`

1-5 sentence plain-language summary of the analysis. Written for a human reviewer who wants to understand the outcome without reading every finding.

Good: `"DCM supports tenant onboarding with proper identity boundary, quota application, and audit trail. Two advisory gaps remain: atomic onboarding composition is not explicitly specified, and audit record composition for onboarding events is implicit. Neither blocks the scenario."`

Bad: `"Mostly supported but some gaps."` (too vague)

## 8. `tool_call_trace`

Diagnostic list of every tool call made during the run. This is the reviewer's window into what the LLM agent actually did — what it searched for, what it read, where it went wrong if the analysis is surprising.

```yaml
tool_call_trace:
- tool: <string>                            # Tool name (e.g., "search_docs", "get_document_section")
  args: <object>                            # Tool arguments
  result_summary: <string>                  # First ~500 chars of the tool result
  purpose: <string>                         # Free-form description (e.g., "turn 0")
- ...
```

In verification mode, the tool_call_trace comes from a single representative sample — specifically, the sample whose verdict matches the consensus verdict. This gives reviewers a coherent audit trail even when the final verdict was voted.

In explore mode, per-sample tool_call_traces are preserved in each per-sample YAML, not merged.

### 8.1 `result_summary`

Bounded to approximately 500 characters. If the tool returned more, it's truncated with `...` suffix. Full tool results are not preserved in the Analysis — they can be reconstructed from the tool call trace by re-running if needed.

### 8.2 `purpose`

Typically `"turn N"` where N is the agent's turn number. May also contain richer descriptions if the agent framework populates them (e.g., `"turn 5 — searching for policy binding semantics"`).

## 9. `sample_annotations`

Present and populated only for verification-mode runs with N≥2 samples. `null` for reproduce mode and for pre-merge explore-mode samples.

```yaml
sample_annotations:
  sample_count: <integer>
  sample_seeds: <list[integer]>
  
  verdict_votes: <object>                   # {verdict: count}
  verdict_tied: <boolean>                   # True if consensus was chosen via tiebreaker
  
  per_sample:                               # Optional — per-sample resource usage
    - seed: <integer>
      tool_call_count: <integer>
      total_tokens: <integer>
      wall_time_seconds: <number>
      verdict: <string>
      confidence: <ConfidenceDescriptor>
  
  component_consensus: <object>             # {component_id: "N/M" agreement count}
  capability_consensus: <object>            # {capability_id: "N/M"}
  data_model_consensus: <object>            # {entity: "N/M"}
  provider_type_consensus: <object>         # {type: "N/M"}
  policy_mode_consensus: <object>           # {mode: "N/M"}
  gap_consensus: <object>                   # {truncated_description: "N/M"}
```

### 9.1 Reading consensus annotations

`"component_consensus": {"tenant_boundary": "3/3"}` means: 3 of 3 samples identified `tenant_boundary` as a required component. Full consensus.

`"gap_consensus": {"atomic onboarding composition...": "2/3"}` means: 2 of 3 samples identified this gap. Partial consensus. The gap appears in the merged output (union semantics), but its annotation flags it as less than unanimous.

A reviewer scanning an Analysis can immediately identify which findings are robust (N/N) and which are speculative (1/N).

### 9.2 Verdict voting semantics

`verdict_votes` records the raw vote distribution. `verdict_tied` is true when multiple verdict buckets tied at the top count, in which case the ensemble merger chose the more conservative verdict (a tie between `supported` and `partially_supported` resolves to `partially_supported`; a tie between `partially_supported` and `not_supported` resolves to `not_supported`).

A tied verdict downgrades `overall_confidence` to at most `medium`, per ensemble merge rules. This is recorded in the confidence descriptor's `factors`:

```yaml
overall_confidence:
  label: medium
  score: 50
  band: medium
  factors:
    base_from_label: 50
    override_rationale: "Downgraded from high due to verdict tie in ensemble"
```

## 10. Analyses from assertion UCs

Assertion UCs produce an Analysis with a simpler shape than analytical UCs. The Analysis still conforms to §2's top-level structure, but most finding lists are empty, and a new `assertion_result` field is present.

```yaml
use_case_uuid: uc-assert-001
analysis_metadata: <...>
scope: <...>

components_required: []
data_model_touched: []
capabilities_invoked: []
provider_types_involved: []
policy_modes_required: []

gaps_identified:                            # Populated only on assertion failure
- description: "3 UC-referenced doc handles failed to resolve"
  severity:
    label: major
    score: 75
    band: high
    factors:
      base_from_label: 70
      override_rationale: "Severity elevated — broken handles compromise corpus integrity"
  confidence:
    label: high
    score: 85
    band: very_high
    factors:
      base_from_label: 85
      override_rationale: null
  rationale: >
    Assertion check_all_uc_handles_resolve found 3 doc handles in the UC corpus
    that do not resolve to existing documents.
  recommendation: >
    Fix the broken references in the affected UCs. See assertion_result.details
    for the specific handles.
  spec_refs_consulted: []
  spec_refs_missing: null

summary:
  verdict: not_supported                    # Assertion failed; UC's precondition not met
  overall_confidence:
    label: high
    score: 85
    band: very_high
    factors:
      base_from_label: 85
      override_rationale: null
  notes: >
    Assertion UC uc-assert-001 failed: 3 doc handles could not be resolved.
    Detailed list in assertion_result.details.

tool_call_trace: []                         # No tool calls for assertion UCs

sample_annotations: null                    # Assertions are deterministic; no sampling

assertion_result:                           # New field specific to assertion UCs
  passed: false
  diagnostic: "3 doc handle(s) failed to resolve."
  details:
    unresolved:
      - uc: cross_domain/tenant-onboarding.yaml
        handle: "50-retired-section"
      - uc: data/persistent-volume-provision.yaml
        handle: "11-storage-providers/Deprecated Section"
      - uc: cross_domain/federation-peering.yaml
        handle: "not-a-real-doc"
  severity: {label: major, score: 75, band: high, factors: {...}}
  confidence: {label: high, score: 85, band: very_high, factors: {...}}
  assertion_module: dcm.dav.assertions.handle_resolution
  assertion_function: check_all_uc_handles_resolve
  wall_time_seconds: 0.124
```

Semantics for assertion-UC Analyses:

- On pass (`assertion_result.passed: true`), `gaps_identified` is empty and `verdict` is `supported`
- On fail, a single gap is synthesized from the assertion result, and `verdict` is `not_supported`
- `tool_call_trace` is empty — assertions don't use LLM tool calls
- `sample_annotations` is null — assertions are deterministic
- `components_required`, `data_model_touched`, etc. are empty — assertions don't do architectural analysis
- `confidence` on assertion results is typically `high` because the finding is deterministic, but may be `medium` if the assertion itself depends on heuristics

Hybrid UCs produce Analyses with both the assertion shape (§10) and the analytical shape (§6–§8) populated. If the assertion portion fails, the analytical portion is skipped and its finding lists are empty.

## 11. JSON Schema (machine-validatable)

A JSON Schema definition matching this spec ships at `dav/specs/schemas/analysis.schema.json`. Tools can validate Analyses programmatically:

```bash
python -m dav.tools.validate_analysis \
  --schema dav/specs/schemas/analysis.schema.json \
  --analysis /tmp/analysis-uc008.yaml
```

The JSON Schema is the authoritative machine-readable version of this spec. If the prose here and the schema disagree, the schema wins for validation purposes; the disagreement should be filed as a spec bug.

JSON Schema version: draft-2020-12.

*The schema file ships separately from this markdown spec.*

## 12. Differential analyses

A common operation is comparing two Analyses — for regression gating, trend tracking, or impact assessment.

DAV ships `dav analysis diff <before.yaml> <after.yaml>` producing a structured diff. The diff output is a separate schema (not defined here) but the important invariants:

- Diffs are computed at the **field level**, not byte level (timestamps and run_ids are elided)
- Diffs distinguish **verdict bucket transitions** (critical for regression gating) from **score drift** (minor) from **consensus drift** (noise)
- A regression is canonically defined as: `verdict` moved from `supported`/`partially_supported` toward `not_supported`, OR `overall_confidence.band` dropped by 2+ levels, OR any gap's severity moved up to `critical`

See `03-determinism-invariants.md` (pending authoring) for the full regression-gating algorithm.

## 13. Pretty-print and summary forms

DAV emits Analyses as YAML by default. Alternative forms:

- `dav analysis show <file>` — colored, paginated human-friendly view
- `dav analysis summary <file>` — 10-line TL;DR (verdict, confidence, counts, top 3 gaps)
- `dav analysis markdown <file>` — markdown rendering for inclusion in documentation
- `dav analysis html <file>` — HTML rendering for the Review Console

These are render-only; the YAML is the canonical form.

## 14. Validation rules

DAV validates Analyses before emitting them. Runtime validation errors are bugs in DAV (not consumer errors), but they're listed here so external tools can implement the same rules.

### 14.1 Structural rules

1. YAML parses
2. All required top-level keys present (§2)
3. `analysis_metadata.mode` is one of `verification | reproduce | explore`
4. `analysis_metadata.sample_count ≥ 1`
5. If `mode == reproduce`, `sample_count == 1` and `sample_annotations` is null
6. If `mode == verification` and `sample_count > 1`, `sample_annotations` is populated
7. `summary.verdict` is one of `supported | partially_supported | not_supported`

### 14.2 Scoring rules

All severity and confidence descriptors in the Analysis conform to `05-use-case-schema.md` §6 and §9.4. Specifically:

- Label in valid vocabulary
- Score in 0-100
- Score within the label's band
- Band auto-populated correctly

### 14.3 Referential integrity

- `spec_refs` values that look like spec handles resolve against the consumer's spec corpus (warning, not error — a missing reference might be the exact reason a gap was flagged)
- `sample_annotations.sample_seeds` length matches `sample_count`
- `sample_annotations.component_consensus` keys are a subset of `components_required[].id` values (same for other consensus dicts)

### 14.4 Logical consistency

- If any gap has `severity.label == critical`, `summary.verdict` should not be `supported` (warning, not error)
- If `sample_annotations.verdict_tied == true`, `overall_confidence.factors.override_rationale` should mention the tie (warning)

## 15. Non-goals

This schema does not define:

- **Diff output format** — separate schema, defined alongside `dav analysis diff`
- **Variance report format** — separate schema for explore mode
- **Trend-tracking dashboard format** — separate, not DAV-proper
- **Real-time streaming** — Analyses are emitted at end-of-run; streaming partial outputs is out of scope for this schema
- **Localization** — rationale and notes are emitted in the prompt's language (typically English)

## 16. Example Analyses

### 16.1 Analytical UC, verification mode (ensemble)

```yaml
use_case_uuid: uc-seed-008a
analysis_metadata:
  model: qwen
  endpoint_url: "http://vis.roadfeldt.com:8000/v1"
  timestamp: "2026-04-24T04:50:26.807853+00:00"
  engine_version: "v1.0.0"
  engine_commit: "b7f36ada63b1c9..."
  consumer_version: "dcm@abc1234"
  mode: verification
  run_id: verification-a3f7e120
  tool_call_count: 58                       # 3 samples × ~19 turns each
  total_tokens: 287000
  wall_time_seconds: 812.5
  sample_count: 3
  sample_seeds: [42, 123, 456]
  inference_topology: "layer-split-q8-dual-r9700"

scope:
  type: tenant_profile
  value: fsi

components_required:
- id: tenant_boundary
  role: Represents the new tenant's identity boundary.
  confidence:
    label: high
    score: 90
    band: very_high
    factors:
      base_from_label: 85
      override_rationale: "Appeared in all 3 samples with identical role description"
  rationale: >
    The tenant entity is created during onboarding with group_class='tenant_boundary',
    which establishes identity scope for all subsequent policy evaluations.
  spec_refs:
    - "49-implementation-specifications/9.1 New Tenant Onboarding Flow"
# ... (other components, capabilities, data_model, etc.) ...

gaps_identified:
- description: Atomic onboarding composition is not explicitly addressed in the spec.
  severity:
    label: advisory
    score: 15
    band: very_low
    factors:
      base_from_label: 10
      override_rationale: "Slightly elevated — affects audit semantics for sovereign deployments"
  confidence:
    label: medium
    score: 50
    band: medium
    factors: {base_from_label: 50, override_rationale: null}
  rationale: >
    The onboarding flow describes 7 provisioning steps but does not specify
    partial-completion rollback semantics.
  recommendation: >
    Add a section to Doc 49 describing atomic onboarding composition.
  spec_refs_consulted:
    - "49-implementation-specifications/9.1 New Tenant Onboarding Flow"
    - "07-service-dependencies/§8 Compound Service Compensation"
  spec_refs_missing: "Atomic onboarding composition in 49-implementation-specifications"

summary:
  verdict: supported
  overall_confidence:
    label: high
    score: 82
    band: very_high
    factors:
      base_from_label: 85
      override_rationale: "Minimum across 3 samples was high; one sample at medium-high"
  notes: >
    DCM supports tenant onboarding with identity boundary, quota application, auth
    configuration, and audit trail. Two advisory gaps remain. Verdict 'supported'
    on 3/3 samples; consensus is robust.

tool_call_trace:
  # ... trace from the sample whose verdict matched consensus ...

sample_annotations:
  sample_count: 3
  sample_seeds: [42, 123, 456]
  verdict_votes:
    supported: 3
    partially_supported: 0
    not_supported: 0
  verdict_tied: false
  per_sample:
    - seed: 42
      tool_call_count: 18
      total_tokens: 92000
      wall_time_seconds: 265.1
      verdict: supported
      confidence:
        label: high
        score: 85
        band: very_high
        factors: {base_from_label: 85, override_rationale: null}
    - seed: 123
      tool_call_count: 22
      total_tokens: 105000
      wall_time_seconds: 295.3
      verdict: supported
      confidence:
        label: high
        score: 82
        band: very_high
        factors:
          base_from_label: 85
          override_rationale: "Sample exploration found one borderline-missing section"
    - seed: 456
      tool_call_count: 18
      total_tokens: 90000
      wall_time_seconds: 252.1
      verdict: supported
      confidence:
        label: high
        score: 90
        band: very_high
        factors: {base_from_label: 85, override_rationale: null}
  component_consensus:
    tenant_boundary: "3/3"
    quota_profile: "3/3"
    auth_provider: "3/3"
    audit_stream: "3/3"
    policy_profile: "2/3"
    policy_profile_binding: "1/3"
  capability_consensus:
    tenant_onboarding: "3/3"
    policy_binding: "2/3"
    audit_recording: "3/3"
  gap_consensus:
    "atomic onboarding composition is not expli...": "3/3"
    "audit record composition for onboarding ev...": "1/3"
```

### 16.2 Assertion UC, passing

```yaml
use_case_uuid: uc-assert-001
analysis_metadata:
  model: null                               # Assertions don't use inference
  endpoint_url: null
  timestamp: "2026-04-24T15:30:12.001+00:00"
  engine_version: "v1.0.0"
  engine_commit: "b7f36ada..."
  consumer_version: "dcm@abc1234"
  mode: reproduce
  run_id: reproduce-a1b2c3d4
  tool_call_count: 0
  total_tokens: 0
  wall_time_seconds: 0.118
  sample_count: 1
  sample_seeds: null

scope:
  type: global

components_required: []
data_model_touched: []
capabilities_invoked: []
provider_types_involved: []
policy_modes_required: []
gaps_identified: []

summary:
  verdict: supported
  overall_confidence:
    label: high
    score: 85
    band: very_high
    factors: {base_from_label: 85, override_rationale: null}
  notes: "Assertion passed: all UC-referenced doc handles resolve."

tool_call_trace: []
sample_annotations: null

assertion_result:
  passed: true
  diagnostic: "All UC-referenced doc handles resolve."
  details: null
  severity:
    label: advisory
    score: 10
    band: very_low
    factors: {base_from_label: 10, override_rationale: null}
  confidence:
    label: high
    score: 85
    band: very_high
    factors: {base_from_label: 85, override_rationale: null}
  assertion_module: dcm.dav.assertions.handle_resolution
  assertion_function: check_all_uc_handles_resolve
  wall_time_seconds: 0.118
```

## 17. Evolution and versioning

Analysis schema version pairs with Use Case Schema version. A DAV v1.x release targets schema v1.x for both; consumers upgrade in lockstep.

Version history:

- **v1.0** — initial. Six finding lists, severity/confidence descriptors from UC spec, sample_annotations for ensemble runs, assertion_result for assertion UCs.

## 18. Open questions

### 18.1 Severity aggregation onto verdict

The relationship between `gaps_identified[].severity` and `summary.verdict` is currently implicit (LLM judgment). `10-calibration-and-correctness.md` will define an explicit formula. When that lands, this schema may add a `verdict_derivation` field recording how verdict was computed from gaps.

### 18.2 Streaming / partial outputs

Long-running analyses (explore mode with N=10) could benefit from streaming partial results. Not yet specified. Would require schema additions for partial-state Analyses.

### 18.3 Cross-UC findings

Some findings are relevant across multiple UCs (e.g., "DCM uses UUIDs inconsistently across the spec"). Today each UC's Analysis contains only findings for that UC. A cross-UC roll-up format is a future concern.

### 18.4 Diff output schema

Differential analyses (`dav analysis diff`) produce their own structured output. That schema is not yet defined here. Likely home: a companion spec or an appendix to this one.

## 19. Changelog

- **2026-04-24** — v1.0 initial. Six finding lists, severity/confidence descriptors inherited from spec 05 §6, sample_annotations for ensemble runs, assertion_result for assertion UCs, tool_call_trace for audit, validation rules and JSON Schema pointer.
