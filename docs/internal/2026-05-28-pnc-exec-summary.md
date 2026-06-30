# PNC Engagement — Executive Summary
## Red Hat FlightPath × PNC CTO Architecture Organization
### May 28, 2026 — Pittsburgh, PA (In-Person)

---

## Meeting Purpose

Red Hat FlightPath team met with PNC's CTO infrastructure architecture organization to align on intent-as-code strategy and explore co-engineering on the DCM (Data Center Management) open source project.

---

## Key Topics Discussed

### 1. Intent-as-Code / Infrastructure-as-Code Strategy

PNC's CTO organization is building a code-first approach to solution architecture and infrastructure lifecycle management. Their current tooling includes ARIS (solution design), Nautobot (network source of truth), and LikeC4 (code-first architecture DSL). PNC described themselves as "very, very early" in this journey.

Red Hat presented DCM — an open source control plane that manages the lifecycle of infrastructure data through intent capture, policy-driven enrichment, provider abstraction, and realized-state tracking.

**Outcome:** Both organizations recognized strong alignment between PNC's goals and DCM's architecture. PNC expressed interest in co-engineering rather than building from scratch.

### 2. DCM Architecture Walkthrough

Presented DCM's core model:
- **Layered data definitions** — base resource types with customization layers (organizational, location-specific, user-specific) collapsed into a single enriched request
- **Policy engine** — OPA/Rego policies for validation, enrichment, placement, and security enforcement; removes humans from the approval loop while preserving governance
- **Service provider abstraction** — providers register capabilities and catalog items; DCM handles placement and routing without domain-specific knowledge
- **Four data stores** — Intent (raw request), Request (enriched), Realized (provider receipt), Discovered (current state) — enabling drift detection and lifecycle tracking
- **Dependency graph** — built into the data model for workload portability and rehydration

### 3. Live Demo — Application-as-a-Service

Demonstrated the Red Hat Summit demo: a three-tier application (web, app, database) deployed via DCM with:
- Policy-driven sovereign placement (region constraints via OPA)
- Automated failover when a data center goes offline
- Workload rehydration to a new provider while maintaining all constraints
- Cost-based placement when no specific constraints are provided

### 4. Alignment with PNC's Existing Work

Strong parallels identified between PNC's current architecture and DCM:

| PNC Concept | DCM Equivalent |
|---|---|
| Engineering patterns (RHEL, Apache, etc.) | Base resource type definitions |
| Solution design patterns (multi-tier apps) | Composite service definitions |
| ARIS compliance rules | Policy engine (OPA/Rego) |
| LikeC4 code-first architecture | Intent-as-code data model |
| Nautobot network source of truth | Discovered state store |

### 5. Use Case Validation with AI Tooling

Red Hat demonstrated an AI-powered tool (DAV) that evaluates customer use cases against architectural specifications to identify gaps and validate coverage. PNC agreed to provide their priority use cases for analysis.

### 6. Cross-Industry Collaboration

PNC was informed that multiple major financial institutions (Barclays, Bank of America, Wells Fargo, JPMC) are pursuing similar intent-as-code strategies and engaging with DCM. Red Hat proposed re-establishing a cross-industry collaboration forum for shared learning.

---

## Key Outcomes

1. **Co-engineering invitation accepted** — PNC expressed interest in contributing to DCM development rather than building an independent solution
2. **Use cases to be provided** — PNC will provide priority use cases (solution architecture, provisioning, configuration management, DR/placement, cost analysis) for gap analysis
3. **Regular cadence established** — Follow-up meetings scheduled for continued architecture alignment
4. **DCM demo deployment** — PNC plans to deploy the DCM Summit demo internally for hands-on evaluation
5. **Data model collaboration** — PNC's engineering patterns team identified as natural contributors to the Universal Data Lifecycle Model (UDLM)

---

## Benefits to PNC

- **Accelerated path to production** — Leverage an existing open source control plane instead of building from scratch
- **Policy-driven governance** — Codify security, compliance, and architectural standards; remove manual approval bottlenecks
- **Workload portability** — Decouple applications from specific infrastructure providers via the abstraction layer
- **Drift detection** — Continuous state reconciliation between intended and actual infrastructure configuration
- **Industry alignment** — Access to lessons learned from peer financial institutions pursuing the same goals
- **Cost optimization** — Policy-driven placement enables automated cost-based provider selection

---

## Next Steps

| Action | Owner | Timeline |
|---|---|---|
| Provide priority use cases (deck format) | PNC | ~1 week |
| Run gap analysis on PNC use cases | Red Hat (FlightPath) | Upon receipt |
| Deploy DCM Summit demo for internal evaluation | PNC | TBD |
| Schedule recurring architecture alignment sessions | Both | Weekly/biweekly |
| Share UDLM data model specs for PNC review | Red Hat | Ongoing |
