# UDLM: Unifying All Data and Dependency Graphs
### Executive Brief

---

## Slide 1: The Problem — Fragmented Data, Invisible Dependencies

Every enterprise manages thousands of resources across dozens of tools. Each tool has its own data model, its own dependency tracking, and its own view of the world. The result:

- **No single system knows what depends on what.** The VM platform tracks VM-to-storage dependencies. The network tool tracks switch-to-router dependencies. The application platform tracks service-to-database dependencies. Nobody tracks the full chain from application through container through VM through network through storage through data center.

- **Impact analysis is manual.** "If we decommission this storage array, what breaks?" requires a human to walk across 6 tools, cross-reference spreadsheets, and hope nothing was missed.

- **Drift is invisible.** A dependency that existed at deployment time may have changed — a DNS record moved, a load balancer re-pointed, a database failover switched the target. No tool tracks the full dependency chain from intent through current truth.

- **Rehydration is impossible.** You can't rebuild an environment from intent if you don't know what depends on what. Disaster recovery becomes "rebuild each piece and hope the connections work" rather than "replay the dependency graph in order."

---

## Slide 2: What UDLM Does Differently

UDLM doesn't replace your tools. It provides a **universal data layer** that every tool reads and writes, with a **dependency graph** that spans all of them.

### One Data Model for Everything

Every resource — a VM, a container, a network port, an IP address, a DNS record, a certificate, a storage volume, a deployment, an artifact, a policy — is a UDLM entity with:

- A **UUID** that follows it from creation to decommission
- A **four-state lifecycle**: intent (what was asked for) → request (what was approved) → realized (what was built) → discovered (what actually exists right now)
- **Field-level provenance**: who changed what, when, under what authority
- **Type classification**: the Resource Type Registry says what kind of thing it is

### One Dependency Graph Across Everything

When resource A depends on resource B, that relationship is tracked in the UDLM dependency graph — regardless of which tool manages A and which manages B. The graph supports:

- **Declared dependencies** — stated at request time ("this VM needs an IP address")
- **Realized dependencies** — reported by the provider ("this VM was assigned IP 10.0.1.5 from pool X")
- **Observed dependencies** — discovered after the fact ("SBOM analysis shows this container uses library Y which depends on library Z")

The gap between declared and observed dependencies is drift — the same drift detection model that tracks resource state also tracks relationship state.

---

## Slide 3: What the Unified Graph Enables

### Impact Analysis — One Query

"What depends on this storage array?"

Without UDLM: walk across 6 tools, cross-reference, hope nothing was missed. Time: hours to days.

With UDLM: one reverse-dependency query that traverses the full graph — storage array → volumes → VMs → containers → applications → services → load balancers → DNS records. Time: seconds. Every entity in the chain is identified with its UUID, owner, environment, and current lifecycle state.

### Rehydration — Replay the Graph

"Rebuild this environment from scratch."

Without UDLM: manually figure out the order, rebuild each piece, reconnect the dependencies. Time: days to weeks. Accuracy: hope.

With UDLM: replay stored intents in dependency order. The graph dictates the sequence — databases before application tiers, application tiers before web tiers, web tiers before DNS records. Each resource is reprovisioned through the standard policy pipeline, so sovereignty constraints, security hardening, and cost placement all reapply automatically.

### Decommission — Cascade Safely

"Decommission this database instance."

Without UDLM: decommission and discover what breaks. Or refuse to decommission because you don't know what depends on it.

With UDLM: reverse-dependency query shows 3 applications, 2 batch jobs, and a reporting service depend on this database. The governance matrix determines: notify owners, wait for acknowledgment, migrate dependents, then decommission. No surprise outages.

### Drift Detection — Across the Full Chain

"Has anything changed that we didn't approve?"

Without UDLM: each tool checks its own resources. Cross-tool drift (a DNS record points to a different IP than the realized state says) is invisible.

With UDLM: drift detection runs across the full graph. The DNS provider reports the A record points to 10.0.1.6. The compute provider reports the VM is at 10.0.1.5. The dependency says DNS should point to the VM. The drift is detected, correlated, and policy determines the response.

---

## Slide 4: The Dependency Graph Spans Domains

The power of the unified graph is that it crosses every domain boundary:

```
┌─────────────────────────────────────────────────────────────────┐
│                        APPLICATION                              │
│  React App ──depends──► API Service ──depends──► Auth Service   │
└───────┬──────────────────────┬─────────────────────────┬────────┘
        │                      │                         │
        ▼                      ▼                         ▼
┌───────────────┐  ┌───────────────────┐  ┌──────────────────────┐
│   COMPUTE     │  │     DATABASE      │  │      IDENTITY        │
│  Web Pod      │  │  Postgres (HA)    │  │  OIDC Provider       │
│  App Pod      │  │    ├─ Primary     │  │  Certificate (TLS)   │
└───────┬───────┘  │    └─ Replica     │  └──────────┬───────────┘
        │          └────────┬──────────┘             │
        ▼                   ▼                        ▼
┌───────────────┐  ┌───────────────────┐  ┌──────────────────────┐
│   NETWORK     │  │     STORAGE       │  │    GOVERNANCE        │
│  LB VIP       │  │  PVC (100Gi)      │  │  Data classification │
│  DNS A record │  │  Backup schedule  │  │  Sovereignty zone    │
│  Firewall ACL │  │  Snapshot policy  │  │  Compliance policy   │
└───────┬───────┘  └────────┬──────────┘  └──────────────────────┘
        │                   │
        ▼                   ▼
┌───────────────────────────────────────┐
│           INFRASTRUCTURE              │
│  Host Node → Rack → Data Center       │
│  Network Switch → Router → WAN        │
│  Storage Array → Disk → RAID Group    │
└───────────────────────────────────────┘
```

Every arrow is a tracked, typed dependency in the UDLM graph. Every node is an entity with a four-state lifecycle. Every layer is managed by a different provider — but the graph connects them all.

### Software Delivery Adds Another Dimension

With the Delivery family extension, the graph also tracks:

- Which **source commit** produced which **artifact**
- Which **build** consumed which **dependencies** (libraries, base images)
- Which **scans** attested which **artifacts** (vulnerability, compliance, SBOM)
- Which **signatures** verify which **artifacts**
- Which **deployments** placed which **artifacts** in which **environments**

Now one query answers: "Show me every deployment running an artifact built from source that depends on log4j, deployed in production, in a sovereignty zone that requires HIPAA compliance."

---

## Slide 5: How Tools Participate

Each tool becomes a UDLM provider. It doesn't change what it does — it just records what it did and what it depends on in a format every other tool can read.

| Tool | What it provides | Dependencies it declares |
|------|-----------------|------------------------|
| **Compute platform** (OpenShift, VMware, KubeVirt) | VMs, containers, pods | Depends on: network, storage, identity |
| **Network platform** (Ansible, Cisco, F5) | IPs, DNS, VIPs, ACLs | Depends on: infrastructure, identity |
| **Storage platform** (Ceph, Pure, NetApp) | Volumes, snapshots, backups | Depends on: infrastructure |
| **Identity provider** (LDAP, OIDC, Keycloak) | Users, groups, certificates | Depends on: infrastructure |
| **Build system** (Tekton, Jenkins, Actions) | Artifacts, SBOMs | Depends on: source, libraries |
| **Scanner** (Trivy, Grype, Snyk) | Attestations, scan results | Depends on: artifact |
| **Deployment tool** (ArgoCD, Flux, Ansible) | Deployments | Depends on: artifact, compute, network |
| **Cost management** (Koku, CloudHealth) | Cost data, chargeback | Depends on: all resources it meters |
| **ITSM** (ServiceNow, Jira) | Tickets, approvals | Depends on: the entities they govern |

Each provider implements the UDLM provider contract once. After that, its resources and dependencies are part of the unified graph — visible to every other provider, queryable across domains, governed by policy.

---

## Slide 6: Why This Matters for Sovereignty

Sovereignty constraints apply across the full dependency chain, not just to individual resources. A sovereign workload isn't just a VM in the right region — it's:

- A VM in the right region
- Connected to storage in the right region
- With DNS resolving within the right jurisdiction
- Using certificates issued by an approved authority
- Running an application built from approved source
- With dependencies scanned and attested
- Governed by policies that enforce all of the above

Without a unified dependency graph, you enforce sovereignty per-tool and hope nothing crosses a boundary. With UDLM, sovereignty policy evaluates the **entire chain** — and rehydration replays the entire chain with sovereignty constraints enforced at every step.

---

## Slide 7: The Value Proposition

| Without unified data + graph | With UDLM |
|-----|-----|
| Impact analysis: hours of cross-tool investigation | Impact analysis: one query, seconds |
| Decommission: "what breaks?" is unknown | Decommission: full dependent chain visible, cascade governed |
| Rehydration: manual, error-prone, weeks | Rehydration: automated replay in dependency order, hours |
| Drift: detected per-tool, cross-tool drift invisible | Drift: detected across the full graph including relationships |
| Sovereignty: enforced per-resource, chain gaps invisible | Sovereignty: enforced across the full dependency chain |
| Audit: reconstruct from logs across tools | Audit: query the four-state lifecycle with field-level provenance |
| New tool integration: bespoke integration with every other tool | New tool: implement provider contract once, join the graph |

### The Single Question That Justifies This

**"If a critical vulnerability is announced in a library, can you tell me — within minutes, not days — every application, deployment, environment, and infrastructure resource affected, ranked by business impact, with the full dependency chain from library through artifact through deployment through infrastructure?"**

If you can't answer that today, you need a unified data model and dependency graph. That's UDLM.

---

## Slide 8: UDLM as the Unified Inventory — One Source of Truth

Every enterprise has an inventory problem. The CMDB says there are 4,200 VMs. The hypervisor platform says 4,350. The monitoring tool sees 3,900. The cost tool bills for 4,500. Nobody agrees because each tool maintains its own inventory from its own perspective, refreshed on its own schedule, with its own definition of what counts.

UDLM solves this by making every tool a **contributor** to one inventory rather than an **owner** of a separate one.

### The Four States ARE the Inventory

| State | Inventory question it answers | Who contributes |
|-------|-------------------------------|-----------------|
| **Intent** | What did we ask for? What SHOULD exist? | The requesting team, the service catalog, the architecture definition |
| **Requested** | What was approved? What is the governed version of the request? | The policy engine, the approval workflow, the governance matrix |
| **Realized** | What was actually provisioned? What does the provider say it built? | The provider — VMware, OpenShift, the network platform, the storage array |
| **Discovered** | What actually exists RIGHT NOW? | Observability tools, scanners, runtime probes, network discovery |

The inventory isn't a snapshot maintained by one team. It's the **convergence of all four states across all providers**. When they agree, the inventory is clean. When they disagree, that's a finding — a resource that was requested but never realized, a resource that exists but was never requested, a resource that drifted from its realized configuration.

### What Traditional CMDBs Get Wrong

A CMDB is a **single-state inventory** — it records what someone says exists, updated manually or by periodic discovery. It doesn't know what was intended, what was approved, or what the provider actually built. It can't answer:

- "Is this resource here because someone requested it, or is it shadow IT?"
- "Was this configuration approved, or did someone change it after deployment?"
- "The CMDB says this VM has 16GB RAM, but the hypervisor reports 8GB — which is right?"

UDLM answers all three: intent says what was requested, request says what was approved, realized says what the provider built, discovered says what's actually there. The CMDB question — "what exists?" — is just the Discovered state. UDLM adds the **why**, the **how**, and the **should**.

### Reconciliation Is Automatic

| Reconciliation | What it catches | Traditional approach | UDLM approach |
|---------------|-----------------|---------------------|---------------|
| **Intent vs Discovered** | Shadow IT — resources that exist but were never requested | Manual audit, usually annual | Continuous: anything in Discovered with no matching Intent is flagged |
| **Requested vs Realized** | Provisioning errors — what was built doesn't match what was approved | Hope and spot-checks | Continuous: field-level comparison, drift on any delta |
| **Realized vs Discovered** | Configuration drift — something changed after provisioning | Agent-based scanning, periodic | Continuous: provider reports Realized, observability reports Discovered, policy governs the response |
| **Intent vs Requested** | Policy impact — what governance changed about the original request | Not tracked anywhere | Fully tracked: every enrichment, every default, every policy that fired |

---

## Slide 9: Operational Metadata — Everything You Need to Know, In One Place

Beyond the inventory (what exists), UDLM captures the **operational metadata** that every team needs but no tool provides completely:

### Per-Resource Operational Record

Every UDLM entity carries:

| Metadata | What it answers | Who uses it |
|----------|----------------|-------------|
| **Owner** (tenant, team, individual) | Who is responsible for this resource? | Operations, cost allocation, incident routing |
| **Classification** (data sensitivity, compliance domain) | What governance applies? | Security, compliance, sovereignty |
| **Cost** (capex, opex, chargeback rate) | What does this resource cost and who pays? | Finance, FinOps, capacity planning |
| **Lifecycle state** (operational, suspended, decommissioning) | Is this resource active, paused, or being retired? | Operations, capacity, audit |
| **Provenance** (who created, who modified, when, why, under what authority) | Who did what and when? | Audit, compliance, incident investigation |
| **Dependencies** (what it depends on, what depends on it) | What breaks if this changes? What does this need to function? | Change management, impact analysis, DR planning |
| **Policy history** (which policies evaluated, what they decided) | Why was this resource configured this way? | Architecture review, compliance evidence |
| **SLO/SLA** (performance targets, availability requirements) | What service level is expected? | Operations, monitoring, capacity |
| **Sovereignty** (region, jurisdiction, data residency requirements) | Where must this resource exist and where must its data stay? | Compliance, legal, sovereignty enforcement |
| **Maturity** (capability score, assessment findings) | How mature is this capability? Where are the gaps? | Architecture, planning, roadmap |

### No Tool Has All of This Today

The hypervisor knows the resource specs but not the cost. The cost tool knows the cost but not the dependencies. The CMDB knows the owner but not the policy history. The monitoring tool knows the current state but not the intent. The ticketing system knows who approved it but not what was actually built.

UDLM is the convergence point. Each tool contributes the metadata it owns. The unified record is the combination. No tool has to change what it stores — it just also writes to UDLM so the complete picture is available in one place.

### Queries That Become Trivial

Questions that today require cross-referencing 4-6 tools and spreadsheets become single queries:

| Question | Tools needed today | With UDLM |
|----------|-------------------|-----------|
| "Show me all production resources in Region A owned by Team X with a cost above $500/month" | CMDB + cost tool + tagging system | One query on entities with matching owner, classification, sovereignty zone, and cost |
| "Which resources were provisioned in the last 30 days without a change ticket?" | CMDB + ITSM + provisioning logs | Intent state with no matching ITSM Action policy record |
| "What is our total infrastructure cost per capability domain?" | Cost tool + manual capability mapping | Aggregate cost from Realized state grouped by capability taxonomy |
| "Show me every resource that was modified by someone other than the owner in the last 7 days" | Audit logs across all tools | Provenance query: `modified_by != owner AND modified_at > 7d ago` |
| "Which resources have no observability coverage?" | Monitoring tool + CMDB diff | Entities with Realized state but no Discovered state updates from any observability provider |
| "What's the blast radius if data center 2 goes offline?" | Network tool + CMDB + application mapping + manual dependency tracing | Reverse-dependency traversal from DC2's infrastructure through every layer to every affected application and service |

---

## Slide 10: From Fragmented Tools to Unified Operations

### Before UDLM

```
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│ VMware   │ │ Network  │ │ Storage  │ │  CMDB    │ │ Cost     │
│ inventory│ │ inventory│ │ inventory│ │ "truth"  │ │ billing  │
│ 4,350 VMs│ │ 12K ports│ │ 800 vols │ │ 4,200 VMs│ │ 4,500 VMs│
└────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘
     │            │            │            │            │
     └────────────┴────────────┴────────────┴────────────┘
                              │
                    Manual reconciliation
                    Spreadsheets, scripts
                    Annual audit (maybe)
                              │
                              ▼
                      "We think we have
                       about 4,200 VMs"
```

### After UDLM

```
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│ VMware   │ │ Network  │ │ Storage  │ │ Monitor  │ │ Cost     │
│ provider │ │ provider │ │ provider │ │ provider │ │ provider │
└────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘
     │            │            │            │            │
     └────────────┴────────────┴────────────┴────────────┘
                              │
                     UDLM Unified Data Layer
                     ┌─────────────────────┐
                     │ Intent:     4,180    │ ← what was requested
                     │ Requested:  4,180    │ ← what was approved
                     │ Realized:   4,350    │ ← what providers built
                     │ Discovered: 3,900    │ ← what's actually running
                     │                     │
                     │ Delta:              │
                     │  170 realized but   │ ← shadow or orphaned
                     │      never intended │
                     │  450 realized but   │ ← offline, suspended,
                     │      not discovered │   or monitoring gap
                     └─────────────────────┘
                              │
                     Policy engine acts on deltas
                     Automatically, continuously
```

The numbers don't need to agree — the **deltas between them** are the operational intelligence. UDLM doesn't pick one number. It tracks all four and lets policy determine what each delta means.

---

## Slide 11: Enabling Automation Without Separate Integrations

### The Integration Tax Today

Every automation needs data from multiple sources. An Ansible playbook that provisions a VM needs:

- The VM specification → from the service catalog or request ticket
- The target hypervisor → from the CMDB or capacity tool
- The IP address → from the IPAM
- The DNS record → from the DNS management tool
- The firewall rules → from the security team's spreadsheet
- The cost center → from the finance system
- The approval status → from the ticketing system
- The compliance requirements → from the governance wiki

That's **7 separate integrations** for one playbook. Each integration is bespoke — custom API calls, custom credential management, custom error handling, custom data transformation. Multiply by every automation across the enterprise. The integration tax is enormous: more code maintaining integrations than doing actual work.

### With UDLM: The Data Is Already There

When the automation fires, the UDLM entity already contains everything:

```yaml
# The UDLM entity the automation receives — one object, complete
entity:
  uuid: uc-abc123
  type: Compute.VirtualMachine

  intent:                          # ← what was requested
    cpu: 4
    memory: 16Gi
    purpose: "web tier for app X"
    environment: production
    region: us-east

  requested:                       # ← what policy approved + enriched
    cpu: 4
    memory: 16Gi
    network:
      vlan: 150
      ip: 10.0.1.42               # ← IPAM already allocated
      dns: web01.app-x.prod.internal  # ← DNS already assigned
    storage:
      size: 100Gi
      class: ssd-encrypted         # ← policy selected based on production + data classification
    firewall:
      rules: [allow-443, allow-80] # ← security policy injected
    cost_center: CC-4521           # ← finance data already attached
    compliance:
      domains: [pci, sox]          # ← governance already evaluated
    approval:
      change_id: CHG0012345        # ← ITSM already created + approved
      approved_by: j.smith
      approved_at: 2026-06-24T14:30:00Z

  dependencies:                    # ← what this resource needs (already resolved)
    - entity: uc-def456            # the storage volume
    - entity: uc-ghi789            # the network port
    - entity: uc-jkl012            # the DNS record
```

The automation receives **one object** with everything it needs. No CMDB query. No IPAM lookup. No cost center search. No approval status check. The policy engine already gathered all of that during the request lifecycle. The automation just provisions.

### How This Changes Automation Architecture

| Without UDLM | With UDLM |
|--------------|-----------|
| Playbook has 7 integration tasks before the real work starts | Playbook receives a complete entity and provisions |
| Each integration needs credentials, error handling, retries | One data source, one credential (the provider contract) |
| Data format differs per source — transformation code everywhere | One data model, one format, one schema |
| If the IPAM is down, the whole playbook fails | IPAM already ran during the request lifecycle — the IP is in the entity |
| New data source = new integration in every playbook that needs it | New data source = new policy or provider that enriches the entity before automation runs |
| Testing requires mocking 7 external systems | Testing uses a UDLM entity fixture — one object |

### The Provider Contract Is the Integration

Instead of every automation building its own integrations, each **data source implements the provider contract once**:

- The **IPAM** is a provider. When DCM needs an IP for a new VM, the IPAM provider allocates one and writes it to the Requested state. Done.
- The **DNS platform** is a provider. When the VM is realized, the DNS provider creates the record and writes it to the Realized state. Done.
- The **cost system** is a provider. It attaches cost data to every entity. Done.
- The **ITSM** is a provider. It creates the change ticket during the request lifecycle and writes the approval status. Done.

Each integration is built **once, by the team that owns the data source** — not rebuilt in every automation by every team that needs the data. The IPAM team builds the IPAM provider. Every automation that needs an IP gets it for free because the IPAM provider already enriched the entity.

### Automation Becomes Stateless

The most powerful consequence: automation no longer needs to maintain state about the environment. Today, playbooks query the environment to understand what exists before acting. With UDLM, the entity **is** the environment state — intent, requested, realized, and discovered are all in one place. The automation doesn't need to discover the current state; it's already in the entity's Discovered state. It doesn't need to check what was approved; it's in the Requested state. It doesn't need to verify dependencies; they're in the dependency graph.

Automation becomes a pure function: **entity in → action → updated entity out.** No side-channel data gathering. No implicit state. No hidden dependencies on tools that might be down.

### Event-Driven Automation Without Polling

Today, event-driven automation requires each automation to poll or subscribe to each data source independently. With UDLM, the event catalog provides a single event stream:

- `entity.state_changed` → "something changed, here's the entity with all its data"
- `entity.drift_detected` → "discovered differs from realized, here's the delta"
- `entity.dependency_changed` → "something this entity depends on changed"

An EDA rulebook subscribes to UDLM events — not to 7 separate tool APIs. One subscription. One event format. One data model. The rulebook matches on entity type, lifecycle state, severity, and any metadata field — and when it fires, the entity already has everything the remediation playbook needs.

```yaml
# EDA rulebook — one event source, complete data
- name: Remediate production drift
  hosts: all
  sources:
    - udlm.events:
        event_types: [entity.drift_detected]
        filter:
          classification.environment: production
          drift.severity: [critical, high]
  rules:
    - name: Auto-remediate known drift patterns
      condition: event.drift.type in ["config_changed", "resource_scaled"]
      action:
        run_playbook:
          name: remediate_drift.yml
          extra_vars:
            entity: "{{ event.entity }}"    # ← complete entity, no lookups needed
            drift: "{{ event.drift }}"
            intent: "{{ event.entity.intent }}"
```

No CMDB integration in the rulebook. No monitoring tool integration. No approval check. The entity arrived complete. The automation acts.

---

*UDLM specification: github.com/dcm-project/udlm*
*DCM reference realization: github.com/dcm-project/dcm*
