# Running the Data Center on a Common Model
### How DCM and UDLM operationalize the management of a data center — every resource, every stakeholder, end to end

**A whitepaper on the operational model of Data Center Management (DCM) over the Universal Data Lifecycle Model (UDLM).**

_Working draft (2026-06-18). Companion to "Infrastructure Lifecycle Without a Common Language" (whitepaper #1,
which argues *why* a common data model is needed). This paper assumes that thesis and answers the operational
question: **how do you actually run a data center on it** — every resource type, every stakeholder, through the
full lifecycle, governed and audited? Conventions inherited from #1: facts are snapshot-dated and reproducible;
DCM is the **reference implementation, not the product**; the model is realization-neutral; customer specifics are
internal-only and to be genericized for any public version. Grounded in the dcm-project runtime
(`control-plane`, the provider repos, the enhancement proposals, and the UDLM substrate spec) and in DAV's live
use-case corpus. For internal review._

---

## 1. The Problem — operating a data center, today

A data center is not a pile of servers; it is a continuously-managed estate of **resources** — virtual machines,
clusters, containers, networks, IP space, storage, identities, certificates, cost centers, and the policies and
people that govern each one. Operating it means running every one of those resources through its whole life:
requested, approved, built, observed, drifted, remediated, re-homed, retired — while a dozen internal groups
(security, standards, networking, storage, compute, compliance, operations, finance) each need a say.

Today that operation is **fragmented by resource and by tool**. Each platform (the hypervisor, the Kubernetes
estate, the IPAM, the CMDB, the cost tool, the ticketing system) models its own resources its own way, exposes
its own API, and enforces its own slice of policy. The operators in the middle stitch them together with bespoke
integrations, runbooks, spreadsheets, and tribal knowledge. There is no single place that knows, for any
resource, *what was intended, what was approved, what was built, and what actually exists right now* — so drift
goes unnoticed, provenance is lost, audit is manual, and every governance decision is re-litigated per tool.

The cost of operating this way compounds: every change touches several systems by hand; every audit is an
archaeology project; every new requirement (sovereignty, confidential computing, FinOps) is bolted onto each
tool independently. The data center is *managed*, but not *operationalized* — there is no common operating model
beneath the tools.

> The thesis of whitepaper #1: the missing piece is a **common data model** for the resource lifecycle. This
> paper takes that as given and shows what becomes possible when the whole data center is operated *through* it.

---

## 2. The Operating Model — intent in, outcome out

DCM operates the data center on a single, simple contract with its consumers: **declare the outcome you want;
the platform makes it real and keeps it real.** This is an **intent-based service model** — the declared outcome
*is* the intent; *how* it is achieved (which provider, which automation, which platform) is the platform's
concern, not the consumer's.

That operating model rests on three things UDLM makes machine-native:

**2.1 The four-state lifecycle is the operating spine.** Every resource is tracked through four states, each a
persisted record, so the operator always has the complete picture:

- **Intent** — what the consumer asked the outcome to be.
- **Requested** — the policy-assembled, provider-ready request DCM dispatched.
- **Realized** — what the provider actually built (the system of record).
- **Discovered** — what is observed to exist right now (ground truth).

The *gaps between these states are the operational signals*: Intent↔Requested shows what governance changed;
Requested↔Realized shows what the provider did; Realized↔Discovered is **drift**; Intent↔Discovered answers
"is the original need still met?" Operating the data center *is*, in large part, continuously closing these gaps.

**2.2 Every stakeholder plugs in through a contract — the core stays domain-ignorant.** The control plane holds
the data and enforces policy; it does not understand storage, or networking, or compliance. Domain expertise
lives at the edges: **providers** realize resources through the provider contract; **governance stakeholders**
(security, standards, compliance, finance) inject enrichment, validation, and gatekeeping through the policy
contract. Any group adds capability or governance *without modifying the core* — which is what lets one platform
operate every resource type and admit every stakeholder.

**2.3 Outcomes are realized by providers, methods hidden.** A request for a resource (or a higher-order outcome
that *derives* a set of resources) is realized by whatever provider DCM places it on — a hypervisor, a cluster
operator, an automation engine (Ansible today, AAP later), a cost service. The consumer never picks the method;
swapping the method is an internal change with no impact on the contract. This is what makes the data center
**operable as a service catalog** rather than a pile of tools.

---

## 3. The Runtime — how a request becomes a running resource

DCM's reference runtime is a single **control-plane** process (`dcm-project/control-plane`): one Go service, one
Postgres database, an API at `/api/v1alpha1`. Four operating domains live inside it — **catalog** (the
consumer-facing intake), **placement** (orchestration and provider selection), **policy** (governance checks), and
**sp** (the service-provider abstraction) — and they call each other **in-process** on the provisioning path. The
only things outside the process are the **providers** themselves and a **NATS** bus that carries asynchronous
provider status back as CloudEvents.

> *Architectural note (current as of 2026-06):* these four domains were originally separate microservices
> (`api-gateway`, `catalog-manager`, `placement-manager`, `policy-manager`, `service-provider-manager`). They were
> **consolidated into the control-plane monolith** in May 2026, and those repos are archived. Any older diagram
> showing a gateway fronting four managers over HTTP is now historical — the synchronous path is in-process.

**The end-to-end motion** (the declarative provisioning flow, `enhancements/declarative-api`):

1. **Intent.** A consumer submits a `CatalogItemInstance` — a catalog item plus parameters — via
   `POST /api/v1alpha1/catalog-item-instances`. It names *no provider*. This is the Intent state: immutable,
   GitOps-stored, "what was asked."
2. **Assemble.** **catalog** resolves the blueprint and parameters into an *effective resource graph*
   (`spec.resources[]`) — the constituent resources the outcome implies.
3. **Wire & order.** **placement** parses **CEL** expressions (`${…}`) that wire resources to one another, builds a
   **dependency DAG**, and computes topological levels so independent resources build in parallel and dependents
   wait. The run and a snapshot of the graph are persisted.
4. **Govern.** **policy** evaluates **every resource** against policy-as-code (embedded **OPA/Rego**) — *all* must
   pass before *any* resource is created. The approved, provider-ready form is the **Requested** state: immutable,
   "what was approved and dispatched."
5. **Realize.** placement returns `202 Accepted` with a `run_id`, then walks the DAG, dispatching each resource
   through **sp** to the external **provider**, which builds it. As providers report back over NATS, the
   control-plane writes the **Realized** state — versioned snapshots, an `is_current` marker, "what the provider
   actually built."
6. **Observe.** Discovery continuously refreshes the **Discovered** state — "what exists right now." The delta
   against Realized is **drift**, the central operational primitive.

The four states are **parallel records, not sequential steps** — each is queryable at any time, and immutability is
enforced in the database itself (`REVOKE UPDATE/DELETE` + row-level security on the Intent/Requested rows). That is
what lets the operator always answer *intended vs approved vs built vs actual* for any resource, from the data
alone — the capability the fragmented status quo (§1) cannot offer.

---

## 4. Every Resource, Every Stakeholder — the taxonomy and the seams

For one platform to operate *every* resource and admit *every* stakeholder, two things have to be open: the
vocabulary of what can be managed, and the contracts by which capability and governance plug in.

**4.1 A four-level resource taxonomy — portable intent, specific realization.** UDLM names resources at four levels
(`udlm entities/resource-type-hierarchy.md`):

> **Resource Type Category → Resource Type → Resource Type Specification (versioned) → Provider Catalog Item**
> e.g. `Compute` → `Compute.VirtualMachine` → `Compute.VirtualMachine v2.1.0` → `Nutanix.VM.Small`.

Consumers declare intent at the **Resource Type** level — portable, vendor-neutral. DCM's Request Payload Processor
narrows that to a concrete **Provider Catalog Item** by a specificity-narrowing algorithm (unresolvable →
`422 RESOURCE_TYPE_NOT_FOUND`). Portability is preserved by **`providerHints`**: only minimal common fields are
standardized; platform-specific knobs ride along as hints that a provider uses if it recognizes them and silently
ignores otherwise. The implemented service types today are **VM, Container, Database, Cluster** (with a `cost`
type proposed).

The default **Resource Type Categories** — the operating domains — are **Compute, Network, Storage, Platform,
Security, Observability** (resources) plus **Business** and **Identity** (information), with **Process** and
**Application** added by org authority. Each category has a **Resource Type Authority** that owns its
specifications — `Compute.*` → virtualization, `Network.*` → network operations, `Security.*` → the CISO office —
which is how the data center's existing org boundaries map onto the model instead of being flattened by it.

**4.2 Providers plug in by capability, not by type.** Rather than a fixed set of provider types, UDLM defines a
**unified provider contract** (base + capability extension, `udlm contracts/provider-contract.md`): the base every
provider implements — **Registration, Health, Sovereignty, Accreditation, Governance Matrix, Zero-Trust identity,
Lifecycle** — and a **capability** that varies. The capability vocabulary is closed and small:

| Capability | What the provider does |
|---|---|
| `realize_resources` | provision / update / decommission resources (a hypervisor, cluster, container platform) |
| `serve_data` | answer authoritative queries about external truth (IPAM, CMDB) |
| `authenticate` | turn identity into tokens/roles |
| `federate` | act as another UDLM peer (mTLS + dual audit + sovereignty pre-check) |
| `execute_workflows` | run ephemeral workflows that leave no persistent resource |

A provider may declare several (InfoBlox is both `realize_resources` and `serve_data`). Adding a new provider is
"base contract + capability extension, **no core changes**." Providers self-register
(`SUBMITTED → VALIDATING → PENDING_APPROVAL → ACTIVE`), carry a zero-trust certificate (rotated every 90 days),
declare their `naturalization_format` (how they translate the portable payload into their native API — e.g.
`infoblox_wapi_v2`), and **must** expose `GET /api/v1/capabilities` so DCM integrates them by capability-matching
rather than manual wiring. Health is continuous: a degraded provider gets reduced routing; an unavailable one gets
**no** new routing *and* triggers drift detection across everything it hosts.

**4.3 Governance stakeholders plug in through policy.** The same openness applies to governance. The **policy
contract** (`udlm contracts/policy-contract.md`) is **data-driven**: a policy declares what request fields,
operation contexts, evaluation contexts, or entity metadata it matches — there are no routing tables to maintain —
and emits one of eight typed outcomes (validate, mutate, select-provider, gate, etc.) across ten operation types
(provision, modification, drift remediation, decommission, rehydration, …). Policies are authored as **Rego**,
stored in the database, compiled on startup, and scoped Global / Tenant / User. So security attaches a gate,
standards attaches a transformation, finance attaches a budget check — each a policy, none of them a code change to
the core. This is the mechanism behind §2.2: domain expertise at the edges, a domain-ignorant core.

---

## 5. Operating Concerns, End to End

Operating a data center is more than provisioning. DCM turns each of the recurring operational concerns into a
first-class, governed, audited capability over the same data model:

- **Provisioning** — intent → realized, governed on every request (§3; the catalog→placement→policy→sp path).
- **Composition & dependencies** — a **Composite Service** declares its constituents plus a dependency DAG and
  delivery requirements (required / partial / optional, `provided_by`); standard providers realize the parts and
  DCM's own machinery orders them — there is *no* meta-provider (`udlm entities/composite-service-model.md`).
- **Governance & policy** — Rego policy-as-code applied per-resource before any create (§4.3); ten operation types,
  Global/Tenant/User scopes; every decision recorded.
- **Drift & reconciliation** — Realized vs Discovered, continuously (`udlm foundations/four-states.md`); an
  unavailable provider forces drift detection on all its hosted entities; policy decides revert / adopt / alert /
  escalate via the `drift_remediation` operation.
- **Sovereignty & residency** — first-class, immutable placement constraints. Providers carry a
  `sovereignty_declaration` (jurisdictions, residency zones, sub-processors); placement filters on it; `federate`
  requires a sovereignty pre-check before any cross-peer flow. Topology is explicit — a nine-layer default from
  Country down to Unit (`udlm topology/location-topology-layers.md`). Sovereignty also has a **day-0** dimension:
  standing the platform up at all in a disconnected or air-gapped site, with no path to the internet. The
  **dcm-bootstrap** appliance (`heatmiser/dcm-bootstrap`) answers it — a RHEL image-mode (bootc) image with DNS,
  DHCP, TFTP, an image registry, and a content mirror baked in as podman-quadlet services; built connected,
  transported to the field, and booted, with site-specific configuration injected at first boot from a config
  drive. It is the foundation onto which DCM and its providers run, so a sovereign estate can be brought up and
  operated entirely behind the air gap.
- **Cost / FinOps** — cost is *adopted*, not re-modeled: served by a Cost-Management provider backed by **Red Hat
  Lightspeed Cost Management / Project Koku**, conformed to **FOCUS** and OpenCost, bound to resources by identity
  (dcm enhancements PR #57 / #60) — see whitepaper #1's "adopt, don't absorb" principle.
- **Audit & provenance** — every state and every field carries who/what/when; a tamper-evident, SHA-256-chained
  audit record with per-field provenance makes the whole estate reconstructable from the data alone
  (`udlm observability/universal-audit.md`).
- **Decommission & DR** — `decommission` runs dependency checks → notification → revocation → provider teardown;
  **rehydration** recreates a resource (or a whole environment) from stored **Intent**, *re-evaluating current
  policy* on the way (so it absorbs policy, environment, and provider changes), with a Recovery Policy that walks
  the dependency DAG in reverse to compensate (`enhancements/rehydration-flow`, `udlm lifecycle/operational-models.md`).

---

## 6. In Practice — operating concerns drawn from real engagements

These are not hypotheticals. DAV (the Data Architecture Validator) holds an operational use-case corpus —
**74 use cases** evaluated against the DCM/UDLM architecture (point-in-time 2026-06-18, reproducible from the DAV
database; customer-derived, paraphrased here). What makes the corpus useful for *this* paper is its shape: it is
dominated by the everyday operating motions of a data center, not greenfield demos. By **lifecycle phase**, the
corpus is **new requests (58)**, **drift detection (9)**, **modification (5)**, **rehydration (5, faithful +
provider-portable)**, **brownfield ingestion (3)**, and **decommission (1)** — i.e. most of the demand is the
*ongoing operation* of resources, exactly the part fragmented tooling handles worst. By **governance context**, a
third carry hard constraints: **compliance-gated (25)**, **audit-heavy (10)**, **sovereignty-enforced (3)**.

Each scenario below is a real operating motion expressed as a loop over the four states — declare the **Intent**,
let DCM assemble the **Requested**, a provider produce the **Realized**, and discovery keep **Discovered** honest.

**Provisioning & self-service** (the 58 new-request majority)
- **Zero-touch branch provisioning** — an intake form expresses the intent; DCM assembles the request, routes it
  through ServiceNow approval as a policy gate, and a provider configures devices and notifies the cabling team —
  end to end, no hand-stitching. *(Intent → governed Request → Realized)*
- **Path-to-production enforcement** — compliant standard requests reach production with *no human bottleneck*;
  only requests that fail a governance check stop for review. Policy is the gate, not a meeting.
- **Conversational (MCP) network intake** — users ask for network services in natural language; an AI agent
  validates the inputs and emits correct automation, hiding the automation engine and network internals entirely.
  The intent-based contract in its purest form: outcome in, method invisible.
- **Auditable self-service** — self-service access bound to consistent, auditable entitlement models (FSI profile).

**Composition, dependencies & disaster recovery** (the rehydration/decommission phases)
- **Full data-center rehydration** — rebuild an entire estate from stored *intent* records to last-known-good
  state, replaying the dependency graph in the right order. The four-state model is the backup.
- **Workload portability** — relocate a workload and all its dependencies onto a new provider, then cleanly
  decommission the source — realize-elsewhere then retire-with-provenance, one governed motion.
- **Reverse-dependency safe decommission** — block a retirement while active transitive dependents exist,
  surfacing ownership and criticality from the dependency graph before anything is torn down.

**Governance, supply chain & compliance** (the 25 compliance-gated UCs)
- **SLSA build-pipeline-as-a-service** — a hardened pipeline emits signed artifacts and SBOMs as an *outcome*, so
  app teams never run their own build/security tooling.
- **SBOM trust-chain validation** — no application reaches production with unverified dependencies; trust-coverage
  is reported per build as a governed gate.
- **CVE library-version compliance** — within hours of a disclosure, identify every affected app by exact library
  version and environment and trigger prioritized rebuilds — a Discovered-state query answering "who is exposed?"

**Drift detection & event-driven remediation** (the 9 drift UCs)
- **Network configuration drift remediation** — detect drift within one scan cycle; auto-remediate known-safe
  deviations, escalate unknown changes through an incident workflow. Realized-vs-Discovered, continuously.
- **Event-driven remediation** — known incident types remediated automatically within minutes, with a complete
  event-to-action audit trail.

**Sovereignty & confidential computing** (the 3 sovereignty-enforced UCs + attestation set)
- **Attestation-gated production inference** — run inference inside hardware TEEs so data-in-use stays protected;
  placement is gated on attestation as a first-class, immutable constraint.
- **Attestation-gated key custody** — release keys only after validating TEE integrity and identity binding.
- **Secure multi-party model training** — train on encrypted data from multiple partners without exposing
  cleartext (confidential containers).
- **Cross-cloud data-residency enforcement** — isolate storage and compute across clouds so data stays inside its
  policy boundary, enforced at placement.

**Cost, metering & observability**
- **Cost-based auto-placement** — provision on the cheapest *qualifying* provider, with the cost recorded in the
  realized receipt (cost adopted via FOCUS, not re-modeled — §5).
- **OpenTelemetry enforcement** — every production service emits standardized telemetry; no uninstrumented code
  reaches production (observability as an outcome, an automation-derived provider).
- **Tenant metering privacy** — no tenant can see another tenant's consumption; isolation enforced by policy on
  the metering data itself.

The through-line: every one of these is the *same* operating loop over the *same* four states, differing only in
the provider that realizes it and the policy that governs it. That sameness — one operating model under twenty
very different motions — is the whole point.

---

## 7. Where This Stands — honest current state

DCM is a **reference implementation** of the UDLM operating model — realization-neutral by design, so the point is
the model, not this codebase. What exists today is real and load-bearing in the right places, and honestly
incomplete in others; both matter for an operational reader.

**What runs.** The control-plane monolith is working software: the catalog → placement → policy → sp path,
CEL-wired dependency DAGs, embedded OPA/Rego policy, and NATS-driven status are implemented (`dcm-project/control-plane`),
with real service providers behind it — **OpenShift clusters** via ACM/HyperShift, **VMs** via KubeVirt/CNV,
**containers** via Kubernetes — a CLI, and a Podman-quadlet deployment kit. A three-tier-app demo provisions web +
app + database and reports a single aggregated status. For day-0 standup — including the hardest case, a
disconnected or air-gapped site — the **dcm-bootstrap** bootc appliance (`heatmiser/dcm-bootstrap`, §5) brings up
the foundation services (DNS, DHCP, TFTP, registry, content mirror) and installs the OpenShift clusters the
providers run on, from an immutable image with site config injected at first boot.

**The flagship operational proof — sovereignty through an outage.** The sovereignty-rehydrate demonstration is the
clearest single narrative of the whole model operating at once: three data centers across two regions, with a
priority-1 global Rego policy declaring *"no approved region, no deployment."* When the workload's home region goes
down, DCM doesn't blindly fail over to the nearest capacity — it **replays the stored Intent through rehydration**,
re-evaluates the sovereignty policy, and lands the workload only in a region that remains compliant. Four-state
lifecycle, dependency model, policy-as-code, and sovereignty primitives, exercised together, on running software.

**What is still spec or proposal** (stated plainly): much of the UDLM substrate is authoritative-but-in-flight —
its content lives on in-progress feature branches rather than a tagged release; the universal-audit chain and parts
of the composite/dependency and discovery machinery are specified ahead of full implementation; and the
cost/FinOps provider (Koku-backed, FOCUS-conformant) is an open proposal, not merged. DCM is **not yet in
production at a customer site** — this paper describes an operating model and the capabilities demonstrated, not a
deployed-scale claim.

The value is exactly that: a demonstrated, coherent way to operate an entire data center — every resource, every
stakeholder, through the full lifecycle — on one governed, audited, machine-native model, with methods abstracted
behind intent. The §6 corpus shows the demand is real and broad; §2–§5 show the model answers it with *one* loop;
and the work ahead is finishing the implementation, not rethinking the design.

---

## 8. The Destination

### The End Goal

A single unified control plane and data model to manage the lifecycle and configuration of everything in a data center and enterprise — resources, processes, policies, services, cost, compliance. Not just the twenty resource types in §6. Everything that has a lifecycle. Managed through one data model, one policy engine, one set of contracts.

The operational paper you just read demonstrates that the model works for the data center: VMs, clusters, containers, networks, storage, cost, sovereignty, bootstrap, rehydration — all through one loop. The question is whether that loop stops at infrastructure, or whether it extends to everything the enterprise operates.

### Two Paths, Same Destination

**Integrate into all Red Hat products.** RHEL, OpenShift, Ansible, RHOAI, ACM, ACS, Quay, Cost Management — each speaks UDLM natively. Each product becomes a provider or a policy source. The §3 operating loop — intent → policy → provider → realized → discovered → drift → remediate — runs identically whether the resource is a VM, a container build, a compliance scan, or a cost allocation. Organizations get unified lifecycle management because their existing stack already speaks the common language.

**DCM as the control plane that binds them.** DCM sits above the products as the orchestration and governance layer. Products are providers. DCM routes, governs, tracks. The §5 provider onboarding contract that works for a VM provider works for a CI/CD provider, an observability provider, a cost management provider, an ITSM provider. Every integration is the same shape.

These paths are not mutually exclusive. They serve the same purpose: the operating model in §2–§5 becomes the operating model for *everything*, not just infrastructure.

### The Model Is Domain-Agnostic

The through-line of §6 — "every one of these is the same operating loop over the same four states, differing only in the provider that realizes it and the policy that governs it" — applies beyond the data center. The four-state lifecycle, provider contracts, policy governance, and dependency graphs describe anything that flows from intent through realization to discovered truth. The entity-type family mechanism exists specifically so new domains extend the model without changing the substrate:

- **Infrastructure lifecycle** — proven (this paper, the sovereign-rehydrate demo)
- **Software delivery lifecycle** — designed (the Delivery family extension: source → build → scan → sign → promote → deploy, all through the same loop)
- **Capability and maturity reasoning** — proven (the Knowledge family, DAV's 84-UC evaluation)
- **Operational intelligence** — designed (observability providers, event correlation, resilience posture scoring)

Each new domain exercised the same primitives and validated that they were sufficient. The data center is the proving ground. The enterprise is the destination.

---

_Companion: whitepaper #1 ("Infrastructure Lifecycle Without a Common Language") for the data-model thesis, and the
executive brief for the vision and partner CTA. UDLM spec: github.com/dcm-project/udlm · DCM: github.com/dcm-project/dcm · DAV: github.com/croadfeldt/dav._
