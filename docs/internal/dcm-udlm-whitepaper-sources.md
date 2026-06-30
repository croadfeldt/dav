# DCM/UDLM Whitepapers — Sources & Provenance

_How both whitepapers were built, what every load-bearing fact is grounded in, and exactly how to regenerate that
grounding — so the papers can be **recreated from scratch** or **modified with confidence**. Internal, living doc.
`docs/internal/` is gitignored; these artifacts stay local. Last updated 2026-06-18._

> Conventions used in the papers (keep them when editing): facts are **snapshot-dated + reproducible**; DCM is the
> **reference implementation, not the product**; the model is **realization-neutral**; customer specifics are
> **internal-only** and must be genericized for any public version. The through-line thesis is in §6 below.

---

## 1. The artifacts (what exists, where)

**Whitepaper #1 — "Infrastructure Lifecycle Without a Common Language"** (the *why*: argues for a common
infrastructure-lifecycle data model).
- Canonical source: `docs/internal/dcm-udlm-whitepaper.md`
- Companions: `dcm-udlm-executive-brief.md` (vision + partner CTA), `dcm-udlm-thesis.md`,
  `dcm-udlm-whitepaper-public.md` (genericized projection), `dcm-udlm-whitepaper-sessionB-original.md` (the
  pre-merge original; kept for provenance — see the merge note in the plan file referenced in §7).
- Review loop (in `~/`, not the repo): `dcm-udlm-whitepaper.md.docx` (Chris's docx carrying 15 reviewer comments),
  `dcm-udlm-whitepaper-CHANGES.md` (the cherry-pickable OLD→NEW change-list + figure placements). See §5.

**Whitepaper #2 — "Running the Data Center on a Common Model"** (the *how*: how DCM/UDLM operationalize running a
data center — every resource, every stakeholder, end to end).
- Canonical source: `docs/internal/dcm-udlm-operations-whitepaper.md` (7 sections; companion to #1, lifecycle-led).
- Built 2026-06-18; grounding in §3 + §4 below.

**Figures (8, in `~/`)** — `dcm-fig-*.svg` (drop-in SVG for Word) + two Mermaid sources
`~/dcm-sourcing-method.mmd`, `~/dcm-udlm-lifecycle.mmd`. See §5.

**Reconciliation map** — `~/dcm-project-croadfeldt-reconciliation.md` (what to combine upstream↔croadfeldt; informs
both papers' accuracy). See §4.3.

---

## 2. Source repositories

Two layers. **Don't confuse them** — this is the single most important provenance fact.

| Layer | What it is | Where |
|---|---|---|
| **UDLM** | the realization-neutral substrate **spec** (entity types, four states, contracts, identifiers, events) | upstream `github.com/dcm-project/udlm` · working `github.com/croadfeldt/udlm` |
| **DCM** | the reference **runtime** that realizes UDLM | upstream `github.com/dcm-project/dcm` + `control-plane` + provider repos · working `github.com/croadfeldt/dcm` |
| **DAV** | the Data Architecture Validator — holds the customer use-case corpus + evaluates it against DCM/UDLM | `github.com/croadfeldt/dav` (this repo) |

**Two caveats that any rebuild MUST honor** (they were wrong in older drafts and corrected on 2026-06-18):
1. **DCM is a control-plane monolith now.** The four managers (`catalog-manager`, `placement-manager`,
   `policy-manager`, `service-provider-manager`, `api-gateway`) were **merged into `dcm-project/control-plane`**
   (May 2026) and those repos are **archived**. Any "four managers over HTTP" diagram is historical. (croadfeldt/dcm
   README was fixed for this on its own branch — see croadfeldt/dcm PR #6.)
2. **`dcm-project/udlm` `main` is empty.** All substrate content lives on feature branches **`u/u1`…`u/u15`**
   (foundations → wire contracts → events → provider/policy → entities → governance → lifecycle → observability →
   topology). Cite branch refs until they merge.

---

## 3. Whitepaper #2 — grounding by section (the one most recently built)

Each section's load-bearing facts and where they came from. Paths are dcm-project unless noted.

- **§1 Problem / §2 Operating model** — the intent-based service model thesis (§6 here) + the four-state lifecycle
  (`udlm foundations/four-states.md`, branch `u/u3`). No external facts; argues from the thesis.
- **§3 Runtime** — `enhancements/.../control-plane-monolith/control-plane-monolith.md` (the consolidation);
  `enhancements/.../declarative-api/declarative-api.md` (the end-to-end CatalogItemInstance → catalog → placement
  (CEL+DAG) → policy (per-resource OPA) → sp → provider → NATS flow); `control-plane/README.md` +
  `control-plane/internal/{catalog,placement,policy,sp}/`. Four-state immutability (REVOKE UPDATE/DELETE + RLS):
  `udlm foundations/four-states.md`.
- **§4 Taxonomy & seams** — 4-level hierarchy + categories: `udlm entities/resource-type-hierarchy.md` (`u/u8`);
  unified provider contract + 5-capability vocabulary + `GET /api/v1/capabilities`: `udlm contracts/provider-contract.md`
  + `contracts/capability-discovery.md` (`u/u6`); data-driven policy contract: `udlm contracts/policy-contract.md`
  (`u/u6`) + `enhancements/.../policy-engine/policy-engine.md` (embedded OPA/Rego); service types VM/Container/
  Database/Cluster: `control-plane/api/catalog/v1alpha1/servicetypes/*`.
- **§5 Operating concerns** — composite/DAG: `udlm entities/composite-service-model.md` (`u/u10`); drift:
  `udlm foundations/four-states.md`; sovereignty + 9-layer topology: `udlm topology/location-topology-layers.md`
  (`u/u15`); cost adopted via Koku/FOCUS: dcm `enhancements` **PR #57** (cost SP) + **PR #60** (cost service type);
  audit chain: `udlm observability/universal-audit.md` (`u/u14`); rehydration/DR:
  `enhancements/.../rehydration-flow/rehydration-flow.md` + `udlm lifecycle/operational-models.md` (`u/u13`).
- **§6 In practice** — the **DAV use-case corpus** (§4 below for the exact extraction).
- **§7 Where this stands** — flagship demo: `dcm-project.github.io/content/blog/sovereignty-rehydrate-demo/index.md`;
  real providers: `acm-cluster-service-provider`, `kubevirt-service-provider`, `k8s-container-service-provider`;
  `quadlet-deploy` (podman quadlet kit). Maturity ledger (real vs spec vs archived) in the reconciliation map §6.

---

## 4. How to regenerate the grounding

### 4.1 The DAV use-case corpus (grounds whitepaper #2 §6, and #1's evidence numbers)

- **Where:** the deployed DAV Postgres, pod `dav-review-db` in namespace `dav`. **App DB is `dav_review`** — NOT the
  pod's `$POSTGRES_DB` (which is `postgres` and empty). Use-case table is **`managed_use_cases`**; the UC body is
  YAML in the `yaml_content` column (domain/persona/profile/lifecycle live there, extracted via regex on that text).
  The `uc_*` tables hold analysis output (gaps/analyses), `themes` is empty (themes computed on demand).
- **Snapshot (2026-06-18):** 74 operational DCM UCs (+7 DAV product self-tests, excluded). Lifecycle phases:
  new_request 58 · drift 9 · modification 5 · rehydration 5 · brownfield 3 · decommission 1. Governance:
  compliance_gated 25 · audit_heavy 10 · sovereignty_enforced 3. Gap severities (uc_gaps): moderate 1021 · major 121
  · minor 106 · advisory 80 · critical 15.
- **Re-extract** (read-only; paraphrase customer content, never reveal identifiers):
  ```bash
  oc exec -n dav deploy/dav-review-db -- bash -lc \
    'psql -U "$POSTGRES_USER" -d dav_review -c "<SQL>"'
  # tables: \dt ; UC body: SELECT yaml_content FROM managed_use_cases ;
  # lifecycle dist: regex substring on yaml_content for scenario.dimensions.lifecycle_phase
  ```
- **Themes / priority tiers** are NOT stored — they're computed live by the roadmap API. To get the 11 themes / 3
  tiers, call `GET /api/analysis/roadmap?group_by=theme` against the running DAV, don't query the DB.

### 4.2 The dcm-project runtime survey (grounds #2 §3–§5, §7)

Re-run with `gh` (read-only): `gh repo list dcm-project --limit 50`; tree via
`gh api repos/dcm-project/<repo>/git/trees/HEAD?recursive=1 --jq '.tree[].path'`; file via
`gh api -H "Accept: application/vnd.github.raw" repos/<o>/<r>/contents/<path>`. For udlm, list branches and read from
`u/u*` (main is empty). Verify archived state: `gh repo view dcm-project/<repo> --json isArchived`.

### 4.3 The reconciliation map

`~/dcm-project-croadfeldt-reconciliation.md` — the two-directional "what to combine" analysis (croadfeldt→upstream:
intent thesis, adopt-by-reference, cost→FOCUS, provenance; upstream→croadfeldt: monolith reality, capability vocab,
4-level taxonomy). Keeps both papers factually aligned with the live repos.

---

## 5. Figures & the #1 review loop

**Figures** (hand-authored SVG; validate with `python3 -c "import xml.dom.minidom as M; M.parse('f.svg')"`):
`dcm-fig-sourcing-method` · `-udlm-lifecycle` · `-four-state-lifecycle` · `-pluggable-architecture` · `-landscape`
· `-broken-closed-loop` · `-evidence` · `-adoption`. Two have Mermaid sources (`~/dcm-sourcing-method.mmd`,
`~/dcm-udlm-lifecycle.mmd`) — edit the `.mmd`, re-render, then hand-touch the SVG. Placements + captions are in
`~/dcm-udlm-whitepaper-CHANGES.md` (FIGURES section). The four-state figure encodes **stages-as-acts**
(intent/request/realize/discover) → **stores** (Intent/Requested/Realized/Discovered); the acts framing is
**whitepaper-only**, deliberately NOT in the spec.

**Whitepaper #1 review loop:** reviewer left **15 comments** in `~/dcm-udlm-whitepaper.md.docx`. Chris edits the
**docx in place** to preserve the comments (do NOT overwrite the docx from the .md). The actionable edits live as a
cherry-pickable OLD→NEW list in `~/dcm-udlm-whitepaper-CHANGES.md`. Open item: figure says store "**Request**",
spec enum says "**Requested**" — not reconciled (spec form is lower-risk).

---

## 6. The thesis & decisions (the *why* — don't re-derive)

The settled through-line both papers project from (full detail in the memory files named below):
- **Intent-based service model: the declared OUTCOME *is* the intent.** Orgs declare outcomes; the system deploys
  them; methods are the platform's concern. Maps to UDLM's Intent state.
- **Adopt external standards by reference, never absorb** (tenet T5; ADS-001..010; ADR-017). Tier-1 value/codelist
  = field constraint; Tier-2 record/schema (FOCUS, OSCAL, SCIM) = full apparatus.
- **Cost retired from the registry** → adopted via FOCUS v1.4 + OpenCost (Information/cost-recovery provider).
- **UDLM is pre-1.0** (`udlm/0.1`, type versions `0.1.0`); 1.0 is the earned-stability milestone.
- **Process defaults:** subject-scoped PRs (one subject, ≤2–3k lines) + document-the-why (ADRs/design notes).

Recorded in this account's memory (the durable "why"): `project_udlm_intent_adopt`, `project_dcm_udlm_whitepaper`
(carries the #2 build facts + the DB/table/branch gotchas), `project_dcm_at_home`, `project_osac`. The combining
plan is the plan file `declarative-jumping-robin.md` (UDLM-definition resolution, audience decisions, fact-verify list).

---

## 7. Playbooks

**To RECREATE a whitepaper from scratch:** (1) read §6 + the memory files for the thesis; (2) re-survey dcm-project
per §4.2 (mind the two caveats in §2); (3) re-extract the DAV corpus per §4.1 and re-date the snapshot; (4) follow
the section→source map in §3; (5) keep the conventions in the header banner; (6) regenerate figures from §5.

**To MODIFY a whitepaper:** edit the canonical `.md` in `docs/internal/`. If a load-bearing fact changes, update its
source citation here too. If a number changes (gap counts, UC counts, named criticals), re-pull per §4.1 and
**re-date the snapshot line** in the paper. Keep #1 and #2 consistent with each other and with the executive brief.
For #1's reviewed copy, hand the change to the docx (preserve comments) — don't regenerate the docx from the .md.

**Re-verify before any external/public use:** the UC/gap counts + named criticals (moving target — re-pull),
the dcm-project specifics (branch refs change as `u/u*` merge), and genericize all customer-derived specifics.

---

## 8. Complete repository inventory

**dcm-project (upstream).** Real implementations: `control-plane` (the live monolith — catalog/placement/policy/sp
in-process, embedded OPA, Postgres, NATS consumer), `cli`, `acm-cluster-service-provider`,
`kubevirt-service-provider`, `k8s-container-service-provider`, `three-tier-app-demo-service-provider`,
`quadlet-deploy` (podman-quadlet deploy kit), `shared-workflows` (reusable GH Actions),
`dcm-project.github.io` (Hugo docs site + the sovereignty-rehydrate demo blog). Spec/proposal: `udlm` (substrate,
on `u/u*` branches), `dcm` (HLD/ADR/taxonomy), `enhancements` (design proposals — open: #57 cost SP, #60 cost
service type, #55 composite catalog item; merged: #33 dependsOn). **Archived (do not cite as current):**
`api-gateway`, `catalog-manager`, `placement-manager`, `policy-manager`, `service-provider-manager`, plus older
`*-archived` repos.

**croadfeldt (working).** `croadfeldt/dcm` (branches: `docs/data-policy-boundary` = PR #5;
`docs/control-plane-runtime-topology` = PR #6 README fix; remotes: `origin`=croadfeldt, `dcmproject`=upstream),
`croadfeldt/udlm` (branch `feat/resource-type-registry` = PR #1), `croadfeldt/dav` (this repo; whitepaper branch
work under `feat/dcm-uc-prioritization`). Evaluation comments posted on dcm-project/enhancements #57 + #60.

**Day-0 / bootstrap (heatmiser).** `heatmiser/dcm-bootstrap` — a **RHEL image-mode (bootc) appliance** that stands
up DCM/OpenShift in **disconnected or air-gapped** sites: an immutable image with DNS, DHCP, TFTP, an image
registry, and a content mirror baked in as **podman-quadlet** services, built connected, transported to the field,
and booted with site config injected at first boot from a config drive (renderer EE + vault). Uses containerized
Ansible **Execution Environments** (no host venvs) from the companion `heatmiser/ee-builds`; installs clusters via
`rhvp.ocp_landing_zone`; physically-bound images for disconnected anaconda-ISO. Key docs in-repo:
`docs/architectural-decision-podman-quadlets.md`, `docs/bootc-image-mode-recommended-practices.md`. This is the
foundation **below** the runtime — it brings up the substrate the DCM control plane + providers run on. Cited in
the operational whitepaper §5 (sovereignty/day-0) and §7 (what runs). Snapshot: last pushed 2026-06-15, active.

---

## 9. Additional source documents (context inputs)

Beyond the repos + corpus, these `docs/internal/` files informed framing/evidence (customer-derived → internal only):
`2026-05-19-barclays-dcm-followup-takeaways.md`, `2026-05-28-pnc-exec-summary.md`,
`2026-06-02-dcm-cost-mgmt-meeting-takeaways.md`, `udlm-sdlc-customer-blueprint.md`,
`udlm-sdlc-extension-blueprint.md`. The combining-plan + open decisions live in the plan file
`declarative-jumping-robin.md` (referenced in §6). External proxy stat used in #1 comment #2: **Stripe, "The
Developer Coefficient" (2018)** — ~$300B/yr lost global GDP, ~$85B/yr maintaining bad code (no duplication-specific
stat exists; this is the stand-in for "duplicated engineering cost").

---

## 10. Production method & strategy (how the papers were actually built)

The whitepapers were produced with AI assistance (Claude/Opus, this account). The reusable method:

**Whitepaper #2 (operational) — the strategy that worked:**
1. **Thesis-stable spine first.** Draft the sections that don't depend on freshly-gathered data (problem, operating
   model, the honest-current-state framing) from the settled thesis (§6) — so the structure is fixed before facts land.
2. **Parallel background research agents.** Dispatch two independent agents concurrently (prompts in §11): one
   surveys the dcm-project runtime, one extracts the DAV corpus from the live DB. Run them in the background and keep
   drafting the spine while they work.
3. **Ground section-by-section as agents return.** Map each section to its sources (the §3 map), fill `⟦GROUND⟧`
   placeholders with cited facts, and **correct the draft against what the survey actually found** (this is how the
   "four managers" error became the control-plane-monolith note — the spine had described separate services).
4. **Honest maturity close.** End with what runs vs what is spec/proposal, named plainly. No deployed-scale claims.
5. **Verify coherence end-to-end**, then store provenance (this doc) + memory.

**Whitepaper #1 (revision) — the delivery model that worked:** the reviewed copy lives in a **docx with comments**,
so revisions are delivered as a **cherry-pickable OLD→NEW change-list** (`~/...-CHANGES.md`) that Chris applies into
the docx in place — NOT as silent edits to the `.md` (which would lose the comments). Figures delivered as drop-in
SVG with placements/captions in the same change-list.

**Cross-cutting strategies:** ground every load-bearing claim in a citable source (repo path + branch, or a
reproducible DB query); keep DCM framed as the reference implementation; snapshot-date all moving numbers; respect
subject-scoped commits when the work touches the repos; genericize customer specifics for any public projection.

---

## 11. The research-agent prompts (verbatim intent — reusable)

These are the prompts used to gather #2's grounding; re-dispatch (updating dates) to refresh it.

**Agent A — "Survey dcm-project repos":**
> Survey the **dcm-project** GitHub org repos and return a tight digest of what's usable for an OPERATIONAL
> whitepaper: "how DCM and UDLM operationalize managing a data center and all its resources." Read-only. Use `gh`
> (`repo list`; `git/trees/HEAD?recursive=1` for structure; raw `contents` for files). Cover dcm, udlm, enhancements,
> the managers, the providers (acm/kubevirt/k8s-container/three-tier-demo), quadlet-deploy, shared-workflows, cli.
> Return a structured digest: (1) DCM runtime architecture + end-to-end request flow; (2) provider ecosystem
> (registration/capabilities/naturalize); (3) resource taxonomy + capability domains; (4) operational-concern
> coverage map (governance/placement/composition/drift/sovereignty/cost/audit/decommission/DR) with the repo/spec
> per concern; (5) examples/demos; (6) maturity (real vs spec vs archived). Cite repo/path for load-bearing facts.

**Agent B — "Extract DAV use cases from DB":**
> Extract a grounding digest of DAV's customer use cases from the deployed DB. Read-only SELECTs only; no secrets;
> **paraphrase** (NDA — no customer identifiers). Access via `oc exec -n dav deploy/dav-review-db -- bash -lc
> 'psql -U "$POSTGRES_USER" -d <db> -c "<SQL>"'`. Find the real UC table (NOT `managed_use_cases`-assumption — verify;
> app DB is `dav_review`, not the pod's `$POSTGRES_DB`). Return: (a) total UC count + per-project; (b) distribution
> by domain/persona/profile/lifecycle_phase; (c) capability themes + priority tiers + gap severities if present;
> (d) ~15–20 representative concrete operational scenarios (one-line paraphrased intent + domain/lifecycle) spanning
> provisioning, governance, network, cost, confidential-compute, drift/remediation, cross-domain, sovereignty,
> identity, decommission. Favor operational scenarios over test/baseline UCs.

_(Both were run as concurrent background agents; their digests are summarized in memory `project_dcm_udlm_whitepaper`
and were the direct source for #2 §3–§7.)_

---

## 12. Decisions log (shaping feedback that's baked into the text)

Resolved during authoring — keep these when editing (full "why" in the memory files §6):
- **UDLM definition (keystone):** UDLM = Universal Data **Lifecycle** Model (infra-lifecycle data contract), NOT
  "capability/maturity model." Capability/maturity is a layer **DAV performs over** UDLM data.
- **Vision/CTA out of the whitepaper** → moved to the executive brief. Whitepaper stays evidence-led + restrained
  (CNCF/community readers are allergic to marketing).
- **Four-state framing:** stores are the nouns (Intent · Requested · Realized · Discovered); the **acts**
  (intent/request/realize/discover) are a whitepaper-only teaching device, **deliberately NOT in the spec**.
  "Request" act = *the act of sending the policy-derived outcome request to a provider.*
- **#1 comment resolutions:** #2 → Stripe Developer-Coefficient proxy stat (§9); #10 → DCM-as-scaffolding (DCM
  bootstraps implementing the contracts); #13/#14 → pulled the PNC "asked for CI/CD" anecdote (interest only);
  present-tense for shipped capabilities; orchestration-vs-systems-integration distinction.
- **Audience:** internal-rich canonical (gitignored) + a separate genericized public version; never publish the
  customer specifics.
- **Open/unreconciled:** figure store label "Request" vs spec enum "Requested" (spec form lower-risk).
