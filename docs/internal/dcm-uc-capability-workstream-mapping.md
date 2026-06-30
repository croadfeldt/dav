# DCM 6-Week Demo — Use Case / Capability / Workstream Mapping

> **Purpose:** Definitive use case selection for the 6-week demo roadmap.
> **For:** Engineering meeting 2026-06-30 (Chris / Piotr / team)
> **Source:** DAV project 20 corpus (156 UCs); scoping set "DCM-Corpus-Piotr-feedback" (28 UCs, quality model)
> **Supersedes:** Previous ~65-UC broad mapping and the 22-UC v1 mapping

## How to read this document

**Structure:** UCs are organized by the 8 acceptance gates from the roadmap (section 6). Each UC
appears exactly once under its primary gate. Cross-gate UCs that exercise multiple gates are called
out separately as the demo's backbone.

**Columns:**
- **UC** — short name derived from the UC handle
- **UUID** — DAV identifier (truncated for readability)
- **Handle** — full UC handle from DAV
- **Capabilities** — IDs from the DCM Capabilities Matrix (311 capabilities)
- **Workstream** — primary workstream from the roadmap (WS-A through WS-J)
- **Week** — target demo week
- **Status** — Exists in DAV / Needs modification / Needs creation

**Piotr-feedback UCs** are marked with `[PF]`. These come from scoping set 27 ("DCM-Corpus-Piotr-feedback")
and represent the quality standard for UC scoping and detail.

**Trifecta UCs** (tagged `dr-a` through `dr-e`, `a2`, `a4`, `a9`) are architecture-validation UCs from
Piotr's review decisions. Each validates that a DCM capability exists AND that UDLM models the data behind
it. Demo-path trifectas are included; post-demo trifectas are tracked in a separate section.

---

## Acceptance Gates (from roadmap section 6)

| # | Gate | Demo week |
|---|------|-----------|
| 1 | Intent is system-of-record | wk3 |
| 2 | Realized reconciled with provenance | wk3 |
| 3 | Dependency graph exists and is honored | wk3 |
| 4 | Rebuild plan dynamically derived, dependency-ordered, from bare metal | wk4 |
| 5 | RTO measured, not asserted | wk4 |
| 6 | Idempotency / no-op on re-apply | wk4 |
| 7 | Auditability end-to-end | wk5 |
| 8 | (Stretch) Provider-portable rebuild | wk5 |

---

## Demo Use Cases (by acceptance gate)

### Gate 1: Intent is system-of-record (wk3)

Every layer submitted as a UDLM intent, persisted, retrievable as a typed resource.

| # | UC | UUID | Handle | Capabilities | Workstream | Week | Status |
|---|-----|------|--------|-------------|------------|------|--------|
| 1 | **VM as UDLM resource** — Define a VirtualMachine as a first-class UDLM resource with spec/status; a submitted intent is persisted and retrievable as a typed resource | `uc-895e5ab0` | `libvirt-vm-provider/standard/vm-resource-representation` | REQ-002, STO-001, GOV-003, CAT-001, CAT-002 | **WS-D** | wk2 | Exists |
| 2 | **Architecture-to-composite-request** — A solution architecture in a code-first DSL decomposes into individual resource requests with dependencies resolved, enriched with policies, and orchestrated across providers | `uc-a4a4f8def3ca` | `cross-domain/solution-architecture-deployment` | CMP-001, CMP-002, RDG-001, REQ-001, REQ-005, CAT-001 | **WS-C** | wk3 | Exists |
| 3 | **Standard VM provision** `[PF]` — Consumer requests a VM with standard multi-tenant isolation; exercises the full intent-to-realized pipeline with policy, provider allocation, and audit | `uc-seed-001a` | `compute/vm-standard-provision` | REQ-001 thru REQ-008, PRV-001 thru PRV-005, POL-001, IAM-007, AUD-001 | **WS-B** | wk2-3 | Exists |
| 4 | **Profile-scoped policy boundary** `[PF]` — A minimal-profile request succeeds where an FSI-profile request would fail, proving policy applicability is by resolved-profile membership, not global | `uc-seed-009a` | `governance/minimal-profile-policy-scope-boundary` | POL-004, POL-005, POL-012, POL-013, IAM-007 | **WS-F** | wk3 | Exists |

> **Note:** UC #3 (`uc-seed-001a`) replaces the previous `uc-canonical-001` which was a thin test UC. The Piotr-feedback version has proper dimensions, 4 success criteria, 4 domain interactions, and explicit idempotency requirements.

### Gate 2: Realized reconciled with provenance (wk3)

Status read back from the provider, stored with field-level provenance.

| # | UC | UUID | Handle | Capabilities | Workstream | Week | Status |
|---|-----|------|--------|-------------|------------|------|--------|
| 5 | **VM status provenance** — Reconcile provider-reported VM status with provenance so the realized model is trustworthy and auditable; each realized field carries provenance (which provider/run produced it, when) | `uc-8b603f5a` | `libvirt-vm-provider/standard/vm-status-provenance` | PRV-005, STO-002, STO-006, DRF-001 | **WS-I** | wk3 | Exists |
| 6 | **Persistent volume provision** `[PF]` — Provision a block volume and attach it to a VM; exercises cross-resource dependency with tenancy enforcement and quota tracking on the realized state | `uc-seed-004a` | `data/persistent-volume-provision` | REQ-001, PRV-001, PRV-005, POL-001, IAM-007, STO-002 | **WS-B** | wk3 | Exists |

### Gate 3: Dependency graph exists and is honored (wk3)

Inter-tier deps declared and discovered; undeclared edges flagged as drift.

| # | UC | UUID | Handle | Capabilities | Workstream | Week | Status |
|---|-----|------|--------|-------------|------------|------|--------|
| 7 | **Dependency graph as first-class data** — UDLM models the resource dependency graph (containment, requirement, shared-fault-domain edges) as a first-class queryable structure; DCM derives ordering and impact from it | `uc-73071912` | `dcm-core/standard/udlm-dependency-graph-data-model` | RDG-001, STO-001, CMP-002 | **WS-I** | wk3 | Exists |
| 8 | **Cross-provider dependency ordering** — Realize a VM only after its cross-provider prerequisites exist (e.g., host bridge), in topological order; a missing dependency blocks, not silently skips | `uc-a537b0a9` | `libvirt-vm-provider/standard/cross-provider-dependency-ordering` | RDG-001, CMP-002, REQ-005, PRV-001 | **WS-B** | wk3 | Exists |
| 9 | **Dependency failure surfaced** — A missing/misconfigured dependency (e.g., VM NIC requires absent host bridge) is detected, blocks convergence, and reports the broken edge rather than failing silently | `uc-4908573a` | `libvirt-vm-provider/standard/dependency-failure-impact` | RDG-001, DRF-002, DRC-001 | **WS-B** | wk3 | Exists |

### Gate 4: Rebuild plan dynamically derived, dependency-ordered (wk4) — THE HEADLINE

The system reads stored data + dependency graph, re-runs placement/policy/provider-resolution to compute what comes back and in what order, and the derived plan is shown before it executes.

| # | UC | UUID | Handle | Capabilities | Workstream | Week | Status |
|---|-----|------|--------|-------------|------------|------|--------|
| 10 | **Full DC rehydration from intents** — Rebuild an entire data center from scratch by replaying all stored intents through the control plane starting from bare hardware with bootstrap media; dependency graph ordering respected; realized state matches replayed intents | `uc-126b4231c0f8` | `cross-domain/full-dc-rehydration` | LCM-005, REQ-002, REQ-005, PRV-001, PRV-003, RDG-001, CMP-002, GOV-006 | **WS-A** | wk4 | Exists |
| 10a | **Bare-metal PXE provision** — A node PXE-boots, phone-homes via DNS-SRV, posts a hardware manifest keyed on DMI serial; control plane matches to a BareMetalInstance intent, applies placement/policy, triggers ABI install; realized with provenance; no BMC; runs on libvirt VMs | `uc-baremetal-pxe-provision` | `baremetal/intent-driven-pxe-provision` | REQ-002, REQ-005, PRV-001, PRV-003, PRV-005, GOV-006, STO-001 | **WS-A** | wk2-3 | **New** |
| 11 | **Provider-failure recovery** `[PF]` — Provider goes down mid-realization; recovery policy classifies partial state, decides requeue/hold/fail; partial resources reconciled or released; alternate provider dispatched if eligible and permitted | `uc-seed-006a` | `compute/vm-provision-with-provider-failure` | PRV-007, REQ-004, REQ-005, LCM-001, DRC-001 | **WS-B** | wk4 | Exists |

### Gate 5: RTO measured, not asserted (wk4)

Recovery time and completeness measured per resource and per capability domain.

| # | UC | UUID | Handle | Capabilities | Workstream | Week | Status |
|---|-----|------|--------|-------------|------------|------|--------|
| 12 | **Resilience posture rehydration test** — Periodically replay intents in a test environment to measure rehydration time and completeness, producing a resilience posture score per capability domain with trends tracked over time | `uc-b53c099c325d` | `observability/resilience-posture-rehydration-test` | LCM-005, OBS-006, REQ-002, OBS-001 | **WS-E** | wk4 | Exists |

### Gate 6: Idempotency / no-op on re-apply (wk4)

Unchanged intent converges to no-op; no indeterminate resources without a recovery record.

| # | UC | UUID | Handle | Capabilities | Workstream | Week | Status |
|---|-----|------|--------|-------------|------------|------|--------|
| 13 | **VM lifecycle reconciliation** — Reconcile VM lifecycle intent (present, running, stopped, absent) to realized state idempotently; re-applying unchanged intent is a no-op; drift between intent and realized is detected and corrected | `uc-c600fab7` | `libvirt-vm-provider/standard/vm-lifecycle-reconciliation` | LCM-001, DRF-002, DRC-001, REQ-002, STO-002 | **WS-B** | wk3-4 | Exists |
| 14 | **Drift detection and remediation** — Continuously compare discovered state against realized state and original intent; remediation action matches policy for drift type and severity; all drift events recorded with before/after state | `uc-6e1e27735e9c` | `observability/drift-detection-remediation` | DRF-001, DRF-002, DRF-004, DRC-001, PRV-005, STO-002, STO-003 | **WS-E** | wk3-4 | Exists |

### Gate 7: Auditability end-to-end (wk5)

Provisioning, drift, decommission carry audit trails.

| # | UC | UUID | Handle | Capabilities | Workstream | Week | Status |
|---|-----|------|--------|-------------|------------|------|--------|
| 15 | **Merkle-tree audit verification** `[PF]` — A compliance auditor requests cryptographic verification that historical audit events have not been tampered with; signed tree heads, inclusion proofs, and consistency proofs produced; verification is read-only | `uc-seed-007a` | `governance/audit-merkle-tree-verification` | AUD-001, AUD-003, AUD-007, AUD-008 | **WS-E** | wk5 | Exists |
| 16 | **Policy override approval workflow** `[PF]` — Time-bounded override of a soft-enforcement governance policy; override eligibility evaluated per-policy, routed to authorized approver, scoped to matched request; hard-enforcement policies unoverridable; audit-linked | `uc-seed-005a` | `governance/policy-override-approval` | POL-014, POL-016, POL-019, AUD-001 | **WS-C** | wk5 | Exists |

### Gate 8: (Stretch) Provider-portable rebuild (wk5)

When a provider is unavailable, the planner re-resolves onto a different provider.

| # | UC | UUID | Handle | Capabilities | Workstream | Week | Status |
|---|-----|------|--------|-------------|------------|------|--------|
| 17 | **Provider registration and capability advertisement** — Register a compute provider that advertises capability and per-host capacity so DCM can place VM intents; a second provider plugs in via the same contract | `uc-cd9b798f` | `libvirt-vm-provider/standard/provider-registration-capability` | PRV-001, PRV-006, PRR-001, PRR-002 | **WS-H** | wk3-5 | Exists |
| 18 | **Workload portability across providers** — Move an existing workload from one provider to another preserving all dependencies via the dependency graph, with placement policies selecting the target provider | `uc-a4f95cd66c7c` | `cross-domain/workload-portability` | LCM-005, REQ-005, PRV-001, RDG-001 | **WS-H** | wk5 | Exists |

---

## Cross-Gate UCs (span multiple gates — highest value)

These UCs exercise capabilities across multiple acceptance gates. They are the demo's backbone and should be prioritized for implementation.

| UC # | UC | Gates | Why it's high-value |
|------|-----|-------|---------------------|
| 3 | Standard VM provision `[PF]` | G1, G2, G6, G7 | End-to-end pipeline: intent capture, policy, realization, provenance, audit. The foundational happy-path. |
| 10 | Full DC rehydration | G1, G3, G4, G5 | THE HEADLINE. Exercises intent-as-SoR, dependency graph, dynamic derivation, and RTO in one UC. |
| 14 | Drift detection/remediation | G2, G6 | Three-way comparison (intent/realized/discovered) proves both provenance and idempotency. |
| 11 | Provider-failure recovery `[PF]` | G4, G6 | Validates dynamic re-planning AND recovery record semantics (no indeterminate state). |
| 8 | Cross-provider dependency ordering | G1, G3 | Proves the dependency graph is real and honored across provider boundaries. |

---

## Piotr-Feedback Validation UCs

### On the demo path (included above)

These trifecta UCs from Piotr's review decisions (DR-A through DR-E, A2, A4, A9) validate that DCM capabilities AND their UDLM data models work together. Only the pairs directly exercised by the demo arc are included.

| # | Decision | Capability UC | Data Model UC | Capabilities | Workstream | Gates | Week |
|---|----------|--------------|---------------|-------------|------------|-------|------|
| 19 | **DR-E: Policy resolution** — DCM resolves request profile from approved list + default; evaluates only that profile's policies; three-state audit outcome (pass/fail/out-of-scope); out-of-scope is first-class, not absence | `uc-policy-resolution-capability` | `uc-policy-applicability-data-model` | POL-004, POL-005, POL-012, POL-013 | **WS-F** | G1, G7 | wk3 |
| 20 | **DR-B: Profile resolution** — Instance declares approved_profiles[] + default_profile; tenant onboarding binds tenant to resolved profile via policy_profile DCMGroup; onboarding is atomic; profiles are capability sets compared by content, not rank | `uc-profile-resolution-capability` | `uc-profile-approved-list-data-model` | POL-005, GOV-001, GOV-008, IAM-007 | **WS-F** | G1 | wk3 |
| 21 | **DR-D: Audit chain** — DCM produces signed Merkle tree heads + inclusion/consistency proofs with in-boundary signing; UDLM models audit events/epochs/proofs as append-only; output independently re-verified by auditor; tampering detected | `uc-audit-chain-proofs-capability` + `uc-audit-chain-output-verification` | `uc-audit-chain-data-model` | AUD-001, AUD-003, AUD-007, AUD-008 | **WS-I** | G7 | wk5 |

### Post-demo validation (tracked, not 6-week scope)

These trifecta pairs are architecturally important but do not serve the demo's two-act arc directly. They are tracked for the next phase.

| Decision | Capability UC | Data Model UC | Handle prefix | Why deferred |
|----------|--------------|---------------|---------------|-------------|
| **DR-A: Brownfield adoption** | `uc-brownfield-adoption-capability` | `uc-brownfield-adoption-data-model` | `cross-domain/brownfield-adoption-*` | Demo provisions greenfield (Act I). Brownfield adopt-in-place + coexistence + cutover is post-demo scope. The end-to-end UC (`uc-seed-bfi-001`, Ansible inventory ingestion) is the full brownfield story — a natural follow-up when customer co-engineering begins. |
| **DR-C: Delegated identity** | — | `uc-identity-reference-data-model` | `identity/identity-reference-*` | Connected delegation (`uc-identity-deleg-001`) is foundational infrastructure. The demo uses Keycloak for authn but does not exercise the delegated-identity data model or the disconnected/sovereign identity projection. |
| **A4: Peer coordination** | `uc-peer-coordination-capability` | `uc-cross-dcm-audit-data-model` | `cross-domain/peer-*` | Sovereign decommission with peer (`uc-seed-002a`) requires a second DCM instance. Demo is single-instance. The peer-ack + held-on-unreachable semantics are validation targets for the federation workstream post-demo. |
| **A9: Telemetry export** | `uc-telemetry-export-capability` | `uc-telemetry-udlm-data-model` | `observability/telemetry-*` | Universal telemetry export (one UDLM surface over OTLP/Prometheus/bus) is breadth — not on the provision-destroy-rebuild critical path. The end-to-end UC (`uc-seed-obs-001`) is a natural WS-F follow-up. |
| **A2: Tenancy data model** | — | `uc-tenancy-data-model` | `compute/tenancy-*` | Tenant isolation enforcement (`uc-tenant-iso-001`) is covered implicitly by UC #3 (standard VM provision, which includes tenant-isolation policy). The explicit data-model validation (tenant_boundary DCMGroup, attachment confinement) is a refinement for post-demo. |

### Piotr-feedback UCs not selected for either list

These are from the 28-UC quality-model set but are redundant with selected UCs or not on the demo path:

| UUID | Handle | Why excluded |
|------|--------|-------------|
| `uc-seed-002a` | `cross-domain/sovereign-decommission-with-peer` | Requires peer DCM instance; covered by A4 trifecta deferral |
| `uc-seed-003a` | `identity/auth-provider-drift-detection` | Identity drift detection is DR-C territory; demo does not exercise IdP drift |
| `uc-seed-008a` | `cross-domain/tenant-onboarding` | Tenant onboarding is Act I setup, not the demo arc itself; if needed, add it (see Gaps section) |

---

## Capability Density (top 10)

Which capabilities are exercised by the most demo UCs — prioritize these for implementation.

| Rank | Capability | ID | Demo UC count | UCs | Gate coverage |
|------|-----------|-----|---------------|-----|---------------|
| 1 | Intent State Capture | REQ-002 | 5 | #1, #3, #10, #12, #13 | G1, G4, G5, G6 |
| 2 | Provider Registration | PRV-001 | 5 | #3, #8, #10, #11, #17 | G1, G3, G4, G8 |
| 3 | Realized State Reporting | PRV-005 | 4 | #5, #6, #13, #14 | G2, G6 |
| 4 | Dependency Group Submission | RDG-001 | 4 | #7, #8, #9, #10 | G3, G4 |
| 5 | Placement Engine Execution | REQ-005 | 4 | #2, #8, #10, #18 | G1, G3, G4, G8 |
| 6 | Drift Comparison | DRF-002 | 4 | #9, #13, #14, #5 | G2, G6 |
| 7 | Resource State Transitions | LCM-001 | 3 | #11, #13, #3 | G1, G4, G6 |
| 8 | Audit Trail Access | AUD-001 | 4 | #3, #15, #16, #21 | G1, G7 |
| 9 | Rehydration | LCM-005 | 3 | #10, #12, #18 | G4, G5, G8 |
| 10 | Profile Management | POL-005 | 4 | #4, #19, #20, #3 | G1, G7 |

**Critical-path capabilities** (from the Capabilities Matrix dependency map): IAM-001 -> IAM-007 -> CAT-001 -> REQ-001 -> REQ-002 -> REQ-004 -> REQ-005 -> REQ-007 -> PRV-001 -> PRV-003 -> PRV-005 -> LCM-001 -> DRF-001. The demo exercises 12 of these 13 critical-path capabilities.

---

## Workstream Coverage Summary

| Workstream | UC Count (primary) | UC #s | Gates Covered | Demo Weeks |
|------------|-------------------|-------|---------------|------------|
| **WS-A** Bare-metal enablement | 2 | #10, #10a | G1, G3, G4, G5 | wk2-4 |
| **WS-B** Control plane & planner | 5 | #3, #6, #8, #9, #11 | G1, G2, G3, G4, G6 | wk2-4 |
| **WS-C** Approved-architecture catalog | 2 | #2, #16 | G1, G7 | wk3, wk5 |
| **WS-D** UDLM registry | 1 | #1 | G1 | wk2 |
| **WS-E** Acceptance & validation | 3 | #12, #14, #15 | G5, G6, G7 | wk3-5 |
| **WS-F** Software profiles & ops chars | 3 | #4, #19, #20 | G1, G7 | wk3 |
| **WS-G** Demo packaging | 0 | — | (packages all) | wk5-6 |
| **WS-H** Easy provider integration | 2 | #17, #18 | G8 | wk3-5 |
| **WS-I** UDLM conformance | 3 | #5, #7, #21 | G2, G3, G7 | wk3-5 |
| **WS-J** Spec-landing & merge | 0 | — | (precondition) | wk1-3 |

**WS-G** and **WS-J** have zero primary UCs by design: WS-G packages the other workstreams' outputs for delivery, and WS-J is a precondition (merged specs) enabling WS-I/D/H.

---

## UC-to-Gate Matrix (quick reference)

| UC # | UC | G1 | G2 | G3 | G4 | G5 | G6 | G7 | G8 |
|------|-----|----|----|----|----|----|----|----|----|
| 1 | VM as UDLM resource | **X** | | | | | | | |
| 2 | Architecture-to-composite | **X** | | **X** | | | | | |
| 3 | Standard VM provision [PF] | **X** | **X** | | | | **X** | **X** | |
| 4 | Profile-scoped policy [PF] | **X** | | | | | | | |
| 5 | VM status provenance | | **X** | | | | | | |
| 6 | Persistent volume [PF] | | **X** | | | | | | |
| 7 | Dependency graph data model | | | **X** | | | | | |
| 8 | Cross-provider depends_on | **X** | | **X** | | | | | |
| 9 | Dependency failure surfaced | | | **X** | | | | | |
| 10 | DC rehydration (HEADLINE) | **X** | | **X** | **X** | **X** | | | |
| 11 | Provider-failure recovery [PF] | | | | **X** | | **X** | | |
| 12 | Resilience posture / RTO | | | | **X** | **X** | | | |
| 13 | VM lifecycle reconciliation | **X** | **X** | | | | **X** | | |
| 14 | Drift detection/remediation | | **X** | | | | **X** | | |
| 15 | Merkle-tree audit [PF] | | | | | | | **X** | |
| 16 | Policy override approval [PF] | | | | | | | **X** | |
| 17 | Provider registration | | | | | | | | **X** |
| 18 | Workload portability | | | | | | | | **X** |
| 19 | DR-E Policy resolution [PF] | **X** | | | | | | **X** | |
| 20 | DR-B Profile resolution [PF] | **X** | | | | | | | |
| 21 | DR-D Audit chain [PF] | | | | | | | **X** | |

**Gate coverage totals:** G1=8, G2=4, G3=4, G4=3, G5=2, G6=4, G7=5, G8=2. All gates covered.

---

## Identified Gaps

### Gates with thin coverage

| Gate | UC count | Assessment | Recommendation |
|------|----------|------------|----------------|
| **Gate 5 (RTO measured)** | 2 (#10, #12) | Adequate but only #12 explicitly measures RTO. #10 exercises rehydration but does not capture wall-clock metrics. | Consider adding an explicit RTO measurement step to UC #10's success criteria, or modify it to require that recovery time per resource is reported. |
| **Gate 8 (Provider-portable)** | 2 (#17, #18) | Both are stretch-week UCs. If stretch is cut, gate 8 has zero live coverage. | Acceptable since gate 8 is declared stretch. Fallback: narrate the provider contract + demonstrate registration (#17) even without full portability. |

### Workstreams with potential UC gaps

| Gap | Assessment | Action |
|-----|-----------|--------|
| **WS-A has only 1 primary UC** (#10) — **RESOLVED** | UC #10a (`uc-baremetal-pxe-provision`) now covers the PXE + DNS-SRV phone-home + ABI install path as a standalone intent-driven operation. | Authored 2026-06-30; submitted to DAV (readiness score 72). |
| **WS-D has only 1 primary UC** (#1) | The UDLM registry workstream has one resource-representation UC. Other resource types the demo touches (BareMetalInstance, Network, Storage, AppTier) need registry types but lack explicit UCs. | Acceptable — WS-D's job is to add minimum-viable types; the UCs that exercise them are under other workstreams. |
| **Tenant onboarding** | The demo provisions a 3-tier app (Act I), which implies a tenant exists. `uc-seed-008a` (tenant onboarding, FSI profile, atomic) is a Piotr-feedback UC but was excluded because onboarding is setup, not the demo arc. | If the demo needs to SHOW onboarding as part of Act I step 1, `uc-seed-008a` should be added under WS-B / Gate 1. |

### Missing capabilities in the demo path

| Capability area | What's missing | Impact |
|-----------------|---------------|--------|
| **Composite service composition** | No UC explicitly tests CMP-003 (partial delivery / DEGRADED state) or CMP-004 (composite compensation on failure). UC #2 covers CMP-001 and CMP-002 only. | Low for demo — the demo's composite is the 3-tier app happy path. Partial delivery is a post-demo concern. |
| **Scheduled/deferred requests** (SCH-*) | No demo UC exercises scheduling. | Not on the demo path — all requests are immediate. |
| **ITSM integration** (ITSM-*) | No demo UC exercises ITSM. | Not on the demo path — post-demo breadth. |

---

## Summary

- **21 primary UC entries** comprising 26 DAV UC records (3 trifecta pairs consolidated; 6 canonical-002 profile variants replaced by 1 Piotr-feedback UC)
- **9 of 21 UCs are from the Piotr-feedback quality-model set** (`uc-seed-001a`, `uc-seed-004a`, `uc-seed-005a`, `uc-seed-006a`, `uc-seed-007a`, `uc-seed-009a`, plus 3 trifecta pairs comprising 7 UCs)
- **All 8 acceptance gates covered** — the headline gate (G4: dynamic rebuild) has the single most valuable UC in the corpus (#10)
- **8 of 10 workstreams have primary UCs** — WS-G and WS-J are enablers by design
- **Zero duplicates** — each UC appears exactly once under its primary gate/workstream
- **3 trifecta pairs on demo path** (DR-B, DR-D, DR-E); **5 deferred** (DR-A, DR-C, A2, A4, A9)
- **0 gaps requiring authoring** — bare-metal provisioning UC authored and submitted (uc-baremetal-pxe-provision)

*Curated 2026-06-30 for the engineering meeting. Source data: DCM Capabilities Matrix (311 capabilities across 39 domains), 6-week demo roadmap (10 workstreams, 8 acceptance gates), DAV project 20 corpus (156 UCs), scoping set "DCM-Corpus-Piotr-feedback" (28 UCs, set ID 27). All UC data fetched live from the DAV API.*
