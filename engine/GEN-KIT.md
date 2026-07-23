# DAV UC generation kit — hammer round 2026-07-16

You are generating **DAV use cases** (v1.0 YAML) to stress-test the **UDLM/DCM architecture**. Each UC is a
scenario the architecture must (or must not) support; DAV's engine will later return a verdict
(`supported` / `partially_supported` / `unsupported`) and surface gaps. **Your UCs should probe the edges** —
including scenarios the architecture may NOT handle. Finding gaps is the goal, not writing softballs.

## Exact schema (copy this shape — every field matters)

```yaml
uuid: uc-hammer-<theme>-<NNN>            # unique; <theme> = your theme slug; NNN zero-padded 001..
handle: <theme>/<short-slug>            # e.g. sovereignty/cross-border-migration-denied
scenario:
  description: >                        # 3-6 sentences. Concrete actor + situation + what the system MUST
                                         # do. Name the architectural mechanism under test. Enterprise-real.
  actor:
    persona: <role>                     # e.g. platform-engineer, application-team-member, security-officer,
                                         # compliance-auditor, tenant-admin, sre, finance-analyst
    profile: <profile>                  # one of: homelab dev standard prod fsi sovereign
  intent: <one line — what the actor is trying to accomplish>
  success_criteria:                     # 4-8 concrete, checkable bullets. What proves the system did it right.
  - ...
  dimensions:                           # ALL SIX required. Use ONLY the vocab below.
    lifecycle_phase: <...>
    resource_complexity: <...>
    policy_complexity: <...>
    provider_landscape: <...>
    governance_context: <...>
    failure_mode: <...>
  profile: <same as actor.profile>
  expected_domain_interactions:         # 2-5 entries; domain ∈ {data, policy, provider, audit}
  - domain: data
    interaction: <what happens in that domain>
generated_by:
  mode: authoring                       # ENUM: regression | pr-targeted | authoring (generated => authoring)
  source: llm-guided                    # ENUM: corpus | llm-unguided | llm-guided | human-authored
  model: claude
  prompt_version: hammer-1.0
  timestamp: '2026-07-16T06:00:00.000000+00:00'
tags: [<theme>, <capability>, <edge-or-simple>, <batch-tag>, ...]
version: 1.0.0
metadata:
  author: dav-hammer
  note: batch=<free-form provenance>     # free-form; put the batch id here (loader drops unknown keys)
```

## Dimension vocabulary — use ONLY these values

**AUTHORITATIVE SOURCE: `engine/src/dav/core/consumer_profile.py` (generic reference profile) +
`use_case_schema.py` (`GenerationMode`/`GenerationSource`).** The engine's ingest-gate validates every UC
against these; a wrong enum value → the UC is **quarantined, not analyzed**. If the profile changes, this
list is stale — regenerate it (see `engine/src/dav/scripts/export_dcm_vocab.py`). Values below verified
against the profile on 2026-07-16.

- **lifecycle_phase:** new_request · modification · decommission · drift_detection · brownfield_ingestion ·
  rehydration_faithful · rehydration_provider_portable · rehydration_historical_exact ·
  rehydration_historical_portable · expiry_enforcement
- **resource_complexity:** single_no_deps · hard_dependencies · composite_service · conditional_soft_deps ·
  process_resource · cross_dependency_payload  *(NOT single_with_deps / multi_dependent / full_stack)*
- **policy_complexity:** system_defaults_only · single_validation · multi_policy_chain · conflicting_policies ·
  orchestration_flow_static · dynamic_conditional_flow · cross_domain_constraint · human_escalation_required ·
  governance_matrix_enforcement · recovery_policy  *(NOT no_policy / multi_validation / cross_domain)*
- **provider_landscape:** single_eligible · multiple_eligible · none_eligible · peer_dcm_required ·
  process_provider · mixed  *(NOT multi_eligible)*
- **governance_context:** no_governance · standard_governance · audit_heavy · compliance_gated ·
  sovereignty_enforced  *(NOT sovereign_mandate)*
- **failure_mode:** happy_path · provider_failure · policy_violation · peer_dcm_disconnect ·
  data_inconsistency · rollback_required · partial_fulfillment · timeout · resource_exhaustion
  *(NOT single_provider_failure)*

Before running a generated batch: load each UC with `UseCase.from_dict()` + `.validate(get_generic_reference_profile())`
— 0 errors means the ingest-gate accepts it.

## Rules

1. **Valid YAML, valid vocab.** Every dimension value from the lists above verbatim. Unique uuid + handle.
2. **Span simple → complex.** Mix `single_no_deps`/`happy_path` simple cases with `composite_service` /
   `governance_matrix_enforcement` / failure-mode complex ones. Roughly half simple, half complex.
3. **Stress the edges.** Include cases that push the architecture: sovereignty conflicts, revoked
   authorizations mid-flight, partial failures, peer disconnects, decommission with dangling dependents,
   drift on immutable records, quota exhaustion, credential expiry mid-dispatch, cross-tenant leakage
   attempts, rehydration of a resource whose provider no longer exists. Some SHOULD probably come back
   `unsupported` — that's a finding.
4. **Enterprise-real.** Multi-tenancy, residency, DR, audit/compliance, chargeback, federation, day-2 ops.
   Ground each in a capability an enterprise actually demands.
5. **One UC per file**, written to your assigned folder, named `<NNN>-<slug>.yaml`.
6. **Quality over filler.** Match the exemplars' depth (below). No lorem, no near-duplicates.

## Two exemplars (match this depth)

See `~/git/dcm/dav/use-cases/compute/idempotent-reconvergence.yaml` (simple) and
`~/git/dcm/dav/use-cases/cross-domain/dynamic-rehydration.yaml` (complex) — read both before generating.
