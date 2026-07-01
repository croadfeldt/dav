# Pipeline Use Cases — Design / Requirements

**Status:** living design doc (started 2026-06-30). Captures the pipeline UC model;
individual UC authoring follows after scope confirmation. Build to this doc; update it
as decisions land.

## 1. Goal

The 6-week roadmap (DCM Section 2) tells a two-act demo story: provision from pattern,
then destroy and rebuild. Today's 28 UCs validate isolated capabilities — they don't
chain into the narrative the demo requires. Pipeline UCs close that gap.

> **Pipeline UCs tell one continuous story.** Each UC's postcondition is the next UC's
> precondition. Engineers implement them in order; customers read them as "what this
> system does for me"; DAV can run them in sequence as an integration test; the demo
> script IS the UC chain.

This is not a replacement for the existing UC corpus. It is a **sequencing layer** on
top of it. Existing UCs that already cover a pipeline step are adopted in-place;
new UCs are authored only for gaps.

### 1.1 Alignment with engineering workflow

Piotr's ask (confirmed across Jun 26, 29, 30 meetings): **UCs → capabilities →
workstreams → prioritize → Jira → milestone delivery.** This pipeline is the
structure that makes that flow work. Each pipeline UC maps to capabilities the
engineering team delivers, organized by workstream and week.

Kevin's density principle: prioritize capabilities that unlock the most UCs. The
capability catalog's hit-count view (e.g., "REQ-001 touches 20/118 UCs") identifies
foundational capabilities to build first.

## 2. The pipeline model

```
Act 0: AUTHENTICATE (wk2)
  Who am I? → Keycloak token with claims → control plane accepts me
       │
       ▼
Act 1: BOOTSTRAP (wk1-2)
  Nothing → bare metal boots → cluster stands up → control plane deploys
       │        (containers on Podman/OCP — no direct OS/KVM install)
       ▼
Act 2: PROVISION FROM COMPOSITE SERVICE (wk3)
  Composite service definition → catalog item → intent submitted
  → decompose → dependency order → validation policy gate
  → providers realize → four states visible
  (the "easy consumption" proof)
       │
       ▼
Act 3: OPERATE (wk3-4)
  System runs → discovered state probed → drift detected → remediated
  → re-apply unchanged intent → no-op (idempotency)
       │
       ▼
Act 4: DESTROY AND REBUILD (wk4 — the headline)
  Kill it → system reads stored intents + dependency graph
  → derives the plan (not replay) → validation policies re-evaluated
  → providers resolved → plan shown → executed → RTO measured
  → realized matches intent
       │
       ▼
Act 5: PORTABILITY (wk5, stretch)
  Provider unavailable → re-resolve onto alternate
  → references rewritten → rebuild from software profile alone
```

## 3. Terminology (decided 2026-06-30)

These decisions are locked. Apply them in all UC authoring.

| Term | Use | Do NOT use |
|---|---|---|
| **Provider** | Generic interface for anything that provides capabilities to DCM | — |
| **Service provider** | A provider whose type is "service" (e.g., VM provisioner) | "resource provider" (proposed then reversed) |
| **Provider type** | Category: service, information, credential, process | "service type" at the top level |
| **Validation policy** | Any policy that evaluates a request (with gating flag for hard blocks) | "gatekeeper policy", "gating policy" |
| **Composite service** | DCM's native concept for multi-component architectural patterns | "likeC4" as a native concept |
| **Realized** | The state where a resource exists and is confirmed | "fulfilled" |

**likeC4 note:** likeC4 is a customer-specific format (PNC). A *process provider*
converts likeC4 → composite service. DCM does not natively speak likeC4 or any
other customer-specific format. UDLM is the core design language; customer format
converters live outside the core (Piotr: "I'm kind of reluctant to have customer-
specific code as part of generic solution").

## 4. Relationship to existing UC corpus

Each pipeline step maps to the **trifecta** model from Piotr's feedback:

| Layer | Purpose | Example |
|---|---|---|
| **Seed UC** | The scenario (pipeline step) | `vm-standard-provision` |
| **Capability UC** | The system can do it | `profile-resolution-capability` |
| **Data-model UC** | The data model supports it | `tenancy-data-model` |

Pipeline ordering applies to the seed UCs. Capability and data-model companions
validate the substrate each seed UC relies on but don't carry pipeline sequence
numbers — they stand on their own.

### 4.1 Adoption rules

1. If an existing seed UC already covers a pipeline step, adopt it — add the pipeline
   fields, don't rewrite it.
2. If no seed UC exists, author a new one in the appropriate domain directory.
3. Every seed UC in the pipeline MUST have capability + data-model companions (trifecta
   complete). If companions don't exist yet, author them alongside the seed.

### 4.2 Data sensitivity constraint

UDLM provides hooks and design language. DCM is the implementation. **Neither owns
or stores sensitive customer data** (contract pricing, customer PII). UCs must model
references and integration hooks, not the sensitive data itself (Kevin Cattell,
Jun 30 cost management meeting).

## 5. Schema extension

Two new optional fields under `scenario`:

```yaml
scenario:
  preconditions:
    - "Actor holds a valid Keycloak token with tenant and role claims"
    - "Control plane is deployed and accepting requests"
  postconditions:
    - "VM is realized, reachable, and recorded in the realized store"
    - "Four-state stores reflect Intent → Requested → Realized for the VM"
```

### 5.1 Field definitions

| Field | Type | Required | Description |
|---|---|---|---|
| `preconditions` | `list[string]` | optional | Conditions that must be true before this UC can execute. References postconditions of upstream pipeline UCs. |
| `postconditions` | `list[string]` | optional | Conditions this UC guarantees on success. Referenced as preconditions by downstream pipeline UCs. |

These fields are optional — non-pipeline UCs (standalone capability and data-model
UCs) don't need them.

### 5.2 Pipeline metadata

A new optional top-level field for pipeline sequencing:

```yaml
pipeline:
  act: 2                         # Act number (0-5)
  sequence: 7                    # Global sequence within the pipeline
  week: "wk3"                    # Roadmap week
  depends_on:                    # UUIDs of upstream pipeline UCs
    - uc-pipeline-006
```

This is authoring metadata only — DAV's engine does not enforce execution order.
It enables tooling (Jira sync, DAV console, demo scripts) to present UCs in
pipeline order.

## 6. Definitive UC list for Jira (34 stories)

34 UCs go to Jira as Stories in FLPATH. Pipeline UCs replace 6 overlapping
Section 16 UCs. All 42 UCs in the DCM corpus validate against the JSON schema.

### 14 Pipeline UCs (new, in git YAML files)

| Seq | Handle | UUID | Act | WS | Week |
|---|---|---|---|---|---|
| 1 | `identity/actor-authentication` | uc-pipeline-authn-001 | 0 | WS-B | wk2 |
| 2 | `infrastructure/bare-metal-pxe-bootstrap` | uc-pipeline-bm-001 | 1 | WS-A | wk2 |
| 3 | `infrastructure/cluster-bootstrap` | uc-pipeline-cluster-001 | 1 | WS-A | wk2 |
| 4 | `infrastructure/control-plane-deployment` | uc-pipeline-cp-001 | 1 | WS-B | wk2 |
| 5 | `cross-domain/composite-service-to-catalog-item` | uc-pipeline-catalog-001 | 2 | WS-C | wk3 |
| 6 | `cross-domain/composite-service-provision` | uc-pipeline-composite-001 | 2 | WS-B | wk3 |
| 7 | `data/four-state-store-conformance` | uc-pipeline-4state-001 | 2 | WS-D | wk2-3 |
| 8 | `governance/sovereignty-validation-policy` | uc-pipeline-sov-001 | 2 | WS-F | wk3 |
| 9 | `observability/drift-detection-remediation` | uc-pipeline-drift-001 | 3 | WS-B | wk4 |
| 10 | `compute/idempotent-reconvergence` | uc-pipeline-idempotent-001 | 3 | WS-B | wk4 |
| 11 | `cross-domain/dynamic-rehydration` | uc-pipeline-rehydrate-001 | 4 | WS-A | wk4 |
| 12 | `observability/rehydration-rto-measurement` | uc-pipeline-rto-001 | 4 | WS-E | wk4 |
| 13 | `cross-domain/provider-portable-rebuild` | uc-pipeline-portable-001 | 5 | WS-B | wk5 |
| 14 | `infrastructure/profile-based-deployment` | uc-pipeline-profile-001 | 5 | WS-F | wk5 |

### 13 Section 16 UCs (kept, no pipeline overlap)

| # | Handle | UUID | Gates | WS | Week |
|---|---|---|---|---|---|
| 1 | `libvirt-vm-provider/standard/vm-resource-representation` | uc-895e5ab0 | G1 | WS-D | wk2 |
| 2 | `cross-domain/solution-architecture-deployment` | uc-a4a4f8def3ca | G1,G3 | WS-C | wk3 |
| 3 | `compute/vm-standard-provision` [PF] | uc-seed-001a | G1,G2,G6,G7 | WS-B | wk2-3 |
| 4 | `governance/minimal-profile-policy-scope-boundary` [PF] | uc-seed-009a | G1 | WS-F | wk3 |
| 5 | `libvirt-vm-provider/standard/vm-status-provenance` | uc-8b603f5a | G2 | WS-I | wk3 |
| 6 | `data/persistent-volume-provision` [PF] | uc-seed-004a | G2 | WS-B | wk3 |
| 7 | `dcm-core/standard/udlm-dependency-graph-data-model` | uc-73071912 | G3 | WS-I | wk3 |
| 8 | `libvirt-vm-provider/standard/cross-provider-dependency-ordering` | uc-a537b0a9 | G1,G3 | WS-B | wk3 |
| 9 | `libvirt-vm-provider/standard/dependency-failure-impact` | uc-4908573a | G3 | WS-B | wk3 |
| 11 | `compute/vm-provision-with-provider-failure` [PF] | uc-seed-006a | G4,G6 | WS-B | wk4 |
| 15 | `governance/audit-merkle-tree-verification` [PF] | uc-seed-007a | G7 | WS-E | wk5 |
| 16 | `governance/policy-override-approval` [PF] | uc-seed-005a | G7 | WS-C | wk5 |
| 17 | `libvirt-vm-provider/standard/provider-registration-capability` | uc-cd9b798f | G8 | WS-H | wk3-5 |

### 7 Trifecta companion UCs (Piotr-feedback validation)

| # | Handle | UUID | Gates | WS | Week |
|---|---|---|---|---|---|
| 19a | `policy-resolution-capability` | uc-policy-resolution-capability | G1,G7 | WS-F | wk3 |
| 19b | `policy-applicability-data-model` | uc-policy-applicability-data-model | G1,G7 | WS-F | wk3 |
| 20a | `profile-resolution-capability` | uc-profile-resolution-capability | G1 | WS-F | wk3 |
| 20b | `profile-approved-list-data-model` | uc-profile-approved-list-data-model | G1 | WS-F | wk3 |
| 21a | `audit-chain-proofs-capability` | uc-audit-chain-proofs-capability | G7 | WS-I | wk5 |
| 21b | `audit-chain-output-verification` | uc-audit-chain-output-verification | G7 | WS-I | wk5 |
| 21c | `audit-chain-data-model` | uc-audit-chain-data-model | G7 | WS-I | wk5 |

### 6 Section 16 UCs REMOVED (replaced by pipeline UCs)

| Removed UC | Replaced by |
|---|---|
| uc-baremetal-pxe-provision (#10a) | uc-pipeline-bm-001 |
| uc-6e1e27735e9c (#14 drift detection) | uc-pipeline-drift-001 |
| uc-126b4231c0f8 (#10 full DC rehydration) | uc-pipeline-rehydrate-001 |
| uc-b53c099c325d (#12 resilience posture) | uc-pipeline-rto-001 |
| uc-a4f95cd66c7c (#18 workload portability) | uc-pipeline-portable-001 |
| uc-c600fab7 (#13 VM lifecycle reconciliation) | uc-pipeline-idempotent-001 |

### Parallel UC streams

**Cost management (Pau Garcia Quiles):** 22 cost UCs incoming, separate from this pipeline.

## 7. Jira mapping

Each UC becomes a **Story** in FLPATH (34 total):

- Labels: `pipeline` + `act-N` (pipeline UCs), gate labels, workstream, demo-week
- Epic: one per act for pipeline UCs
- Trifecta companions: sub-tasks of their seed story
- Jira mapping script: `dav/scripts/dav-to-jira.py`

## 8. Architecture gaps (from roadmap Section 12)

These remain open and affect pipeline UC authoring:

| Gap | Status | Impact on pipeline |
|---|---|---|
| Authorization model | Keycloak = authn confirmed; authz undecided (Authorino vs Kessel vs OPA-native) | Act 0 UC covers authn only; authz UC deferred until decision lands |
| UDLM conformance | Four-state stores not yet wired | Acts 2-4 depend on this; Seq 7 is the conformance UC |
| Bare-metal-as-a-service provider | Definition in progress with OSAC (OSAC = Red Hat-only; DCM needs vendor-agnostic scaffolding) | Act 1 UCs |
| Composite service mapper | Process provider pattern (not likeC4-native) | Act 2, Seq 5 |
| Common taxonomy | New repo needed across DCM/UDLM/OSAC (decided Jun 30) | Terminology alignment for all UCs |

## 9. DAV scoping sets

Pipeline UCs are organized into DAV scoping sets for targeted evaluation:

| Set Name | Contents | Purpose |
|---|---|---|
| `pipeline-all` | All 14 pipeline + 11 adopted = 25 UCs | Full pipeline evaluation |
| `pipeline-wk2` | Seq 1-4 + 7 (5 UCs) | Week 2 deliverable validation |
| `pipeline-wk3` | Seq 5-6 + 8 + adopted wk3 UCs | Week 3 deliverable validation |
| `pipeline-wk4` | Seq 9-12 + adopted wk4 UCs | Week 4 deliverable validation |
| `pipeline-wk5` | Seq 13-14 + adopted wk5 UCs | Week 5 deliverable validation |

Sets are created via the DAV API (`/api/sets`) once UCs are synced to the DAV database.

## 10. DAV ↔ Git sync

UC YAML files in `dcm/dav/use-cases/` are the **source of truth**. The DAV console
database must stay in sync with git:

- **Git → DAV:** UCs authored as YAML are imported into the DAV database via file path
  or API import. This is the primary flow.
- **DAV → Git:** Changes made in the DAV console (e.g., new UCs from UC Assist) should
  be exportable back to YAML. This is a future enhancement — tracked as a DAV engine
  work item for the other session.

## 11. Next steps

1. ~~Author gap UCs~~ — done (14 UCs, all validated)
2. ~~Update dav-to-jira.py~~ — done (all 14 UCs mapped)
3. Create Jira stories (test with `vm-standard-provision` first, then batch)
4. Create DAV scoping sets via API
5. Pau creates `dav` directory in cost DCM provider repo; cost UCs auto-ingested
6. Planning meeting (next week): team estimates sizing per work item
