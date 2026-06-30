# Piotr (pkliczewski) review feedback — decisions & dispositions

_2026-06-29. Source: dcm-project/dcm PR #65 (DAV validation corpus, 9 comments) + PR #64 (capability
taxonomy, 8 comments). Purpose: make the architectural decisions each comment requires, before any UC
re-run. Every architectural decision is recorded as a DecisionRecord (the adopted UDLM Knowledge type)
and decomposed across the **Data · Policy · Provider** triad (DCM ADR-002 / SPEC-DESIGN §29).
NB: line numbers in Piotr's comments are against the PR diff, not current `main` — several #64 items are
already resolved upstream this cycle. Nothing here is posted to the PRs or pushed; this is the decision
artifact for Chris to ratify, then we execute (UC edits → re-ingest A/B; DecisionRecords → dcm; PR replies
on explicit go)._

Legend: **EDIT** = UC text/split (then re-ingest, A/B) · **DR** = architecture DecisionRecord · **DOC** = schema/doc.

### Cross-cutting principle (Chris 2026-06-29): every UC must close the trifecta
For **every** decision/UC below, we don't just assert a behavioral scenario — we ensure the supporting pieces
exist and **work together**: a behavioral UC is backed by **(1) a DCM-capability validation UC** (the DCM
capability that realizes it is present) **and (2) a UDLM-data-model validation UC** (the UDLM entities/fields
that represent it exist). UC ↔ DCM-capability ↔ UDLM-data-model is the trifecta; a behavioral claim with no
backing capability or data model is a gap, not a pass. Where a UC produces an artifact, also add a UC that
**verifies the output** (produce→verify loop, e.g. DR-D). _This is exactly DAV's dual-pipeline purpose:
architecture-gap analysis (does DCM/UDLM support the UC?) feeding the capability roadmap._

---

## Part A — PR #65: DAV validation corpus (9)

### A1 · `schemas/use_case.schema.json` — "describe which properties are used for what" → **DOC**
**Decision:** annotate every property with a draft-07 `description` (and `enum` where it's a controlled
vocab — `profile`, `mode`, `source`, and the six `dimensions.*` which today are bare `type:string`). The
dimension enums should reference the consumer-profile vocabulary (lifecycle_phase, resource_complexity,
policy_complexity [now `single_gating`, not `single_gatekeeper`], provider_landscape, governance_context,
failure_mode). No semantic change — pure documentation + tightening bare strings to enums.
**Execution:** edit the schema file; no re-ingest needed (schema isn't an evaluated UC).

### A2 · `compute/vm-standard-provision` — "covers too much; tenancy in a separate UC" → **EDIT (split)**
**Decision:** Agreed — split. The UC currently bundles (a) standard VM provisioning and (b) data-plane
tenant-isolation enforcement. Split into:
- `compute/vm-standard-provision` — VM allocation in the standard profile, idempotent, audited. Drops the
  tenant-isolation success criteria; keeps `single_gating` policy (basic admission), `single_no_deps`.
- `compute/vm-tenant-isolation-enforcement` (new) — the data-plane tenancy concern: network/storage
  attachments confined to the tenant boundary, isolation policy applied, profile=standard. policy_complexity
  reflects the isolation gating; this is the reusable tenancy building block.
**Rationale:** single-concern UCs are more precisely evaluable and match the corpus-scoping principle.
**Trifecta UCs:** (1) DCM-capability: data-plane tenant isolation on network/storage attachments (the capability
the split-out UC exercises) + idempotent VM provisioning. (2) UDLM-data-model: resource record tenancy fields +
tenant_boundary DCMGroup are modeled.
**Execution:** edit one UC, add one UC (+ trifecta UCs); re-ingest (A/B vs baseline).

### A3 · `cross-domain/ansible-inventory-brownfield-ingestion` — "once discovered, manage old-way+sync, or force DCM API?" → **DR-A**
The UC already encodes coexistence + drift + reversibility; Piotr's question is the *post-cutover* model.
**DR-A — Brownfield ongoing-management model.** _Decision:_ **coexistence-then-cutover, with DCM
authoritative after cutover.** During the coexistence window the legacy inventory remains authoritative and
DCM tracks divergence as **drift** (read-mostly, reversible). At **cutover**, the DCM API becomes the single
authoritative control path; continued out-of-band edits are surfaced as drift to be reconciled or rejected
per policy — DCM does **not** indefinitely two-way-sync with a competing controller (that would institutionalize
split-brain). "Adopt, don't perpetually federate control."
- **Data:** ingested entities carry `provenance=brownfield`; a per-entity `management_authority`
  (legacy | dcm) flips at cutover; Discovered-state drift records persist throughout.
- **Policy:** a coexistence policy classifies legacy-origin drift (tolerate | reconcile | reject) by phase
  (coexistence vs post-cutover) and profile; post-cutover out-of-band change defaults to reject/alert.
- **Provider:** the config-management system acts as an **information provider** (discovery) during
  coexistence; post-cutover the owning **service provider** holds the control path (reverse-placement, #225).
_Ties:_ discovery process backlog #221–228. _UC edit:_ add a success criterion stating the post-cutover
authority flip + that out-of-band edits become reject-class drift.
**Supporting trifecta UCs (required):**
- **DCM-capability UC:** DCM exposes brownfield **adopt-in-place** (ingest hosts/groups/credentials as entities
  with zero resource recreation), coexistence drift detection, the **cutover authority flip**, and reversibility-
  before-cutover — validates the capability exists.
- **UDLM-data-model UC:** `provenance=brownfield`, per-entity `management_authority` (legacy|dcm), Discovered-
  state drift records, DCMGroup mapping, vault-backed credential resources are **UDLM-modeled**.
**Resolved (Chris 2026-06-29):** (1) **EXPLICIT dependency** — the cutover flip is a Discovered→Realized
adoption/claim; DR-A formally **depends on** the discovery/claim process backlog (#221–228, esp. #225
reverse-placement, #226 claim). UC + DR state this dependency. (2) Post-cutover out-of-band response is a
**policy-configurable action** (NOT hardcoded) — **default = reject + alert**; operators may configure
reconcile/tolerate per the coexistence policy. _Carried:_ (3) plaintext→vault credential conversion ties to
the trust/credential model.

### A4 · `cross-domain/sovereign-decommission-with-peer` — "looks like rehydrate; why does sovereign matter?" → **EDIT (clarify) + DR-ref**
**Decision:** Not rehydrate — this is a **peer-coordinated decommission** (two-phase release across DCM
instances + cross-DCM audit stitching). Reframe the description to lead with the *peer-coordination*
mechanic and explicitly contrast with rehydration (rehydrate = replay Intent→new Requested; this = release
both replicas). The **peer-coordination capability is profile-agnostic**; the **sovereign profile is what
*mandates* it** (DR-replica residency + wind-down residency checks). So: generalize the mechanic, keep the
sovereign framing as the *policy that requires* it.
**D·P·P:** _Data:_ resource marked released only with peer-ack metadata; cross-DCM audit record. _Policy:_
sovereignty policy mandates the peer replica + residency check at wind-down; pending-not-silent-complete on
peer unreachable. _Provider:_ `peer_dcm` provider coordinates; service provider releases local.
**Trifecta UCs:** (1) DCM-capability: peer-coordinated two-phase release + cross-DCM audit stitching + hold-on-
peer-unreachable. (2) UDLM-data-model: peer-ack metadata on the released resource + cross-DCM audit record
modeled. _Ties:_ the peer must satisfy the provenance floor (#26) with an in-boundary trust root (#28).
**Execution:** UC text edit (mechanic-first, rehydrate contrast, sovereign-as-mandate); add trifecta UCs;
re-ingest (A/B).

### A5 · `cross-domain/tenant-onboarding` — "clarify tenant↔profile; policy-determined? why extra mapping?" → **DR-B**
**DR-B — Tenant↔Profile relationship.** _Decision (reshaped by the approved-list model, above):_ a **profile
is a capability set** (a config-layer bundle — UDLM layering base/overlay + bound policy-collection). The
**platform declares an approved list of profiles + a default**; **near-term the default applies instance-wide**
(one effective profile per instance — see approved-list section). A **tenant is bound to the instance's resolved
profile** (the default, near-term); **per-tenant override is deferred** (#30/#72, validate-need-first). The
binding is via DCMGroup (`group_class: policy_profile`) — so there is **NO separate bespoke mapping artifact**;
the "extra mapping" Piotr flagged is removed. Posture differentiation across tenants = **separate instances**
near-term.
- **Data:** instance carries `approved_profiles[]` + `default_profile`; tenant = DCMGroup(`tenant_boundary`)
  resolving to the instance profile (a DCMGroup `policy_profile`); no standalone tenant↔profile map.
- **Policy:** onboarding binds the tenant to the resolved (default) profile **atomically**; compliance/
  sovereignty overlays bind through the profile, not per-tenant wiring. (Policy-assisted per-tenant *selection*
  from declared scope is the deferred override #30/#72.)
- **Provider:** auth provider configured with the tenant's claims mapping (delegated — see DR-C).
**Supporting trifecta UCs (required):**
- **DCM-capability UC:** DCM exposes approved-list + default resolution and **atomic** tenant onboarding
  (all-or-nothing: identity boundary + profile binding + quotas + auth claims) — validates the capability.
- **UDLM-data-model UC:** `approved_profiles[]`, `default_profile`, tenant_boundary + policy_profile DCMGroups,
  quota allocations, claims mapping are **UDLM-modeled**.
**Execution:** UC edit (reword "FSI-profile DCM deployment" = the **instance** default profile; tenant binds to
it; drop the implied per-tenant mapping); add trifecta UCs; re-ingest (A/B). Validates
[[project_udlm_layering_customer_validation]].
**Resolved (Chris 2026-06-29):** (1) **compliance/sovereignty overlay = SEPARATE AXIS** — Sovereignty Zone +
Accreditation are their own data-model axis, **composed with** the profile, NOT folded into it. A profile stays
a pure capability set; a tenant's binding = profile (capabilities) **×** compliance/sovereignty overlay
(zone/accreditation). _Carried:_ (2) onboarding atomicity = Orchestration Flow Policy (already in taxonomy).

### A6 · `governance/audit-merkle-tree-verification` — "implies verifying historical-data consistency; investigate how" → **DR-D**
**DR-D — Historical-data integrity / verifiability.** _Decision:_ adopt a **transparency-log / Merkle
append-only model by reference** (RFC 6962-style signed tree heads + inclusion + consistency proofs) rather
than inventing a scheme; the UC stands as the validation target. Verifying "consistency of historical data"
= proving the **audit chain** is append-only and untampered (not re-deriving business state). Under sovereign
profile, signing-key material stays in the sovereignty boundary (already in the UC).
- **Data:** Audit Store epochs with signed tree heads; events addressable by handle+epoch; proofs are
  read-only artifacts.
- **Policy:** sovereignty policy pins signing-key residency; retention policy (OPS-006: Audit ≥ P365D).
- **Provider:** an information provider serves tree-heads/proofs; key custody via the trust-broker /
  attestation plane ([[project_dcm_trust_credential_model]]).
**Execution:** no UC change (it's correct); record DR-D as the "how"; feeds the audit-store spec. Honestly
scope it as **audit-chain** integrity, not arbitrary historical business-state reconstruction.
**ACCEPTED (Chris 2026-06-29) + complete the loop:** add a **UC that verifies the OUTPUT** — an auditor (or
DAV) takes the produced tree heads + inclusion/consistency proofs and **independently re-verifies** them,
asserting the proofs validate and a tampered/missing event is detected (closes the produce→verify loop).
**Supporting trifecta UCs (required — "they all need to work together"):**
- **DCM-capability UC:** DCM exposes the audit-chain capability (signed tree heads, inclusion + consistency
  proofs, in-boundary signing for sovereign) — validates the *capability* exists, not just the scenario.
- **UDLM-data-model UC:** the audit event / epoch / tree-head / proof are **UDLM-modeled** (schema present,
  addressable by handle+epoch, append-only) — validates the *data model* supports it.
- **Resolved (Chris 2026-06-29):** ship **single-signer v1** with split-view/equivocation documented as a known
  limitation; **external witness validation is a tracked follow-up** (croadfeldt/dcm#31 / enhancements#73),
  with **peer-DCM cross-witnessing** as a candidate mechanism. Depends on the trust root (#28).

### A7 · `governance/minimal-profile-policy-scope-boundary` — "related to how policies are implemented/enforced?" → **DR-E (invariant) + EDIT (clarify)**
**Decision:** Yes — it validates the **policy-applicability/binding** layer. Record the invariant.
**DR-E — Policy applicability = resolved-profile membership; out-of-scope ≠ skipped-pass.** Which policies
evaluate a request is determined **by construction** from the request's **resolved profile**; policies not in
that profile are **not evaluated** and are **never recorded as skipped-and-passed** (no silent soft-pass). The
audit record lists exactly what was evaluated.
- **Firing rule (Chris 2026-06-29):** a policy fires iff it is a member of the request's **resolved profile**.
  **Near-term** the resolved profile = the platform **default** (applied instance-wide); **per-tenant override**
  of that selection is deferred (#30/#72). Per the approved-list model (above): **no separate floor/ceiling** —
  the platform governs availability purely by what it **lists** + its default; any always-on baseline is simply
  a profile the platform makes non-removable in how it constructs its approved list. (Earlier
  "floor = DPO-001 system-defaults" framing is **superseded**.)
- **Data:** request record stores the *resolved profile* + its policy set; audit lists policies-evaluated honestly.
- **Policy:** the engine resolves the profile (selection ∈ approved list, else default), selects that profile's
  policies before evaluation; no global fall-through; "out of scope" is a first-class, audited outcome distinct
  from "passed."
- **Provider:** unaffected (providers act on the post-policy payload).
**Supporting trifecta UCs (required — "they all need to work together"):**
- **DCM-capability UC:** DCM exposes profile resolution (approved-list + default + tenant selection) and the
  three-state audit outcome (pass / fail / out-of-scope) — validates the capability exists.
- **UDLM-data-model UC:** approved-list, platform default, tenant selection, and resolved-policy-set are
  **UDLM-modeled** — validates the data model supports it.
**Execution:** UC text edit (validates resolved-profile membership + audit-honesty + a tenant-below-default
case); add the two trifecta UCs; record DR-E; re-ingest (A/B).

### A8 · `identity/auth-provider-drift-detection` — "why cache user/group membership? delegate to IdP" → **DR-C**
Piotr is right; the kinds-vs-capabilities model supports him (auth = a provider *capability*, not a thing
DCM re-owns).
**DR-C — Identity delegation; DCM holds references, not an authoritative membership store.** _Decision:_
DCM **delegates authn/authz resolution to the IdP** (Keycloak/RHSSO) at decision time and does **not**
maintain an authoritative cache of user/group membership. DCM persists only **references** (subject/group
IDs, claims mappings) plus, where required, a **point-in-time projection** used **only** for (a) tamper-evident
audit ("who had access when") and (b) **disconnected/sovereign** operation when the IdP is unreachable. So
the "drift-detection" UC is **reframed and re-scoped**: it is not DCM reconciling a competing cache — it is
detecting that a **disconnected projection** or a **materialized authorization decision** has diverged from
the IdP, which is meaningful chiefly under sovereign/air-gapped and for audit. In a connected prod env, DCM
delegates live (no cache → no drift).
- **Data:** identity **references** + optional immutable point-in-time projection (provenance: observed);
  never the system of record for membership.
- **Policy:** governance policy decides when a projection is permitted (disconnected/sovereign) and which
  drift categories auto-remediate vs escalate.
- **Provider:** auth provider (capability) is the delegation surface to the IdP; IdP is authoritative.
**Execution:** UC edit (re-scope to disconnected/sovereign + audit; drop the "DCM cache as source of truth"
premise; likely move profile prod→sovereign or add a connected-vs-disconnected note); re-ingest (A/B).
Reconcile with [[project_dcm_trust_credential_model]] (uphold/participate/expose planes).
**Supporting trifecta UCs (required):**
- **DCM-capability UC:** DCM **delegates** authn/authz to the IdP at decision time (no authoritative membership
  cache) — the *connected* happy path (delegate live → no drift). This is the capability that proves delegation
  works; the re-scoped drift UC is then the *disconnected/sovereign* exception.
- **UDLM-data-model UC:** identity **references** (subject/group IDs, claims mappings) + optional immutable
  point-in-time **projection** (`provenance=observed`) are **UDLM-modeled**, and DCM is **not** the membership
  system-of-record — validates the data model encodes "reference, not authority."
**Resolved (Chris 2026-06-29, agreed):** (1) **SPLIT** the UC into connected-delegation (happy path = the
DCM-capability UC) + disconnected-projection-drift (the re-scoped original). (2) the disconnected projection is
a **profile-gated capability** — only instances whose profile (capability set) includes disconnected/air-gap
operation enable it (approved-list). (3) maps to the trust model's **participate** plane (DCM participates in,
doesn't own, identity).

### A9 · `observability/udlm-universal-telemetry-export` — "don't see how without writing adapters" → **EDIT (clarify the adapter model)**
**Decision:** Adapters don't vanish — they **invert**. Instead of N per-tool adapters **DCM** writes, DCM
exposes **one** UDLM-modeled, schema-discoverable export surface over **standard transports** (OTLP / Prometheus
scrape / message-bus subscription against the published event catalog). "No bespoke adapters" = **no DCM-side
per-tool integration code**; where a consumer speaks a proprietary protocol, a **thin standard exporter sits
at the consumer edge** (one per protocol, not one per entity type), outside DCM. State this explicitly in the
UC so the claim is honest.
**D·P·P:** _Data:_ telemetry/events/audit as UDLM-modeled, schema-discoverable entities + event catalog.
_Policy:_ per-subscriber authorization + data-classification scoping + retention. _Provider:_ information
provider serves export/discovery; message bus delivers the curated stream. (OBS-002/003, PRV-007.)
**Trifecta UCs:** (1) DCM-capability: single UDLM-modeled export/discovery surface over standard transports
(OTLP/Prometheus/bus) + per-subscriber policy scoping (OBS-002/003, PRV-007). (2) UDLM-data-model: telemetry/
events/audit as UDLM-modeled, schema-discoverable entities + published event catalog. _Output-style check:_ a
**second** consuming tool attaches via the same discovery/subscription path with no export-side change.
**Execution:** UC edit (precise adapter model: uniform surface + standard transports + edge translators);
add trifecta UCs; re-ingest (A/B).

---

## Part B — PR #64: capability taxonomy (8) — mostly already resolved this cycle

| # | Piotr's point (PR-diff line) | Status in current `main` | Action |
|---|------------------------------|--------------------------|--------|
| B1 | L40 "what about placement policy?" | **RESOLVED** — Placement Policy is the 8th Policy type (taxonomy L45, ADR-019). | none (reply: done) |
| B2 | L68 "keep generic, not policy-engine-specific; cost mgmt needs lifecycle events" | **RESOLVED** — Request Orchestrator (L69) is consumer-neutral, explicitly names cost management + drift reconciliation. | none (reply: done) |
| B3 | L140 "remove Backstage; dynamic plugins rejected upstream" | **DECIDED (Chris 2026-06-29): REMOVE RHDH/Backstage ENTIRELY.** | **REMOVE** the whole "RHDH and Backstage Integration Terms" block (taxonomy L136–147: RHDH, DCMService, DCMResource, Software Template, Scaffolder Action, Entity Provider, GUI-011–013) **and sweep all other repo refs** — `docs/specifications/dcm-rhdh-integration-spec.md` (delete), ADR-004, dcm-consumer-gui-spec, DCM-Capabilities-Matrix, dcm-platform-requirements, project-overview, dcm-pattern-catalog-overlay, ADR-016, cncf-strategy, DCM-AI-PROMPT. GUI stays **realization-neutral** (Unified Shell L180 is RHDH-free already — keep). **PatternFly (L146) STAYS** — it's the RH design system/React lib for all DCM GUI, not Backstage-coupled (flag: remove too only if GUI should be fully framework-neutral). Subject-scoped PR; sizable cross-file sweep. |
| B4 | L183 "why this UI + all other UIs?" | **RESOLVED** — "Unified Shell" (L180) = one app, role-gated surfaces; per-surface detail lives in GUI specs (right-altitude), not the taxonomy. | none (reply: unified shell, not multiple apps) |
| B5 | L192 "clarify dual-write/burn-in; why needed" | **RESOLVED** — Dual-Write (L188) + Burn-In (L189) now framed adopt-by-reference (standard store-cutover safety), profile-governed, OPS-002/003. | none (reply: done) |
| B6 | L212 "clarify health/readiness connection" | **RESOLVED** — Liveness/Readiness (L211) adopt-by-reference (K8s probes), HLT-001–006. | none (reply: done) |
| B7 | L215 "use Keycloak terms vs redefine" | **LIKELY RESOLVED** — identity now references Keycloak/RHSSO (L140); no bespoke identity-term redefinition found in current main. | **VERIFY** no redefinition remains; substance governed by **DR-C** (adopt-by-reference identity). |
| B8 | L236 "API backward-compat solved by best practices ([link])" | **LIKELY RESOLVED** — no bespoke API-compat/versioning section found in current main. | **VERIFY** removed; decision = **adopt-by-reference** (semver + deprecation policy), consistent with AEP adoption (#231) + [[feedback_standards_adoption_methodology]]. |

### Adjacent inconsistencies found in current taxonomy (independent of Piotr — worth fixing in the same pass)
- **"8th Policy type" collision:** Placement Policy (L45) **and** ITSM Action Policy (L167) **both** claim to
  be "the 8th." Renumber/reconcile (there are now ≥9 policy outputs).
- **Duplicate "ITSM Integration Terms" sections** (L151 and L162) — overlapping, partly inconsistent. Merge.
- **GUI-011 dynamic-plugin contradiction** (B3 above).

---

## Disposition summary

| Item | Type | Needs Chris? |
|------|------|--------------|
| A1 schema doc | DOC | ratify |
| A2 vm-provision split | EDIT | ratify |
| A3 brownfield mgmt | **DR-A** | ratify decision |
| A4 sovereign-decommission | EDIT + clarify | ratify |
| A5 tenant↔profile | **DR-B** | ratify decision |
| A6 audit historical-integrity | **DR-D** | ratify decision |
| A7 minimal-profile boundary | **DR-E** + EDIT | ratify decision |
| A8 identity delegation | **DR-C** | ratify decision |
| A9 telemetry adapter model | EDIT + clarify | ratify |
| B1,B2,B4,B5,B6 | already resolved | reply "done" (on go) |
| B3 RHDH/GUI-011 | FIX | **decide:** remove vs optionalize |
| B7,B8 | verify + adopt-by-reference | ratify |
| taxonomy inconsistencies | FIX | ratify |

## Execution order (after Chris ratifies)
1. **DecisionRecords A–E** authored into `dcm` (the adopted UDLM DecisionRecord type; subject-scoped PRs, ≤3k).
2. **UC edits/split** (A2,A4,A5,A7,A8,A9) + **schema doc** (A1) in the corpus → **re-ingest, A/B reviewed**.
3. **Taxonomy fixes** (B3 GUI-011 + RHDH-optionalize, 8th-policy renumber, dup ITSM merge) — subject-scoped PR.
4. **PR replies** to Piotr (per-item, Chris's voice, only on explicit go).
5. The re-run then captures the new UCs + dependency fingerprints (deploys the going-forward staleness).

_Standing rules respected: no PR posts/replies, no pushes, no croadfeldt merges without explicit go;
main is protected; DAV stays in positioning. [[feedback_data_policy_provider_triad]],
[[feedback_pr_sizing]], [[feedback_standards_adoption_methodology]]._

---

## Part C — profile / provenance decisions surfaced in this review (Chris, 2026-06-29; FILED as tracking issues)

These came out of the DR-B "two-profile" discussion. **Platform (deployment) profile = uniform ceiling for the
whole instance** (confirmed): resource/policy profiles dial strictness *down* per tenant, but security/auditing
**capabilities** are platform-wide all-or-nothing today. Three forward items were filed in **both** croadfeldt/dcm
(issues enabled on the fork for this) **and** dcm-project/enhancements, cross-linked:

| Item | croadfeldt/dcm | dcm-project/enhancements | Nature |
|------|----------------|--------------------------|--------|
| **C1 · Per-tenant capability enablement** (future) — scope security/auditing capabilities per tenant, not only platform-wide | #25 | #67 | future research |
| **C2 · Peer-DCM provenance floor** — peer DCMs must **express (attested) profile + audit/observe/logging/etc capabilities** so DCM verifies **end-to-end provenance per the required profile**; shortfall → refuse/hold, never silent-complete; `peer_profile ≥ required` (monotonic across hops) | #26 | #68 | requirement |
| **C3 · Capability-vs-request match at selection** — express platform/provider/peer profile + audit/observation/tracking capabilities and **match to each request's needs**; **mismatch → hard-exclude (default), policy-gated consented-downgrade exception, NEVER for compliance-class**. C2 is the peer-DCM special case | #27 | #69 | requirement (mismatch DECIDED) |
| **C4 · Trust root** (TBD — stays open by design) — pure attestation vs technical validation vs both; sovereign root in-boundary | #28 | #70 | foundational TBD |
| **C9 · External witness validation** (DR-D follow-up) — defeat split-view/equivocation; peer-DCM cross-witnessing candidate; single-signer v1 ships with gap documented | #31 | #73 | follow-up |
| **C5 · Governed acceptance of a non-conforming peer profile** (future) — inverse of C2; explicit-consent assurance downgrade, never silent | #29 | #71 | future capability |
| **C8 · Per-tenant profile override** (future, validate-need-first) — tenant selects a profile ≠ platform default; near-term default applies instance-wide | #30 | #72 | future / validation |
| **C6 · Common capability vocabulary** + **C7 · Certified capability-set registry** | _NOT FILED_ | _NOT FILED_ | guard-blocked; awaiting Chris confirm |

**All three tie to attestation & trust** ([[project_dcm_trust_credential_model]] — uphold/participate/expose
planes, attestation-gated, profile-keyed homelab→sovereign): expressed capabilities must be **attested, not
self-asserted**, to be usable for a federation/placement decision.

### Profile model — DECIDED (Chris, 2026-06-29): capability SETS, not hierarchical rank
A **profile is a capability set** (a named preset over the capability vocabulary), **not** a point on a
`minimal<…<sovereign` ladder. All matching is **set containment**, evaluated per-capability:
- **Eligibility:** a candidate (provider or peer) satisfies a need iff `required_caps ⊆ candidate.attested_enabled_caps`.
- **Profiles partially order by ⊆**, but are **not** required to be totally ordered — two profiles can be
  **incomparable** (each carries capabilities the other lacks). Named profiles are optional **presets**/bundles
  for UX; the engine compares sets, not labels.
- **Reframes the items:** C2 peer floor = `transaction.required_caps ⊆ peer.attested_enabled_caps` per hop (not
  `≥` rank). C3 selection = the same containment over providers + peers. C1 (#25) = scoping *which subset* of the
  enabled set applies per tenant.
- This **resolves open-question (a)** below (it was: total order vs lattice → **set-containment lattice**).

#### Profile availability = APPROVED LIST, not floor/ceiling/levels (DECIDED, Chris 2026-06-29)
Supersedes the earlier floor/ceiling framing. **Each DCM instance declares an _approved list_ of profiles it
offers + a _default_ from that list.** **No floor/ceiling primitive** — the approved list IS the control
surface. Each DCM instance has its **own** approved list + default.
- A **profile is a capability set** (above); the approved list is the set of profiles the instance can actually
  support (capability-bounded) and chooses to offer.
- **Profiles are CONFIGURABLE + COMBINABLE (decided, Chris 2026-06-29):** profiles are **platform capability
  sets** that are **selectable in sets and customizable** — the platform **composes** its profiles from
  capability building blocks rather than picking from a fixed `minimal…sovereign` enum. The named profiles
  become **default/example presets**; an operator can define/combine custom profiles (set algebra over the
  capability vocabulary). This is the proper model. _Strongly anticipated:_ **per-tenant** custom sets — left
  for now (the deferred per-tenant override, #30/#72).
- **NEAR-TERM (decided, Chris 2026-06-29): the platform DEFAULT applies instance-wide — one effective profile
  per instance** (effectively the platform default is the *only* profile in use for the time being).
  Differentiation across postures is by **separate instances** (each with its own approved list + default).
- **FUTURE — tenant profile override** (tenant selects a profile other than the default, mixed-profile tenancy
  within one instance) → moved to future state, **validate-the-need first**: croadfeldt/dcm#30 /
  dcm-project/enhancements#72. Related to per-tenant capability enablement (#25/#67).
- **DR-B restated:** platform = the **approved-list + default** (applied instance-wide near-term). Drops the
  bespoke tenant↔profile mapping. Per-tenant selection is the deferred override (#30/#72).
- **Peer acceptance** of a profile not on the local approved list (non-conforming) → **future capability**,
  filed: croadfeldt/dcm#29 / dcm-project/enhancements#71 (governed, explicit-consent assurance downgrade).

#### Comparison is by CONTENT, not name (Chris, 2026-06-29)
A capability-set **name** is meaningless across a trust boundary — only its **content** is. Three stacked layers:
1. **Common capability vocabulary (prerequisite):** capability identifiers must come from a **shared, versioned
   registry** so "content" is comparable across parties (a capability means the same everywhere). Without it,
   neither content nor name comparison is sound. (Natural extension of the Resource Type Registry tiering:
   core/community/organization.)
2. **Content (structural) comparison = ground truth:** containment is over actual content —
   `required_content ⊆ candidate.attested_content` — never the label.
3. **Certified capability sets (optional name shorthand):** a registered, versioned set (`name@version → fixed
   content`) with a **conformance certification against a common reference**. Attesting *certification to a
   reference set* lets the name stand in for content — valid **only** because the cert binds name→content
   commonly. **Even then, a verifier MAY re-verify content** (profile/risk-governed: sovereign re-verifies;
   lower profiles accept the cert). Attestation takes either form (enumerated content **or** certification-to-
   reference-set); verifier policy decides sufficiency. Defense-in-depth — a cert never fully substitutes for
   content at high assurance.
- **New registry concerns surfaced (candidate tracking items, not yet filed):** (i) common capability
  vocabulary; (ii) certified capability-set registry + conformance. Both extend the Resource Type Registry +
  the trust/attestation model; strong cross-org/OSAC angle (how independent vendors trust claims without
  trusting labels). _Ask Chris whether to file these like #25/#26/#27._

#### Trust root — TBD (foundational takeaway, Chris 2026-06-29)
Every capability/profile verification + attestation above must terminate in a **root of trust**, or "attested"
is circular (a claim signed by whoever's key we happen to hold). **Open: what anchors it?**
- **Pure attestation (assertion-based):** a trusted authority *signs the claim*. Roots in a PKI / conformance-
  body / federation signing hierarchy (x509 chains, Sigstore, in-toto/SLSA, a "certified-X" mark). Scales
  cross-org; the only way to anchor **process/organizational** capabilities. Weakness: asserts the property
  held *at certification time*; trusts issuer diligence + key custody — not proof of current running state.
- **Technical validation (measurement-based):** *measure/prove* the live system. Roots in hardware/measured
  boot — TPM remote attestation, confidential computing / TEE (SEV-SNP, TDX), reproducible builds + transparency
  logs. Proves *actual current state*; hard to forge. Weakness: roots in a silicon/hardware **vendor** (trust
  shifts to them); uneven deployability (homelab may lack TEE); can't cover process/organizational capabilities.
- **Both / layered (leaning, NOT decided):** technical validation anchors **substrate** caps (in-boundary key
  custody, measured config, TEE); attestation anchors **process/organizational** caps (conformance,
  accreditation); strongest when an attestation *embeds* technical-validation evidence. **The required
  trust-root strength is itself profile-governed** — i.e. it's part of the capability set (homelab may accept
  self-signed/pure attestation within its boundary; sovereign requires TEE-backed validation **and** an
  accredited authority).
- **Sovereignty constraint:** for sovereign assurance the **root of trust itself must be within the
  sovereignty boundary** — cannot anchor in a foreign CA or foreign silicon vendor's attestation service.
  → strong DAV validation UC (root-of-trust outside boundary → sovereign request refused).
- **OSS/RH-aligned candidates:** Keylime (remote attestation, CNCF), confidential containers, Sigstore
  (signing/transparency) — per [[feedback_oss_redhat_preference]].
- **Status: TBD.** Foundational to [[project_dcm_trust_credential_model]] — the missing anchor beneath
  uphold/participate/expose + attestation-gating. Decide: pure attestation vs technical validation vs both,
  and where each root terminates per profile. _Track as an item? (not yet filed)_

> **PENDING on the filed issues:** #26/#68 and #27/#69 bodies still carry the superseded `≥`-ladder wording.
> Clarifying comments were drafted to correct them to the set-containment model but **NOT posted** (await Chris's
> go — posting under his identity needs per-item approval). Draft text is staged in the session scratchpad.

**Open semantics — RESOLVED (Chris 2026-06-29):**
- ~~(a) total order vs lattice~~ → **capability-set containment** (above).
- ~~(b) C3 mismatch~~ → **hard-exclude is the DEFAULT (fail-safe)**; a **policy-gated, explicitly-consented
  downgrade** is allowed as an exception — **NEVER for compliance-class** capabilities (sovereign residency,
  etc. are un-waivable). Same shape as DR-A (default + policy-configurable exception) and the peer
  non-conforming-acceptance issue (#29/#71 = this exception applied to peers).
- (c) C2/C3 still suggest a **new DAV validation UC** (peer/provider lacking a required capability → request
  refused with provenance preserved; consented-downgrade only via policy, audited, never for compliance).

_These extend the federation/placement model; they land in the dcm spec (registration + placement + trust)
on Chris's go, alongside DR-A…DR-E. Not pushed; issues are the tracking surface._
