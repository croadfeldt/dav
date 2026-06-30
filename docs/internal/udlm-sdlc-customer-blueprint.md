# Unifying Your Software Delivery Lifecycle with UDLM

**A practical blueprint for enterprise adoption**

_For enterprise architects, platform engineering leaders, and DevSecOps teams who are managing dozens of disconnected tools across their software delivery lifecycle and want a single, governed data model underneath all of them._

---

## The Problem You Already Have

Your software delivery lifecycle involves dozens of tools. Source control. Build systems. Artifact registries. Vulnerability scanners. SBOM generators. Signing services. Test frameworks. Deployment tools. Change management. Monitoring. Each one produces data. Each one stores it differently. Each one has its own representation of what an artifact is, what a deployment is, what "approved" means.

You can answer "did the build pass?" You can answer "did the scan find vulnerabilities?" You can answer "is the deployment healthy?" What you cannot easily answer is:

- **What is deployed in production right now, who built it, who approved it, what vulnerabilities does it have, and what does it depend on?**
- **If a new CVE is announced for a library, which deployed applications are affected — across all environments?**
- **When this artifact was promoted from staging to production, what attestations existed at promotion time vs what exists now?**
- **What percentage of our production deployments come from fully attested, signed, scanned sources vs unverified sources?**

You cannot answer these because the data is fragmented across tools that don't share a common model. The vulnerability scanner doesn't know what the deployment tool deployed. The deployment tool doesn't know what the signing service signed. The change management system has a ticket number but no link to the actual artifact digest. Connecting them requires custom integration — bespoke glue code that breaks when any tool changes.

This is the same broken information loop that UDLM was designed to close for infrastructure. And the solution is the same: a common, machine-native data model that every tool reads and writes, governed by policy, tracked through a lifecycle.

---

## What UDLM Gives You

UDLM is not another tool in your pipeline. It is the **data layer beneath all of them**. Every tool in your SDLC becomes a provider that reads and writes UDLM data. Every governance requirement becomes a policy that evaluates UDLM data. Every audit question becomes a query against UDLM data.

### The Four States Applied to Software Delivery

Every artifact in your delivery lifecycle exists in four states simultaneously:

| State | What it answers | Example |
|-------|----------------|---------|
| **Intent** | What did someone ask for? | "Build the main branch of service-api and deploy to staging" |
| **Requested** | What was approved and dispatched? | The intent after policy enrichment: "build from commit abc123, scan with Trivy, sign with Cosign, deploy to staging-us-east with 3 replicas and these resource limits" |
| **Realized** | What was actually produced? | "Image sha256:def456 built by Tekton pipeline run #789, pushed to registry, signed, scan passed with 0 critical/2 medium" |
| **Discovered** | What actually exists right now? | "3 pods running image sha256:def456 in staging-us-east, all healthy, SBOM shows 847 dependencies, no new CVEs since last scan" |

The gap between any two states tells you something actionable:

- **Intent vs Requested** reveals what policy changed about your request (enrichment, defaults, constraints added)
- **Requested vs Realized** reveals whether what was built matches what was approved
- **Realized vs Discovered** reveals drift — what's running doesn't match what the deployment record says
- **Intent vs Discovered** reveals whether the original need is being met

Right now, you reconstruct these gaps manually by cross-referencing logs across tools. With UDLM, they are queries.

---

## The Adoption Path: Start Where the Pain Is

You do not need to unify everything on day one. UDLM is designed for incremental adoption. Start with the highest-pain integration gap, prove value, expand.

### Stage 1: Artifact Provenance — "What did we deploy and where did it come from?"

**The problem:** You can see what's running in production. You can see what was built. But connecting a running container to the exact source commit, build pipeline, scan results, and approval chain that produced it requires manual investigation across 4-6 tools.

**What you do:**
- Model your artifacts (container images, packages) as UDLM entities
- Model your builds as UDLM entities linked to source and artifact entities
- Model your scan results and signatures as attestation entities linked to artifacts

**What you get:**
- One query: "For the image running in pod X, show me the source commit, the build that produced it, every scan result, every signature, and who approved it"
- Full provenance chain from source to production — machine-readable, not reconstructed from logs
- A foundation for every subsequent stage

**Tools involved:** Your build system, your registry, your scanner, your signing service. Each writes UDLM data when it completes its work. No tool changes what it does — it just records what it did in a common format.

**Time to value:** Weeks, not months. The data model is simple at this stage — entity creation + relationship linking. The value is immediate: incident response time drops from hours of log correlation to one query.

### Stage 2: Policy-Governed Pipeline — "Enforce governance without manual gates"

**The problem:** Your pipeline has manual checkpoints — someone reviews scan results, someone approves promotion, someone opens a change ticket. These gates are slow, inconsistent, and bypassable.

**What you do:**
- Express your governance requirements as UDLM policies:
  - GateKeeper: "No deployment without a passing vulnerability scan and a valid signature"
  - Validation: "SBOM must conform to CycloneDX schema"
  - Governance Matrix: "Production deployments require approval from security and the service owner"
- Wire the policies into the pipeline flow — each stage triggers the next via data state changes

**What you get:**
- Governance that cannot be accidentally skipped — it's structural, not procedural
- Consistent enforcement across every pipeline, every team, every artifact
- Human approval where it matters (production promotion), automation everywhere else
- Complete audit trail of every policy evaluation and every decision

**Tools involved:** Your existing pipeline tools + a policy engine (OPA/Rego). The pipeline continues to run your existing tools — UDLM adds the governance and audit layer.

**Time to value:** A few weeks after Stage 1 is in place. The policies are simple to write once you have the data.

### Stage 3: Environment Promotion — "Promote with confidence, not with hope"

**The problem:** Promoting from dev to staging to production is either manual (slow, error-prone) or automatic (fast, terrifying). You want automatic promotion with the confidence of manual review.

**What you do:**
- Model each environment as a sovereignty zone with accreditation requirements:
  - Development: any artifact, any attestation level
  - Staging: requires passing vulnerability scan + SBOM
  - Production: requires all of staging + valid signature + human approval + change ticket
- Model promotions as explicit entities with approval chains
- Let the governance matrix enforce the requirements per environment tier

**What you get:**
- Automatic promotion when all requirements are met — no human in the loop for dev-to-staging when scans pass
- Guaranteed governance for production — the same rigor infrastructure sovereignty provides
- Emergency override model — hotfixes can bypass normal gates with dual approval, compensating controls, and full audit trail
- Visibility: "What attestations existed when this artifact was promoted?" is a query

**Tools involved:** Your deployment tools (ArgoCD, Flux, Ansible) + your change management system (ServiceNow). Each participates as a provider.

### Stage 4: Vulnerability Response — "A CVE was announced. What's affected?"

**The problem:** A new CVE is published for a library. How many of your deployed applications use it? You don't know without scanning everything again and manually correlating with deployment records.

**What you do:**
- Continuous discovery: your scanners periodically re-scan deployed artifacts and update the Discovered state
- When a new vulnerability is found in a previously-clean artifact, an `artifact.quarantined` event fires
- A lifecycle policy triggers: "For every Deployment Entity using this artifact, evaluate severity and initiate rebuild or rollback"

**What you get:**
- Hours, not days, from CVE announcement to "here are the 47 deployments affected, ranked by severity and environment"
- Automated rebuild pipeline triggered for affected artifacts (if the fix is a library update)
- Automated rollback for critical vulnerabilities in production (if the fix isn't available yet)
- Executive dashboard: "What percentage of our production deployments are currently affected by known CVEs?"

**Tools involved:** Your scanners running continuously + your existing build and deployment tools responding to UDLM events.

### Stage 5: Supply Chain Trust — "How much of our software comes from verified sources?"

**The problem:** You use thousands of open source libraries. Some come from public registries with no verification. Some come from vendored copies. Some come from hardened repositories. You don't know the ratio, and you can't answer "what percentage of our production software has verified provenance?"

**What you do:**
- Model your library sources (Maven Central, PyPI, NPM, hardened repos like LightWell) as providers with trust classifications
- Model each dependency as a relationship between your artifact and the library source
- Score trust coverage: what percentage of each artifact's dependencies come from trusted vs untrusted sources

**What you get:**
- Per-artifact trust score: "This image is 73% trusted (487/667 dependencies from hardened sources)"
- Estate-wide trust trending: "Trust coverage improved from 31% to 73% over six months as we onboarded hardened libraries"
- Policy enforcement: "No production deployment with trust coverage below 60%"
- Prioritization: "These 12 libraries appear in the most artifacts and have the lowest trust scores — harden these first"

### Stage 6: The Complete Pipeline — "Everything as a service, working together"

At this stage, your entire SDLC is unified under UDLM:

```
Source push
  → Build-as-a-service (Tekton/Actions/Jenkins)
    → Scan-as-a-service (Trivy/Grype/Snyk)
      → Sign-as-a-service (Cosign/Notary)
        → Test-as-a-service (JUnit/Cypress/K6)
          → Promote-as-a-service (governance matrix evaluation)
            → Deploy-as-a-service (ArgoCD/Flux/Ansible)
              → Monitor-as-a-service (continuous discovery)
                → Change-as-a-service (ServiceNow integration)
```

Each "-as-a-service" is a provider that implements the UDLM contracts. Each transition is policy-driven. Each artifact is tracked through the four states. Each decision is auditable. Adding a new tool means implementing the provider contract once — not building custom integrations with every other tool in the chain.

---

## What Changes for Your Teams

### For Platform Engineers

You stop building glue code between tools. You build providers that implement the UDLM contracts. When the organization switches from Trivy to Grype, you change one provider registration — the pipeline definition, the policies, and the audit trail don't change.

### For Security Teams

You stop writing policies in wiki pages and hoping teams follow them. You write OPA/Rego policies that the system enforces. Your attestation requirements are structural — no artifact reaches production without satisfying them. When you need an emergency exception, the override model captures the approval, the compensating control, and the expiry.

### For Developers

Your experience improves. Instead of navigating four portals to understand why your deployment was blocked, you get a single view: "Your deployment to staging was rejected because: vulnerability scan found 2 critical CVEs in dependency X. Remediation: update X to version Y (hardened version available)." The governance that used to feel like bureaucracy becomes immediate, actionable feedback.

### For Compliance and Audit

Every question you ask today — "who approved this deployment?", "what version was deployed when?", "was this artifact scanned?" — becomes a query against structured data with provenance. Audit preparation drops from weeks of evidence collection to an export.

### For Leadership

You get a real-time, machine-generated answer to "how healthy is our software delivery pipeline?" Trust coverage, vulnerability exposure, deployment velocity, governance compliance, policy override frequency — all derived from the same data model, all comparable over time, all reproducible.

---

## How It Relates to What You Already Have

UDLM does not replace your tools. It unifies the data beneath them.

| What you have today | What it becomes with UDLM |
|--------------------|--------------------------| 
| GitHub/GitLab | A source provider — writes Source Entities when commits are pushed |
| Tekton/Jenkins/Actions | A build provider — writes Build and Artifact Entities when builds complete |
| Quay/Harbor/Artifactory | An artifact provider — stores artifacts, serves Discovered state on query |
| Trivy/Grype/Snyk | A scan provider — writes Attestation Entities with scan results |
| Cosign/Notary | A signing provider — writes Attestation Entities with signatures |
| ArgoCD/Flux | A deployment provider — writes Deployment Entities, reports Discovered state |
| ServiceNow/Jira | An ITSM provider — writes change records, receives deployment notifications |
| OPA/Kyverno | Policy evaluation — enforces GateKeeper and Validation policies |
| Splunk/Dynatrace | Observability — feeds Discovered state with runtime data |

Each tool does what it already does. It just records what it did in a format every other tool can read. That's the unification.

---

## What You Need to Get Started

1. **Pick your Stage 1 pain point.** For most organizations, it's artifact provenance — "what's running and where did it come from?" If your pain is different (policy enforcement, vulnerability response, promotion governance), start there.

2. **Identify 3-4 tools** in the chain for that pain point. Those become your first providers.

3. **Define the entities** — typically Source, Artifact, Build, and 1-2 Attestation types for Stage 1.

4. **Wire the data flow** — each tool writes UDLM entities when it completes its work. Start with simple REST writes to a central store; optimize later.

5. **Write your first policy** — the governance rule you wish you could enforce today but can't because the data is fragmented.

6. **Query it** — ask the question you couldn't answer before. When you can answer it in one query instead of four-tool log correlation, Stage 1 is proven.

7. **Expand** — add the next tools, the next policies, the next environment tiers. Each stage builds on the foundation.

The community is building this now. The specification, the reference implementation, and early provider integrations are available. The institutions that engage now shape the standard — they are co-authors, not consumers.

---

*UDLM specification: github.com/dcm-project/udlm*
*DCM reference realization: github.com/dcm-project/dcm*
*Reference implementation: github.com/croadfeldt/dav*

*This blueprint is a practical guide for adoption, not a specification. The entity types, provider mappings, and policy examples are illustrative — refine them for your organization's tools, governance requirements, and deployment model.*
