# DCM Roadmap — Session Summary for the Building Session

_Context handoff for the session executing the 6-week demo roadmap. Covers everything decided and discussed across the planning sessions (this Claude session) and the Jun 26 roadmap meeting with Piotr Kliczewski and David Cannon._

---

## The Situation

Chris has a **6-week window** to produce a customer-shippable demo of DCM/UDLM. The demo will be:
1. **Handed to select FSI customers** for co-engineering
2. **Demoed live at a community gathering** of those customers
3. **Packaged as software profiles** a customer can re-stand-up in their own homelab

The full roadmap is at `dav/docs/internal/dcm-udlm-6-week-demo-roadmap.md`. The execution handoff is at `dav/docs/internal/dcm-udlm-6-week-demo-handoff.md`. This document summarizes the key decisions and context from the planning work and the Jun 26 meeting.

---

## Key Decisions from the Jun 26 Meeting (Chris, Piotr, David)

### 1. Backend functionality over UI
The team agreed that **data model, composite services, and rehydration take priority over UI**. Piotr: "functionality wise what they really need is running applications." The UI can come later — having customers complain about the interface means they care enough to use it.

### 2. What customers are asking for (the deliverables)
Chris laid out the priority deliverables:
1. **Bootstrap DCM from bare metal** (laptop + thumb drive → running system)
2. **Bare metal as a service** (provision bare metal nodes via DCM)
3. **Rehydration based on the data model** (dependency graph, dynamic derivation)
4. **Composite services** (ingest likeC4/C4 patterns → governed multi-tier deployment) — PNC specifically requested this
5. **Easy provider integration** (make it simple to add new service providers)
6. **Easy consumption** (reduce adoption friction — multiple customers cited this as their #1 challenge)

### 3. OSAC integration strategy = scaffolding, not dependency
OSAC is **Red Hat-specific** — "there is nothing agnostic about them" (Piotr). So:
- DCM **cannot rely on OSAC for all providers**
- Instead: **scaffold how someone creates a provider** and make it easy
- Use OSAC's work where it fits (they have enclave deployment, AI service types)
- DCM focuses on the **control plane: types, policies, orchestration**
- OSAC provides **infrastructure management pieces** that DCM calls

### 4. Data model is the #1 technical gap
Chris: "The thing that we didn't have for Summit — we had a very particular thing enabling that three-tier application. It didn't use much of the data model if anything." Piotr confirmed: "it was really basic."

**The data model must be implemented for real.** Not fit-for-purpose Summit demo approximations. The control plane needs:
- UDLM four-state stores (intent, requested, realized, discovered)
- Dependency graph that enables rehydration ordering
- Composite service model for architecture-as-input
- Entity model with stable UUIDs and provenance

### 5. Security in the control plane is required
Authentication via **Keycloak** is confirmed. Authorization is an open design question — Keycloak doesn't do authz. Options discussed:
- Alterino (OCP-based, OSAC uses it)
- Kessel (not ready)
- Needs design investigation as part of the roadmap

### 6. AI service types — future but noted
Piotr proposed supporting AI service types (deploy agents, LLMs) as a resource type. Would need UDLM resource types for AI + integration with OpenShift AI or similar. Not a 6-week priority but noted for the roadmap.

### 7. Timeline and process
- Chris refines the roadmap and sends to Piotr (done — the roadmap doc)
- **Monday engineering meeting**: sign off on the roadmap
- **Wednesday**: communicate to stakeholders
- Chris validates with customers in parallel
- OSAC follow-up scheduled early July
- Chris will share with Michael to explore collaboration/assistance

---

## What This Session Produced (Context the Building Session Needs)

### Documents in `dav/docs/internal/`

| Document | What it is |
|----------|-----------|
| `dcm-udlm-6-week-demo-roadmap.md` | **The authoritative plan.** 9 workstreams (A-I), 6 weekly milestones, 8 acceptance gates, risk table with pre-decided fallbacks. Read this first. |
| `dcm-udlm-6-week-demo-handoff.md` | **The execution pickup.** Self-contained onboarding: where everything lives, how to access DAV, week 1 tasks in order, working constraints. |
| `dcm-udlm-whitepaper.md` | Internal whitepaper — the thesis, evidence, and vision. §7 has "The Destination" (unified control plane for everything). |
| `dcm-udlm-whitepaper-public.md` | Public/community edition — genericized, no customer names, no Red Hat internal refs. |
| `dcm-udlm-operations-whitepaper.md` | Operational whitepaper — how you run a data center on the model. §7 has honest maturity. §2-5 define the operational loop. |
| `dcm-udlm-executive-brief.md` | 2-page brief — carries the vision ("everything as a service") and partner CTA. |
| `dcm-udlm-thesis.md` | The core argument in one page — 5-link chain of reasoning, 4 disciplined negatives. |
| `udlm-sdlc-extension-blueprint.md` | Delivery family extension — how UDLM extends to cover the full SDLC. |
| `udlm-sdlc-customer-blueprint.md` | Customer-facing adoption guide for SDLC unification. |
| `2026-06-23-ibm-concert-competitive-analysis.md` | IBM Concert comparison — where DCM should close gaps. |
| `dav-value-proposition-deck.md` | DAV's role as the reasoning layer for DCM. |
| `udlm-unified-data-dependency-exec-brief.md` + `.pptx` | Exec deck: UDLM as unified data + dependency graph. |
| `2026-06-16-security-audit-full.md` | Comprehensive security audit (4 critical, 12 high findings). |
| `2026-06-16-security-audit-handoff.md` | Security remediation handoff (prioritized, actionable). |

### Use Cases in DAV (project 20, DCM)

The DAV database has **129+ UCs** across these sets:

| Set | Count | Source |
|-----|-------|--------|
| OSAC Use Cases | 15 | Metering (VM, BM, DBaaS, storage, DNS, etc.) |
| Truist - Consulting Approach Doc | 14 | Network automation, ServiceNow, F5, AI intake |
| Truist - Network Automation (Kranthi) | 6 | Branch provisioning, config backup, drift, MCP intake |
| Truist - Platform Services (Reverse EBC) | 4 | Event-driven remediation, testing, OTel, KPI |
| PNC - Transcript-Derived | 5 | Rehydration, path-to-production, cost placement, drift, solution architecture |
| BofA - Transcript-Derived | 3 | DC rehydration, VM factory conformance, domain boundary |
| Barclays 2026-05-20 | 6 | FSI governance, auth, ansible, cross-domain, regulated DC |
| Cross-Customer - DCM Capabilities | 3 | Workload portability, reverse dependency, schema migration |
| Supply Chain Security — CI/CD Services | 6 | Hardened library resolution, SBOM, trust coverage, SLSA pipeline |
| Concert — Capability Gap Analysis | 15 | Observability providers, cost, incident, vulnerability, resilience, ITSM |
| Sovereign BU | 2 | Confidential computing |
| DCM Core Architecture | 5 | Core architecture validation |
| Libvirtd VM Provider | 10 | VM provider integration |
| Various Truist/customer sets | ~35 | Additional customer-specific UCs |

### The Critical Gap: UDLM Conformance (WS-I)

**This is the most important thing the building session needs to understand.**

The DCM control plane today uses UDLM *concepts* but is **not a UDLM-conformant realization**. The Summit demo's data model was fit-for-purpose, not spec-compliant. The roadmap adds **WS-I** to make DCM genuinely implement UDLM:

- **Four-state stores** — intent (immutable), requested (append-only per policy cycle), realized (versioned snapshots), discovered (ephemeral) — per `foundations/four-states.md`
- **Entity model** — stable UUIDs, field-level provenance, data classification, lifecycle state — per `foundations/entity-types.md`
- **Provider contract** — registration with capability declaration, health check, state-change reporting — per `contracts/provider-contract.md`
- **Policy contract** — evaluation context, convergence model, eight policy types — per `contracts/policy-contract.md`
- **Wire compatibility** — API output readable by any UDLM-conformant system

**Minimum for the 6 weeks:** four-state stores + entity model (the visible part). Provider/policy contract alignment is incremental. Wire compatibility and conformance endpoints are stretch.

**The discipline: every piece of data the demo shows on screen must be a real UDLM entity in a real UDLM four-state store.**

### Week 1 Priorities (in order)

1. **★ Verify the Act II trigger** — does `:rehydrate` re-run placement/policy/provider-resolution (dynamic derivation) or is it static replay / spec-only?
2. **★★ Audit UDLM conformance gap** — map current Go structs/DB schema against UDLM spec, produce gap inventory
3. **Vertical slice on laptop** — `make compose-up` + three-tier provider, prove provision + rebuild
4. **likeC4 pipeline spike** — author the "Sovereign 3-Tier" model, prove `export json`, sketch mapper
5. **UDLM registry gap inventory** — which resource types the demo needs that are missing
6. **Author bare-metal UCs** in DAV project 20
7. **WS-H scoping** — read provider contract, scope libvirt-as-DCM-provider wrap

### The Two North Stars

Everything serves these:
1. **Easy consumption** — one pattern → governed multi-tier outcome, no hand-wiring
2. **Easy provider integration** — adding a provider is cheap and contract-driven

### Risks with Pre-Decided Fallbacks

| Risk | Fallback |
|------|----------|
| Act II trigger is spec-only | Wire the minimal path that invokes the existing planner over stored state |
| Provider wrap (libvirt) slips | Demote to narrated: show contract + guide without live plug-in |
| Install-half needs Redfish | PXE-next-boot or virsh/sushy stand-in |
| Full PXE-on-VMs slips | Pre-stage cluster, rehydrate from cluster layer up |
| UDLM conformance larger than scoped | Protect four-state stores (the visible part), incremental everything else |
| likeC4 mapper slips | Hand-author the CatalogItem, show likeC4 as design source |

**Guiding rule:** protect the vertical slice (one pattern → full stack → rehydrate) over breadth. Narrow arc that round-trips beats wide arc that doesn't rehydrate.

---

## What the Meeting Didn't Cover (But the Roadmap Does)

- The **likeC4 → composite service pipeline** (WS-C) — designed in roadmap §7
- **Software profiles** (WS-F) — baseline + homelab, not hardware topologies
- **Operational characteristics doc** (WS-F) — day-2 operations grounded in the operations whitepaper
- The **co-engineering hand-off kit** (WS-G) — pinned repos, profiles, runbook, patterns, provider guide
- **Acceptance gates** (roadmap §6) — 8 DAV-corpus-derived criteria, mapped to weeks
- The **security audit findings** that are relevant to the control plane (auth guard gaps, no DB backup, etc.)

---

## Access

- **DAV API:** `https://10.0.90.22:8843` or `https://dav.roadfeldt.com:8843`
- **Auth:** `Authorization: Bearer $(cat ~/.claude-personal/.dav-token)` or `$(cat ~/.claude-work/.dav-token)`
- **DCM project:** `X-DAV-Project: 20`
- **UDLM spec:** `/Users/chris/git/udlm` (branch `feat/resource-type-registry`)
- **DCM design docs:** `/Users/chris/git/dcm`
- **Control plane:** `github.com/dcm-project/control-plane`
- **dcm-bootstrap:** `github.com/heatmiser/dcm-bootstrap`
