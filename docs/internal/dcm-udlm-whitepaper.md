# Infrastructure Lifecycle Without a Common Language
### How UDLM and DCM make infrastructure lifecycle — and the capability and maturity reasoning built on it — machine-native

**A whitepaper on the Universal Data Lifecycle Model (UDLM) and Data Center Management (DCM).**

_Merge note (2026-06-16): canonical merge of two independent drafts; base = the comprehensive draft. Changes in this pass: the "everything as a service" vision and partner CTA moved to the companion executive brief (this paper stays evidence-led); the evidence numbers snapshot-dated and the named critical gaps hedged (the set shifts as use cases are added); and the UDLM framing made consistently lifecycle-first — UDLM is the universal lifecycle data model for cross-system compatibility, and capability/maturity reasoning is what DAV derives and represents in it. Follow-ups in a second pass: "capability" defined explicitly (a discrete unit of functionality in the DCM taxonomy — the demand/supply meeting point) with UDLM kept strictly as the data model (not "the capability layer"); the quantitative figures marked point-in-time (2026-06-16, reproducible); OSAC reframed as a potential integration *partner* whose real capability is platform **provisioning** — a realization of UDLM data operationalized by DCM ("better together"; AI is a buzzword, NOT the claimed value), with benefits stated honestly as capabilities out-of-the-box + time-to-value + built-in governance; explicitly NOT an adopter/reference use case *of* UDLM, and not the owner; and developer portals/Backstage reframed as one pluggable, surface-agnostic integration *example* (not an architectural dependency), foregrounding the integration-focused, extensible design with governance, provenance, and orchestration as the structural, load-bearing concerns. Third pass: concrete engagement timeline woven in (Navy origin late-2024 → §2; Summit May-2025 independent convergence + June-2025 IBM-NY convening with the Red Hat FlightPath team → §2 Industry Validation; first DCM engineering sample / May-2026 sovereign-workload rehydration demo → §5 "First Engineering Sample"; active co-engineering + consolidate-into-one-open-platform + benefit triad fewer-efforts/time-to-market/corporate-support → §6 "Co-Engineering Momentum"; current-state line synced). All names are internal-only and to be genericized for the public version. For review._

_Fourth pass (2026-06-28): §3 updated for the architecture decided since — **provider kinds vs. capabilities** (auth + credentials are declared capabilities, not provider kinds; providers named by yield), **trust as a brokered capability** (broker-not-authority, non-exposure of brokered values, attestation-graded selection, self-attestation), and **the recorded "why"** (scoped, validation-backed DecisionRecord). Added **Composite Services: A Managed Offering** — the offering as its own managed catalog-item class with an author → version/diff → approve → catalog-manage lifecycle, distinct from resource providers (the version-diff = "update your existing deployment model"). Terminology note: the gating policy type is **Gating Policy** (renamed from "GateKeeper" to avoid the OPA/Kubernetes collision). DAV remains in this narrative as the reasoning/assessment layer (the spec-level DAV de-pin does not apply to positioning docs)._

---

## 1. The Problem

Every organization that manages infrastructure at scale has the same problem. Multiple internal groups — the CISO office, the standards body, the networking team, the storage team, the compute team, the compliance group, the operations team — all have a say in the lifecycle of every resource. A virtual machine isn't just a VM. It's a compute resource that the standards team defined, the security team must approve, the networking team must connect, the compliance team must validate, the operations team must monitor, and the cost management team must account for.

Today, there is no standardized way for these groups to participate. Each builds bespoke integration with whatever provisioning system exists. Each maintains its own representation of what resources look like and what policies apply. Each enforces its own rules through its own mechanisms — a spreadsheet here, a manual approval gate there, a custom script somewhere else.

The information loop is broken. A resource is requested, provisioned, modified, drifted, remediated — but the groups who define standards, enforce security, or manage compliance have no structured visibility into what was *intended* versus what was *requested* versus what was *realized* versus what *actually exists right now*. Drift goes undetected because there is no machine-readable baseline to compare against. Provenance is lost because changes aren't tracked through a common model. Audit is manual because the data isn't in a shape that tools can consume.

This is not a theoretical problem. Over the course of 2025, six major financial services institutions — independently, without knowledge of each other's efforts — asked Red Hat for the same thing: a standardized way to manage infrastructure lifecycle, close the information loop, and let their internal stakeholders participate through a common framework rather than bespoke integration. Each institution was building its own version. Independently. Incompatibly. At enormous cost.

In one session, an architect at a global institution asked five times in three hours to "see the data model" — because there was no shared format to compare two platforms that were solving the same problem differently. Neither team could evaluate the other's approach without a common representation of what capabilities existed, what state resources were in, and how policies were applied.

At another institution, the CTO's architecture organization was building code-first infrastructure definitions using a domain-specific language — but had no way to automatically validate those definitions against the use cases they were supposed to serve. The architecture was code, but the validation was still manual.

At yet another, capability and maturity data lived in slide decks, spreadsheets, and consultants' heads. Not machine-readable. Not versioned. Not comparable across tools, teams, or time.

The cost of this status quo is staggering: duplicated engineering across every institution, vendor lock-in because each tool invents its own representation, no ecosystem because there is no shared shape to build on, and no ability to compare capability or maturity assessments across organizations, vendors, or time periods. Every institution pays this cost independently, and the industry as a whole cannot learn from shared experience because there is no common language to share it in.

---

## 2. Origin: From Operational Need to Open Standard

### The Navy Problem

The data model that became UDLM was born from a concrete operational need. The work began in late 2024 with the United States Navy, which needed to manage infrastructure across disconnected environments — ships operating independently from shore, sometimes for extended periods without connectivity. They needed:

- **Standardized definitions** for every resource type, so a VM defined on shore meant the same thing as a VM deployed on a ship
- **Observability** into what was deployed on disconnected platforms, without requiring constant connectivity
- **Provenance** of every change — who changed what, when, why, and under what authority
- **End-to-end automation** that worked identically across connected and disconnected environments
- **Audit and reporting** that could be reconstructed from the data alone, without relying on the humans who were present
- **A closed information loop** between where decisions were made (shore) and where resources were operated (ships) — so that shore-side standards bodies, security teams, and operations groups maintained visibility and control even when the ship was disconnected

The problem was not automation. Automation tools exist. The problem was the **data**: how resources are represented, how their lifecycle is tracked, how dependencies between resources are codified, and how state flows from intent through realization to discovered truth.

### The Strategic Insight

The deeper requirement was not just "track resources." It was that **every internal group that needs a say in the lifecycle** — the standards body defining what a base VM looks like, the security team enforcing encryption policy, the networking team providing IP allocation as a service, the compliance team gatekeeping production deployments — all needed a standardized way to participate in the process.

Not through bespoke integration with a control plane. Not through custom APIs that each team builds and maintains. Through **standardized contracts in the architecture, the API, and the data model** that allow any internal group to integrate smoothly — to augment capabilities, to inject governance, or to offer services — without modifying the core system and without understanding every other group's domain.

This is the design principle that makes UDLM a standard, not an application. The control plane is intentionally domain-ignorant. It manages data and enforces policy. Providers own domain expertise and define what services they offer. The policy engine allows any organizational stakeholder to inject governance — enrichment, validation, gatekeeping — without requiring the control plane to understand what that governance is about. The standardized contracts are what hold it together.

### UDLM: The Data Model

From this insight, the Universal Data Lifecycle Model was born. UDLM is not a product. It is a wire-compatible substrate — a set of contracts that any system can implement. Its core abstraction is a four-state lifecycle (intent → request → realized → discovered) that closes the information loop. Around that core, the specification (currently ~55 documents) defines the contracts that enable any participant to plug in: provider contracts, policy contracts, data-store contracts, governance, observability, and a conformance framework. The technical detail is in §3; here, the point is that UDLM was designed to codify the strategic insight — standardized participation — into something machine-checkable and implementable.

### DCM: Operationalizing the Model

DCM was designed to operationalize UDLM as a running infrastructure lifecycle management system — a policy engine, provider abstraction, dependency graph, and cost analysis integration. DCM is one realization of UDLM. Any system that implements the UDLM contracts is a peer.

### Emergent Capabilities: The Model Proved Itself

The strongest validation of a data model is when new requirements can be addressed through the model's existing extension mechanisms rather than requiring a fundamental rearchitecture.

**Sovereignty** — data residency enforcement, region-constrained placement, regulatory compliance — was never a design goal of the original Navy work. But when the requirement arose, it mapped naturally onto existing primitives: placement policies acting on metadata, provider contracts declaring regional capabilities, the four-state lifecycle tracking where data was intended to reside versus where it actually resides.

**Federation** — peered control planes operating across organizational boundaries — emerged from the same primitives: standardized contracts mean two UDLM-conformant systems can exchange data, the dependency graph tracks cross-boundary relationships, and governance policies can enforce boundaries between federated peers.

**Cross-domain orchestration** — coordinating actions across networking, compute, storage, and security domains — was enabled by the provider contract model: each domain participates as a provider, the control plane orchestrates through policy, and the dependency graph ensures correct ordering.

None of these capabilities were in the original design brief. The model's design principles — standardized contracts, policy injection by any stakeholder, dependency graph with provenance, four-state lifecycle — created the conditions for these capabilities to emerge. But they emerged *through engagement with real requirements*, not in a vacuum. Customer and partner feedback refined how provider contracts handle federation, how policy injection scales across sovereignty domains, and how the dependency graph supports cross-boundary relationships. The model is better because of that engagement, and these emergent capabilities are now core tenants of the architecture.

### Industry Validation

As the model matured, engagement with major financial services institutions both shaped and validated it. The arc was deliberate. At Red Hat Summit in May 2025, separate conversations with several large financial institutions surfaced the same desire — to centralize the management of resource lifecycles — that each had arrived at independently. In June 2025, the Red Hat FlightPath team convened their executives, directors, and representatives in New York at IBM, so the institutions could learn from each other directly. The engagements since have both refined the model and seeded a community. Concretely, those engagements surfaced requirements such as:

- One institution brought the question of **service boundaries** — how should federated, loosely-coupled broker instances interoperate with a centralized control plane? This drove refinements to UDLM's provider contracts and peering model, clarifying how distributed architectures participate in a shared lifecycle.
- Another needed **automated policy-driven placement** — routing resource requests to the right provider based on cost, capability, and regulatory constraints. This requirement exercised UDLM's policy contracts and influenced how placement semantics were formalized.
- A third asked for **full data center rehydration** — rebuilding an entire environment from stored intent records by replaying the dependency graph. This validated the four-state lifecycle and dependency model, and drove improvements to how intent records are versioned and replayed.
- A fourth needed **path-to-production enforcement** — validating every deployment against architecture standards before it reaches production. This shaped how UDLM's governance contracts codify approval gates as automated policy checks rather than manual processes.

Some of these requirements mapped naturally to existing primitives. Others drove new contracts, refinements, and extensions. The model was designed to be extensible — and these engagements exercised that extensibility, making the standard stronger through the community's real-world requirements.

---

## 3. UDLM: The Technical Foundation

UDLM is a wire-compatible substrate for systems that manage data through its lifecycle from intent to realization. Any system conformant to UDLM produces data that any other conformant system can read, interpret, and exchange. This section provides enough technical detail to evaluate the model's credibility without being a specification dump. The full specification lives in the UDLM repository.

### The Four-State Lifecycle

The core abstraction is the lifecycle of a resource through four canonical states:

1. **Intent** — what the consumer actually requested, captured unadulterated. This is the raw expression of need before any enrichment or policy is applied.
2. **Request** — the enriched and validated request, after policy has added defaults, organizational context, and governance checks. This is what gets sent to a provider.
3. **Realized** — the receipt from the provider: what was actually done, with the specific parameters, identifiers, and configuration of the provisioned resource.
4. **Discovered** — the current observed state of the resource, gathered through continuous probing. This is ground truth.

The gap between any two states is meaningful. Intent versus request reveals what policy changed. Request versus realized reveals what the provider actually did. Realized versus discovered reveals drift. Intent versus discovered reveals whether the original need is still being met. Closing these gaps — maintaining visibility across all four states — is what "closing the information loop" means in practice.

### Contracts, Not Code

UDLM defines *contracts*, not implementations. A provider contract specifies what a service provider must declare (capabilities, catalog items, cost model) and what it must report (state changes, realized configuration, discovered state). A policy contract specifies how governance is injected (evaluation context, convergence rules, modes). A data-store contract specifies how each lifecycle state is persisted and queried.

These contracts are what enable the "plug in any internal group" design principle. A new provider implements the provider contract. A new governance stakeholder implements the policy contract. Neither needs to understand or modify the control plane.

### Provider Kinds vs. Capabilities

A provider is distinguished by its **interaction shape** — how data flows and what it does — not by a long taxonomy of types. There are a small number of provider **kinds** (a resource/service provider that realizes resources, an information provider that serves authoritative data, a process provider that runs jobs to completion, and a peer for federation). Everything else a provider offers is a declared **capability** — a *yield* — layered onto a kind: authentication, credential issuance, notification, telemetry. Auth and credentials are **capabilities, not separate provider kinds**. This keeps the model small and open: you grow what the platform can do by declaring capabilities, not by minting new provider types. Providers are named by what they *yield*.

### Trust as a Brokered Capability

For credentials and identity, the control plane is a **trust broker, not a trust authority**. It does not custody, pass, or negotiate the brokered secret value — the value flows directly between producer and consumer; the control plane only *selects* a qualified provider, *scopes* the request, *gates* on attestation, and *audits* (the non-exposure invariant). Credentials are brokered the way resources are placed: a request declares what it needs and the assurance it requires; each candidate provider declares the credential capability and attestation level it can meet; the platform selects against the active profile's trust floor. A *claim* of capability is not *trust* in it — attestation is verified and graded on a ladder from self-asserted to hardware-attested. Security, trust, and fit-for-purpose deliberately outrank portability here. The control plane attests to **itself** by the same machinery, publishing a verifiable trust posture — the same bar it asks of every provider.

### The Recorded "Why"

Decisions are first-class data, not tribal knowledge. A **DecisionRecord** captures *why* a decision was made — the rationale resolving a finding about a capability, a policy, or a provider adoption — anchored to what it justifies, versioned, and paired with audit and field-level provenance. It is **scoped** (architecture, policy, or provider) and reaches authoritative status only through scope-appropriate validation (use-case/conformance, policy shadow-mode, or attestation verification) — the *why* is earned, not asserted. This is the substrate counterpart of an architecture decision record made machine-trackable, so a system carries its own decision provenance natively.

### Conformance

A system claims UDLM conformance by:

1. Implementing every required contract across the specification
2. Publishing a schema bundle at a well-known endpoint
3. Publishing a conformance declaration
4. Passing the conformance test suite

This is modeled after mature standards like OSCAL. Conformance is machine-checkable, not self-asserted.

### The OSCAL Analogy

OSCAL (Open Security Controls Assessment Language) made compliance machine-native. Before OSCAL, compliance posture was documented in Word files and spreadsheets — not machine-readable, not versioned, not comparable across tools. OSCAL defined a standard shape for compliance data, and an ecosystem emerged: tools that produce OSCAL, tools that consume it, tools that compare assessments over time.

UDLM aims to do the same for **infrastructure lifecycle** — how resources are represented, how their state flows from intent through realization to discovered truth, and how any system can read and exchange that data. And once lifecycle data is in a common, machine-readable shape, the reasoning built on top of it — capability coverage, maturity, where the gaps are, and what to build next — becomes machine-native too, instead of living in slide decks and consultants' heads. UDLM defines the standard shape; DCM and DAV are the first tools in what should become an ecosystem.

### Composite Services: A Managed Offering, Not a Provider

Most real consumption is not a single resource — it is a multi-tier *architecture*: a web tier, an app tier, a database, a network, storage, the dependencies between them, and the policy that governs them. UDLM models this as a **composite service**: a catalog-level offering that names its constituent resource types, the dependency graph among them, the binding fields a consumer fills, and the layered configuration (a locked base owned by a platform/technology team, plus the user-customizable fields it exposes — collapsing into one entity that records, with field-level provenance, which layer set each value). A consumer picks the offering, supplies a few bindings, and the control plane decomposes it into a governed multi-tier request — *declare what, not how*.

Crucially, a composite service **offering is its own class of managed artifact — not a provider.** A provider *realizes* a resource; an offering is an *authored, governed definition* that is consumed to produce a request. They sit at opposite ends of the pipeline, so offerings need their **own creation and lifecycle-management system**, distinct from resource realization:

- **Author** the offering (constituents, dependency graph, binding fields, base/user configuration layers).
- **Version and diff** it — an offering is versioned, and a new version yields a *delta*; existing deployments reconcile to it. This is exactly the act of **updating an existing deployment model**: change the offering, and what's deployed converges to the change set rather than being rebuilt wholesale.
- **Govern and approve** — an approval gate (a Gating Policy) promotes an offering to published/authoritative, carrying a DecisionRecord of who approved it and against what criteria.
- **Catalog-manage** — publish, list, supersede, deprecate — with the same status, provenance, and audit as any managed resource.

This reuses primitives already in the model (the service catalog, the policy engine for the approval gate, the DecisionRecord for approval provenance, the artifact lifecycle) rather than inventing a parallel mechanism. It is a defined long-term capability — *a suite of approved architectures, authored once and consumed many times, managed like a first-class resource.*

---

## 4. Where UDLM Sits in the Landscape

UDLM is not the first attempt to model infrastructure or capabilities. It is important to understand where it complements, rather than competes with, existing standards and tools.

**OSCAL** is compliance-as-data. UDLM is infrastructure-lifecycle-as-data — the common contract for how resources are represented and how their state is tracked across systems. They are complementary siblings occupying the same architectural layer (structured, machine-native data models) but addressing different domains; and because UDLM puts lifecycle data in a shared shape, capability and maturity reasoning (as DAV demonstrates) becomes possible on top of it. Mappings between OSCAL controls and UDLM are a natural integration point — a compliance finding maps to a capability gap.

**ArchiMate and TOGAF** are heavyweight enterprise architecture frameworks. They model the structure of an enterprise — business processes, application components, technology infrastructure — through formal notations and extensive metamodels. UDLM is lighter weight and more narrowly focused: it models the *lifecycle and state* of infrastructure resources, not the full enterprise architecture. ArchiMate models structure; UDLM models lifecycle. They operate at different altitudes.

**Developer portals and scorecards (Backstage is one example)** are a *surface* layer — a UI through which developers and platform engineers interact with service catalogs, maturity dashboards, and self-service provisioning. UDLM is the lifecycle data layer beneath, and the relationship is deliberately **integration-focused and surface-agnostic**: because the data and contracts are standardized, a portal can integrate by consuming UDLM data — a Backstage plugin could render maturity scores; DCM could back its catalog — but no particular front end is part of the architecture or guaranteed in any direction. The presentation layer is a choice, not a dependency. This is the same extensibility that lets a provider or a governance stakeholder plug in (§2, §3): the load-bearing concerns — governance, provenance, and orchestration — live in the contracts and the lifecycle model, and integrations attach on top without the core having to know about them.

**Proprietary platforms** — ServiceNow CMDB, vendor-specific maturity assessment tools, cloud provider resource managers — each define their own representation of resources and capabilities. They are not interoperable, not comparable, and not extensible by the organization using them. UDLM provides the common data contract that these systems could speak if they chose to — or that organizations can use to build an interoperability layer on top of them.

UDLM does not compete with any of these. It sits beneath them as the data contract layer.

---

## 5. Evidence: The Model in Action

Claims about data models are easy to make and hard to prove. DAV (Data Architecture Validator) was built to validate the DCM architecture: it evaluates real use cases against real architectural specifications and produces gap analyses, maturity, and roadmaps — and it represents all of that in UDLM, which exercises and validates the data model itself. DCM, in turn, has a first engineering sample that runs the model on real infrastructure. The evidence below is from actual customer engagements, not synthetic benchmarks.

A note on terms and figures. A **capability** here means a discrete unit of functionality in the DCM taxonomy — the common vocabulary where use-case *demand* and provider *supply* meet (a use case resolves to the capabilities it requires; a provider declares the capabilities it offers). UDLM is the data model that represents and moves capabilities and resource state; DAV reasons *over* them. And the figures in this section are **point-in-time** (as of 2026-06-16): the use-case corpus, the specification, and the analysis all evolve, so every quantitative claim is a reproducible query against the current data, not a fixed fact.

### The Numbers

DAV evaluated **84 use cases** sourced from **six financial services institutions** against the DCM/UDLM architecture. These use cases span infrastructure provisioning, policy governance, cost management, network automation, supply chain security, confidential computing, event-driven remediation, and cross-domain orchestration. They were not curated to make the model look good — they were submitted by customers to represent their actual operational needs.

The evaluation surfaced **248 gaps** (snapshot: 2026-06-16; the analysis is a reproducible query, regenerated as use cases or the specification change): 3 critical, 18 major, 135 moderate, 27 minor, and 17 advisory. Each gap is tied to the specific use case that demands the missing capability, with a structured assessment of what is partially supported and what is missing.

These gaps were clustered by the tooling into **11 capability themes** across **3 priority tiers**:

- **Tier 1** (security and trust spine): confidential computing and TEE attestation, sovereignty and federation, audit and governance and policy — the foundational decisions everything else depends on
- **Tier 2** (data protection and integration surface): encryption and data integrity, cross-cloud residency, workflow automation and orchestration — the contracts and interfaces
- **Tier 3** (domain capabilities): metering and FinOps, AI intake, base provisioning, identity and drift — capabilities that consume the Tier 1 and 2 foundations

### The Dogfooding Proof

The standard's own architectural roadmap was generated by its own reference implementation. This is not a hand-authored document — it is a reproducible query against the DAV database. The roadmap is a function of the use cases and the architecture, regenerated on demand as either changes.

In this snapshot the critical gaps cluster on trust-boundary and sovereignty decisions — for example attestation-gated key release, sovereign signing-key residency, and federation lifecycle. They were identified by machine analysis of customer use cases against the specification, not by a committee discussing priorities in the abstract. This is what machine-native capability reasoning looks like when the underlying data model is right.

### Cross-Architecture Comparison

Two independently-built infrastructure lifecycle management platforms — one using a centralized control plane with external service providers, the other fully federated with loosely-coupled broker instances communicating via a pub/sub event bus — were evaluated against the same UDLM model.

The comparison surfaced alignment points neither team had identified: both had independently arrived at similar entity models, similar lifecycle state tracking, and similar provider abstraction patterns. It also surfaced structural differences — centralized versus federated dependency graph ownership, orchestration models, schema ownership — that represent genuine architectural tradeoffs, not bugs to fix.

This is the value of a common data model: it makes comparison possible. Without a shared representation, the teams spent hours talking past each other using different terminology for the same concepts. With the model, the alignment and divergence were immediately visible.

### Continuous Architectural Validation

One institution asked for DAV to be integrated into their CI/CD pipeline: when architecture specifications change, automatically validate that all customer use cases are still satisfied before the change is merged. The specification becomes a continuous architectural gate — not a point-in-time assessment, but an ongoing contract between the architecture and the use cases it serves.

### Foundational Capability Discovery

Demand density analysis across organizations revealed a pattern that manual prioritization consistently misses: low-profile infrastructure capabilities — dependency graph traversal, schema versioning, reverse-dependency queries — appear in relatively few use cases directly, but are **blocking dependencies** for the high-profile capabilities that institutions actually ask for. Without the data model making these relationships explicit, teams consistently prioritize the visible capabilities and discover the foundational gaps only when they try to build.

### Strategic Sourcing: The Capability Map as a Partner Decision Framework

The DCM capability model has proven valuable not only for gap analysis but for **strategic sourcing** — deciding which partner should be the single strategic source for each capability domain.

A repeatable 8-step process applies the Use-Case → Capability method in "sourcing mode": demand flows from use cases through roles and goals to capabilities (what the business needs), and a provider's offerings are crosswalked against those same capabilities (what the provider can supply). The capability map is the meeting point. Because both demand and supply resolve to the same capabilities, providers can be evaluated objectively and a single strategic source can be chosen per capability.

A global systems integrator was crosswalked against all 13 DCM capability domains using a 6-dimension scoring rubric (capability coverage, delivery maturity, skills and scale, integration fit, NFR fit, and strategic alignment). Each capability received an R4 disposition:

- **Adopt** (strategic single source): 7 capabilities including provisioning, monitoring, network management, incident response, change management, and security — areas where the integrator's run-at-scale operations and global SOCs were strong
- **Uplift** (adopt and co-develop gaps): 4 capabilities including asset inventory, service catalog, cost management, and identity — partial coverage requiring co-investment
- **Replace/Build**: 1 capability (facilities/DCIM) — outside both Red Hat's and the integrator's portfolios, requiring a specialist

The combined sourcing view revealed that the technology vendor (Red Hat) and the delivery partner are **complementary layers, not competing options** — Red Hat supplies the technology and build capability; the integrator supplies delivery and operate capability. The overlay sorted every capability into co-deliver, vendor-led, integrator-led, or gap zones, producing a defensible, traceable sourcing decision rather than a vendor-by-vendor opinion.

This demonstrates a use of the capability model that extends beyond architecture evaluation: DCM capabilities become the **common language for multi-partner portfolio governance**, with two-way traceability from provider through capability to use case, living dispositions that are re-scored on roadmap or contract changes, and a "Do No Harm" principle applied to anything retired or replaced.

### Internal Red Hat Ecosystem

The model has attracted collaboration from within Red Hat as well. OSAC (the Open Sovereign AI Cloud) is a potential **integration partner**, and the fit is concrete. OSAC's real strength is **provisioning platforms** — and platform provisioning is precisely a *realization* of UDLM data operationalized by DCM (the "realized" state of the four-state lifecycle, §2–3). The two are better together: DCM and UDLM supply the governed, machine-native lifecycle — the data model, policy, provenance, and orchestration — and OSAC supplies the provisioning that turns intent into realized platforms. For customers that means a partner's provisioning capability available **out of the box**, governed and traceable by design, at a fraction of the **time-to-value** of building it themselves. The Cost Management team is integrating their repositories — including metering schemas, cloud bill ingestion, and operator metrics — so that cost and FinOps capabilities can be evaluated against the same use case corpus. A confidential computing team has contributed use cases spanning attestation, key custody, data residency, and secure model training — capabilities that emerged naturally from UDLM's sovereignty primitives.

A semiconductor partner has invested in a co-engineering lab with physical hardware (bare metal servers, enterprise storage arrays, and OpenShift clusters) specifically for multi-customer DCM validation and development. This lab serves as a neutral ground where the community can test the standard against real infrastructure without relying on any single organization's environment.

### First Engineering Sample

The model is not only paper and database queries. In May 2026, the first DCM engineering sample was demonstrated end to end by **rehydrating a sovereign workload** — rebuilding a region-constrained environment from stored intent records by replaying the dependency graph, with sovereignty policy enforced throughout. This exercised the four-state lifecycle, the dependency model, and the sovereignty primitives together, on running software rather than in the abstract. It is the same rehydration capability that one institution asked for (§2), shown working.

### Honest Assessment

DAV is a working reference implementation with real output from real engagements. It is not yet deployed in production at a customer site. The evidence presented here validates the approach and the data model — it demonstrates that UDLM's shape is right for this problem. It does not claim scale. The value is in the demonstrated capability of machine-native reasoning over a shared data model, and the proof that the model's primitives are sufficient for the problems the industry faces.

---

## 6. Governance and the Path Forward

### Open by Default

UDLM is licensed under Apache 2.0 with Developer Certificate of Origin (DCO). The specification repository contains 55 documents with clean IP history. The governance model and contribution guidelines are established.

UDLM, the standard, is deliberately separable from everything built around it: DCM is one **realization**, DAV is a **reference implementation**, and OSAC (the Open Sovereign AI Cloud) is a potential **integration partner** — a platform whose provisioning capability combines with UDLM and DCM, not the owner of the standard. This separation is what keeps the standard neutral, and it is load-bearing for the CNCF path.

### Co-Engineering Momentum

The institutions that independently converged on this need in 2025 are now actively re-engaged in co-engineering. Several are building their own limited-scope versions in parallel; the goal of the current engagement is to fold the lessons from those efforts into a single open-source platform — one that may be productized later in some fashion — and to bring those organizations deeper into the community as co-authors rather than consumers. The case for consolidating is concrete: **fewer duplicated engineering efforts** across every institution, **faster time-to-market** for new capabilities, and the backing of **corporate support** rather than each maintaining a fragile in-house fork. This is the community flywheel the governance model is built to sustain.

**The goal is CNCF.** UDLM is being built with the intent to submit it to the Cloud Native Computing Foundation — first as a sandbox project, then incubation. CNCF provides the neutral governance home, the ecosystem credibility, and the multi-vendor trust that a standard like this requires to achieve broad adoption. Kubernetes, Prometheus, Open Policy Agent, and OSCAL's ecosystem all demonstrate that standards succeed when they are vendor-neutral and community-governed. UDLM is being designed to meet that bar from day one: Apache-2.0 licensing, DCO, clean IP history, and a governance model that requires maintainers from more than one organization before submission. The path to CNCF is not aspirational — it is the reason every structural decision about neutrality, governance, and contributor experience is being made the way it is.

### The Contributor Path

The value ladder for community participation is:

1. **Use it** — evaluate the UDLM spec, point DAV at your architecture, bring your use cases
2. **Extend it** — add capabilities to the taxonomy, define domain-specific contracts
3. **Co-design it** — participate in RFC processes for spec changes, review and improve contracts
4. **Maintain it** — become a maintainer, with commit authority and architectural stewardship

The on-ramp is real today: the UDLM specification repository, the conformance test framework, and the reference implementation are available for evaluation and contribution.

### The Sequence

- **Phase 0** — Align the engineering team on the three-layer framing, make architectural decisions on the critical gaps, co-author the honest current state
- **Phase 1** — UDLM v0 contract: extract, formalize, and test the versioned data-model specification. The Tier 1 capability themes (trust, sovereignty, governance) drive entity priorities
- **Phase 2** — DCM taxonomy v1 and conformance: seed the core capability taxonomy with the extension model, publish the conformance test suite
- **Phase 3** — Ecosystem: recruit non-Red-Hat maintainers, publish mappings to OSCAL and developer portals (e.g. Backstage), ship adopter tooling
- **Phase 4** — CNCF application, supported by real adoption and multi-organization maintainership

### Where Things Stand Today

The UDLM specification exists with 55 documents. DCM is a working realization, with a first engineering sample demonstrated in May 2026 via sovereign-workload rehydration. DAV is a working reference implementation that has evaluated 84 use cases from 6 institutions and generated a machine-native roadmap. The model's primitives have been validated by emergent capabilities (sovereignty, federation), by independent industry demand, and by active co-engineering with the institutions that converged on the need.

What does not exist yet: production deployment at a customer site, completed OSAC (or other partner) integration, non-Red-Hat maintainers, formal CNCF submission, or a curated capability taxonomy. These are the gaps the community can fill.

---

## 7. The Destination

### The End Goal

A single unified control plane and data model to manage the lifecycle and configuration of everything in a data center and enterprise — resources, processes, policies, services, cost, compliance. Everything that has a lifecycle, managed through one data model, one policy engine, one set of contracts.

This is not a product goal. It is a platform goal. The question is not "will DCM be the product that does this?" — it is "will UDLM be the data model that enables this, and will DCM be one of the realizations that proves it?"

### Two Paths, Same Destination

**Integrate into all Red Hat products.** RHEL, OpenShift, Ansible, RHOAI, ACM, ACS, Quay, Cost Management — each speaks UDLM natively. Each product is a provider or a policy source within the model. Customers get unified lifecycle management because their existing stack already speaks the common language. UDLM becomes the integration contract that binds the portfolio together.

**DCM as the control plane that binds them.** DCM sits above the products as the orchestration and governance layer. Products are providers. DCM routes requests, enforces policy, tracks lifecycle. Customers get a single pane of control regardless of which products they use — Red Hat or otherwise.

These paths are not mutually exclusive. The first makes each product UDLM-aware. The second makes DCM the orchestrator. Together: every product speaks the language, and one control plane governs the conversation. Either path could include integration into broader platform initiatives. The model is not bound to one home — it is bound to a purpose.

### The Model Is Domain-Agnostic

The four-state lifecycle, provider contracts, policy governance, and dependency graphs are not IT-specific primitives. They describe anything that flows from intent through realization to discovered truth. The entity-type family mechanism exists specifically so new domains can extend the model without changing the substrate:

- **Infrastructure lifecycle** — proven (the origin, the FSI engagements, the Summit demo)
- **Software delivery lifecycle** — designed and blueprinted (the Delivery family extension)
- **Capability and maturity reasoning** — proven (the Knowledge family, DAV, 84 UCs evaluated)
- **Operational intelligence** — designed (observability providers, event correlation, resilience scoring)

Each new domain exercised the model's extensibility and validated that the primitives were sufficient. Where those domains lead next is determined by the community's needs, not by a boundary drawn today.

---

_The companion executive brief carries the full vision of composable, policy-governed services and the partner call to action. The ask of this paper is narrower and concrete:_

- **Contributors** — extend the DCM taxonomy, co-design the UDLM spec.
- **Adopters** — operate via UDLM, and report back.
- **The first maintainer from a second organization** — the single highest-leverage step toward a credible, neutral standard.

The destination is CNCF.

---

*UDLM specification: github.com/dcm-project/udlm*
*DCM reference realization: github.com/dcm-project/dcm*
*DAV reference implementation: github.com/croadfeldt/dav*

*This document contains evidence derived from customer engagements conducted under NDA. All customer references are anonymized. All quantitative figures are point-in-time snapshots (2026-06-16), traceable to the DAV reference implementation's database and reproducible from the current data.*
