# DAV Feature Requests & Takeaways — Meeting Transcript Analysis (March–June 2026)

Consolidated analysis of 9 meeting recordings spanning March 19 – June 2, 2026. Transcribed via Whisper, analyzed for DAV-relevant takeaways. Covers engagements with PNC, Bank of America, Barclays, Truist, JPMC (Jeff), and internal Red Hat sessions.

---

## Meetings Analyzed

| Date | Duration | Meeting | DAV Relevance |
|------|----------|---------|---------------|
| 2026-03-19 | 26 min | PNC — VCF migration / rehydration pitch | Medium |
| 2026-03-23 | 25 min | Red Hat internal — BofA account sync | Low |
| 2026-04-06 | 29 min | Red Hat internal — BofA account call | Medium |
| 2026-04-16 | 36 min | DCM overview with Jeff (external partner) | Medium |
| 2026-05-08 | 31 min | Flight Path intro — Truist (Joe) | None |
| 2026-05-18 | 46 min | DCM deep-dive — Barclays/Apex pre-session | High |
| 2026-05-20 | 2h53m | DCM + Apex alignment — Barclays full session | High |
| 2026-05-28 | 2h57m | DCM presentation + service mesh — PNC in-person | High |
| 2026-06-02 | 54 min | Truist — ServiceNow/AAP integration | None |

---

## Feature Requests (aggregated across all meetings)

### 1. CI/CD Gate Mode (headless/CLI)

**Source:** PNC meeting (2026-05-28)

Chris explicitly pitched DAV as a CI/CD pipeline validator: "As we make changes to the data model for the architecture, we can validate that all of your use cases are going to be fulfilled." This implies DAV needs:
- A CLI/API mode that runs headlessly
- Pass/fail output plus structured gap report
- Integration with git-based architecture repos (run on PR/push)

This is the strongest new feature request across all recordings.

### 2. Cross-Architecture Comparison Mode

**Source:** Barclays meetings (2026-05-18, 2026-05-20)

Both Barclays sessions centered on comparing DCM (centralized control plane) vs Apex (federated broker model). Christy's team wants to compare DCM blue boxes against what they already have, find gaps, and phase a transition. DAV should be able to ingest two architecture specs and produce a structured comparison: alignment, differences, tradeoffs, integration points.

### 3. Federated Architecture Evaluation

**Source:** Barclays meetings (2026-05-18, 2026-05-20)

DAV currently evaluates use cases against centralized specs. Apex is fully federated with per-domain broker instances communicating via pub/sub. DAV needs to handle or acknowledge federated control plane patterns vs centralized ones.

### 4. Dependency Graph Completeness Analysis

**Source:** Barclays meeting (2026-05-20), PNC meeting (2026-05-28)

Both DCM and Apex struggle with reverse dependency queries. Dependency graph completeness should be an evaluation criterion when analyzing use cases — flag scenarios where the dependency model is insufficient for the use case (e.g., rehydration requires full forward+reverse dependency traversal).

### 5. Policy Coverage Analysis

**Source:** Barclays meetings (2026-05-18, 2026-05-20)

OPA/Rego policies are the brains of DCM. DAV could evaluate whether a use case has sufficient policy coverage across placement, sovereignty, enrichment, and validation dimensions.

### 6. Cost Model Readiness Flagging

**Source:** Barclays meetings (2026-05-18, 2026-05-20)

FinOps/cost analysis was a major Barclays ask. DAV could flag use cases that require cost model metadata in provider definitions when that metadata doesn't exist.

### 7. Schema/Data Model Comparison

**Source:** Barclays meeting (2026-05-20), Jeff meeting (2026-04-16)

Kevin (Barclays) repeatedly asked to see the data model. Jeff said he'd feed DCM architecture into his own AI system and "ask it to do a comparison." DAV needs to ingest structured schemas (JSON Schema, OpenAPI) not just prose docs.

### 8. Workload Rehydration Assessment

**Source:** PNC meeting (2026-03-19), PNC meeting (2026-05-28), BofA via Barclays meeting

Victor (PNC CTO) wants segmentation: "what can be rehydrated vs. what has to be migrated?" DAV could evaluate workload characteristics against target-platform requirements to flag non-compliant patterns. Bank of America wants full data center rehydration.

---

## New Use Case Categories

From across all meetings, these use case categories were identified as targets for DAV analysis:

- **Application rehydration / repave** — can this workload land on the target platform as-is? (PNC)
- **Path-to-production policy enforcement** — does this deployment meet standards before it lands? (PNC)
- **Data sovereignty / region-constrained placement** — OPA policies enforcing geographic constraints (Barclays, Summit demo)
- **Data center failover / DR rehydration** — replay all intents to rebuild an entire DC (BofA)
- **Workload portability** — move a workload from one provider to another (Barclays)
- **Service mesh requirements** — namespace delegation, cross-cluster mesh, JWT auth, circuit breaking, ambient mode (PNC)
- **Cost-based auto-placement** — use cost analysis to select cheapest qualifying provider (Barclays, PNC)
- **Policy-driven architectural enrichment** — auto-enrich requests to meet production requirements (Summit demo)
- **Tool/domain confusion detection** — flag anti-patterns like using Terraform for app config (BofA)

---

## Customer Use Cases Expected

| Customer | Status | Format | Notes |
|----------|--------|--------|-------|
| PNC | Promised ~1 week from 2026-05-28 | Deck | Solution architecture + service mesh RFP requirements |
| Barclays | Ongoing | Apex architecture docs | Blue-box comparison exercise |
| Bank of America | Scoping | TBD | VM factory + rehydration; Chris assessing fit |
| JPMC (Jeff) | Interested | Self-serve | Jeff will pull DCM prompt into his own AI system for comparison |

---

## Repos / Specs to Onboard

- **DCM-project on GitHub** — Summit demo code (service providers, control plane). V1 quality, V2 incoming.
- **UDLM** — Already visible to PNC; needs to be presentation-ready since customers are looking at it directly.
- **OSAC providers** — Pre-built automation providers. Could become additional spec repos DAV validates against.
- **Cost operator** — Existing OCP operator being considered for DCM control plane integration.
- **PNC service mesh RFP spreadsheet** — Detailed requirements for service mesh evaluation.
- **DCM AI prompt document** — PR #63 on DCM repo. Being shared externally as onboarding material. DAV's ingested version should stay current.

---

## Positioning Insights

### "Don't tell the doers about DCM" (BofA, 2026-04-06)
Ed (SA) cautioned Chris that DCM/DAV value lands at the strategy/architecture level, not at the practitioner level. The tooling needs to produce outputs that practitioners consume (pass/fail, recommendations) without requiring them to understand the model.

### "Cost avoidance is bullshit" (PNC, 2026-03-19)
Victor (PNC VP) rejected cost-avoidance ROI pitches. He wants Red Hat to demonstrate accelerated "path to production" and rehydration capability. DAV should frame its outputs around capability enablement, not cost savings.

### Jeff will compare independently (2026-04-16)
Jeff explicitly said he would pull the DCM architecture into his own internal AI system and do his own comparison. This validates that DAV-style LLM architecture analysis has external demand. It also means the AI prompt document (PR #63) needs to be comprehensive since it's the input.

### DCM operational model diagram missing (Barclays, 2026-05-18)
Christy asked for an operational model diagram (not just architecture). Chris acknowledged one doesn't exist. When created, DAV should ingest it alongside the architectural specs.

### DCM orchestration ≠ Ansible orchestration (Barclays, 2026-05-18)
Important distinction: DCM orchestration is internal policy sequencing/job management, not AAP job templates. DAV should capture this clearly when evaluating orchestration-related use cases.

---

## Operational Follow-Ups

1. **UDLM needs to be presentation-ready** — PNC was looking at it during the meeting and specs are incomplete.
2. **Multi-tenant use case management** — Multiple customers sending use cases (PNC, Barclays, BofA, JPMC). DAV needs customer/tenant tagging.
3. **Cross-customer collaboration forum** — Chris wants to re-establish the NYC-hosted forum from last year. DAV could be the demo vehicle.
4. **Ansible Automation Orchestrator** — New AAP product (~Sept 2026). Barclays seeking early adopter. Not DCM but could integrate.
5. **Intel-funded lab** — Physical co-engineering environment with bare metal + Pure Storage. Target for multi-customer DCM testing.
6. **PNC follow-up meetings** — Weekly/biweekly cadence being established. PNC wants to deploy DCM Summit demo internally.

---

## Implementation Priority (for DAV)

1. **CI/CD gate mode** (#1) — Strongest signal; Chris is actively pitching this to customers
2. **Multi-tenant UC management** — Multiple customers sending use cases now
3. **Cross-architecture comparison** (#2) — Would have saved hours of manual comparison at Barclays
4. **Schema/data model ingestion** (#7) — Kevin's #1 ask; Jeff's independent comparison need
5. **Rehydration assessment** (#8) — PNC and BofA both need this
6. **Dependency graph analysis** (#4) — Both DCM and Apex need this
7. **Policy coverage** (#5) and **cost model readiness** (#6) — Follow-on features
