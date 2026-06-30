# DAV Feature Requests — DCM / Cost Management Meeting (2026-06-02)

Chris demoed DAV to the DCM team (Piotr Kliczewski, Kevin Cattell, Pau Garcia Quiles, David Cannon). The team wants to use DAV for prioritization and cross-team coordination. Below are the concrete feature requests and operational follow-ups extracted from the meeting.

## Feature Requests (prioritized by team demand)

### 1. UC Priority / Weighting Meta-Tags

**What:** Add a priority or importance field to use cases — a weighting factor so UCs can be ranked and ordered for roadmap planning.

**Who asked:** Kevin Cattell.

**Why it matters:** The team agreed to focus on delivering use cases in priority order rather than all at once. Without weighting metadata, there's no way to express "this UC matters more than that one" in the system. Piotr reinforced this: estimation and prioritization must happen before committing to delivery timelines.

**Suggested approach:** Add an optional `priority` or `importance` field (e.g., integer 1-5 or enum critical/high/medium/low) to the UC data model. Surface it in the UC list/detail views. Allow sorting/filtering by priority. Consider making it settable during bulk import and via the wizard.

---

### 2. Cross-UC Capability Demand Density

**What:** After analyzing multiple UCs, aggregate the capabilities each UC demands and show which capabilities appear across the most UCs — the "density of need."

**Who asked:** Kevin Cattell.

**Quote:** "Here's the capability list that it demands to support your use case. And here's all the other requests I've gotten that have also similar requests."

**Why it matters:** This answers "what should we build first?" by showing where demand clusters. It's a layer above per-UC analysis — it's cross-UC synthesis. The Review & Plan tab already does consolidated review of multiple runs; this would add structured capability extraction and aggregation on top of that.

**Suggested approach:** During analysis (or as a post-analysis step), extract a structured list of capabilities/requirements each UC demands. Store these as structured data (not just prose). Then provide a view that aggregates across UCs in a Set: "capability X is demanded by 8/15 UCs, capability Y by 12/15." This could be a new section in Review & Plan, or a standalone "Capability Map" view.

---

### 3. Foundational Dependency Detection

**What:** Surface capabilities that aren't heavily demanded on their own but are blocking dependencies for many other capabilities. These "boring but foundational" building blocks should float to the top of the priority list.

**Who asked:** Kevin Cattell.

**Quote:** "Sometimes you find a capability that's not critically important, a fundamental dependency for a bunch of others... those things pop to the top because they're foundational building blocks."

**Why it matters:** Demand density (#2) alone can miss infrastructure-level capabilities that everything else depends on. A capability like "unified identity model" might only appear explicitly in 2 UCs but is implicitly required by 10 others.

**Suggested approach:** This is a graph analysis on top of #2. When extracting capabilities, also extract dependency relationships (capability A requires capability B). Then compute which capabilities have the highest transitive dependent count. This is harder than #2 and may be a follow-on.

---

### 4. UC Quality Feedback Loop

**What:** Score use case definitions for clarity and completeness and feed that back to the author. Help standardize how UCs are written across teams.

**Who asked:** Kevin Cattell.

**Quote:** "The clarity and completeness of their definition may also be in question... if we could capture that as a capability set and say this is the way we should describe our use cases collectively, I think that would be much better."

**Why it matters:** The system's analysis quality is bounded by the quality of the UC definition. Bad UCs produce bad analyses. The shallow-analysis detector already flags UCs where the engine couldn't produce deep analysis — this would be an author-facing version that helps people write better UCs before analysis runs.

**Suggested approach:** Add a "UC readiness" score or checklist (e.g., has clear scope, is a single unit of work, has testable acceptance criteria, specifies target domain). Could run as a lightweight LLM pass during UC creation/edit, or as a batch check on a Set before triggering a run.

---

### 5. Maturity Assessment Mode

**What:** Analyze an external system's architecture against a spec and produce a maturity score — "how mature is system X against standard Y?"

**Who asked:** Kevin Cattell.

**Quote:** "We could analyze somebody else's system and then qualify them. We could actually say how mature they are against this standard."

**Why it matters:** Flips the perspective from "does the spec support this UC?" to "how well does implementation X conform to spec Y?" Useful for customer assessments.

**Suggested approach:** This is a variant of the existing analysis flow where the "UC" is replaced by an external system description and the analysis measures conformance rather than gap. Lower priority — the current UC-driven flow covers the team's immediate needs.

---

### 6. Customer-Facing Mode (longer-term)

**What:** Make DAV available to customers so they can ask "can your product do X?" and get an analysis against released product specs (OpenShift, RHOAI, etc.).

**Who asked:** Pau Garcia Quiles.

**Priority:** Long-term vision. Not actionable now but validates the direction.

---

## Operational Follow-Ups

### 7. Multi-User Authentication

Pau asked for access to DAV. Chris agreed to add users. DAV currently runs behind OCP oauth-proxy on Chris's home cluster with no multi-user auth. Need to figure out external access — options include opening the route publicly with proper OIDC/auth, VPN access, or similar.

### 8. New Spec Repos to Onboard (Cost Management)

Pau's cost management stack should be added as spec sources alongside DCM and UDLM. Repos shared in the meeting:

- **koku** — server-side cost management. Note: has two code paths (Trino for SaaS, Postgres for self-managed). The schemas and cost model definitions live here.
- **cost-mgmt-operator** — operator that gathers metrics from clusters. Currently uses Prometheus queries; changing to support more data sources for MAAS.
- **integrations/sources** — cloud bill ingestion. Separate repo, donated to the platform team.

Additional context from Pau:
- FOCUS standard (industry-standard billing format) support planned for Q3/Q4 2026. FOCUS 1.1/1.2 added custom columns which undermines the standard somewhat.
- Kepler (sustainability/energy) is in backlog but blocked on Kepler reaching GA — Pau considers it unreliable, especially on bare metal. Low customer demand post-2024 US election.
- MAAS integration is actively in progress — will change how the operator gathers data.

### 9. Networking/Storage Domain Experts

Kevin spoke with Joe and Brandon, who are willing to help write use cases for networking and storage domains as foundational building blocks. Chris should follow up with Kevin to connect with them and get those UCs into DAV.

### 10. Piotr's Concern About Michael's OSAC Use Cases

Piotr flagged that Michael's OSAC use cases felt biased toward OpenMeter/OSAC rather than being neutral. Piotr questioned some of them and got no reply. Worth being aware of this when interpreting OSAC analysis results — the UCs themselves may need review for neutrality.

---

## Implementation Priority (Chris's read of the meeting)

1. **UC priority tags (#1)** — smallest scope, highest immediate value for roadmap planning
2. **Cross-UC capability demand density (#2)** — the synthesis the team actually needs for prioritization
3. **Onboard cost-mgmt repos (#8)** — enables Pau's team to start using DAV
4. **Multi-user auth (#7)** — unblocks Pau accessing the tool
5. **UC quality feedback (#4)** — builds on existing shallow-analysis detector
6. **Foundational dependency detection (#3)** — graph analysis, harder, follow-on to #2
7. **Maturity assessment (#5)** and **customer-facing (#6)** — longer-term
