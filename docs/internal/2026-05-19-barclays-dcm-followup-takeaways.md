# DAV Feature Requests — Barclays DCM Follow-Up Meeting (2026-05-19)

Chris demoed the DCM architecture and Summit rehydration demo to Barclays (Kevin Cattell, Christie, John, Kellian/Folly) with Red Hat (Adam, Ryan Goodson, Deb Brubaker, Liz Spangler). The meeting compared DCM and Barclays' Apex platform architectures, identified overlaps and gaps, and discussed community-building toward a shared data model / co-engineering effort.

## DAV-Relevant Feature Requests and Improvements

### 1. Cross-Architecture Comparison Mode

**What:** DAV should be able to ingest two (or more) architectural specs and produce a structured comparison: where they align, where they differ, what the tradeoffs are, and where one could inform the other.

**Why it matters:** This entire meeting was Chris doing that comparison manually — walking through DCM's architecture alongside Apex's and finding the overlaps and gaps in real time. Key differences surfaced:
- **Centralized vs federated control plane** — DCM has a central control plane with external service providers; Apex is fully federated with loosely-coupled broker instances communicating via pub/sub.
- **Dependency graph ownership** — DCM maintains the dependency graph centrally; Apex tracks dependencies within each broker's manifests, and reverse-lookup ("what depends on this resource?") is an expensive global scan they haven't solved yet.
- **Orchestration model** — DCM uses policy-driven orchestration through the control plane; Apex currently uses Ansible playbooks and is considering a workflow coordinator.
- **Schema ownership** — DCM envisions a central base definition that providers extend; Apex has a universal AEP-compliant schema with resource-type-specific extensions owned by each domain team.

**Suggested approach:** A new analysis mode where DAV ingests two spec repos and runs a structured comparison. Output would be: shared concepts (mapped terminology), architectural differences (with tradeoff analysis), gaps in each relative to the other, and potential integration points. This would replace the manual back-and-forth that consumed 90 minutes of this meeting.

---

### 2. Schema / Data Model Ingestion and Analysis

**What:** DAV should be able to ingest and analyze structured data model definitions (JSON schemas, resource type specs, API payload schemas) — not just prose architecture documents.

**Who asked:** Kevin Cattell, repeatedly. He asked to "see the data model" at least 4-5 times throughout the meeting and said he couldn't connect the dots without seeing concrete schema examples.

**Why it matters:** The DCM v2 data model isn't fully codified yet. Kevin's repeated requests highlight that prose descriptions of architecture aren't sufficient — stakeholders need to see and compare actual schema definitions. DAV currently analyzes markdown/text specs; extending to structured schema formats (JSON Schema, OpenAPI specs, protobuf definitions, etc.) would make the analysis far more concrete.

**Suggested approach:** Add the ability to ingest JSON Schema files, OpenAPI specs, or similar structured definitions as spec sources. During analysis, DAV could then reference specific schema fields, compare schemas across repos, and identify gaps at the field level rather than just the conceptual level.

---

### 3. Multi-Customer Use Case Aggregation (reinforces earlier meeting's #2)

**What:** Chris is collecting use cases from multiple banks and wants to show each one what the industry collectively needs, without breaking confidentiality.

**Context from the meeting:** Chris explicitly listed use cases driven by different banks:
- **Barclays** — Apex/DCM interoperability, federated orchestration, schema evolution
- **Bank of America** — Full data center rehydration ("walk in with a thumb drive and a laptop")
- **JPMC** — Policy-driven placement
- **PNC** — Auto-identify appropriate architecture/platform for a given workload and deploy it

Chris said: *"What they learn, you guys can benefit from, and vice versa... this is the community we want to build."*

**How this applies to DAV:** This is the same cross-UC capability demand density feature from the 2026-06-02 meeting, but now with an explicit multi-organization dimension. DAV needs:
- Organization/stakeholder tags on use cases (which bank or team submitted them)
- Anonymization or abstraction when presenting cross-org aggregations
- The ability to show "3 of 4 organizations need dependency graph traversal" without revealing which specific organizations

---

### 4. Atom/Molecule Capability Decomposition

**What:** Decompose use cases into atomic capabilities ("atoms") and show how they compose into complex services ("molecules").

**Who asked:** Adam (Red Hat) and Christie (Barclays). Adam: *"This concept of building atoms, and then taking atoms and building molecules... what are the atoms in your system? Because with that, we can start to construct all kinds of more complex interactions."*

**Why it matters:** This is complementary to the foundational dependency detection from the 2026-06-02 meeting but with a specific framing: the team wants to identify the smallest reusable capability units (atoms) and show how multiple use cases compose them differently (molecules). This helps answer "what are the minimal building blocks we need to ship first?"

**Suggested approach:** During UC analysis, extract capabilities at two levels:
- **Atoms** — indivisible capabilities (e.g., "allocate IP address", "validate entitlement", "create DNS record")
- **Molecules** — compositions of atoms that form a deliverable service (e.g., "provision VM" = allocate IP + create DNS + attach storage + deploy OS)

Then show which atoms are shared across the most molecules, and which molecules are demanded by the most UCs.

---

### 5. Policy Completeness Analysis

**What:** Given a set of use cases and a set of OPA/Rego policies, analyze whether the policies cover all the requirements the use cases demand.

**Why it matters:** Policies are the "brains" of DCM — they do enrichment, validation, placement, and gatekeeping. The Summit demo showed region-based placement policies, provider-online checks, and sovereignty constraints. As the policy library grows, the team needs to know: are there use cases that demand policy coverage we don't have yet?

**Suggested approach:** Ingest the policy directory as a spec source alongside the architecture docs. During analysis, DAV would flag use cases that require policy-driven behavior (placement, validation, enrichment, authorization) and cross-reference against what the existing policy set covers.

---

### 6. Provider Contract Validation

**What:** Analyze whether a service provider's declared capabilities and contractual obligations cover what the use cases demand of them.

**Why it matters:** DCM has an explicit contract model with providers: they must declare their catalog items, report state changes, respond to reservation requests, and participate in the dependency graph. The meeting surfaced that these contracts are not yet fully specified. DAV could validate proposed provider contracts against the use case corpus.

**Kevin's question:** *"How is the communication between them happening? Is it the responsibility of the provider to call back if there is a change?"* — This is exactly the kind of contract gap DAV could surface.

---

### 7. New Use Case Categories to Add

The meeting surfaced several use case categories that should be represented in DAV's corpus:

- **Data sovereignty / region-constrained placement** — Policies that enforce geographic constraints on resource placement (the Summit demo's core scenario)
- **Full data center rehydration / DR** — Replay all intents to rebuild an entire data center from scratch (Bank of America's ask)
- **Workload portability** — Move a workload from one provider to another using the same intent data, leveraging the dependency graph to move everything together
- **Schema versioning and migration** — Kevin's DNS TTL example: what happens when a base schema changes and there are a million existing records on the old version?
- **Cross-provider dependency graph traversal** — "Give me all the dependents of this resource" across federated providers (Apex's current gap)
- **Cost-based auto-placement** — Use cost analysis policies to automatically select the cheapest provider that meets requirements
- **Policy-driven architectural enrichment** — User requests a production web server; policies auto-enrich to add HA, LTM/GTM, multi-region redundancy

---

## Operational Follow-Ups

### 8. DCM GitHub Repos to Onboard

The `dcm-project` GitHub organization was shown on screen. Contains the engineering code from the Summit demo (service providers, control plane, demo scenarios). Chris noted this is v1 / Summit-demo quality and v2 will differ, but it's the current state of the art. Should be onboarded as a spec source.

### 9. Barclays Follow-Up Action Items

From the meeting, Barclays (Folly and team) committed to:
- Track open items: finops integration, authorization model, observability
- Get on the AAP Automation Orchestrator early adopter program (pinging Rich)
- Continue the community cadence — explicitly called out that they don't want another year-long gap between meetings

### 10. AAP Automation Orchestrator (Context)

Red Hat is building an "Automation Orchestrator" as part of or alongside AAP. It's a no-code/low-code workflow builder on top of Ansible with EDA triggers, AI-driven analysis (human-in-the-loop), and deterministic execution. Targeting ~September release. Not directly DAV-relevant, but worth tracking as a potential DCM orchestration substrate. Barclays asked to be early adopters.

### 11. Intel Lab for Co-Engineering

Ryan Goodson described an Intel-funded lab with physical hardware (bare metal servers, Pure Storage array, OCP clusters) for neutral co-engineering. Mike Savage (field CTO chief architect) built the bootstrap process to stand up everything from bare metal. This lab is the target environment for validating DCM + Apex integration work.

---

## Key Architectural Insights for DAV's DCM Analysis

These are important context points that should inform how DAV analyzes DCM specs:

1. **DCM's four data stores:** Intent (raw consumer request) → Request (enriched/validated) → Realized (provider receipt) → Discovered (current state probing). DAV should understand this lifecycle when analyzing gaps.

2. **DCM doesn't have domain knowledge.** It manages data and relationships. Providers own domain expertise. This means DAV's analysis should focus on data-model-level gaps, not domain-specific implementation gaps.

3. **The data model IS the product.** Adam was explicit: DCM and the data model are potentially two separate CNCF communities. The data model defines how to represent and relate resources; DCM operationalizes it. DAV should analyze them as separate concerns.

4. **Apex's AEP standard.** Barclays adopted AEP (API Enhancement Proposals) as their API standard. Resource types map to URL endpoints, payloads follow a standard schema with resource-type-specific configuration blocks, and everything gets a BRN (Barclays Resource Name). If DAV ever ingests Apex specs, AEP compliance is the schema contract.

5. **The dependency graph gap is shared.** Both DCM and Apex struggle with "give me all dependents of resource X." DCM has it centralized but not fully implemented; Apex has it federated and does expensive global scans. This is a cross-org foundational capability gap — exactly what DAV's demand density analysis should surface.

---

## Implementation Priority (relative to DAV)

1. **Cross-architecture comparison mode (#1)** — Highest unique value; no other tool does this. Would have saved 90 minutes of manual comparison in this meeting alone.
2. **Schema/data model ingestion (#2)** — Unblocks concrete analysis; Kevin's #1 ask. Without it, DAV is limited to prose-level analysis which isn't sufficient for this persona.
3. **Atom/molecule decomposition (#4)** — Directly actionable for the community's next step of identifying core building blocks.
4. **Multi-customer aggregation (#3)** — Already identified in the 2026-06-02 meeting; this meeting reinforces urgency with explicit multi-bank context.
5. **Policy completeness (#5)** and **provider contract validation (#6)** — Follow-on once the core analysis improves.
6. **New UC categories (#7)** — Can be added incrementally as the community submits them.
