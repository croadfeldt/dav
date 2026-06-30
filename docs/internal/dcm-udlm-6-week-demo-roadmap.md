# DCM / UDLM — 6-Week Roadmap to a Customer-Shippable Rehydration Demo

> **Status:** draft · **Author:** Chris Roadfeldt (with Claude) · **Drafted:** 2026-06-24 · **Rev:** 2026-06-28 (goals locked from Jun 26 meeting; real-mechanics + bare-metal-bootstrap + approved-architecture-catalog committed; merge-work + architecture-gaps added)
> **Horizon:** 6 weeks (target live demo ≈ week 6) · **Home:** `dav/docs/internal/` (**private draft —
> stays here**, per Chris 2026-06-26).
> **Executing this?** Start with the self-contained pickup: [`dcm-udlm-6-week-demo-handoff.md`](./dcm-udlm-6-week-demo-handoff.md).
>
> **Decisions (Chris, 2026-06-26):** (1) keep this a **private draft** in `dav/docs/internal/`. (2) The
> deliverable is **software enablement, not a lab buildout** — *"dedicated lab specs are not important, only
> software enablement is."* So the roadmap targets **software that can drive the full arc on any conformant
> substrate** (VMs included); the lab is just a runtime. (3) Bare-metal boot uses dcm-bootstrap's
> **PXE + DNS-SRV phone-home** discovery (confirmed below) — **no BMC required**, runs on libvirt VMs;
> virtual-BMC only if the private install collection turns out to need Redfish. (4) **Author the bare-metal
> UCs** in DAV to back the leg we show (committed, wk1–2). (5) Rehydrate-fallback authority — pending
> (see §11).

## 1. What we are building and why

A **skeleton working demo** — a walking skeleton, thin but end-to-end — with **two co-equal acts** on one
3-tier application, from **bare metal up**:

> **Act I — provision from a pattern:** an architect's **pre-approved architectural pattern** (likeC4)
> stands up the initial resources — bare-metal node → OpenShift cluster → VM → 3-tier app (web/app/db).
> **Act II — dynamic rebuild:** kill it, and the system **dynamically reasons over the dependency graph +
> stored data + current policies + available providers to figure out *what* to rebuild and *in what order*,
> then does it** — coming back matching intent, re-governed by policy.

> **Bare metal up = DCM bootstraps its own foundation.** "From bare metal" is literal and recursive: the arc
> includes **standing up the bare-metal substrate DCM itself runs on** — via Heatmiser **`dcm-bootstrap`**
> (PXE → discovery ISO → DNS-SRV phone-home, no BMC) — *before* DCM goes on to provision the cluster, VM, and
> app tiers. The self-hosting story (the system brings up its own ground, then everything above it) is part of
> the headline.

**Real scenarios, real mechanics — non-negotiable.** This version runs **real-life scenarios with real-life
mechanics**: every act executes through the *actual* control-plane logic and *actual* providers, and **Act II's
rehydrate reuses the exact plan-and-execute path of the initial provisioning** — the same placement, policy, and
provider-resolution machinery, re-invoked over stored state. No static replay, no mocked demo shortcut.

**Architecture ingestion → composite resource request (first-class capability).** The version must be able to
**ingest a likeC4 architecture model and map it to a composite resource request** — i.e. an architect's diagram
(`likeC4 export json`) is translated by a mapper into a **UDLM composite service / DCM CatalogItem**, which on
submission becomes a **composite resource request (intent)** the control plane decomposes, places, governs, and
realizes. This is the consumption front door (goal #1) and Act I's mechanism: *architecture-as-input → governed
multi-tier outcome*, no hand-wiring of individual resources. See WS-C.

**The point of the demo is Act II's intelligence, not a script.** The headline is *the graph, the data, the
policies, and the providers working together to derive the rebuild plan and execute it* — placement (CEL +
DAG) + policy (OPA/Rego) + provider resolution, run over stored state, **computing** the plan rather than
following a recorded one. The demo must **surface that derived plan on stage** (the computed order +
placement + policy decisions) before it executes, so the audience sees the system *think*, not just
*replay*. Act I proves the other half of the value: **consuming infrastructure is as easy as picking a
pattern**.

**Ultimate goals (the why behind both acts).** Everything serves two adoption north-stars:
1. **Easy consumption** — a consumer gets a correct, governed, multi-tier outcome by declaring one pattern,
   not by wiring resources. (Act I is this made visible.)
2. **Easy provider integration** — adding a new provider to realize resources is cheap and contract-driven,
   so the catalog of what-you-can-consume grows by community contribution. (We demonstrate/narrate the
   provider contract and what it takes to plug one in — see WS-H.)
The demo exists to **promote use of the system and enable consumers**: easy in, easy to extend, and the
hard part (figuring out the rebuild) handled for you.

It exists to be **handed to select customers** for **co-engineering**, and to be **demoed live at a
community gathering of those customers**. **The deliverable is software enablement, not a lab buildout** —
the goal is *software that can drive the full arc on any conformant substrate*, so the demo is portable by
construction. A dedicated lab is merely where we run it; **no specific lab topology is a requirement**. That
makes the **packaged software profiles + defined operational characteristics** the real first-class
secondary deliverable: a customer stands up the same software on their own homelab VMs and co-engineers
against it. Concretely, *substrate-agnostic* means every layer must be drivable on **libvirt/KVM VMs**, not
just physical hardware (the bare-metal boot path below makes this true).

This is not a green-field bet. A **sovereignty-rehydrate demo already ran on real software in May 2026**
(`dcm-project.github.io` blog; `dcm-udlm-operations-whitepaper.md` §7) — three data centers, a priority-1
sovereignty Rego gate, intent replayed through rehydration on outage, landing only in a compliant region.
The 6-week task is to **extend that proven core** to the *full bare-metal → cluster → VM → 3-tier arc*,
drive Act I from a **likeC4 pattern**, make Act II's **derived plan visible**, and **package it as profiles**
a customer can take home.

The narrative spine is the **four-state lifecycle** (`dcm-udlm-thesis.md`,
`dcm-udlm-operations-whitepaper.md` §2): **Intent → Requested → Realized → Discovered**. Every layer of
the demo — bare metal, cluster, VM, each app tier — is submitted as a UDLM **intent**, enriched by
**policy** into a Requested form, **Realized** by a provider, and continuously **Discovered**. Act II's
rebuild is *not* a stored script: the system reads the stored **data** (intents + the dependency graph +
last-known-good), re-runs **placement** to compute order and **policy** to re-gate, resolves **providers**
for each resource, and **derives** the plan. That dynamic derivation — graph + data + policy + providers
together — is the thesis the demo must make visible at every step.

## 1a. Goals (Jun 26 engineering meeting) — and how the roadmap delivers them

These are the goals from the **Jun 26 2026 meeting** (Chris · Piotr Kliczewski · David Cannon), locked
2026-06-28. They are *the* acceptance frame for this version: every workstream traces to one.

**Two north stars (the why — everything serves these):**
- **N1 · Easy consumption** — a consumer gets a correct, governed, multi-tier outcome by declaring *what*,
  not wiring *how*. (Adoption friction was cited as customers' #1 challenge.)
- **N2 · Easy provider integration** — adding a provider is cheap and contract-driven, so the catalog of
  what-you-can-consume grows by contribution.

**Six capability goals (what customers asked for):**

| Goal | What it means | Delivered by |
|------|---------------|--------------|
| **G1 · Bootstrap DCM from bare metal** | Laptop + thumb drive → a running DCM that stands up its own foundation (self-hosting / day-0; Heatmiser `dcm-bootstrap` PXE + phone-home, no BMC) | **WS-A** (+ §2 Act I step 2) |
| **G2 · Bare metal as a service** | Provision bare-metal nodes *through* DCM — `BareMetalInstance` as a consumable resource | **WS-A** + **WS-D** (BM resource type) + **WS-B** (BM provider/realization) |
| **G3 · Rehydration from the data model** | Dependency graph + **dynamic derivation** rebuilds the estate to intent, reusing the actual initial-provision plan+execute path (no static replay) | **WS-B** (dynamic planner) on **WS-I** (real data model) |
| **G4 · Composite services from approved architectures** | A **suite of approved architectures** authored in likeC4, consumed as governed multi-tier deployments (**PNC-requested**) | **WS-C** (approved-architecture catalog) |
| **G5 · Easy provider integration** (= N2) | Scaffold provider creation; prove it with a working example added in the 6 weeks | **WS-H** |
| **G6 · Easy consumption** (= N1) | Reduce adoption friction to near-zero — one pattern → governed outcome | **WS-C** + the Service Catalog |

**Shaping decisions (the how — constraints on every goal):**
- **Backend over UI** — data model, composite services, rehydration first. (Piotr: *"what they really need is running applications."*) UI follows.
- **Data model implemented for real** — the #1 technical gap. The control plane becomes a genuine **UDLM realization** (**WS-I**): four-state stores, dependency graph, composite-service model, entity model with UUIDs + provenance. This is the credibility keystone — *every datum the demo shows is a real UDLM entity in a real four-state store.*
- **OSAC = scaffolding, not dependency** — OSAC is Red Hat-specific; DCM cannot depend on it for all providers. DCM owns the **control plane (types, policies, orchestration)** and makes provider creation easy (**WS-H**); use OSAC's pieces where they fit (enclave deployment, AI service types).
- **Security in the control plane is required** — **Keycloak** for authn (confirmed); **authz is an open design decision** (Alterino vs Kessel) — resolved within the roadmap (see §8/WS-B security note).

**Goal coverage check:** G1→WS-A · G2→WS-A/D/B · G3→WS-B/I · G4→WS-C · G5→WS-H · G6→WS-C; enablers: **WS-I** (conformance, underpins G3 + credibility), WS-D (registry), WS-E (acceptance), WS-F (profiles), WS-G (packaging). No meeting goal is unowned.

**Deferred (post-roadmap TODO — not a 6-week goal):** **AI service types** (Piotr's proposal — deploy
agents / LLMs as a UDLM resource type + OpenShift-AI integration). Noted here so it isn't lost; Chris works
it after this roadmap is locked. *Tracked separately.*

## 2. The demo, concretely (the walking skeleton)

A single scripted run, ~15 minutes on stage:

**Act I — provision from a pattern (easy consumption)**

1. **Pattern (the consumption front door).** An architect authors a "Sovereign 3-Tier Web App" as a
   **likeC4 model** (web → app → db, with a sovereignty constraint on placement). `likeC4 export json` →
   a small **mapper** turns it into a **UDLM composite service** / **DCM CatalogItem** — a
   *pre-approved architectural pattern*. The consumption story: a consumer picks this pattern, supplies a
   couple of bindings, and is done. *This is the composite-services-as-patterns requirement and goal #1.*
2. **Day-0 / bare metal.** `dcm-bootstrap` stands up the substrate (DNS/DHCP/TFTP/HTTP, local registry,
   content mirror as podman-quadlets). Nodes boot by **PXE → discovery ISO → DNS-SRV phone-home** (a
   *pull* model, no BMC): each node netboots, finds the controller via `_dcm-automation._tcp` SRV, and
   POSTs a hardware manifest keyed on DMI serial; the cluster then installs via Agent-Based Installer
   (`rhvp.ocp_landing_zone`). **Cluster nodes are libvirt VMs PXE-booting the same path** — no virtual BMC
   needed for discovery (see §3/§8 for the one install-half caveat).
3. **Provision the full stack from the one pattern.** Submit the composite-service intent. The control
   plane decomposes it and runs Catalog → **placement** (CEL + DAG → dependency order db→app→web) →
   **policy** (OPA/Rego sovereignty gate) → **providers**: cluster (ACM SP, already up), **VM**
   (`kubevirt-service-provider`), **app tiers** (`three-tier-app-demo-service-provider`: PetClinic web +
   app + Postgres). Realized state recorded; Discovered state probed. *One pattern → a governed multi-tier
   estate, no hand-wiring.*
4. **Show the four states.** Display Intent (immutable), Requested (what policy added), Realized (provider
   receipt), Discovered (live truth). Drift = Realized vs Discovered.

**Act II — dynamic rebuild (the system figures it out)**

5. **Disaster.** Destroy the cluster / fail the region.
6. **Derive the plan, live.** Trigger rebuild-from-stored-state. The system reads the stored **data**
   (intents + dependency graph + last-known-good), **re-runs placement** to compute *what* must come back
   and *in what order*, **re-evaluates policy** against current conditions, and **resolves a provider** for
   each resource — then **surfaces the computed plan on screen** (the ordered DAG + placement + policy
   verdicts) *before executing it*. This is the demo's headline: graph + data + policy + providers deciding
   the rebuild dynamically, not a recorded script. (Triggered via `…:rehydrate`; **entity UUIDs preserved**,
   new instance IDs minted, old cleaned up.)
7. **Execute & prove.** Run the derived plan; **measure RTO**; Realized matches Intent across every tier;
   the app serves again. *Stretch:* a provider is unavailable, so the plan **re-resolves onto a different
   provider** (provider-portable) with DNS/network/storage references rewritten — showing the planning is
   genuinely dynamic, and previewing goal #2 (providers are pluggable).

## 3. What's already real vs what we must build

Grounding the plan honestly (from repo + whitepaper + corpus research):

| Building block | State today | 6-week job |
|---|---|---|
| **Control-plane** (`dcm-project/control-plane`) | **Runs.** Go monolith, Postgres/SQLite, NATS; catalog→placement→policy(OPA)→SP flow in-process; `make compose-up`. | Stand it up in the lab; wire providers; **verify `:rehydrate` is wired, not enhancement-only**. |
| **3-tier provider** (`three-tier-app-demo-service-provider`) | **Runs.** PetClinic web/app/db; OpenShift backend default. | Use as-is for the app tier; drive it from the composite intent. |
| **VM provider** (`kubevirt-service-provider`) | Exists. | Stand up KubeVirt/CNV on the lab cluster; provision the VM tier. |
| **Cluster provider** (`acm-cluster-service-provider`) | Exists. | Cluster comes up via dcm-bootstrap; provider represents/manages it. |
| **Dynamic rebuild planner** (= placement + policy + provider-resolution, re-run over stored state) | **The reasoning machinery exists** (placement CEL+DAG, OPA policy, SP resolution all run today). The `:rehydrate` *trigger* is **designed** (`enhancements/rehydration-flow`, `@croadfel` reviewer): ID-separation, dependency-order replay, policy re-eval. | **Wk1: confirm the trigger re-runs placement/policy/provider-resolution over stored state (derives the plan), not just a static replay.** If the trigger is spec-only, wire the minimal path that *invokes the existing planner* over stored state — **and surface the derived plan**. *#1 risk; this is the demo's headline.* |
| **dcm-bootstrap** (`heatmiser/dcm-bootstrap`) | **Most productized.** bootc appliance; day-0 quadlets (dnsmasq/TFTP/nginx/quay); **boot = PXE → discovery ISO → DNS-SRV phone-home, no BMC** (matched on DMI serial); ABI install via `rhvp.ocp_landing_zone` (that collection is **private/404**). | Build connected; PXE-boot **libvirt VMs** (no virtual BMC for discovery). *Integration cost = the install half's power model is unverified (private); add PXE-next-boot or `virsh`/sushy stand-in if it needs Redfish.* |
| **likeC4** | Mature tooling; `export json`, Model API, MCP server. | **Write the likeC4-JSON → DCM-catalog mapper** (no built-in mapper). |
| **UDLM registry** (`croadfeldt/udlm`, `feat/resource-type-registry`) | ~7 resource types; lifecycle + composite-service model + likeC4 mapping **designed and complete**. | Add **minimum-viable** types the demo touches: BareMetalInstance/HostType, VirtualNetwork/Subnet, Storage, App/Tier. |
| **DAV DCM corpus** (project 20) | **129 UCs**; 6 rehydration UCs; 8 acceptance criteria derivable. | Use as **acceptance gates** (§6). |
| **The rehydration demo itself** | **Already shown May 2026** (sovereignty-rehydrate). | **Extend** it to the full bare-metal→3-tier arc + likeC4 front door + profiles. |
| **UDLM conformance in the control plane** | **Conceptual, not implemented.** The control plane uses UDLM vocabulary (four states, providers, policies) but the data model is fit-for-purpose/Summit-demo, not spec-conformant. No UDLM entity schema, no four-state store contracts, no provider/policy contract alignment, no wire compatibility. | **WS-I:** Refactor to genuine UDLM conformance. Minimum: four-state stores + entity model (the visible part). Incremental: provider/policy contract alignment. Stretch: wire compatibility + conformance endpoints. **This is what makes the demo credible** — customers inspecting the API should see UDLM data, not a bespoke model. |

**Honest gaps the whitepapers themselves flag** (`operations-whitepaper.md` §7): UDLM substrate is on
in-flight feature branches (not tagged); universal audit chain specified-not-complete; cost/FinOps
provider blocked. **None are on the critical path for this demo** — note them, don't solve them here.

## 4. Workstreams

- **WS-A · Bare-metal software enablement** — drive dcm-bootstrap's **PXE + phone-home** boot against
  **libvirt VMs** (substrate-agnostic; no BMC for discovery); write the dnsmasq `dhcp-boot`/HTTP-boot
  directives + rebuild the discovery ISO with our controller config; resolve the install-half power model
  (PXE-next-boot vs `virsh`/sushy stand-in). *Not a hardware buildout — software that boots the arc anywhere.*
- **WS-B · Control plane, providers & dynamic planner** — stand up control-plane; wire cluster/VM/3-tier
  providers; sovereignty Rego gate; **make Act II derive the rebuild plan from stored state (placement +
  policy + provider-resolution) and surface that plan** — the demo's headline.
- **WS-C · Approved-architecture catalog (Act I, easy consumption — N1/G4/G6)** — the goal is a **suite of
  approved architectures, authored in likeC4, that consumers pick and consume.** This maps onto **existing
  primitives — reuse, don't invent:**
  - **likeC4 = the authoring notation; the mapper = an authoring/import *adapter* (tooling, not a provider).**
    `likeC4 export json` → mapper → a **UDLM composite-service / DCM CatalogItem** definition. Runs at
    curation time, once per architecture; it produces a catalog artifact, it does not realize a resource and
    is not selected by placement — so it is **not a resource provider** (an *Information Provider* only if the
    suite is sourced from an external likeC4 registry).
  - **"Approved" = a governance state:** an architecture enters the suite by passing an **approval Gating
    Policy** and carrying **DecisionRecord** provenance (who approved it, against what criteria); it is
    published via the normal artifact lifecycle (`submitted → validating → active`). The *approved suite* =
    the catalog entries in `active`/CANONICAL that cleared the gate.
  - **Consume = the standard Service Catalog path:** pick a pattern → supply bindings → **composite resource
    request (intent)** → placement + policy + provider-resolution realize it. Nothing new. *This is the
    consume-by-pattern front door (N1/G6) and the architecture-as-input capability (G4).*
  - **Customer-derived requirements (anonymized FSI; see §7a) the mapper/pipeline must honor:**
    (i) **two-level config** — preserve the *base engineering pattern* (tech-team-owned, policy-enforced,
    consumer-immutable) vs *user-customizable fields* split, collapsing to one UDLM entity with
    **field-level provenance** (this is UDLM layering, which the customer validated as identical to their
    model); (ii) **compliance checking visible** — Gating + Validation policies fire on the architecture in
    the request lifecycle and are *shown on stage* (their biggest open problem); (iii) **versioned +
    diffable** composites so V1→V2 drives **delta** deployment (design now, demo as stretch). This pipeline
    is the customer's intended **adoption mechanism** (contribute-vs-build-own) — highest-leverage in WS-C.
- **WS-D · UDLM registry** — minimum-viable resource types the demo touches (BareMetal/HostType,
  Network, Storage, App/Tier); commit to the registry branch.
- **WS-E · Acceptance & validation** — DAV UCs as gates; RTO measurement harness; the 8 criteria green.
- **WS-F · Software profiles & operational characteristics** — portable **software profiles** (which
  providers/resource-types/patterns + the VM-substrate config are enabled), *not* hardware topologies;
  a baseline profile (where we demo) + a homelab profile (scaled-down VMs); operational-characteristics
  doc (day-2: drift, RTO, idempotency, audit).
- **WS-G · Demo packaging & co-engineering** — runbook, live demo script, customer hand-off kit, rehearsal.
- **WS-H · Easy provider integration (goal #2)** — make plugging in a provider cheap and contract-driven.
  Document the **provider contract** (`contracts/provider-contract.md` + `dcm-providers.json`, 5 provider
  types) as a "write a provider in N steps" guide; the bar to clear is a **working example added during the
  6 weeks** — ideally wrap **`dcm-provider-libvirt`** (Phase A done) as a DCM provider so "add a provider"
  is shown, not just told. Feeds task #198. *Scope-controlled: narrate if building slips (see §8).*
- **WS-I · UDLM conformance in the control plane (the spec-to-runtime bridge)** — the control plane today
  uses UDLM *concepts* but is not a UDLM-conformant realization. The Summit demo's data model was
  fit-for-purpose, not spec-compliant. This workstream makes DCM a **genuine UDLM realization** — so the
  demo doesn't just use the vocabulary, it implements the contracts. This is what gives the demo credibility
  when we hand it to customers and say "this is built on UDLM." Concretely:
  - **Four-state data stores:** refactor the control plane's persistence to implement UDLM's four canonical
    stores (intent, requested, realized, discovered) as defined in `foundations/four-states.md` — not
    ad-hoc tables that approximate them. Intent is immutable/append-only. Requested is append-only per
    policy evaluation cycle. Realized is versioned snapshots with `is_current`. Discovered is ephemeral,
    refreshed per discovery run.
  - **Entity model:** resources are UDLM entities with stable UUIDs (assigned at intent, carried through
    all states), field-level provenance, data classification, and lifecycle state per `foundations/entity-types.md`.
  - **Provider contract:** providers implement the UDLM provider contract — registration with capability
    declaration, health check, state-change reporting, cost model exposure — per `contracts/provider-contract.md`.
    The current SP interface is bespoke; align it to the contract.
  - **Policy contract:** OPA/Rego policies follow the UDLM policy contract — evaluation context,
    convergence model, the eight policy types — per `contracts/policy-contract.md`. Current policies are
    functional but not structured as UDLM policy types.
  - **Wire compatibility:** the control plane's API produces data that any UDLM-conformant system can
    read. Resource representations on the wire match the UDLM entity schema. This is what makes DAV able
    to evaluate DCM's architecture against the spec it claims to implement.
  - **Conformance endpoints (stretch):** `/.well-known/udlm/schema-bundle` and
    `/.well-known/udlm/conformance` per `CONFORMANCE.md`. Not required for the demo but signals
    seriousness to customers who inspect the API.
  
  **Scope for the 6 weeks:** the four-state stores and entity model are the minimum — they're what the
  demo visibly surfaces (slide 4: "show the four states"). Provider and policy contract alignment is
  high-leverage but can be incremental (align the interfaces, not a full rewrite). Wire compatibility
  and conformance endpoints are stretch. **The discipline: every piece of data the demo shows on screen
  should be a real UDLM entity in a real UDLM four-state store, not a demo-only approximation.**
- **WS-J · Spec-landing & merge work (the substrate this is built on must be *landed*, not in-flight).**
  WS-I and the registry refactor must build against **merged canon**, not 49 open PRs. State today:
  - **Upstream `croadfeldt/udlm` + `croadfeldt/dcm` `main`** are current and authoritative — all of this
    session's work merged: UDLM credentials/provider-contract/relationships/foundations/DecisionRecord +
    the **GateKeeper→Gating Policy** rename; DCM ADRs **001–022** (incl. the trust model), the rename, the
    #69 control-plane fixes, and the **full UDLM registry** (meta-schema + 20 resource types). *Build from
    `main`, not `feat/resource-type-registry`.*
  - **Downstream `dcm-project/udlm`** has **~19 open PRs and an EMPTY `main`**; **`dcm-project/dcm`** has
    **~30 open PRs**. **The substrate the engineering team reviews is not yet merged.** Until it lands,
    "DCM is built on UDLM" is a claim against moving branches.
  - **Merge work to do (drives WS-D/WS-I credibility):** (1) get the downstream UDLM PRs reviewed + merged
    in dependency order (**foundations → registry → contracts → entities → governance/lifecycle/obs**), so
    the registry + four-state + provider/policy contracts WS-I targets are *canon*; (2) merge the DCM ADRs +
    control-plane + architecture-standards PRs; (3) two reconciliation flags before merge: the
    `kubernetes-compatibility` 4-line terminology port lands on `pr/dsp5-spec-integration`, and
    `pr/dx1-future-features` should be **closed/trimmed** (it republishes the now-deleted `future-features/`
    folder). This is the **#163 re-engagement** lever: the bottleneck is review/merge throughput, not more
    PRs. **A landed spec is a precondition for WS-I being real rather than aspirational.**

## 5. Six-week timeline (demo-anchored)

Each week ends on a **demoable milestone**. The discipline: a thin slice works end-to-end early, then
widens — never a big-bang integration in week 6.

### Week 1 — Spine up; vertical slice on a laptop; de-risk the climax; assess UDLM conformance gap
- WS-B: `make compose-up` control-plane + `three-tier-app-demo` provider on Kind/Podman → an
  **app-tier intent provisions web/app/db** (Act I in miniature). **Verify the Act II trigger re-runs the
  planner over stored state** (placement + policy + provider-resolution → a *derived* plan), not a static
  replay — read the code/integration tests. *Single most important task of the week — it's the headline.*
- WS-I: **audit the control plane's current data model against the UDLM spec.** Map the existing Go structs
  / DB schema to UDLM's four-state stores + entity model. Produce a gap inventory: what's already aligned,
  what needs refactoring, what's missing. This determines WS-I's scope for weeks 2–4.
- WS-C: author the likeC4 "Sovereign 3-Tier" model; prove `likeC4 export json`; sketch the mapper.
- WS-H: read the provider contract; scope the libvirt-provider-as-DCM-provider wrap.
- WS-D: inventory exact UDLM registry gaps the demo touches; pick the minimum-viable set.
- WS-E: **author the bare-metal provisioning UCs** in DAV (project 20) so the leg we show is
  criteria-backed (the corpus has only `greenfield` + stubs today) — *committed per Chris 2026-06-26*.
- **Milestone:** app-tier provision + rehydrate runs on a laptop; **rehydrate maturity is known** (and a
  fallback decided if it's spec-only); bare-metal UCs drafted; **UDLM conformance gap inventory produced.**

### Week 2 — Bare-metal software enablement + cluster on VMs + four-state stores
- WS-A: build dcm-bootstrap **connected**; **PXE-boot libvirt VMs** through the discovery ISO →
  phone-home → ABI install (no BMC for discovery); resolve the install-half power model
  (PXE-next-boot, else a `virsh`/sushy stand-in). Substrate-agnostic by construction.
- WS-B: deploy the control-plane onto the cluster (quadlet kit); stand up KubeVirt/CNV; **VM provider
  provisions a VM**.
- WS-I: begin **four-state store refactoring** based on wk1 gap inventory. Priority: intent store
  (immutable, append-only) and realized store (versioned snapshots). The data the demo surfaces in
  slide 4 ("show the four states") must come from real UDLM-conformant stores, not approximations.
  Align entity representations to carry stable UUIDs + provenance fields.
- **Milestone:** a cluster stood up *from PXE on VMs* via dcm-bootstrap, control-plane live on it, VM
  tier provisions — proving the boot path runs on arbitrary software substrate. **Intent and realized
  stores are UDLM-conformant.**

### Week 3 — Act I: full provision arc, driven by the pattern, on UDLM data
- WS-C: finish the **likeC4-JSON → CatalogItem mapper**; one composite-service intent.
- WS-B: that single intent provisions **cluster (up) → VM → web/app/db**, dependency DAG honored,
  **sovereignty Rego gate** enforced. Surface the **four states**. *This is the easy-consumption proof.*
- WS-I: complete **requested and discovered stores**. Requested = post-policy enrichment, append-only per
  evaluation cycle. Discovered = provider-reported current state, ephemeral/refreshed. The "show the four
  states" demo moment now shows **real UDLM four-state data**, not demo-only fields. Begin **provider
  contract alignment** — providers register capabilities and report state changes per the UDLM contract.
- WS-H: wrap **`dcm-provider-libvirt`** as a DCM provider far enough to demonstrate "adding a provider."
- **Milestone:** one pattern intent provisions the full 3-tier app end-to-end; **all four UDLM states
  visible and conformant**; a second provider plugged in via the UDLM provider contract.

### Week 4 — Act II: dynamic rebuild (the headline) on UDLM-conformant data
- WS-B/E: destroy, then **derive the rebuild plan live** — re-run placement over stored state to compute
  *what* + *what order*, **re-evaluate policy**, **resolve providers**, and **render the computed plan
  on screen** before executing. Then execute: **UUIDs preserved**, **RTO measured**. Include the bare-metal
  leg as far as feasible (re-provision via dcm-bootstrap, or rebuild from the cluster layer up with the
  bare-metal step recorded).
- WS-I: the dynamic rebuild reads from **UDLM-conformant intent stores** and writes to **UDLM-conformant
  realized stores**. The derived plan references UDLM entity UUIDs and UDLM dependency relationships.
  This is the ultimate proof: the rehydration works *because* the data model is right — not despite
  a bespoke data model. **Policy contract alignment** — the sovereignty Rego gate is structured as a
  UDLM GateKeeper policy with evaluation context, not a standalone Rego file.
- **Milestone:** kill → *system derives and shows the plan from UDLM data* → rebuilds; RTO measured;
  criteria **1–6** green. **The rehydration is provably a UDLM operation, not a DCM-specific one.**

### Week 5 — Harden, software profiles, operational characteristics, conformance
- WS-F: finalize the **baseline software profile**; author the **homelab profile** (scaled-down VMs);
  write the **operational-characteristics doc** (four-state day-2 loop, drift modes, RTO targets,
  idempotency, audit). WS-D: commit the minimum UDLM registry types. *Stretch:* provider-portable rehydration.
- WS-I: **wire compatibility verification** — confirm that the control plane's API output for entities,
  lifecycle states, and dependency relationships can be consumed by DAV (or any UDLM-conformant tool)
  without translation. *Stretch:* conformance endpoints (`/.well-known/udlm/{schema-bundle,conformance}`).
- WS-E: all acceptance gates green in DAV.
- **Milestone:** demo reproducible **from the software profile alone, on plain VMs**; ops characteristics
  defined; **control plane output is wire-compatible UDLM data.**

### Week 6 — Rehearse, hand-off kit, live demo
- WS-G: dry runs incl. **failure-mode rehearsal** (what we do live if a step fails); assemble the
  **co-engineering hand-off kit** (repos pinned, profile, runbook, likeC4 patterns, the ops-characteristics
  doc); deliver the **live demo at the community gathering**.
- **Milestone:** live demo delivered; co-engineering kit in customers' hands.

## 6. Acceptance gates (DAV-corpus-derived)

The demo is "backed by the corpus" (DCM project 20, 129 UCs) when it demonstrates these. Keystone UC in
brackets; map each to the week it goes green.

1. **Intent is system-of-record** — every layer submitted as a UDLM intent, persisted, retrievable as a
   typed resource. [`full-dc-rehydration`, `vm-resource-representation`] — wk3.
2. **Realized reconciled with provenance** — status read back from the provider, stored with field-level
   provenance. [`vm-status-provenance`] — wk3.
3. **Dependency graph exists and is honored** — inter-tier deps declared *and* discovered; undeclared
   edges flagged as drift. [`topology-auto-discovery`, `drift-detection-remediation`] — wk3.
4. **Rebuild plan is *dynamically derived*, dependency-ordered, from bare metal** — the system reads
   stored data + the dependency graph, **re-runs placement/policy/provider-resolution to compute** what
   comes back and in what order (not a recorded script), and the **derived plan is shown** before it
   executes; post-rebuild all resources in last-known-good, realized matches intent.
   [`full-dc-rehydration`] — wk4. *Hardest, and the demo's headline.*
5. **RTO measured, not asserted** — recovery time + completeness per resource/domain.
   [`resilience-posture-rehydration-test`] — wk4.
6. **Idempotency / no-op on re-apply** — unchanged intent converges to no-op; no indeterminate resources
   without a recovery record. [`vm-lifecycle-reconciliation`, `vm-provision-with-provider-failure`] — wk4.
7. **Auditability end-to-end** — provisioning, drift (before/after), decommission carry audit trails.
   [`greenfield`, `sovereign-decommission`] — wk5.
8. **(Stretch) Provider-portable rebuild** — when a provider is unavailable, the planner **re-resolves**
   onto a different provider with DNS/net/storage reference rewrite + validated source decommission;
   peer-DCM ack + residency preserved. [`workload-portability`, `sovereign-decommission-with-peer`] — wk5 stretch.

**North-star success measures (the ultimate goals — beyond the corpus gates):**
- **N1 · Easy consumption.** A consumer gets the full governed 3-tier estate from **one pattern + a few
   bindings** — measured by the count of consumer-supplied fields and steps (target: trivially few). Act I.
- **N2 · Easy provider integration.** Adding a provider is **contract-driven and cheap** — measured by a
   *second provider actually plugged in during the 6 weeks* (`dcm-provider-libvirt`) and an "add a provider
   in N steps" guide a community contributor can follow. WS-H.

**Corpus gap — being closed:** the **bare-metal bookend is the least-evidenced leg** — one real UC
(`greenfield`) plus stubs, matching the code (no bare-metal provider design). **Decision (Chris 2026-06-26):
author the bare-metal provisioning UCs** in DAV project 20 during wk1–2 so the leg we show is
criteria-backed. The boot mechanism is now known (PXE + phone-home, §3) and runs on VMs, so the
enablement is software, not hardware — the UCs should describe *intent-driven PXE provisioning of a node
(physical or VM) discovered by serial*, matching dcm-bootstrap's actual flow.

## 7. Composite services as pre-approved patterns (the likeC4 pipeline)

This is a distinct, high-leverage deliverable and the demo's most novel front door. UDLM already has the
**composite-service model** (`entities/composite-service-model.md`) and a **designed 1:1 likeC4 mapping**
(`docs/likec4-and-udlm.md`, with `udlm.resourceType`/`udlm.edge` annotations and a worked 3-tier example).
likeC4 emits machine-readable JSON. The only missing piece is the **mapper** (WS-C):

```
likeC4 DSL  ──likec4 export json──▶  model JSON  ──mapper──▶  UDLM composite service / DCM CatalogItem
 (architect authors the pattern)      (elements + edges)        (the pre-approved, governed pattern)
```

A "pre-approved architectural pattern" = a **catalog-level composite service** with constituent resource
types + a `depends_on` DAG + binding fields. The architect designs in likeC4 (visual, reviewable, C4),
exports, and the mapper lands it as a catalog item that placement/policy can govern. **Pin a likeC4
version** (JSON schema evolves). Mapper fallback: hand-author the CatalogItem, show the likeC4 model as
the design source (front door shown; automated mapper as fast-follow).

### 7a. Customer code-first requirements (anonymized FSI engagement)

A major FSI CTO solution-engineering org is moving to a **code-first solution architecture** — likeC4
replacing a drawing-first design tool — where *"exactly what you have coded becomes what you deployed; it
**is** what you deployed."* **This pipeline is their intended adoption mechanism**, not a nice-to-have:
their position was *"we could just contribute to it instead of building from the ground up"* — so getting
WS-C right decides **co-engineer-with-us vs build-their-own**. (Source:
`likec4-customer-goals-anonymized.md`.) Five capabilities they need, mapped to what we already have:

1. **Define architectures as code** → likeC4 model → composite service (WS-C core).
2. **Diagrams are derived, not authoritative** — "an ancillary artifact." Code is the source of truth;
   likeC4 renders the picture. Aligns with code-first; nothing to build.
3. **Drive automation from the code** → the composite request is realized by the control plane (Act I).
4. **Version comparison → delta deployment** — *V1→V2 diff drives what the automation does* (act on the
   change, not a full redeploy). **NEW requirement:** the mapper must emit **versioned** composite services
   that can be diffed, and the control plane must act on the **delta** (lands on UDLM versioning + the
   four-state model — Requested vs current Realized = the change set). *Demo: show a V1→V2 pattern edit
   producing only the delta. Stretch for the 6 weeks; design it in now.*
5. **Compliance checking in the pipeline** — *their biggest open problem in the code-first transition.* The
   existing tool checks designs against org rules before deploy; the code-first path must preserve that. In
   DCM terms: **Gating Policy + Validation policies fire during the request lifecycle.** **The customer must
   SEE this in the demo** — it's the confidence test for abandoning their drawing tool. *Make the
   sovereignty Gating gate (Act I) explicitly a "compliance check on the architecture" moment on stage.*

**The keystone insight — two-level configuration = UDLM layering (a customer-validated proof point).**
The customer's non-negotiable pattern: a **base configuration** owned by a technology team (e.g. the Linux
team's base config — consumers **cannot** change it) + **user-level configuration** (the fields the
consuming team may fill; the tech team defines *what* is customizable). When shown UDLM's layering
(base → location → enclave → request), they **immediately recognized it as identical** to their model
(base engineering pattern → user customization → policy enrichment → provider realization). This is strong
external validation of the layering data model — and a **requirement on the mapper**: it must **preserve the
base/user split with provenance** so (a) **policy enforces the base** (no consumer overrides what the
technology team defined), (b) users customize **only** their designated fields, and (c) the merged result
collapses into one UDLM entity that records **which layer set each value**. *This is layering + field-level
provenance doing exactly the job a real FSI needs — lead with it.*

**Ongoing reconciliation (beyond point-in-time deploy).** The customer also wants continuous
config-management — a pull-based "what should I look like?" daemon model. **DCM's four-state model already
answers this:** Discovered is continuously refreshed by providers and compared to Realized; drift is
surfaced. *Demo should show drift detection, not only the initial provision* (ties to Act II / the
four-state slide).

## 8. Risks, cut-lines, fallbacks

A skeleton survives by having **decided cut-lines** before week 6, not by hoping everything lands.

| Risk | Likelihood | Fallback (decided now) |
|---|---|---|
| **Act II trigger is spec-only / does a *static* replay instead of re-running the planner** | Medium | Wk1 verify. The reasoning machinery (placement/policy/SP-resolution) already exists; if the trigger is missing or static, wire the minimal path that **invokes the existing planner over stored state and renders the derived plan**. Headline → top priority. |
| **WS-H provider wrap (libvirt-as-DCM-provider) slips** | Medium | Demote N2 to *narrated*: show the provider contract + the "add a provider in N steps" guide and the libvirt Phase-A code, without a live second-provider plug-in. Goal #2 still represented. |
| **Install-half (`provision_baremetal`/`install_abi`) secretly needs Redfish** (collection is private/404) | Medium | Discovery needs **no BMC** (PXE + phone-home, proven). For the install reboot: PXE-next-boot if it supports it, else a `virsh`/**sushy-emulator** stand-in giving each VM a Redfish endpoint. Resolve wk2. |
| **Full PXE-on-VMs boot slips** | Low–Med | Pre-stage the cluster and rehydrate from the **cluster/VM/app** layers, with the bare-metal step **recorded** not live. (Corpus + code both thinnest here; now UC-backed.) |
| **Disconnected dcm-bootstrap path slips** | Medium | Demo **connected**; ship disconnected as a *documented profile*, not a live step. |
| **likeC4→catalog mapper slips** | Low | Hand-author the CatalogItem; likeC4 model shown as design source. |
| **Full UDLM registry types slip** | Medium | Minimum-viable stubs for only the types the demo touches; full registry is post-demo. |
| **UDLM conformance refactoring is larger than scoped** | Medium | The gap inventory (wk1) determines actual scope. Minimum: four-state stores + entity model (the visible part). Provider/policy contract alignment can be incremental — align the interfaces without full rewrite. Wire compatibility and conformance endpoints are stretch. **Protect the four-state stores** — that's what the demo surfaces. |
| **Audit chain / FinOps incomplete** | Known | Out of scope; whitepapers already flag these as "work ahead." Don't solve here. |

**Guiding rule:** protect the **vertical slice** (one pattern intent → full stack → rehydrate, even if
narrow) over breadth. A narrow arc that genuinely round-trips beats a wide one that doesn't rehydrate.

## 9. Software profiles & operational characteristics (software enablement, not hardware)

Per Chris's steer (2026-06-26): **the deliverable is software enablement, not a lab buildout** — *"dedicated
lab specs are not important, only software enablement is."* So a "profile" is **software configuration**
(enabled providers, resource types, patterns, and the VM-substrate boot config), not a hardware topology.
The demo must run **from the software profile alone, on plain libvirt VMs** — that is what makes it portable
to any customer homelab and turns customers into co-engineers.

- **Baseline software profile (WS-F):** the provider set (cluster/VM/3-tier), the enabled UDLM resource
  types, the likeC4 pattern(s), and the **PXE-on-VMs boot config** (dnsmasq dhcp-boot/HTTP-boot directives,
  discovery-ISO controller config, the power-control stand-in). Must reproduce the demo **from the profile
  alone** by wk5 — *on whatever VMs are handy*, not a named lab.
- **Homelab profile (WS-F):** a scaled-down variant (SNO/compact cluster, fewer VMs) using dcm-bootstrap's
  SNO/compact ABI topologies. Same software, smaller substrate. Ship in the hand-off kit.
- **Operational characteristics doc (WS-F):** what *running* this looks like day-2, grounded in
  `dcm-udlm-operations-whitepaper.md` §2–5 — the four-state loop, drift detection modes (auto-revert /
  adopt / alert / escalate), **RTO targets**, idempotency guarantees, immutability of Intent/Requested,
  audit. This is what lets customers operate (not just boot) the demo, and it's the explicit
  "define the operational characteristics" ask.

## 10. Co-engineering & the community demo

The whitepapers' co-engineering thesis (`whitepaper.md` §6: six FSIs converged independently; CNCF path;
"contributor ladder: use → extend → co-design → maintain") is the *why* behind shipping this as a kit, not
a slide. The week-6 deliverable to the community is the **hand-off kit**:

- Pinned repos (dcm-bootstrap, control-plane + providers, udlm registry branch, the likeC4 patterns).
- The **baseline + homelab software profiles**.
- The **runbook** (stand-up → provision → rehydrate).
- The **operational-characteristics doc**.
- The **likeC4 pattern library** (starting with Sovereign 3-Tier) as the extensible front door — the
  natural first thing a co-engineering customer authors their own pattern against (goal #1).
- The **"add a provider in N steps" guide** + the libvirt provider as a worked example (goal #2) — the
  natural second thing a co-engineering customer extends.

The live demo *is* the invitation: "here's the walking skeleton; here's how you run it in your homelab;
here's where you co-engineer." OSAC is the natural provisioning-provider integration to name as
"better together," but it is **not** a 6-week dependency.

## 11. Decisions

**Resolved (Chris, 2026-06-26):**
1. **Doc home** — keep as a **private draft** in `dav/docs/internal/`. ✅
2. **Bare-metal ambition** — software enablement, not a lab buildout; drive dcm-bootstrap's **PXE +
   phone-home boot on libvirt VMs** (no BMC). Confirmed the boot mechanism supports this. ✅
3. **Substrate** — *"dedicated lab specs are not important, only software enablement is."* Roadmap targets
   software that runs the arc on **any VM substrate**; no named-lab dependency. ✅
4. **Bare-metal UCs** — **author them** in DAV project 20 (wk1–2) so the leg is criteria-backed. ✅

**Resolved (Chris, 2026-06-28):**
5. **Dynamic-planner — build the real path; real mechanics, no static replay.** The next version must run
   **real-life scenarios with real-life mechanics**. Rehydrate **uses the actual plan-and-execute logic of
   the initial provisioning** — the `:rehydrate` trigger re-invokes the *same* placement + policy +
   provider-resolution machinery over stored state to derive the plan, then executes it through the *same*
   providers. No static replay, no mocked demo path, no descope. Wk1 still *verifies* the wiring; if the
   trigger is spec-only, **we build the minimal path that invokes the existing planner over stored state and
   renders the derived plan** (authorized). The dynamic derivation through real mechanics *is* the demo.
6. **Bare-metal bootstrap of DCM itself is in scope.** The arc includes **standing up the bare-metal
   foundation that DCM runs on in the first place** — not only provisioning an application from bare metal,
   but bootstrapping the substrate DCM itself lives on — using the Heatmiser **`dcm-bootstrap`** work
   (PXE → discovery ISO → DNS-SRV phone-home; no BMC). The self-hosting story (DCM brings up its own
   bare-metal + cluster, then provisions the rest) is part of the headline, on real mechanics.

---

## 12. Architecture gaps & open decisions (for Chris — triage queue)

The deltas between the goals and what the architecture actually specifies/implements today. Split by what
they demand of *you*: a **decision**, a **build**, or **defer**. (DAV project 20's 129-UC DCM corpus is the
systematic gap-finder behind much of this; a focused DAV pass can sharpen any row — offered, not yet run.)

### A. On the critical path for the 6-week demo
| Gap | Status today | Decision / Build | Next |
|-----|--------------|------------------|------|
| **UDLM conformance in the control plane** (four-state stores, entity model, provider/policy contract alignment, wire) | Conceptual — Summit data model was fit-for-purpose | **Build (WS-I)** — minimum = four-state stores + entity model | Wk1 conformance audit → gap inventory |
| **Dynamic rehydration planner wiring** (`:rehydrate` re-invokes real placement/policy/provider over stored state) | Reasoning machinery exists; trigger may be spec-only/static | **Build (WS-B)** — decision #5 resolved: build the real path | Wk1 verify, then wire |
| **Bare-metal-as-a-service** (a provider that realizes `BareMetalInstance`, not just dcm-bootstrap booting) | dcm-bootstrap boots nodes; no "BM as a consumable resource via DCM" provider | **Build** — BM resource type (WS-D) + a BM service provider (WS-B) | Scope the BM provider seam wk1–2 |
| **likeC4 → composite-service mapper** (authoring/import adapter) | Does not exist | **Build (WS-C)** — adapter, not a provider | Wk1 spike |
| **Approved-architecture governance** (Gating Policy approval gate + DecisionRecord provenance → published catalog) | Designed (this session); not implemented as a flow | **Build (WS-C)** — reuse Gating Policy + DecisionRecord + artifact lifecycle | Demo can hand-author "approved" if the gate slips |
| **Authorization model** (Keycloak = authn ✓; authz undecided) | **Open** — Alterino (OCP, OSAC uses it) vs Kessel (not ready) vs OPA-native | **DECISION (yours)** + build the chosen path | Resolve early; security-in-control-plane is required |

### B. Architecture decisions you should make (not just build)
| Decision | The question | Lean |
|----------|--------------|------|
| **Authz substrate** | Alterino vs Kessel vs OPA/Rego-native (you already run OPA for policy) | OPA-native is least-new-dependency for the demo; Alterino if you want OSAC alignment |
| **Provider extension model** (#198) | Is "easy provider integration" a *contract + guide* (doc) or a *real SDK/scaffold*? | Demo bar = wrap `dcm-provider-libvirt` live (WS-H); SDK is post-demo |
| **Composite-service composition scope** | Composition across capabilities (identity + credential + compute) is handled "one layer up" (orchestration-flow) — is that built or asserted? | Confirm it exists for the 3-tier arc; don't over-build |
| **Discovered/brownfield ingestion** (ADR-017, tasks #221–229) | Real customers have existing estates; does the demo show *greening* discovered resources, or only greenfield? | Defer for the demo (greenfield arc); flag as the obvious next capability customers will ask for |
| **Trust/credential broker** (ADR-022) | Designed this session (broker, CPX-001, attestation); when does it get implemented vs stay spec? | Spec for the demo; implement post-demo (note it in the credibility story) |

### C. Known gaps — deferred / tracked (not 6-week)
- **AI service types** (deploy agents/LLMs as a UDLM resource type) — task #234, post-roadmap.
- **Cost/FinOps provider** — blocked (whitepaper §7); off critical path.
- **Universal audit chain** — specified, not complete.
- **Wire compatibility + conformance endpoints** (`/.well-known/udlm/*`) — WS-I stretch.
- **Multi-tenancy (RLS, ADR-014)** — designed; confirm demo runs single-tenant, RLS post-demo.
- **Vendor/custom provider extension + registry governance at scale** — post-demo ecosystem work.
- **Composite-service *offering* creation & management system** — composite offerings are a **new class of
  managed catalog item** (authored, versioned, approved, diffable, deprecatable) needing their own
  management surface, **distinct from resource providers** (which realize resources, not author offerings).
  Long-term architecture capability; canonical reference: **croadfeldt/udlm#21**. Reuses catalog + Gating
  Policy + DecisionRecord + artifact lifecycle. *Not 6-week scope; the version-diff→delta need (the
  "update an existing deployment model" case) is the near-term sliver, designed in WS-C.*

**Honest framing for the meeting:** the demo's job is to prove the *spine* (G1–G4 + N1/N2) on **real
mechanics** with a **real data model (WS-I)**. Sections B/C are the architecture's known frontier — name
them so customers/engineers see we know where the edges are, and so they become the *next* roadmap, not
surprises.

## 13. OSAC integration (scaffolding, not dependency)

**Meeting decision (Jun 26):** OSAC (Open Sovereign AI Cloud, `osac-project`) is **Red Hat-specific** —
*"there is nothing agnostic about them"* (Piotr). So DCM **cannot depend on OSAC for all providers**.
Position:
- **DCM owns the control plane** — types, policies, orchestration, the four-state data model. This is the
  agnostic core and our differentiator.
- **OSAC is integrated as a Provider, not a foundation.** It is **structurally pre-aligned** already — its
  `FieldDefinition{path, editable, default, validation_schema}` is independently the same as UDLM's
  constraint-profile (base/user layering), and its spec/status + JSON-Schema catalog mirror UDLM. The clean
  "better together": **OSAC becomes a DCM Provider; DCM owns the state stores; OSAC realizes what it's good
  at** (enclave deployment, sovereign VM/bare-metal/cluster, OpenShift AI / AI service types).
- **The real leverage for adoption is making provider creation easy** (WS-H / G5) — *scaffold how someone
  writes a provider* — so OSAC is one provider among many, not the substrate. Use OSAC's pieces where they
  fit; don't couple to them.
- **Demo relevance:** none on the critical path — the demo runs on our own providers (cluster/VM/3-tier +
  bare-metal). OSAC integration is a **post-demo early-July follow-up** (a worked second provider proves
  G5). Reference: `udlm/docs/osac-better-together.md`, [[project_osac]]. **Owner action:** the OSAC follow-up
  is scheduled; Chris to share with Michael for collaboration.

## 14. Governance, neutrality & contributor on-ramp

The substrate for this **already exists** — this section is positioning + the open decisions, not new work:
- **Contribution model is specified** — `udlm/governance/federated-contribution-model.md` (four contributor
  types, the universal contribution pipeline, per-type review requirements) + `registry-governance.md` (the
  three-tier registry: Core / Verified-Community / Organization). `CONTRIBUTING.md` exists in both repos.
- **CNCF / community path is documented** — `dcm/docs/specifications/cncf-strategy.md` (Sandbox path,
  operator-ecosystem as the primary leverage point, KubeCon, standards positioning DMTF/FinOS/OpenInfra).
  This is the **community→CNCF flywheel** (task #159).
- **Neutrality is the live governance question** — DCM/UDLM are currently `croadfeldt`-authored, published
  **downstream** to the vendor-neutral `dcm-project` org. For genuine multi-vendor adoption the governance
  has to read as *project*, not *one person's repo* (DAV already de-pinned from the architecture this
  session — same instinct, applied to the spec). **Open decisions for Chris:** (a) governance model +
  maintainer/steering structure for `dcm-project`; (b) when to formalize the contributor on-ramp publicly;
  (c) CNCF Sandbox submission timing relative to the demo. Tracked: tasks **#163** (re-engage engineers),
  **#165** (governance, neutrality, on-ramp).
- **Demo relevance:** the demo *is* a governance act — it's the co-engineering artifact (WS-G) that makes
  the contributor on-ramp real (customers contribute providers/patterns rather than fork). "Easy provider
  integration" (G5) and the contribution pipeline are the same story from two angles.

## 15. Whitepaper & positioning (revision, not creation)

The narrative assets **exist** (in `dav/docs/internal/`, private) — for the meeting this is *which to use*,
not *write new*:
- **Thesis / why** — `dcm-udlm-thesis.md` (one-page argument), `dcm-udlm-whitepaper.md` (internal, full),
  `dcm-udlm-whitepaper-public.md` (genericized — **the shareable one**), `dcm-udlm-executive-brief.md`
  (2-page, partner CTA), `dcm-udlm-operations-whitepaper.md` (how you run a DC on the model; §7 honest
  maturity), `udlm-unified-data-dependency-exec-brief.md`/`.pptx` (deck).
- **DAV stays in the whitepaper.** The DAV de-pin applies only to the **normative architecture/spec** (DCM/UDLM
  docs), where DAV must be testbed/consumer, non-normative. The **whitepaper is a positioning/narrative asset**
  — DAV is legitimately part of that story (the reasoning/assessment layer, the find→track→validate→record
  loop, the find-the-gaps engine). Keep mentioning DAV here; do **not** strip it.
- **What's stale (one-line fix each before any external share):** the **GateKeeper → Gating Policy** rename
  must be reflected; the registry is now **published with 20 resource types** (not "~7"); build/branch refs
  point to `main`, not feature branches. *(The DAV de-pin is **not** a whitepaper fix — see above.)*
- **The two strongest positioning beats for tomorrow** (both validated this session): (1) **a regulated FSI
  independently recognized UDLM's layering as identical to their base/user config model** — external proof
  the data model is right ([[project_udlm_layering_customer_validation]]); (2) **the demo runs on real
  mechanics with a real UDLM data model** (WS-I), not a Summit-style approximation — the credibility leap.
- **Open decision for Chris:** whether tomorrow needs a *new* short "current-state + roadmap" deck (task
  #161) or the meeting runs off this roadmap + the existing public whitepaper. Recommendation: **roadmap +
  public whitepaper for the eng meeting; build the customer-facing current-state deck after sign-off** (so
  it reflects the agreed plan).

---

*Sources: local repo analysis (`croadfeldt/udlm` + `croadfeldt/dcm` `main` — all spec work merged this
cycle; downstream `dcm-project/*` PRs in flight;
`dcm-provider-libvirt`); external research (`heatmiser/dcm-bootstrap`, `github.com/dcm-project`,
`likec4.dev`); DAV DCM corpus (project 20, 129 UCs, 6 rehydration); internal whitepapers
(`dcm-udlm-whitepaper.md`, `-operations-whitepaper.md`, `-thesis.md`, `-executive-brief.md`,
`-sources.md`). The sovereignty-rehydrate precedent (May 2026) is the confidence anchor.*
