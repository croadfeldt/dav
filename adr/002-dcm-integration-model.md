# ADR-002 — DCM Integration Model: DAV as a DCM-Managed Capability

**Status:** Proposed (forward-looking, deferred 6-12 months)
**Date:** 2026-04-25
**Author:** Chris Roadfeldt
**Related:** ADR-001 (DAV is a Consumer-Agnostic Framework)

---

## 1. Context

ADR-001 establishes DAV as a consumer-agnostic framework with DCM as its first consumer. Under that ADR, DCM *uses* DAV — a DAV deployment runs in its own namespace, DCM's engineers invoke DAV to validate architectural changes, DAV produces analyses that inform DCM's evolution. This is the basic dependency relationship: DAV is a tool DCM consumes.

That relationship is sound and sufficient. But it leaves value on the table. DCM is itself a framework for managing capabilities in sovereign clouds. DCM has a capability model, a policy engine, an orchestration engine, an audit trail, and a provider model. Validation is a natural fit within that capability model — architectures need validation, and DCM's users (platform operators) are exactly the audience that would benefit from "validate this proposed change" as a first-class DCM capability.

This ADR describes a future integration model where DAV is *also* registered as a DCM-managed capability: a `process` provider that DCM users can invoke through DCM's standard interfaces. Under this integration:

- DAV remains a standalone framework (ADR-001 is not superseded)
- DCM gains a built-in validation capability backed by DAV
- The integration is optional — DAV without DCM and DCM without DAV both remain valid
- DCM users can compose validation into larger workflows

This is an embedded-capability integration, not a fusion of the two systems.

## 2. Decision

Define a DAV-as-capability integration for DCM in which DAV is registered as a DCM-managed `process` provider. The integration is specified here; implementation is deferred until DAV and DCM are each mature enough to make the integration valuable (estimated 6-12 months out).

## 3. Integration Model

### 3.1 Provider type

DAV is registered as a **`process` provider** in DCM's provider model. It is not a `service` provider (services deliver end-user-consumable resources; validation is an operational workflow) nor an `information` provider (information providers return static data; validation produces analyses through computation). The `process` type captures workflow-driven capabilities with meaningful internal state.

### 3.2 Capability declaration

The DAV capability is declared in a DCM capability manifest:

```yaml
capability:
  id: architecture-validation
  handle: system/capability/validation/dav
  provider_type: process
  provider_handle: system/provider/process/dav
  description: >
    Architecture validation via the DAV framework. Produces structured
    analyses (components, capabilities, data model impact, gaps) for
    proposed architectural changes. Supports verification (CI-gradient
    gating), reproduce (single-sample debugging), and explore (variance
    surveys for authoring) modes.
  capability_class: operational
  sovereignty: configurable           # Can run fully on-prem with local inference
  dependencies:
    required:
      - system/capability/inference-endpoint
      - system/capability/mcp-server
    optional:
      - system/capability/audit-trail
      - system/capability/workflow-orchestration
  audit_level: required
  policy_domains_applicable:
    - architecture
    - compliance
    - risk
```

This manifest is supplied by the DCM-DAV integration — not by DAV itself. DAV doesn't know DCM's capability model exists. DCM's capability registry imports this manifest when a DCM deployment opts into DAV integration.

### 3.3 Provider registration

A DCM deployment that wants DAV integration registers the DAV provider:

```yaml
provider:
  handle: system/provider/process/dav
  provider_type: process
  description: "DAV validation framework integration"
  capabilities:
    - system/capability/architecture-validation
  endpoints:
    - name: primary
      type: http
      url: "http://dav-engine.dav.svc:8080"
      auth: service_account
  configuration:
    inference_endpoint:
      type: openai_compatible
      url_config: dcm-configmap-ref/dav-inference-endpoint
      model_config: dcm-configmap-ref/dav-inference-model
    consumer_content:
      repo_url: "https://github.com/<dcm-org>/dcm"
      content_path: "dav/"
      branch_ref: dcm-configmap-ref/dav-consumer-branch
  lifecycle:
    deploy: ansible_playbook        # Today; operator in future
    configure: via_capability_api
    decommission: ansible_playbook
```

The provider registration is what makes DAV invocable via DCM's standard provider API. DCM users don't call DAV directly — they request the `architecture-validation` capability, and DCM's orchestrator routes the request to the DAV provider.

### 3.4 Capability API surface

The capability exposes four operations through DCM's standard provider contract:

**`initiate-validation`** — Begin a validation run.
Input: use case UUID (or inline UC spec), target spec revision, mode selector (verification/reproduce/explore), optional scope (tenant_profile, environment, component).
Output: run ID, expected completion window.

**`get-validation-status`** — Poll status of an in-progress run.
Input: run ID.
Output: state (queued/running/complete/failed), progress metadata, partial results if available.

**`get-validation-result`** — Retrieve completed analysis.
Input: run ID.
Output: full Analysis artifact conforming to DAV's Analysis Output Schema, plus an integration wrapper with DCM audit metadata.

**`list-validations`** — Query historical runs.
Input: filter criteria (scope, mode, date range, verdict).
Output: list of run IDs with summary metadata.

These are DCM-standard provider API shapes, not DAV API shapes. The DAV provider translates between them and DAV's native CLI/API at invocation time.

### 3.5 The integration layer

Between DCM's capability API and DAV's native interface lives a thin integration layer. Responsibilities:

- Translate DCM capability requests into DAV CLI invocations or API calls
- Wrap DAV outputs in DCM-standard result envelopes with audit metadata
- Emit DCM-standard audit events to DCM's audit trail (in addition to whatever DAV logs natively)
- Enforce DCM-level policy decisions before invoking DAV (e.g., rate limiting, consumer-scope authorization)
- Inject DCM-level context (requesting actor, requesting tenant scope) into DAV prompts as additional domain context

The integration layer lives in the DCM repo, not in DAV. DAV remains framework-agnostic. The integration layer is the thing that knows both DCM and DAV.

Candidate home: `dcm/dav-integration/` or `dcm/integrations/dav/`. Separate from `dcm/dav/` which contains DCM's DAV-consumer content (use cases, prompts, calibration). The integration layer is the *code* that wires DAV into DCM's capability model; `dcm/dav/` is the *content* DAV consumes when validating DCM itself.

### 3.6 Concrete example invocation flow

A DCM user proposes a new policy change. Their workflow includes a validation step.

```
1. User submits policy-change proposal via DCM API
2. DCM's orchestrator initiates the approval workflow
3. Workflow step: invoke architecture-validation capability
   - DCM orchestrator calls: POST /capabilities/architecture-validation/initiate
     body: {
       use_case_ref: "system/validation/policy-change-structural",
       target: { spec_revision: "dcm@abc1234", change_set: [...] },
       mode: "verification",
       scope: { type: "tenant_profile", value: "fsi" }
     }
   - DAV integration layer translates this to a DAV CLI invocation
   - DAV runs Stage 2 with sampling ensemble (N=3)
   - DAV integration layer wraps the result with DCM audit metadata
   - DCM orchestrator receives: run_id + "in-progress" status
4. Workflow waits (async) for validation completion (~10 min in verification mode)
5. DAV integration layer posts completion event to DCM
6. DCM orchestrator retrieves validation result
7. Workflow step: policy-approval-gate
   - Inputs: validation analysis
   - Rule: if any assertion UC failed (hard gate), block
   - Rule: if analytical UC produced critical gaps, require human review
   - Rule: otherwise, surface verdict and gaps to approver but don't block
8. Human approver reviews the analysis in DCM's UI (which embeds DAV's Review Console)
9. On approval, DCM applies the change
10. Audit trail records: {change, validation_run_id, approver, timestamp}
```

This is the scenario that makes DAV-as-capability worth building. Validation is woven into a real DCM workflow, produces auditable artifacts, and supports both automatic gating (assertions) and human review (analyses).

## 4. Policy Distinctions

A critical design decision: **not all DAV outputs can serve as hard gates.**

### 4.1 Hard gates

Hard gates block workflows on failure. They must be deterministic, fast, and correct with near-zero false-positive rate.

In DAV, only **assertion UCs** (uc_type=assertion, see ADR-001 §5.5) produce results suitable for hard gating. These are Python functions that return pass/fail with diagnostic. Tier 1 deterministic, fast, meaningful when they fail.

When DCM's policy engine references DAV results for hard gating:
- DCM's policy language must be able to filter for assertion-UC results specifically
- DCM must NOT autogate on analytical-UC verdicts (which are LLM-produced and probabilistic)

### 4.2 Advisory gates

Advisory gates surface information to humans without blocking. These are appropriate for **analytical UC** results.

When DCM's workflow engine invokes analytical UCs:
- Results appear in DCM's review interface
- Human approver sees verdict, components, capabilities, gaps
- Approver decides whether to proceed
- Approver's decision plus the analysis is captured in the audit trail

### 4.3 Enforcement via capability manifest

The capability manifest (§3.2) declares which UC types produce which gate class. The `capability_class` and `policy_domains_applicable` fields combined with UC-level `gate_class` fields let DCM's policy engine distinguish:

```yaml
use_case:
  uc_type: assertion
  gate_class: hard                    # Can block workflows
  ...

use_case:
  uc_type: analytical
  gate_class: advisory                # Informs, does not block
  ...

use_case:
  uc_type: hybrid
  gate_class: mixed                   # Assertion portion blocks; analytical advises
  ...
```

DCM's policy engine honors these classifications. A policy like "block policy-change approval if any hard-gate validation fails" works correctly; a hypothetical policy like "block if any DAV verdict is partially_supported" would be flagged as a misuse because it tries to hard-gate on an analytical result.

## 5. Sovereignty Considerations

DCM's target audience is sovereign cloud operators. DCM customers care deeply about data residency and inference-endpoint data handling.

### 5.1 Inference endpoint as deployment configuration

DAV uses any OpenAI-compatible endpoint. This ADR requires that DCM-DAV integration preserve this property:

- The inference endpoint is configured per DCM deployment, not hardcoded
- Sovereign deployments point DAV at an in-environment LLM (e.g., llama.cpp on local hardware)
- Less-regulated deployments may point at vendor APIs (OpenAI, Anthropic, etc.)
- DCM's capability manifest declares `sovereignty: configurable` — the integration can run fully sovereign

### 5.2 Spec content handling

DAV's MCP server reads the consumer's spec content to produce analyses. For DCM, this is the DCM spec itself plus any change-set data included in the validation request.

For sovereign deployments:
- The MCP server runs in-environment
- Spec content never leaves the environment
- Only prompts and tool-call sequences (which include snippets of spec content) reach the inference endpoint
- If the inference endpoint is also in-environment, no data leaves the sovereign boundary

For sovereign deployments that cannot accept any spec content reaching an LLM (even an on-prem one), the integration can disable analytical UCs entirely and permit only assertion UCs. Pure-assertion mode is sovereign-safe because it's deterministic Python with no LLM involvement.

### 5.3 Audit implications

DCM's audit trail records validation invocations. When validation involves an external inference endpoint, the audit record must capture:

- Which inference endpoint was used
- What data was sent (prompt hash, sampled, or logged in full depending on policy)
- What was received (response hash or content)
- Whether the endpoint was in-sovereign-environment

This is standard DCM audit trail practice applied to the validation capability. No new mechanisms required, just explicit use of existing ones.

## 6. Integration Modes

Not every DCM deployment needs DAV integration. The integration is opt-in, and there are three reasonable modes.

### 6.1 Mode A: No integration

DCM is deployed without DAV integration. DCM users who want validation run DAV separately as a standalone tool (per ADR-001). This is the baseline.

### 6.2 Mode B: DAV available as a capability

DCM is deployed with the DAV provider registered. DCM users can invoke the `architecture-validation` capability through DCM's standard APIs. DCM's workflow engine can include validation steps. DCM's policy engine can reference validation results. DAV pods run in the deployment; DCM manages their lifecycle via the provider's deploy/configure/decommission operations.

This is the standard integration mode.

### 6.3 Mode C: DAV as a mandatory workflow step

A DCM deployment's policy may require validation for certain change classes. For example: a policy operator requires DAV validation for all policy-change proposals targeting `fsi` tenant profiles. The DAV capability becomes effectively-mandatory for those workflows.

This is the same as Mode B from DAV's perspective — the integration layer is unchanged. What differs is DCM's policy expression, which can now enforce "validation must pass before this workflow proceeds."

## 7. Bootstrapping Considerations

Self-validating systems have bootstrapping problems. DCM runs DAV to validate DCM. Changes to DCM's DAV integration itself are *also* architectural changes — should they be validated by DAV?

### 7.1 The stratification model

Define three strata:

**Stratum 0 (foundational):** DCM's fundamental capability model, policy engine, provider model, audit trail format. Changes at this stratum cannot be validated by DAV because DAV depends on them. Changes require human review and formal ADR.

**Stratum 1 (integration):** DCM-DAV integration layer, DAV provider registration, capability manifest for DAV. Changes here affect how validation works. Validatable *in principle* by DAV but not reliably — meta-validation of the integration risks circular dependencies. Changes require human review.

**Stratum 2 (operational):** Specific UCs, calibration references, validation policies. Changes here are routine and fully validatable by DAV. This is where DAV adds the most value.

The stratification lives in DCM's policy vocabulary. Changes are annotated with their stratum, and the policy engine enforces the appropriate validation discipline for each.

### 7.2 Practical implication

Most day-to-day DCM evolution is Stratum 2. New UCs, updated calibration references, tightened validation policies. DAV validates these routinely.

A small fraction of DCM evolution is Stratum 1. Changes to how DAV is registered as a capability, how the integration layer behaves, how validation results flow into policy decisions. These are rare (handful per year) and always human-reviewed.

Stratum 0 changes are architectural. Maybe one per quarter. Always formal ADR and always human-reviewed.

## 8. Open Design Questions

These questions are intentionally not-yet-answered. They'll be resolved during implementation, ideally documented in follow-on ADRs.

### 8.1 Sync vs async invocation

Validation runs can take minutes (verification mode) to tens of minutes (explore mode). DCM's capability API has to accommodate both synchronous short-running operations and async long-running ones.

Candidate patterns:
- Always async; return run ID immediately; poll for completion
- Mode-dependent: reproduce mode synchronous (fast enough to block on), verification/explore async
- Configurable per invocation with a default policy

Best-fit answer depends on how DCM's workflow engine handles async operations today. That design exists in DCM's architecture; this ADR defers to it.

### 8.2 Capability scoping within a DCM tenant

DCM is multi-tenant (Flavor 3 from ADR-001 design conversation). A tenant may want validation scoped to that tenant's own policy decisions. Should each tenant have its own DAV invocation history? Or is validation deployment-wide with per-tenant annotations in results?

The latter is simpler (one DAV deployment, one invocation history) and matches DCM's data model better. The former is stronger isolation but requires more infrastructure (per-tenant DAV instances, duplicated inference capacity).

Default recommendation: shared DAV deployment, per-tenant annotations. Revisit if isolation is required.

### 8.3 Review Console integration

DAV's Review Console is a standalone web UI. DCM has its own UI story. Two options:

- Embed Review Console views in DCM's UI (iframe, shared styles)
- Link out to Review Console from DCM's UI
- Rebuild Review Console as a DCM UI plugin

Option 3 is most architecturally clean but most expensive. Option 2 is cheap but breaks the single-pane-of-glass promise. Option 1 is middle-ground.

Defer this decision. Initial implementation can link out. Integrated UI is a future enhancement.

### 8.4 Calibration data governance

DCM's DAV-consumer calibration references live in `dcm/dav/calibration/`. When DCM is deployed, do these calibration references travel with the deployment? Are they configurable per-tenant? Who authors them in an operational deployment context?

Likely answer: calibration references are part of DCM's spec repo, travel with deployments as configuration, and are governed by the same authorship policy as spec changes generally. Tenant-specific calibration (if needed) is a future enhancement requiring its own ADR.

## 9. Dependencies on DCM Features

For DAV-as-capability integration to be clean, certain DCM features must exist in stable form. Listed here to make dependencies explicit.

**Required:**
- DCM capability model (exists, stable)
- DCM provider model (exists, stable)
- DCM policy engine with policy-as-code (exists, stable per ADR 006)
- DCM audit trail with Merkle format (exists, stable per Doc 16 §8)
- DCM capability API surface (initiate/get-status/get-result/list) — exists in current architecture, assumed stable

**Preferred but not strictly required:**
- DCM workflow orchestration (exists in current design; integration benefits if it's mature)
- DCM tenant-profile-scoped policy (exists; enables §8.2 tenant scoping)
- DCM UI plugin model (future; enables §8.3 option 3)

**Not required but enabling:**
- DCM operator (not built yet; if exists, DAV operator can coordinate with it)
- DCM service catalog UI (not critical to integration; nice for surfacing validation capability to users)

If any required feature changes materially during DCM's evolution, this ADR must be re-evaluated.

## 10. Not Doing

Explicit resistance to scope creep:

- **Making DAV a required DCM dependency.** DCM works without DAV. Adding DAV-as-capability is opt-in.
- **Making DCM a required DAV dependency.** DAV works without DCM. Standalone deployment remains supported indefinitely per ADR-001.
- **Fusing DAV's policy-evaluation logic with DCM's policy engine.** They remain separate subsystems even under integration. DAV's own internal policy (ensemble merge rules, gating policy) is not replaced by DCM's policy engine.
- **Absorbing DAV's audit trail into DCM's audit trail.** They remain separate, though the DCM-DAV integration produces cross-referencing entries (DCM audit records a DAV run; DAV audit records its own internal state).
- **Embedding DAV's Review Console as a DCM-native UI component.** Link-out first; embed later if demanded.
- **Building DCM-specific prompt content into DAV.** DCM's prompt content lives in `dcm/dav/prompts/` (per ADR-001). The integration layer passes this through to DAV; DAV remains content-agnostic.
- **Tight coupling of DAV release cycle to DCM release cycle.** DAV versions independently; DCM declares a DAV-version-compatible range.

## 11. Success Criteria

The integration is complete and successful when:

1. A DCM deployment can be configured to include DAV as a registered `process` provider.
2. DCM users can invoke `architecture-validation` through DCM's standard capability API without direct knowledge of DAV.
3. DCM workflows can include validation steps with sync or async semantics as appropriate.
4. DCM's policy engine can reference validation results with correct hard-gate/advisory-gate distinction.
5. DAV deployment lifecycle (deploy, configure, decommission) is managed through DCM's provider lifecycle operations.
6. DCM's audit trail captures validation invocations with sufficient metadata for compliance scenarios.
7. Sovereign-deployment configuration exists and is tested; full-sovereign operation (all components in-environment) is verified.
8. A user can disable DAV integration and continue using DCM without functional regression.
9. A user can use DAV standalone (not via DCM) and see identical functionality to ADR-001's baseline.

Criteria 8 and 9 are the acid tests for clean integration. Failure of either means coupling has leaked.

## 12. Timeline

This integration is deferred. Estimated 6-12 months from DAV initial release (per ADR-001 migration timeline). Rough readiness signals:

- DAV has been in stable production use validating DCM for 3+ months
- DCM's capability model has been exercised by multiple non-trivial providers (proof the model is real)
- DCM has a clear workflow orchestration story (not assumed; this ADR requires it)
- A concrete user request for "validate X within a DCM workflow" exists — building speculatively before real demand is premature

When those signals are met, implementation ADRs (002a, 002b, etc.) can break this integration into discrete work units.

## 13. References

- ADR-001 (DAV is a Consumer-Agnostic Framework) — defines DAV as standalone framework with DCM as first consumer
- Doc 16 §8 (DCM audit trail Merkle model)
- Doc 49 §9.1 (DCM capability invocation model)
- DCM provider model documentation
- DCM policy engine documentation

## 14. Changelog

- **2026-04-25** — ADR authored. Status: Proposed (forward-looking, deferred 6-12 months).
