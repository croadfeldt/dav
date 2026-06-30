# Response to Ondra's Comment on "placement(CEL+DAG)"

_For pasting into the Google Doc comment thread._

---

## The Response

Good catch, Ondra — that phrasing was imprecise. Let me clarify the three distinct steps and how they differ.

What's described in the roadmap as "placement(CEL+DAG)" is actually **three sequential steps in a single pipeline**, not three descriptions of the same use case:

### Step 1: Decomposition (the DAG)
When a composite resource request comes in (e.g., a 3-tier web app), the control plane **decomposes** it into constituent atomic resource requests using the dependency graph (DAG). The DAG defines the ordering: database must be realized before the app tier (because the app tier needs the DB's IP), and the app tier before the web tier. The dependency graph also defines **field injection** — the realized value of one constituent (e.g., `realized_fields.primary_ip` from the DB) gets injected into the next constituent's request fields (e.g., `fields.db_host` on the app tier).

This is the composite service model — it's catalog-level definition, not placement.

**Spec reference:** [`dcm-project/dcm/architecture/convergence-engine/dependency-orchestration.md`](https://github.com/dcm-project/dcm/blob/main/architecture/convergence-engine/dependency-orchestration.md) — §1 "Request dependency graph submission and parsing." Implements UDLM's [`lifecycle/request-dependency-graph.md`](https://github.com/dcm-project/udlm/blob/main/lifecycle/request-dependency-graph.md).

### Step 2: Placement (OPA/Rego)
For **each** decomposed constituent, the placement engine determines **which provider** should realize it. Placement policies (OPA/Rego) evaluate constraints: sovereignty (region/jurisdiction), cost (cheapest qualifying provider), capability (does this provider support this resource type?), availability (is the provider online?), and priority bands (premium/standard/budget).

This is where "if you only asked for a VM and didn't specify a provider, cost-based placement picks the cheapest one" happens. It's per-constituent, not per-composite.

**Spec reference:** [`dcm-project/dcm/architecture/topology/placement-and-priority-bands.md`](https://github.com/dcm-project/dcm/blob/main/architecture/topology/placement-and-priority-bands.md) — implements UDLM's [`topology/location-topology-layers.md`](https://github.com/dcm-project/udlm/blob/main/topology/location-topology-layers.md).

**Policy evaluation reference:** [`dcm-project/dcm/architecture/convergence-engine/policy-evaluation.md`](https://github.com/dcm-project/dcm/blob/main/architecture/convergence-engine/policy-evaluation.md) — OPA as the evaluation engine (line 253), Gating Policy enforcement, Validation, Transformation, and Governance Matrix rules evaluated during the nine-step assembly.

### Step 3: Provider Resolution & Dispatch
After placement selects a provider for each constituent, the control plane **dispatches** the request to that provider. The provider realizes the resource and reports back the realized state. This is the provisioning step.

### Why they sound similar

Paragraphs 1, 3, and 4 in the roadmap all describe the **same end-to-end pipeline** (decompose → place → dispatch → realize) but from different angles:
- **Paragraph 1** describes it as the Act I narrative (what the audience sees)
- **Paragraph 3** describes it as the building-block inventory (what code exists)
- **Paragraph 4** describes it as workstream B (what needs to be wired)

They're the same pipeline. I should have been clearer that "placement(CEL+DAG)" means "the full pipeline: DAG-ordered decomposition + OPA placement + provider dispatch" — not a single step.

### The CEL part specifically

CEL (Common Expression Language) is referenced in ADR-016 ([`dcm-project/dcm/architecture/adr/016-application-definition-language.md`](https://github.com/dcm-project/dcm/blob/main/architecture/adr/016-application-definition-language.md)) as one of the options for how composite items express their decomposition rules. KRO uses ResourceGraphDefinitions with CEL expressions. The current control plane implementation uses CEL for expression evaluation within the dependency graph — but the ADR is still open on whether CEL is the long-term choice vs other options (API-only, YAML manifests, external DSL). The DAG structure itself is UDLM-defined; CEL is how expressions within the DAG nodes are evaluated.

---

## Summary for the comment

**Short version to paste:**

> Good question. "placement(CEL+DAG)" was imprecise — it's actually three sequential steps in one pipeline:
>
> 1. **Decomposition** — the DAG breaks the composite into constituents in dependency order (DB before app, app before web) with field injection between them. This is the composite service model, not placement. Spec: [`convergence-engine/dependency-orchestration.md`](https://github.com/dcm-project/dcm/blob/main/architecture/convergence-engine/dependency-orchestration.md)
>
> 2. **Placement** — for each constituent, OPA/Rego policies select which provider realizes it (sovereignty, cost, capability constraints). Spec: [`topology/placement-and-priority-bands.md`](https://github.com/dcm-project/dcm/blob/main/architecture/topology/placement-and-priority-bands.md)
>
> 3. **Provider dispatch** — the selected provider receives the request and realizes the resource.
>
> Paragraphs 1, 3, and 4 describe this same pipeline from different angles (narrative, building blocks, workstream). They're not different use cases — they're the same pipeline described for different audiences. I'll tighten the language to make the distinction clearer. CEL is used for expression evaluation within the DAG (see [ADR-016](https://github.com/dcm-project/dcm/blob/main/architecture/adr/016-application-definition-language.md)); OPA handles placement policy evaluation ([`convergence-engine/policy-evaluation.md`](https://github.com/dcm-project/dcm/blob/main/architecture/convergence-engine/policy-evaluation.md)).
