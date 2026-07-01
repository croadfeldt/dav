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

## 6. Pipeline UCs by week (finalized)

All 14 gap UCs have been authored and validated. 42 total UCs in the DCM corpus
(28 existing + 14 new pipeline UCs). All validate against the JSON schema.

### Week 2 — Authenticate + Bootstrap

| Seq | Handle | UUID | Act | WS |
|---|---|---|---|---|
| 1 | `identity/actor-authentication` | uc-pipeline-authn-001 | 0 | WS-B |
| 2 | `infrastructure/bare-metal-pxe-bootstrap` | uc-pipeline-bm-001 | 1 | WS-A |
| 3 | `infrastructure/cluster-bootstrap` | uc-pipeline-cluster-001 | 1 | WS-A |
| 4 | `infrastructure/control-plane-deployment` | uc-pipeline-cp-001 | 1 | WS-B |
| 7 | `data/four-state-store-conformance` | uc-pipeline-4state-001 | 2 | WS-D |

**Milestone:** Control plane operational on bare metal; four-state stores initialized.

### Week 3 — Provision from Composite Service

| Seq | Handle | UUID | Act | WS |
|---|---|---|---|---|
| 5 | `cross-domain/composite-service-to-catalog-item` | uc-pipeline-catalog-001 | 2 | WS-C |
| 6 | `cross-domain/composite-service-provision` | uc-pipeline-composite-001 | 2 | WS-B |
| 8 | `governance/sovereignty-validation-policy` | uc-pipeline-sov-001 | 2 | WS-F |
| — | `compute/vm-standard-provision` (existing) | uc-seed-001a | 2 | WS-B |
| — | `cross-domain/tenant-onboarding` (existing) | uc-seed-008a | 2 | WS-B |
| — | `governance/minimal-profile-policy-scope-boundary` (existing) | uc-seed-009a | 2 | WS-F |

**Milestone:** Full provision arc from composite service definition to running stack.

### Week 4 — Operate + Destroy and Rebuild (headline)

| Seq | Handle | UUID | Act | WS |
|---|---|---|---|---|
| 9 | `observability/drift-detection-remediation` | uc-pipeline-drift-001 | 3 | WS-B |
| 10 | `compute/idempotent-reconvergence` | uc-pipeline-idempotent-001 | 3 | WS-B |
| 11 | `cross-domain/dynamic-rehydration` | uc-pipeline-rehydrate-001 | 4 | WS-A |
| 12 | `observability/rehydration-rto-measurement` | uc-pipeline-rto-001 | 4 | WS-E |
| — | `compute/vm-provision-with-provider-failure` (existing) | uc-seed-006a | 4 | WS-B |

**Milestone:** Destroy and rebuild from data model; RTO measured; idempotency proven.

### Week 5 — Portability + Hardening (stretch)

| Seq | Handle | UUID | Act | WS |
|---|---|---|---|---|
| 13 | `cross-domain/provider-portable-rebuild` | uc-pipeline-portable-001 | 5 | WS-B |
| 14 | `infrastructure/profile-based-deployment` | uc-pipeline-profile-001 | 5 | WS-F |
| — | `governance/audit-merkle-tree-verification` (existing) | uc-seed-007a | — | WS-E |
| — | `governance/policy-override-approval` (existing) | uc-seed-005a | — | WS-C |

**Milestone:** Provider portability proven; software profile reproducibility validated.

### Existing UCs adopted into pipeline (not sequenced)

These existing UCs contribute to the pipeline narrative but don't carry pipeline
sequence numbers — they validate specific capabilities the pipeline depends on:

| Handle | UUID | Relevant Act |
|---|---|---|
| `compute/vm-tenant-isolation-enforcement` | uc-tenant-iso-001 | 2 |
| `identity/auth-provider-drift-detection` | uc-seed-003a | 3 |
| `cross-domain/sovereign-decommission-with-peer` | uc-seed-002a | 4 |
| `data/persistent-volume-provision` | uc-seed-004a | 2 |

### Parallel UC streams

**Cost management (Pau Garcia Quiles):** 22 cost UCs incoming as YAML files via a
`dav` directory in the cost DCM provider repo. These feed into the same
UC → capability → workstream → Jira pipeline but are authored by Pau's team, not
as part of this pipeline sequence. DAV will auto-ingest them for gap analysis once
the directory is created.

## 7. Jira mapping

Each pipeline seed UC becomes a **Story** in FLPATH, linked in pipeline order:

- `blocks` / `is blocked by` relationships mirror the `depends_on` UUIDs
- Labels: `pipeline`, `act-N`, `demo-wkN`, workstream labels
- Epic: one per act (e.g., "Act 2 — Provision from Composite Service")
- The trifecta companions become sub-tasks of the seed story
- Pau's 22 cost UCs also become Jira tickets (Piotr's ask, Jun 30)
- Jira mapping script: `dav/scripts/dav-to-jira.py` (updated with all 14 new UCs)

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
