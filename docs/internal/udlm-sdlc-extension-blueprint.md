# UDLM Software Delivery Lifecycle Extension — Blueprint

_Extends UDLM with a **Delivery** entity-type family to support end-to-end software ingestion through deployment. Uses only UDLM's designed extension mechanisms — no spec changes required. DCM serves as the orchestrator, triggering each stage from data state changes via the policy engine._

_Status: Blueprint / design proposal. 2026-06-17._

---

## 1. The Goal

Unify the data for a complete end-to-end software delivery lifecycle — from source code ingestion through build, test, scan, sign, promote, deploy, and operate — under UDLM. Dozens or hundreds of tools participate, each triggered by data state changes, each reading and writing UDLM-conformant data. DCM serves as the single orchestrator: the policy engine evaluates, providers execute, and the four-state lifecycle tracks everything from intent through discovered truth.

The same contracts that let a networking team plug in as a provider and a CISO inject policy for infrastructure lifecycle apply identically to software delivery. A build system is a provider. A vulnerability scanner is a provider. A signing service is a provider. A CI/CD policy is a policy. The model doesn't change — it extends.

---

## 2. The Delivery Family

Following the pattern established by the Knowledge family (DAV), we define a new entity-type family: **Delivery**.

### Family Registration

| Family | Domain (organizing context) | Anchored by | Lifecycle archetype |
|--------|---------------------------|-------------|---------------------|
| **Delivery** | Software supply chain (built, attested, deployed) | SDLC realization / DCM | Delivery: SUBMITTED → BUILDING → BUILT → ATTESTED → PROMOTED → DEPLOYED → DEPRECATED |

### Four-State Interpretation

Every Delivery entity is UDLM Data and exists in the four states. The family interprets them for software delivery:

| UDLM state | Delivery interpretation | Storage semantics |
|------------|------------------------|-------------------|
| **Intent** | `SUBMITTED` — a delivery request ("build this, deploy that") | append-only, immutable record of what was asked for |
| **Requested** | `VALIDATED` — the request after policy enrichment and validation (resolved dependencies, approved source, assigned environment) | append-only per validation cycle |
| **Realized** | `DELIVERED` — what was actually produced (built artifact, signed image, completed deployment) | versioned snapshots, `is_current` flag |
| **Discovered** | `OBSERVED` — what actually exists right now (what's running, what's in the registry, what SBOMs report) | ephemeral, refreshed per discovery run |

The signature operation: the gap between **Delivered** (what we said was deployed) and **Observed** (what's actually running) is drift detection applied to software delivery — identical in shape to infrastructure drift and knowledge gap analysis. A deployed image that no longer matches the registry image, a running container with dependencies not in its SBOM, a service endpoint returning a different version than the deployment record claims — all are Delivered-vs-Observed drift.

### State Machine Skeleton

```
OBSERVED ─┐                              (parallel evidence: runtime probing, registry checks)
          ▼
SUBMITTED ──► VALIDATED ──► BUILDING ──► BUILT ──► ATTESTED ──► PROMOTED ──► DEPLOYED ──► DEPRECATED
   ▲              │                                                              │
   └──────────────┘  (rejected — resubmit)                                       │
                                                                                 ▼
                                                                            DECOMMISSIONED
```

Intermediate states (BUILDING, BUILT, ATTESTED, PROMOTED) are lifecycle progression markers within the Realized state — each represents a provider completing its work and producing new data that triggers the next stage.

---

## 3. Entity-Type Definitions

### 3.1 Source Entity

A version-controlled source artifact: a repository, a commit, a branch, a tag.

| Field | Type | Description |
|-------|------|-------------|
| `source_type` | enum | `repository`, `commit`, `branch`, `tag` |
| `repository_url` | string | The canonical URL of the repository |
| `ref` | string | The git ref (SHA, branch name, tag name) |
| `ref_type` | enum | `commit`, `branch`, `tag` |
| `content_hash` | string | Hash of the content at this ref (for immutability verification) |

**Lifecycle:** A repository is long-lived (SUBMITTED → DEPLOYED = registered and active). A commit is immutable once created (SUBMITTED = pushed, DELIVERED = the commit exists). Branch and tag entities track their lifecycle (created, moved, deleted).

### 3.2 Artifact Entity

An immutable, built output: a container image, a package, a binary, a library.

| Field | Type | Description |
|-------|------|-------------|
| `artifact_type` | enum | `container_image`, `package`, `binary`, `library`, `helm_chart` |
| `artifact_ref` | string | Registry reference (e.g., `registry.example.com/app:v1.2.3@sha256:abc...`) |
| `digest` | string | Content-addressable hash (immutability anchor) |
| `source_entity_uuid` | uuid | The Source Entity this artifact was built from |
| `build_entity_uuid` | uuid | The Build Entity that produced this artifact |
| `size_bytes` | integer | Artifact size |
| `media_type` | string | OCI media type or equivalent |

**Lifecycle:** SUBMITTED (build requested) → BUILDING → BUILT (artifact produced, pushed to registry) → ATTESTED (scanned, signed) → PROMOTED (approved for target environment). An Artifact Entity is immutable after BUILT — it never changes, but it can be DEPRECATED or recalled.

**Key design point — immutable artifact, multiple deployments:** A single Artifact Entity can be referenced by many Deployment Entities. The artifact is built once; each deployment is a separate entity with its own lifecycle. This is a relationship, not a composition — deprecating the artifact cascades to its deployments via dependency policy.

### 3.3 Build Entity

A record of a build execution: what was built, how, by whom, with what inputs.

| Field | Type | Description |
|-------|------|-------------|
| `build_system` | string | Provider identifier (e.g., `tekton`, `github-actions`, `jenkins`) |
| `pipeline_ref` | string | Reference to the pipeline/workflow definition |
| `source_entity_uuid` | uuid | The Source Entity that was built |
| `inputs` | list | Input artifacts (base images, dependencies) as entity UUIDs |
| `outputs` | list | Output Artifact Entity UUIDs |
| `build_config` | object | Build parameters (reproducibility record) |
| `slsa_level` | integer | SLSA compliance level achieved (0-4) |
| `duration_seconds` | float | Build duration |
| `hermetic` | boolean | Whether the build was hermetic (no network access during build) |

**Lifecycle:** SUBMITTED (build triggered) → BUILDING (provider executing) → BUILT (completed, outputs produced) → ATTESTED (provenance attestation attached). Failed builds transition to FAILED with error detail in the Realized state.

### 3.4 Attestation Entity

An evidence record: a scan result, a signature, an SBOM, a test result, a provenance statement.

| Field | Type | Description |
|-------|------|-------------|
| `attestation_type` | enum | `vulnerability_scan`, `compliance_scan`, `sbom`, `test_result`, `signature`, `provenance`, `review_approval` |
| `subject_entity_uuid` | uuid | The entity this attestation is about |
| `predicate_type` | string | in-toto predicate type URI (e.g., `https://slsa.dev/provenance/v1`) |
| `predicate` | object | The attestation content (scan results, SBOM, test outcomes) |
| `attestor` | string | Who/what produced this attestation (tool, person, service) |
| `attestor_identity` | object | Verified identity of the attestor (key fingerprint, OIDC identity) |
| `signature` | string | Cryptographic signature over the attestation |
| `valid_until` | timestamp | Expiry (scans may have a validity window) |
| `verdict` | enum | `pass`, `fail`, `warning`, `info` (for gate-type attestations) |

**Lifecycle:** SUBMITTED (attestation requested) → DELIVERED (attestation produced and signed). Attestations are immutable once produced. A new scan of the same subject produces a new Attestation Entity, not an update to the old one.

**Chain model:** Attestations form a verifiable chain via `subject_entity_uuid` relationships. An artifact's trust level is determined by the set of attestations that reference it — "this image has a passing vulnerability scan, a valid SBOM, a SLSA L3 provenance, and two review approvals." The policy engine evaluates the chain, not individual attestations.

### 3.5 Deployment Entity

A deployment of an artifact to a specific environment.

| Field | Type | Description |
|-------|------|-------------|
| `artifact_entity_uuid` | uuid | The Artifact Entity being deployed |
| `environment` | string | Target environment identifier (e.g., `prod-us-east`, `staging`, `dev`) |
| `environment_classification` | enum | `development`, `staging`, `production`, `dr` |
| `namespace` | string | Kubernetes namespace, cloud project, or equivalent scope |
| `replicas` | integer | Desired replica count |
| `deployment_method` | enum | `rolling`, `blue_green`, `canary`, `recreate` |
| `config` | object | Environment-specific configuration (env vars, secrets refs, resource limits) |
| `health_endpoint` | string | URL for health verification |

**Lifecycle:** SUBMITTED (deployment requested) → VALIDATED (policy checks: sovereignty, attestation chain, environment accreditation) → DEPLOYING (provider executing) → DEPLOYED (running and healthy) → DEPRECATED → DECOMMISSIONED.

**Four-state interpretation for deployments:**
- **Intent:** "deploy image X to prod-us-east with these settings"
- **Requested:** enriched with sovereignty policy (region constraint), hardened with security policy (resource limits, network policy), validated against attestation requirements (all scans pass)
- **Realized:** deployment record — what was actually deployed, with the specific image digest, config hash, replica count
- **Discovered:** what's actually running — pod status, image digests in the runtime, health check results

### 3.6 Promotion Entity

A cross-environment movement of an artifact — the decision record for promoting an artifact from one environment tier to another.

| Field | Type | Description |
|-------|------|-------------|
| `artifact_entity_uuid` | uuid | The artifact being promoted |
| `from_environment` | string | Source environment |
| `to_environment` | string | Target environment |
| `promotion_type` | enum | `automatic` (policy-driven), `manual` (human-approved), `emergency` (override) |
| `required_attestations` | list | Attestation types required before promotion (configurable per environment tier) |
| `approval_chain` | list | Approver identities for manual promotions |

**Lifecycle:** SUBMITTED (promotion requested) → VALIDATED (attestation chain verified, governance matrix checked) → PROMOTED (artifact marked as approved for target environment) → triggers a Deployment Entity in the target environment.

Promotion is explicitly modeled rather than implicit because it's a governance decision — the policy engine determines what attestations are required, the governance matrix determines who can approve, and the audit trail captures the full decision chain.

---

## 4. Resource Type Categories

New categories in the Resource Type Registry, following the existing hierarchy pattern (Category → Resource Type → Resource Type Specification → Provider Catalog Item):

| Category | Resource Types | Example Provider Catalog Items |
|----------|---------------|-------------------------------|
| `SourceCode` | `SourceCode.Repository`, `SourceCode.Commit` | `GitHub.Repository`, `GitLab.Project` |
| `Build` | `Build.ContainerImage`, `Build.Package`, `Build.Binary` | `Tekton.PipelineRun`, `GitHub.ActionsBuild`, `Jenkins.Job` |
| `Artifact` | `Artifact.ContainerImage`, `Artifact.HelmChart`, `Artifact.Package`, `Artifact.Library` | `Quay.Image`, `ArtifactHub.Chart`, `PyPI.Package`, `Maven.Artifact` |
| `Test` | `Test.Suite`, `Test.Result`, `Test.Evidence` | `JUnit.TestRun`, `Cypress.E2E`, `K6.LoadTest` |
| `Scan` | `Scan.Vulnerability`, `Scan.Compliance`, `Scan.SBOM`, `Scan.Secret` | `Trivy.ImageScan`, `Grype.Scan`, `Syft.SBOM`, `Gitleaks.SecretScan` |
| `Signing` | `Signing.Signature`, `Signing.Attestation`, `Signing.Provenance` | `Sigstore.CosignSignature`, `Notary.Notation`, `SLSA.Provenance` |
| `Deployment` | `Deployment.Kubernetes`, `Deployment.VM`, `Deployment.Serverless` | `ArgoCD.Application`, `Flux.HelmRelease`, `Ansible.Playbook` |
| `Promotion` | `Promotion.EnvironmentTier` | `Policy.AutoPromote`, `Approval.ManualGate` |

---

## 5. Event Domains

New event domains in the Event Catalog, using the existing envelope format:

### 5.1 Source Events

| Event Type | Fires when | Urgency |
|------------|-----------|---------|
| `source.commit_pushed` | A new commit is pushed to a tracked repository | low |
| `source.branch_created` | A new branch is created | info |
| `source.tag_created` | A new tag/release is created | low |
| `source.pr_merged` | A pull request is merged (common build trigger) | medium |

### 5.2 Build Events

| Event Type | Fires when | Urgency |
|------------|-----------|---------|
| `build.requested` | A build is submitted to a build provider | low |
| `build.started` | The build provider begins execution | info |
| `build.completed` | Build succeeds, artifacts produced | low |
| `build.failed` | Build fails | medium |
| `build.cached` | Build resolved from cache (no execution needed) | info |

### 5.3 Artifact Events

| Event Type | Fires when | Urgency |
|------------|-----------|---------|
| `artifact.published` | An artifact is pushed to a registry | low |
| `artifact.promoted` | An artifact is approved for a higher environment tier | medium |
| `artifact.quarantined` | An artifact is quarantined due to a failing scan | high |
| `artifact.deprecated` | An artifact is marked deprecated (no new deployments) | medium |
| `artifact.recalled` | An artifact is recalled (existing deployments must be replaced) | critical |

### 5.4 Scan Events

| Event Type | Fires when | Urgency |
|------------|-----------|---------|
| `scan.requested` | A scan is submitted to a scan provider | low |
| `scan.completed` | Scan finishes with results | low |
| `scan.vulnerability_found` | A new vulnerability is found in a previously-scanned artifact | high |
| `scan.vulnerability_cleared` | A previously-found vulnerability is resolved | low |
| `scan.compliance_violation` | A compliance check fails | high |

### 5.5 Signing Events

| Event Type | Fires when | Urgency |
|------------|-----------|---------|
| `signing.requested` | A signing operation is requested | low |
| `signing.completed` | Artifact is signed, signature stored | low |
| `signing.verification_passed` | Signature verification succeeds | info |
| `signing.verification_failed` | Signature verification fails | critical |
| `signing.key_rotated` | A signing key is rotated; affected artifacts flagged | high |

### 5.6 Deployment Events

| Event Type | Fires when | Urgency |
|------------|-----------|---------|
| `deployment.requested` | A deployment is submitted | medium |
| `deployment.validated` | Policy validation passes; deployment approved | low |
| `deployment.rolling_out` | Deployment provider begins rollout | medium |
| `deployment.healthy` | All replicas healthy, health checks passing | low |
| `deployment.unhealthy` | Health checks failing post-deployment | high |
| `deployment.rolled_back` | Deployment rolled back to previous version | high |
| `deployment.decommissioned` | Deployment removed | medium |

### 5.7 Promotion Events

| Event Type | Fires when | Urgency |
|------------|-----------|---------|
| `promotion.requested` | Promotion to a higher environment tier requested | medium |
| `promotion.approved` | Promotion approved (policy or human) | medium |
| `promotion.rejected` | Promotion rejected (missing attestation, policy violation) | medium |
| `promotion.completed` | Artifact successfully promoted and deployed in target environment | low |

---

## 6. Policy Mappings

The eight existing UDLM policy types map to SDLC governance:

| Policy type | SDLC application | Example |
|-------------|-----------------|---------|
| **GateKeeper** | Block delivery stages until conditions are met | "No deployment without a passing vulnerability scan and a valid signature" |
| **Validation** | Verify artifact or attestation correctness | "SBOM must conform to CycloneDX 1.5 schema" / "Test coverage >= 80%" |
| **Transformation** | Enrich artifacts with metadata | "Inject standard OCI labels" / "Add deployment metadata to Helm values" |
| **Recovery** | Handle failures in the delivery pipeline | "On build failure: retry once then notify" / "On scan failure: quarantine artifact" |
| **Orchestration Flow** | Define the delivery pipeline itself | "On source.pr_merged → build → scan → sign → promote(staging) → test → promote(prod) → deploy" |
| **Governance Matrix Rule** | Enforce cross-domain constraints | "Artifacts containing PHI deploy only to HIPAA-accredited environments" / "Emergency hotfixes require dual approval" |
| **Lifecycle** | Cascade lifecycle events | "On artifact.recalled → redeploy all Deployment Entities using this artifact with the latest safe version" |
| **ITSM Action** | Integrate with change management | "On deployment.requested(production) → create ServiceNow change request" |

### The Pipeline as an Orchestration Flow Policy

A CI/CD pipeline IS an Orchestration Flow Policy. The flow definition:

```yaml
policy_type: orchestration_flow
trigger: source.pr_merged
stages:
  - name: build
    provider_capability: realize_resources
    resource_type: Build.ContainerImage
    on_success: scan
    on_failure: notify_team

  - name: scan
    provider_capability: serve_data
    resource_type: Scan.Vulnerability
    on_success: sign
    on_failure: quarantine_artifact

  - name: sign
    provider_capability: authenticate
    resource_type: Signing.Signature
    on_success: promote_staging
    on_failure: alert_security

  - name: promote_staging
    provider_capability: realize_resources
    resource_type: Promotion.EnvironmentTier
    target_environment: staging
    on_success: integration_test
    on_failure: reject_promotion

  - name: integration_test
    provider_capability: serve_data
    resource_type: Test.Result
    on_success: promote_production
    on_failure: rollback_staging

  - name: promote_production
    provider_capability: realize_resources
    resource_type: Promotion.EnvironmentTier
    target_environment: production
    required_approval: governance_matrix
    on_success: deploy
    on_failure: reject_promotion

  - name: deploy
    provider_capability: realize_resources
    resource_type: Deployment.Kubernetes
    on_success: verify_health
    on_failure: rollback_production
```

Each stage triggers the next via data state changes — the build produces an Artifact Entity, which triggers the scan policy, which produces an Attestation Entity, which triggers the signing policy, and so on. The orchestrator (DCM) doesn't know what a container image is or what a vulnerability scan does — it routes data through policies and invokes providers. The domain expertise lives in the providers and the policies, not in the control plane.

---

## 7. Provider Mappings

SDLC tools become UDLM providers by implementing the provider contract. Each declares its capabilities:

| Tool Category | Provider Role | Capability Types | Example Tools |
|---------------|-------------|-----------------|---------------|
| **Source Control** | Source entity creation, webhook triggers | `serve_data` | GitHub, GitLab, Bitbucket |
| **Build Systems** | Artifact production from source | `realize_resources`, `execute_workflows` | Tekton, GitHub Actions, Jenkins, BuildKit |
| **Registries** | Artifact storage, discovery, distribution | `serve_data`, `realize_resources` | Quay, Harbor, Artifactory, GHCR |
| **Scanners** | Vulnerability/compliance analysis, SBOM generation | `serve_data` | Trivy, Grype, Snyk, Syft, Clair |
| **Signing Services** | Cryptographic signing, attestation | `authenticate` | Sigstore/Cosign, Notary, AWS Signer |
| **Test Frameworks** | Test execution, evidence production | `serve_data`, `execute_workflows` | JUnit, Cypress, K6, Selenium |
| **Deployment Tools** | Artifact deployment to environments | `realize_resources`, `execute_workflows` | ArgoCD, Flux, Ansible, Helm |
| **ITSM** | Change management, incident management | `serve_data`, `execute_workflows` | ServiceNow, Jira, PagerDuty |
| **Policy Engines** | Policy evaluation (complementary to DCM's engine) | `serve_data` | OPA/Gatekeeper, Kyverno, Falco |

Each provider implements the universal base contract: registration, health check, sovereignty declaration, accreditation, governance matrix enforcement. The DCM control plane routes to them based on capability matching and policy — the same routing that places a VM on the cheapest qualifying infrastructure provider now places a build on the build system that meets the SLSA requirement.

---

## 8. The Governance Matrix for Software Delivery

The four-axis governance matrix applies directly:

| Axis | Infrastructure example | Software delivery example |
|------|----------------------|--------------------------|
| **Subject** | Who is requesting the VM? | Who triggered the build? Which CI service? |
| **Data** | What classification is this resource? | Does this artifact contain secrets? Is the source repo classified? |
| **Target** | Which data center? What accreditation? | Which registry? Which environment? Is it HIPAA-accredited? |
| **Context** | What compliance domains are active? | Is this an emergency hotfix? What SLSA level is required? |

### Environment Tiers as Sovereignty Zones

Promotion between environments (dev → staging → production) maps to the governance matrix's sovereignty model. Each environment tier has:

- **Accreditation requirements** — what attestations must exist before an artifact enters this tier
- **Access controls** — who can deploy to this tier, who can approve promotions
- **Data classification constraints** — what data classifications are allowed in this tier
- **Audit requirements** — what evidence must be captured for deployments in this tier

Production is a sovereignty zone. Deploying to production requires the same kind of policy evaluation as placing a resource in a sovereign region — the governance matrix checks subject, data, target, and context before allowing the operation.

### Emergency Override Model

Emergency hotfixes use UDLM's existing policy override model:

| Override type | Application |
|---------------|------------|
| **Planned exception** | "This artifact may skip the load test attestation for the next 24 hours — reason: critical security patch" |
| **Exception grant** | "Team X may deploy to production without the standard approval chain until the incident is resolved" |
| **Manual override** | "Bypass the staging promotion gate — dual approval required, compensating control: immediate post-deploy monitoring" |
| **Compensating control** | "Since the vulnerability scan was skipped, add a runtime security monitor for the first 48 hours" |

Every override is audited, time-bounded, and traceable — the same governance rigor applied to infrastructure lifecycle.

---

## 9. DCM as the Orchestrator

DCM's orchestration model is: **event → policy evaluation → provider invocation → new data → repeat**. A software delivery pipeline is exactly this loop.

### The Flow

```
Source push (event)
  → Orchestration Flow Policy matches
    → Build provider invoked (Tekton)
      → Artifact Entity created (REALIZED)
        → GateKeeper policy: "scan required" matches
          → Scan provider invoked (Trivy)
            → Attestation Entity created (REALIZED, verdict: pass)
              → GateKeeper policy: "signature required" matches
                → Signing provider invoked (Cosign)
                  → Attestation Entity created (REALIZED, type: signature)
                    → Orchestration Flow: "promote to staging"
                      → Governance Matrix: staging accreditation check passes
                        → Deployment provider invoked (ArgoCD)
                          → Deployment Entity created (REALIZED)
                            → Discovered state: health checks pass
                              → Orchestration Flow: "promote to production"
                                → Governance Matrix: production requires human approval
                                  → Approval captured (Attestation Entity, type: review_approval)
                                    → Deployment provider invoked (ArgoCD)
                                      → Deployment Entity created (REALIZED)
                                        → Discovered state: healthy
```

Every step produces UDLM Data. Every transition is policy-driven. Every action is auditable. The control plane doesn't know what a container image is — it knows that an entity was realized, a policy matched, and a provider must be invoked. The domain expertise is in the providers and the policies.

### Infrastructure + Software Delivery Unified

When a deployment needs infrastructure (a new cluster, a database, storage), the Deployment provider calls back into DCM to request infrastructure resources — the same way an application-as-a-service provider in the Summit demo called back for its web, app, and database tiers. The dependency graph tracks the relationship: the Deployment Entity depends on the Infrastructure Resource Entities. Decommissioning the infrastructure cascades to the deployments. Rehydrating the infrastructure replays the deployments.

This is the unification: infrastructure lifecycle and software delivery lifecycle share the same data model, the same policy engine, the same governance matrix, the same audit trail. A single query can answer "what is deployed on this infrastructure, who built it, who approved it, what vulnerabilities does it have, and what does it cost?"

---

## 10. Conformance

A realization implementing the Delivery family extensions declares **Conformance with Extensions** per UDLM CONFORMANCE.md §3.3:

1. Full conformance to the UDLM substrate (all universal contracts)
2. Published extensions (Delivery entity types, resource types, events) via the schema-sharing protocol
3. Extensions use only allowed extension points
4. Extensions do not break existing contracts

Any UDLM-conformant peer can read, interpret, and exchange Delivery family data — a DCM instance, a DAV instance, or any future realization.

---

## 11. Implementation Sequence

### Phase 1 — Foundation
1. Define the Delivery family document (following `entities/knowledge-family.md` pattern)
2. Define Source, Artifact, Build, Attestation, Deployment, Promotion entity types with field sets and lifecycle state machines
3. Register resource type categories in the Resource Type Registry
4. Register event domains in the Event Catalog
5. Write conformance test fixtures for the new entity types

### Phase 2 — First Providers
6. Implement a Tekton build provider (Build.ContainerImage)
7. Implement a Quay/registry artifact provider (Artifact.ContainerImage)
8. Implement a Trivy scan provider (Scan.Vulnerability)
9. Implement a Cosign signing provider (Signing.Signature)
10. Wire the Orchestration Flow Policy for a build → scan → sign pipeline

### Phase 3 — Deployment + Promotion
11. Implement an ArgoCD deployment provider (Deployment.Kubernetes)
12. Implement the Promotion entity type with governance matrix integration
13. Implement environment-tier sovereignty zones
14. Wire the full pipeline: source → build → scan → sign → promote(staging) → test → promote(prod) → deploy

### Phase 4 — Ecosystem
15. Implement additional providers (GitHub Actions, Jenkins, Harbor, Grype, Notary, Flux, Helm)
16. Implement ServiceNow ITSM provider for change management integration
17. Implement the attestation chain verification as a GateKeeper policy
18. Build dashboard / observability for the delivery pipeline using UDLM Discovered state

### Phase 5 — LightWell Integration
19. Hardened library repositories as artifact providers
20. Library version compliance as a GateKeeper policy: "all dependencies must come from a trusted source"
21. Trust coverage scoring as a Scan provider: percentage of dependencies from hardened vs public sources
22. Auto-rebuild on library update as a Lifecycle policy: "when a hardened library is updated, trigger rebuild of all consuming artifacts"

---

## 12. What This Enables

When the Delivery family is implemented:

- **One data model for infrastructure + software delivery.** A single UDLM query answers: "What is deployed on this infrastructure, who built it, who approved it, what vulnerabilities does it have, what does it cost, and what does it depend on?"
- **Policy-governed delivery.** Every stage of the pipeline is governed by the same policy engine that governs infrastructure. The CISO's security requirements for deployments are expressed as the same policies that enforce infrastructure sovereignty. No separate governance system for software delivery.
- **Audit from intent to production.** The four-state lifecycle tracks every artifact from "someone requested a build" through "this is what's actually running." Provenance is field-level. Audit is a query, not a reconstruction.
- **Tool-agnostic.** Switch from Tekton to GitHub Actions by changing the provider registration. Switch from Trivy to Grype by changing the scan provider. The pipeline definition (Orchestration Flow Policy) doesn't change — it references capability types, not tool names.
- **AI-enhanced, deterministically governed.** AI can suggest pipeline optimizations, predict build failures, identify vulnerability patterns, and generate attestation summaries. The policy engine retains final authority. The data model makes AI reasoning possible; the governance model keeps it honest.
- **Everything as a service, working together.** Build-as-a-service. Scan-as-a-service. Signing-as-a-service. Deployment-as-a-service. Each team codifies their work into the standardized contracts. The services compose to fulfill whatever delivery intent an organization expresses.

---

*UDLM specification: github.com/dcm-project/udlm*
*DCM reference realization: github.com/dcm-project/dcm*

*This is a design blueprint, not a specification. The entity types, resource types, and events defined here are proposals to be refined through implementation and community feedback — the same process that shaped every other UDLM extension.*
