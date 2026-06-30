# Infrastructure Lifecycle Without a Common Language: How UDLM and DCM Make Capability Reasoning Machine-Native

**A whitepaper on the Universal Data Lifecycle Model (UDLM) and the Data Center Management (DCM)**

---

## 1. The Problem

Every organization that manages infrastructure at scale has the same problem. Multiple internal groups — the CISO office, the standards body, the networking team, the storage team, the compute team, the compliance group, the operations team — all have a say in the lifecycle of every resource. A virtual machine isn't just a VM. It's a compute resource that the standards team defined, the security team must approve, the networking team must connect, the compliance team must validate, the operations team must monitor, and the cost management team must account for.

Today, there is no standardized way for these groups to participate. Each builds bespoke integration with whatever provisioning system exists. Each maintains its own representation of what resources look like and what policies apply. Each enforces its own rules through its own mechanisms — a spreadsheet here, a manual approval gate there, a custom script somewhere else.

The information loop is broken. A resource is requested, provisioned, modified, drifted, remediated — but the groups who define standards, enforce security, or manage compliance have no structured visibility into what was *intended* versus what was *requested* versus what was *realized* versus what *actually exists right now*. Drift goes undetected because there is no machine-readable baseline to compare against. Provenance is lost because changes aren't tracked through a common model. Audit is manual because the data isn't in a shape that tools can consume.

This is not a theoretical problem. Over the course of twelve months, six major financial services institutions — independently, without knowledge of each other's efforts — asked Red Hat for the same thing: a standardized way to manage infrastructure lifecycle, close the information loop, and let their internal stakeholders participate through a common framework rather than bespoke integration. Each institution was building its own version. Independently. Incompatibly. At enormous cost.

In one session, an architect at a global institution asked five times in three hours to "see the data model" — because there was no shared format to compare two platforms that were solving the same problem differently. Neither team could evaluate the other's approach without a common representation of what capabilities existed, what state resources were in, and how policies were applied.

At another institution, the CTO's architecture organization was building code-first infrastructure definitions using a domain-specific language — but had no way to automatically validate those definitions against the use cases they were supposed to serve. The architecture was code, but the validation was still manual.

At yet another, capability and maturity data lived in slide decks, spreadsheets, and consultants' heads. Not machine-readable. Not versioned. Not comparable across tools, teams, or time.

The cost of this status quo is staggering: duplicated engineering across every institution, vendor lock-in because each tool invents its own representation, no ecosystem because there is no shared shape to build on, and no ability to compare capability or maturity assessments across organizations, vendors, or time periods. Every institution pays this cost independently, and the industry as a whole cannot learn from shared experience because there is no common language to share it in.

---

## 2. Origin: From Operational Need to Open Standard

### The Navy Problem

The data model that became UDLM was born from a concrete operational need. The United States Navy needed to manage infrastructure across disconnected environments — ships operating independently from shore, sometimes for extended periods without connectivity. They needed:

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

From this insight, the Universal Data Lifecycle Model was born. UDLM is not a product. It is a wire-compatible substrate — a set of contracts that any system can implement. Its core abstraction is a four-state lifecycle (intent → request → realized → discovered) that closes the information loop. Around that core, 55 specification documents define the contracts that enable any participant to plug in: provider contracts, policy contracts, data-store contracts, governance, observability, and a conformance framework. The technical detail is in §3; here, the point is that UDLM was designed to codify the strategic insight — standardized participation — into something machine-checkable and implementable.

### DCM: Operationalizing the Model

DCM was designed to operationalize UDLM as a running infrastructure lifecycle management system — a policy engine, provider abstraction, dependency graph, and cost analysis integration. DCM is one realization of UDLM. Any system that implements the UDLM contracts is a peer.

### Emergent Capabilities: The Model Proved Itself

The strongest validation of a data model is when new requirements can be addressed through the model's existing extension mechanisms rather than requiring a fundamental rearchitecture.

**Sovereignty** — data residency enforcement, region-constrained placement, regulatory compliance — was never a design goal of the original Navy work. But when the requirement arose, it mapped naturally onto existing primitives: placement policies acting on metadata, provider contracts declaring regional capabilities, the four-state lifecycle tracking where data was intended to reside versus where it actually resides.

**Federation** — peered control planes operating across organizational boundaries — emerged from the same primitives: standardized contracts mean two UDLM-conformant systems can exchange data, the dependency graph tracks cross-boundary relationships, and governance policies can enforce boundaries between federated peers.

**Cross-domain orchestration** — coordinating actions across networking, compute, storage, and security domains — was enabled by the provider contract model: each domain participates as a provider, the control plane orchestrates through policy, and the dependency graph ensures correct ordering.

None of these capabilities were in the original design brief. The model's design principles — standardized contracts, policy injection by any stakeholder, dependency graph with provenance, four-state lifecycle — created the conditions for these capabilities to emerge. But they emerged *through engagement with real requirements*, not in a vacuum. Customer and partner feedback refined how provider contracts handle federation, how policy injection scales across sovereignty domains, and how the dependency graph supports cross-boundary relationships. The model is better because of that engagement, and these emergent capabilities are now core tenants of the architecture.

### Industry Validation

As the model matured, engagement with major financial services institutions both shaped and validated it:

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

### Conformance

A system claims UDLM conformance by:

1. Implementing every required contract across the specification
2. Publishing a schema bundle at a well-known endpoint
3. Publishing a conformance declaration
4. Passing the conformance test suite

This is modeled after mature standards like OSCAL. Conformance is machine-checkable, not self-asserted.

### The OSCAL Analogy

OSCAL (Open Security Controls Assessment Language) made compliance machine-native. Before OSCAL, compliance posture was documented in Word files and spreadsheets — not machine-readable, not versioned, not comparable across tools. OSCAL defined a standard shape for compliance data, and an ecosystem emerged: tools that produce OSCAL, tools that consume it, tools that compare assessments over time.

UDLM aims to do the same for capability and maturity. Before UDLM, capability reasoning — what an organization or platform can do, how mature each capability is, where the gaps are, and what to build next — is ad-hoc, vendor-locked, and unversioned. UDLM defines the standard shape. DCM and DAV are the first tools in what should become an ecosystem.

---

## 4. Where UDLM Sits in the Landscape

UDLM is not the first attempt to model infrastructure or capabilities. It is important to understand where it complements, rather than competes with, existing standards and tools.

**OSCAL** is compliance-as-data. UDLM is capability-and-maturity-as-data. They are complementary siblings occupying the same architectural layer (structured, machine-native data models) but addressing different domains. Mappings between OSCAL controls and UDLM capabilities are a natural integration point — a compliance finding maps to a capability gap.

**ArchiMate and TOGAF** are heavyweight enterprise architecture frameworks. They model the structure of an enterprise — business processes, application components, technology infrastructure — through formal notations and extensive metamodels. UDLM is lighter weight and more narrowly focused: it models the *lifecycle and state* of infrastructure resources, not the full enterprise architecture. ArchiMate models structure; UDLM models lifecycle. They operate at different altitudes.

**Backstage and developer portal scorecards** are the surface layer — the user interface through which developers and platform engineers interact with service catalogs, maturity dashboards, and self-service provisioning. UDLM is the capability-semantics layer beneath. A Backstage plugin could consume UDLM data to render maturity scores; DCM could serve as the backend that Backstage's catalog queries. They are complementary layers, not alternatives.

**Proprietary platforms** — ServiceNow CMDB, vendor-specific maturity assessment tools, cloud provider resource managers — each define their own representation of resources and capabilities. They are not interoperable, not comparable, and not extensible by the organization using them. UDLM provides the common data contract that these systems could speak if they chose to — or that organizations can use to build an interoperability layer on top of them.

UDLM does not compete with any of these. It sits beneath them as the data contract layer.

---

## 5. Evidence: The Reference Implementation in Action

Claims about data models are easy to make and hard to prove. UDLM has a reference implementation — DAV (Data Architecture Validator) — that adopted the UDLM model and uses it to evaluate real use cases against real architectural specifications. The evidence below is from actual customer engagements, not synthetic benchmarks.

### The Numbers

DAV evaluated **84 use cases** sourced from **six financial services institutions** against the DCM/UDLM architecture. These use cases span infrastructure provisioning, policy governance, cost management, network automation, supply chain security, confidential computing, event-driven remediation, and cross-domain orchestration. They were not curated to make the model look good — they were submitted by customers to represent their actual operational needs.

The evaluation surfaced **248 gaps**: 3 critical, 18 major, 135 moderate, 27 minor, and 17 advisory. Each gap is tied to the specific use case that demands the missing capability, with a structured assessment of what is partially supported and what is missing.

These gaps were clustered by the tooling into **11 capability themes** across **3 priority tiers**:

- **Tier 1** (security and trust spine): confidential computing and TEE attestation, sovereignty and federation, audit and governance and policy — the foundational decisions everything else depends on
- **Tier 2** (data protection and integration surface): encryption and data integrity, cross-cloud residency, workflow automation and orchestration — the contracts and interfaces
- **Tier 3** (domain capabilities): metering and FinOps, AI intake, base provisioning, identity and drift — capabilities that consume the Tier 1 and 2 foundations

### The Dogfooding Proof

The standard's own architectural roadmap was generated by its own reference implementation. This is not a hand-authored document — it is a reproducible query against the DAV database. The roadmap is a function of the use cases and the architecture, regenerated on demand as either changes.

The three critical gaps — attestation-gated key release logic, sovereign signing-key residency, and federation decommissioning with an unreachable peer — are all trust-boundary and sovereignty decisions. They were identified by machine analysis of customer use cases against the specification, not by a committee discussing priorities in the abstract. This is what machine-native capability reasoning looks like when the underlying data model is right.

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

The model has attracted collaboration from within Red Hat as well. OSAC (the Open Source Architecture Council) has adopted UDLM and is positioned as a reference use case. The Cost Management team is integrating their repositories — including metering schemas, cloud bill ingestion, and operator metrics — so that cost and FinOps capabilities can be evaluated against the same use case corpus. A confidential computing team has contributed use cases spanning attestation, key custody, data residency, and secure model training — capabilities that emerged naturally from UDLM's sovereignty primitives.

A semiconductor partner has invested in a co-engineering lab with physical hardware (bare metal servers, enterprise storage arrays, and OpenShift clusters) specifically for multi-customer DCM validation and development. This lab serves as a neutral ground where the community can test the standard against real infrastructure without relying on any single organization's environment.

### Honest Assessment

DAV is a working reference implementation with real output from real engagements. It is not yet deployed in production at a customer site. The evidence presented here validates the approach and the data model — it demonstrates that UDLM's shape is right for this problem. It does not claim scale. The value is in the demonstrated capability of machine-native reasoning over a shared data model, and the proof that the model's primitives are sufficient for the problems the industry faces.

---

## 6. Governance and the Path Forward

### Open by Default

UDLM is licensed under Apache 2.0 with Developer Certificate of Origin (DCO). The specification repository contains 55 documents with clean IP history. The governance model and contribution guidelines are established.

OSAC (the Open Source Architecture Council) is positioned as an **adopter** of UDLM, not the owner. DCM is a **realization**. DAV is a **reference implementation**. This separation is deliberate and load-bearing.

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
- **Phase 3** — Ecosystem: recruit non-Red-Hat maintainers, publish mappings to OSCAL and Backstage, ship adopter tooling
- **Phase 4** — CNCF application, supported by real adoption and multi-organization maintainership

### Where Things Stand Today

The UDLM specification exists with 55 documents. DCM is a working realization. DAV is a working reference implementation that has evaluated 84 use cases from 6 institutions and generated a machine-native roadmap. The model's primitives have been validated by emergent capabilities (sovereignty, federation) and by independent industry demand.

What does not exist yet: production deployment at a customer site, non-Red-Hat maintainers, formal CNCF submission, or curated capability taxonomy. These are the gaps the community can fill.

---

## 7. The Vision: Everything as a Service, Working Together

The evidence in the preceding sections demonstrates what UDLM and DCM can do today. But the architecture was designed for something larger: a world where every group that participates in infrastructure lifecycle **codifies their work as a service** — and those services compose naturally to accomplish whatever intent an organization expresses.

### The Universal Integration Hub

Today, organizations integrate their internal teams and external partners through point-to-point connections. The networking team builds a custom integration with the provisioning system. The security team builds another. The cost management team builds a third. Each new participant multiplies the integration surface geometrically. The result is brittle, expensive, and resistant to change.

UDLM replaces this with a hub architecture. Every participant — whether an internal standards body, a security team, a delivery partner, or an automation platform — connects once by implementing the standard contracts. The data model is the integration layer. There is no middleware, no enterprise service bus, no integration platform to maintain. The shared representation of resources, lifecycle states, dependencies, and policies is what enables interoperability. A new team plugs in by implementing the provider contract. A new governance stakeholder plugs in by implementing the policy contract. Neither needs to know about the other. The hub grows without coordination overhead.

This is what "everything as a service" means in practice. The networking team offers IP allocation as a service. The storage team offers volume provisioning as a service. The security team offers policy validation as a service. The cost management team offers chargeback as a service. Each team codifies their work — their expertise, their rules, their operational model — into the standardized contracts. And those services compose: when someone expresses the intent "I need a production web application," the control plane assembles the right services, routes through the right policies, and fulfills the intent without any human needing to understand every domain involved.

### What This Looks Like in Practice

**A new branch office opens.** A technician plugs in a switch and a router. The intent — "new branch, site 4523" — triggers a chain of services, each codified by the team that owns the domain: the provisioning service configures the devices, the networking service allocates DHCP and DNS, the compliance service runs security scans against the organization's rules, and a notification service tells the cabling team where to plug in the uplinks. Each team built their piece independently. The composition fulfills the intent without any single team understanding the full picture.

**A developer requests a production web application.** They specify their code repository and that it needs to be in a specific geographic region. The control plane decomposes this into a web tier, an application tier, and a database — each fulfilled by a different provider service. A sovereignty policy fires because a region constraint is present, eliminating providers outside that region. A cost policy scores the remaining providers and selects the most cost-effective. A security policy enriches the request with production hardening requirements that the developer never specified but the organization requires. The developer asked once. Six services composed. Every governance requirement was met without a single approval ticket.

**A data center goes offline.** The disaster recovery team initiates rehydration. The control plane replays stored intents from the intent store. The dependency graph — built into the data model — knows the database must exist before the application tier, the application tier before the web tier, and the DNS record after the web tier is reachable. Each provider service executes its piece independently, in the order the dependency graph dictates. The composition reconstructs the full environment from intent, including all policy enrichments and sovereignty constraints that were applied to the original deployment.

**A network device drifts from its intended configuration.** The observability service detects that the discovered state no longer matches the realized state — someone changed a configuration outside of the standard process. The policy engine evaluates the drift: for known-safe deviations (a console port left enabled), the remediation service auto-corrects and the ITSM service documents an auto-resolved incident. For unknown changes (an unexpected ACL modification), the incident service escalates to the networking team with a before-and-after diff. All through the same contracts, all auditable, no human in the detection-to-action loop for the known cases.

**A consumer requests a virtual machine without specifying where it should run.** Three providers can serve the request. The cost management service scores each provider's current pricing. The placement policy selects the cheapest option that meets all other constraints. The provisioning service deploys. The cost service records the chargeback against the consumer's cost center. From intent to realized to accounted-for in one flow, with the consumer never needing to know which provider was selected or why.

In every case, the pattern is the same: intent is expressed, services compose to fulfill it, policies govern the composition, and the four-state lifecycle tracks what happened from beginning to end. No team needed to build a custom integration with any other team. Each codified their work once, through the standard contracts, and the hub composed them.

### Governed by Policy, Not Process

In this model, governance is not a manual checkpoint that slows delivery. It is structural — enforced by the architecture itself. Every request flows through the policy engine. Every stakeholder's governance is expressed as policy that fires automatically when relevant data is present. The CISO's encryption requirements, the compliance team's data residency rules, the standards body's architectural constraints — all codified, all enforced, all auditable.

This is fundamentally different from governance-by-process, where a human remembers to check a box, send an email, or open a ticket. Process governance is fragile — it breaks when people are busy, when teams change, when someone doesn't know the process exists. Policy governance is structural — it cannot be accidentally skipped because it is built into the flow of data through the system.

The standards live in the architecture, the API, and the data model. They are not documented in a wiki and hoped for. They are implemented as contracts and enforced.

### AI-Enabled, Deterministically Governed

The machine-native data model creates the foundation that AI needs to reason about infrastructure. AI cannot operate on slide decks and spreadsheets. It requires structured, versioned, comparable data — exactly what UDLM provides.

The architecture is designed with a clear separation: the policy layer is **deterministic** — it always produces the same result for the same input. AI sits **on top of** the deterministic layer, not inside it. AI enhances the system — analyzing patterns across the capability map, suggesting policy optimizations, predicting drift before it occurs, generating capability assessments from natural language descriptions, converting conversations into structured use cases. But the policy engine always has final authority. An AI can recommend; only a policy can enforce.

The reference implementation already demonstrates this pattern. DAV uses an LLM to evaluate use cases against the architecture, but the output conforms to the UDLM schema. AI generates the insight; the data model ensures the insight is structured, comparable, and actionable. The recording-to-use-case pipeline accepts meeting audio and produces structured use cases — AI extracts; the data model standardizes.

Future capabilities enabled by this foundation include:

- **AI-driven intake** — natural language requests translated into structured intent through conversational agents, validated in real-time against the capability map and policy constraints
- **Predictive gap analysis** — AI identifies capability gaps before they manifest as incidents, by analyzing demand patterns, dependency relationships, and drift trends across the estate
- **Automated policy generation** — AI suggests governance policies based on observed patterns, organizational standards, and industry benchmarks — reviewed and approved by humans, enforced deterministically
- **Cross-organization learning** — with a common data model, anonymized capability and maturity assessments can be compared across organizations, enabling industry-wide benchmarking without exposing proprietary details

All of this is enabled by the machine-native data model. All of it is governed by the deterministic policy layer. The AI makes the system smarter. The policies keep it honest. The data model makes it interoperable.

---

## 8. Build With Us

This is not a finished product looking for consumers. It is a shared foundation being built **with** and **for** the community — to enable and empower organizations to solve their own infrastructure lifecycle challenges on common ground.

The community forming around UDLM/DCM already spans three categories of partnership, each engaging through the same capability model:

### For Enterprise Architects and Platform Leaders (Co-Engineering Partners)

You are already building this — independently, expensively, incompatibly. Bring your use cases, your operational models, your hard-won lessons. Shape the standard while it is being formed. The institutions who engage now are co-authors, not consumers.

Six financial services institutions are already co-engineering — contributing use cases, validating the model against their architectures, and shaping the capabilities that the standard will support. One institution took the UDLM architecture, pulled it into their own AI system, and ran their own cross-platform comparison — independently, without Red Hat involvement. That is what empowerment looks like: a standard that is useful to you on your terms, not a product that requires a vendor relationship.

### For Delivery Partners and Systems Integrators

The DCM capability model provides a common language for strategic sourcing decisions. A delivery partner can be crosswalked against the capability map to produce per-capability sourcing dispositions — Adopt, Uplift, Replace, or Decline — with a traceable, defensible decision record rather than a vendor-by-vendor opinion. The result is a multi-partner delivery model where each capability has a single strategic source, overlaps are resolved at portfolio review, and governance is capability-level rather than project-level.

If you are a systems integrator, a managed services provider, or a technology partner: contribute your capabilities to the model. The more partners that are crosswalked against the same capability map, the more valuable that map becomes for everyone — customers can make informed sourcing decisions, partners can identify their strengths and gaps, and the industry develops a shared vocabulary for what "delivering infrastructure lifecycle management" actually means.

### For Red Hat Internal Teams (Technology Partners)

Multiple Red Hat teams are already collaborating through the model. OSAC has adopted UDLM as a reference use case. The Cost Management team is integrating their metering and FinOps capabilities. Confidential computing teams are contributing sovereignty and attestation use cases. Each team participates through the same standardized contracts that external partners use — proving that the model works for internal organizational collaboration, not just external partnerships.

Our role is to steward the standard and empower the community, not to own it. OSAC is an adopter. DCM is a realization. DAV is a reference implementation. The value we create is in the shared foundation that enables everyone — including our customers, our partners, and our own internal teams — to build what they need on their own terms.

The strongest outcome is an ecosystem where UDLM is the lingua franca for infrastructure lifecycle data, governed by CNCF, with Red Hat as one valued participant among many. The community we build now — the co-engineering partners, the delivery partners, the technology contributors — becomes the multi-organization foundation that makes a credible CNCF submission possible.

---

*UDLM specification: github.com/dcm-project/udlm*
*DCM reference realization: github.com/dcm-project/dcm*
*DAV reference implementation: github.com/croadfeldt/dav*

*This document contains evidence derived from customer engagements conducted under NDA. All customer references are anonymized. All quantitative claims are traceable to the DAV reference implementation's database and are reproducible.*
