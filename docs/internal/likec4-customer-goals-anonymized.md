# LikeC4 & Code-First Architecture — Customer Goals (Anonymized)

_Extracted from a customer engagement transcript. Clarifies what the likeC4 pattern pipeline (WS-C) must support and what the customer's platform strategy looks like. For use by the session building the 6-week demo roadmap._

---

## What the Customer Is Building Toward

A major FSI institution's CTO solution engineering organization is pursuing a **code-first approach to solution architecture**. They are in very early stages.

### Current State

- **An enterprise design tool** (drawing-first) is the existing mechanism for solution design. Architects create visual designs, and the tool checks compliance rules against the design.
- A **network source of truth** (fork of Netbox) handles network infrastructure — UI-first, not code-first. Already has intent-as-code concepts for networking.
- **Engineering patterns** are documented specifications for each technology (Linux, web servers, app servers, etc.) — base definitions with user-customizable fields.
- **Solution design patterns** are higher-level compositions — a 3-tier web app combines multiple engineering patterns.
- A team maintains the engineering patterns. Another team builds automation based on them. A third team owns the design tool. A fourth team does infrastructure designs for application teams. These are all under the CTO's solution engineering function.

### Target State

- **LikeC4 as the code-first representation** of solution architectures, replacing the drawing-first tool over time.
- **Code drives deployment** — "exactly what you have coded up in that domain specific language becomes what you deployed. It is the driver of what you deployed."
- **No gap between architecture and reality** — "there's no question about here's the picture that I drew versus what somebody actually deployed. It is what you deployed."
- **Version-controlled** — solution architectures in git, versioned. V1→V2 diff drives the deployment delta — the automation compares current vs previous and acts on the difference.
- **Applied to everything** — not just application architectures but also container platforms, storage, and all managed services. Same code-first source-of-truth approach across the board.

---

## The Two-Level Configuration Pattern

The customer has a clear pattern they need preserved in any code-first model:

1. **Base configuration** — owned by the technology team (e.g., the Linux team defines the base Linux configuration). This is the engineering pattern. Consumers cannot change this.
2. **User-level configuration** — customizable fields that the consuming team fills in. The technology team defines WHAT can be customized; the consuming team fills in the values.

**Worked example from the whiteboard:**
- A solution architecture has web servers, app servers, and a database
- For each web server: base Linux config (from the Linux team) + user-customizable fields
- For each app server: base app server config + user-customizable fields (including database connection pooling rules that compliance wants enforced)
- The deployment pipeline reads the combined config and provisions

**This maps directly to UDLM's layered data model** — the customer immediately recognized the pattern when shown the Navy's layering (base → location → enclave → request) as equivalent to their model (base engineering pattern → user customization → policy enrichment → provider realization).

---

## What the Customer Wants from LikeC4

1. **Define solution architectures as code** — a likeC4 model describes all deployable components (web tier, app tier, database tier, load balancer, network, etc.)
2. **Generate visual artifacts** — "you can generate pictures of it. It's kind of an ancillary artifact." Code is primary; diagrams are derived.
3. **Drive automation** — the likeC4 definition drives the CI/CD pipeline that provisions infrastructure
4. **Compare versions** — V1→V2 diff determines what changed and what the automation needs to do
5. **Check compliance** — replicate what the existing design tool does today: validate designs against organizational rules before deployment. This is their biggest open problem in the code-first transition.

---

## Ongoing Configuration Management

The customer raised a concern beyond point-in-time deployment:

- The likeC4/DCM model describes a **point-in-time deployment**
- But there's **ongoing configuration management** — the environment must be continuously reconciled against its intended state
- They referenced a **pull-based daemon model** where agents on deployed instances periodically check "what should I look like?" as an approach they like
- **Answer:** DCM's four-state model handles this — the Discovered state is continuously refreshed by providers, compared against the Realized state, and drift is detected. Providers manage the resources; DCM manages the data lifecycle.

---

## What This Means for WS-C (the LikeC4 Mapper)

### The mapper must support:

1. **Multi-component solution architectures** — not a single resource but a composite (web + app + db + LB + network + storage). This is the composite service model.

2. **Two-level configuration separation** — base engineering pattern (from the technology team) + user-customizable fields (for the consuming team). The mapper must preserve this separation so that:
   - Policy can enforce the base configuration (no one can override what the Linux team defined)
   - Users can customize only their designated fields
   - The combined result collapses into a single UDLM entity with provenance tracking which layer each value came from

3. **Compliance checking** — the equivalent of the existing design tool's rule checking must happen in the DCM pipeline. In DCM terms: GateKeeper and Validation policies fire during the request lifecycle to enforce organizational standards. The customer needs to SEE this in the demo — it's what their existing tool does and they need confidence the code-first approach preserves it.

4. **Version comparison** — V1→V2 diff for incremental deployments. The mapper must produce UDLM composite services that can be versioned and compared so the automation only acts on the delta.

5. **Ongoing reconciliation** — the Discovered state must continuously reflect what's actually running, compared against the Realized state, with drift surfaced. This is not the mapper's job directly but the demo must show this working end-to-end.

### It's not just a mapper — it's the consumption front door

The customer views this as the replacement for how their entire organization defines and deploys infrastructure. Their existing design tool has hundreds of engineering patterns, compliance rules, and solution templates. The likeC4 → DCM pipeline isn't a nice-to-have; it's the mechanism by which they intend to adopt the platform. Getting this right determines whether they co-engineer with us or build their own.

---

## Customer Engagement Level

The customer offered all three levels:
- **Architecture input** — help define what the model should look like
- **Use cases** — a deck of their specific requirements (being prepared)
- **Co-engineering** — developers want to deploy and try it themselves

They want a regular cadence (weekly or biweekly) and expressed interest in the cross-institution community gathering. They asked for the GitHub repos and want to start evaluating the code.

Their position: "It sounds like we could just contribute to it instead of having to build from the ground up."
