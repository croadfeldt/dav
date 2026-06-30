# DCM DecisionRecords — draft (Piotr-feedback architecture decisions)

_2026-06-29. Staged for Chris's review (dav/docs/internal, gitignored). These are the prose DecisionRecords for
the architecture decisions made answering Piotr's PR-65 corpus feedback. Each follows the DCM ADR format with the
required **Data · Policy · Provider** lens (ADR-002 / adr/README.md) and is the human-prose form of the adopted
UDLM `DecisionRecord` Knowledge type. **Status: Proposed** (not committed, not PR'd — standing rule)._

## Disposition (new ADR vs amendment) — confirm with Chris before splitting into the dcm PR
| DR | Topic | Target in dcm `architecture/adr/` |
|----|-------|-----------------------------------|
| DR-A | Brownfield ongoing-management (coexistence→cutover) | **Amend ADR-017** (brownfield-greening-discovered-ingestion) |
| DR-B | Profiles as capability sets + approved-list | **New ADR-023** (platform/resource profiles) |
| DR-C | Identity delegation (references, not membership) | **Amend ADR-005** (provider-abstraction) + xref ADR-022 |
| DR-D | Audit verifiability (transparency-log, single-signer v1) | **Amend ADR-010** (audit-tamper-evidence) |
| DR-E | Policy applicability = resolved-profile membership | **Amend ADR-006** (policy-engine) + xref ADR-023 |

> Each DR also requires the **trifecta UCs** (DCM-capability + UDLM-data-model validation) in the DAV corpus —
> listed per DR. Behavioral claim with no backing capability/data-model = a gap, not a pass.

---

## DR-A — Brownfield adoption is coexistence-then-cutover; DCM authoritative after cutover
**Status:** Proposed · **Amends:** ADR-017 · **Relates:** discovery/claim process (#221–228, esp. #225/#226)

**Context.** Piotr (PR-65, `ansible-inventory-brownfield-ingestion`): once an estate is discovered/ingested, do
we keep letting the user manage the old way and sync, or force them onto the DCM API? An open-ended two-way sync
with a competing controller institutionalizes split-brain.

**Decision.** **Coexistence-then-cutover.** During coexistence the legacy controller (e.g. Ansible inventory)
remains authoritative; DCM ingests adopt-in-place (zero resource recreation) and tracks divergence as **drift**,
fully reversible. At **cutover** the DCM API becomes the single authoritative control path. Continued out-of-band
edits after cutover are surfaced as drift and handled by a **policy-configurable action — default reject+alert**
(operators may configure reconcile/tolerate). DCM does **not** perpetually two-way-sync. "Adopt, don't perpetually
federate control." The cutover flip is a **Discovered→Realized adoption/claim** and formally depends on the
discovery/claim process (#225 reverse-placement, #226 claim).

- **Data (UDLM):** ingested entities carry `provenance=brownfield`; per-entity `management_authority`
  (legacy | dcm) flips at cutover; Discovered-state drift records persist; vault-backed credential resources
  replace embedded plaintext.
- **Policy (DCM):** a coexistence policy classifies legacy-origin drift (tolerate | reconcile | reject) by phase
  (coexistence vs post-cutover) and profile; post-cutover out-of-band change = configurable, default reject+alert.
- **Provider:** the config-management system is an **information provider** (discovery) during coexistence;
  post-cutover the owning **service provider** holds the control path.

**Consequences.** Clean ownership transfer; no split-brain; reversible until cutover. Depends on the claim/
reverse-placement mechanics maturing (#225/#226). Credential conversion ties to the trust/credential model.

**Trifecta UCs.** (1) DCM-capability: adopt-in-place + coexistence drift + cutover authority flip + reversibility.
(2) UDLM-data-model: `provenance=brownfield`, `management_authority`, Discovered drift records, credential resources.

---

## DR-B — Profiles are capability sets; platform declares an approved list + default (NEW ADR-023)
**Status:** Proposed · **New ADR-023** · **Relates:** ADR-014 (tenancy), ADR-011 (sovereignty); issues #25,#30,#32

**Context.** Piotr (PR-65, `tenant-onboarding`): clarify tenant↔profile; should it be policy-determined; why an
extra mapping? Underlying ambiguity: "profile" was conflated across the platform/instance and the
tenant/resource altitudes.

**Decision.** A **profile is a capability set** (a config-layer bundle: UDLM layering base/overlay + bound
policy-collection). Comparison of capability sets is **content set-containment**, never a name/rank
(`required ⊆ candidate.enabled`); profiles partially order by ⊆ and need not be totally ordered. Each **DCM
instance declares an _approved list_ of profiles + a _default_** — **no floor/ceiling primitive**; the approved
list is the control surface, and each instance has its own. Profiles are **configurable and combinable** —
operators compose them from capability building blocks; the named `minimal…sovereign` profiles are **presets**
(#32). **Compliance/sovereignty (Sovereignty Zone + Accreditation) is a SEPARATE AXIS composed _with_ the
profile**, not folded into it. **Near-term the platform default applies instance-wide** (one effective profile
per instance); per-tenant override is deferred and validate-need-first (#30). The bespoke tenant↔profile mapping
Piotr flagged is **removed** (binding via DCMGroup `policy_profile`).

- **Data (UDLM):** profile = named/versioned capability set; instance `approved_profiles[]` + `default_profile`;
  tenant = DCMGroup(`tenant_boundary`) resolving to the instance profile (DCMGroup `policy_profile`); compliance
  overlay = Sovereignty Zone + Accreditation (separate). No standalone tenant↔profile map.
- **Policy (DCM):** onboarding binds the tenant to the resolved (default) profile **atomically**; profile
  composition bounded by the instance's real capabilities; per-tenant selection is the deferred override (#30).
- **Provider:** providers/peers express capability sets; comparison by containment.

**Consequences.** One coherent model spanning platform and tenant altitudes; operators express any posture by
composition; differentiation across postures is by **separate instances** near-term. Per-tenant variation
(#25 capabilities, #30 profile override) is future. Cross-org trust of capability *names* requires the shared
vocabulary + certified-set registry (#33/#34) and attestation/trust root (#28).

**Trifecta UCs.** (1) DCM-capability: approved-list+default resolution + **atomic** tenant onboarding.
(2) UDLM-data-model: `approved_profiles[]`, `default_profile`, tenant_boundary + policy_profile DCMGroups,
Sovereignty Zone/Accreditation overlay, quotas, claims.

---

## DR-C — Identity is delegated; DCM holds references, not an authoritative membership store
**Status:** Proposed · **Amends:** ADR-005 (provider-abstraction) · **xref:** ADR-022 (trust model, participate plane)

**Context.** Piotr (PR-65, `auth-provider-drift-detection`): why does DCM cache user/group membership — shouldn't
it delegate to the identity provider? The kinds-vs-capabilities model agrees (auth = a provider *capability*).

**Decision.** DCM **delegates authn/authz resolution to the IdP** (Keycloak/RHSSO) at decision time and does
**not** maintain an authoritative membership cache. It persists only **references** (subject/group IDs, claims
mappings), plus — only where required — an immutable **point-in-time projection** used solely for (a)
tamper-evident audit ("who had access when") and (b) **disconnected/sovereign** operation when the IdP is
unreachable. The drift UC is therefore **split**: a connected-delegation happy path (delegate live → no cache →
no drift) and a disconnected-projection-drift exception. The projection is a **profile-gated capability**
(only instances whose profile includes disconnected/air-gap operation enable it).

- **Data (UDLM):** identity **references** + optional immutable projection (`provenance=observed`); DCM is **not**
  the system of record for membership.
- **Policy (DCM):** governance policy decides when a projection is permitted (disconnected/sovereign) and which
  drift categories auto-remediate vs escalate.
- **Provider:** the auth provider (capability) is the delegation surface; the IdP is authoritative.

**Consequences.** No competing membership store; smaller attack/consistency surface; maps to the trust model's
**participate** plane. Disconnected operation becomes an explicit, profile-gated capability rather than an
always-on cache.

**Trifecta UCs.** (1) DCM-capability: connected delegation (no cache, no drift) — the happy path.
(2) UDLM-data-model: references + optional `provenance=observed` projection; not system-of-record.

---

## DR-D — Audit verifiability = a transparency-log (Merkle) adopted by reference; audit-chain integrity scope
**Status:** Proposed · **Amends:** ADR-010 (audit-tamper-evidence) · **Depends:** trust root #28 · **Follow-up:** witnesses #31

**Context.** Piotr (PR-65, `audit-merkle-tree-verification`): this implies verifying consistency of historical
data — investigate how.

**Decision.** Adopt a **transparency-log / Merkle append-only model by reference** (RFC 6962-style: signed tree
heads + inclusion + consistency proofs) rather than inventing a scheme. Scope it honestly: this proves the
**audit chain is append-only and untampered** — NOT arbitrary reconstruction of historical business/resource
state (that is derivable *from* a proven-intact log, not a separate guarantee). Under sovereign profile, signing
key material stays in-boundary (trust root #28). **Ship single-signer v1** with split-view/equivocation
documented as a known limitation; **external witness validation is a tracked follow-up** (#31), with
**peer-DCM cross-witnessing** as a candidate.

- **Data (UDLM):** Audit Store epochs, signed tree heads, inclusion/consistency proofs as UDLM-modeled,
  append-only artifacts addressable by handle+epoch; proofs are read-only.
- **Policy (DCM):** sovereignty policy pins signing-key residency; retention policy (OPS-006 ≥ P365D);
  profile-governed witness requirement (follow-up).
- **Provider:** an information provider serves tree-heads/proofs; key custody via the trust-broker/attestation plane.

**Consequences.** Provable audit integrity by a standard mechanism; honest scope avoids overclaiming. Split-view
remains until witnesses land (#31). Depends on the trust root (#28).

**Trifecta UCs.** (1) **Output-verification UC** (close the loop): an auditor/DAV independently re-verifies the
produced proofs and detects a tampered/missing event. (2) DCM-capability: signed tree heads + inclusion +
consistency proofs + in-boundary signing. (3) UDLM-data-model: audit event/epoch/tree-head/proof modeled,
append-only.

---

## DR-E — Policy applicability = resolved-profile membership; out-of-scope ≠ skipped-pass
**Status:** Proposed · **Amends:** ADR-006 (policy-engine) · **xref:** ADR-023 (profiles), ADR-014 (tenancy)

**Context.** Piotr (PR-65, `minimal-profile-policy-scope-boundary`): is this related to how policies are
implemented/enforced? The UC asserts FSI-scoped policies must NOT evaluate a minimal-profile request, and the
audit must not record them as skipped-and-passed.

**Decision.** Which policies evaluate a request is determined **by construction** from the request's **resolved
profile** (near-term = the platform default, per DR-B; per-tenant override deferred #30). A policy **fires iff it
is a member of the resolved profile**. Policies not in the resolved profile are **not evaluated** and are **never
recorded as skipped-and-passed** (no silent soft-pass) — "out of scope" is a first-class, audited outcome,
distinct from "passed." Audit lists exactly what was evaluated (three states: pass / fail / out-of-scope). Per
the approved-list model there is **no floor/ceiling**; the platform governs applicability purely via its approved
list + default (any always-on baseline is a non-removable component of how a profile is composed).

- **Data (UDLM):** request record stores the resolved profile + its policy set; audit lists policies-evaluated
  honestly with the three-state outcome.
- **Policy (DCM):** the engine resolves the profile, selects that profile's policies before evaluation; no global
  fall-through; out-of-scope is audited as such.
- **Provider:** unaffected (acts on the post-policy payload).

**Consequences.** Audit honesty (no false-positive soft-passes); applicability is constructive and inspectable;
cleanly separates per-tenant policy *strictness* (already expressible via profile) from per-tenant *capability
enablement* (#25, future).

**Trifecta UCs.** (1) DCM-capability: profile resolution (approved-list+default) + three-state audit outcome.
(2) UDLM-data-model: approved-list, default, resolved-policy-set, three-state audit outcome modeled.

---

## Cross-cutting (applies to all DRs)
- Every behavioral UC closes the **UC ↔ DCM-capability ↔ UDLM-data-model** trifecta; produce→verify loops where a
  UC emits an artifact (DR-D). This is DAV's dual-pipeline purpose: gap analysis → capability roadmap.
- Federation/trust capability-matching (peer floor, capability-vs-request match, trust root, witnesses,
  non-conforming-peer, registries) is tracked as issues #25–34 / enhancements #67–76, extending ADR-022.
- Execution order (on Chris's go): split these into ADR-023 + amendments (subject-scoped, ≤3k) → UC edits/splits +
  trifecta/output UCs + schema doc → RHDH/taxonomy sweep → re-ingest (A/B) → PR replies to Piotr last.
