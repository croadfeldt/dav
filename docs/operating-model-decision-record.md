# DAV Operating-Model Decision Record

**Status:** ACCEPTED — Chris ratified the **Purpose / North Star (§0: A-now/B-later)** and all four
decisions 2026-06-30, with one refinement to #4 (architectures own a curated *approved* validation
corpus; UC **purpose** becomes first-class — §6a). Secondary mission added: **assessments** ride the
same shape (§2.2). Later additions (ratified): roadmap is a projection incl. **UC-enablement** as the
engineering bridge (§6b). Proposed, pending ratification: **labels+selectors** scope model (§6c) and
**one capability spine** (§6d).
**Date:** 2026-06-30
**Scope of this DR:** the *core mission only* — analyzing use cases for **gap analysis** and
**roadmapping**. Peripheral capabilities (assessments/maturity wall, recording pipeline, enhancement
PR workbench, customer-demand dedup, blueprints) are explicitly **out of scope here** and will be
re-justified against this model afterward, not the reverse.

---

## 0. Purpose / North Star

**Why DAV exists:** make the **DCM/UDLM architecture provably grounded in real use cases** and drive it
to maturity with **evidence instead of assertion** — so it can earn adoption (engineering team,
community, adopters). *The architecture is the product; DAV is the **instrument** that proves whether it
holds up and shows what to build next.*

**A-now / B-later** (ratified, Chris 2026-06-30) — the orientation that governs every scope decision:

- **A (now): DAV is the instrument for maturing DCM/UDLM.** One operator, a few corpora, optimize for
  **trustworthy output + fast iteration**. Implies: *project* scoping (no hard tenancy), simplicity,
  signal over noise.
- **B (later): DAV as a general assessment product** across many isolated client engagements
  (multi-client isolation, deep RBAC, per-client everything). Deliberately **deferred** — and when it
  comes, isolation is **by deployment**, not schema-per-tenant.

Key insight this resolves: most of the complexity this DR removes (schema-per-tenant, etc.) came from
quietly building for **B** while actually doing **A**. **Decision test for any future capability:** does
it help DAV *prove and mature DCM/UDLM right now*? If it only serves B, it waits.

Mission (the *what*, serving this purpose) is §2; the value bar is **signal over noise** — output must
be trustworthy and decision-grade, because a gap analysis nobody believes is worthless.

---

## 1. Why this record exists

DAV grew feature-by-feature without a ground-up operating model. The accretion produced real friction
we hit this week: an overloaded "ingest" verb, a corpus-vs-architecture confusion that ran an unscoped
job over the wrong files, a UC that hard-failed an entire run on one stray metadata key, and a
schema-per-tenant isolation layer that taxes every query/migration without delivering the guarantee it
implies. This DR fixes the *model*, so subsequent changes build to one intentional design.

Guiding value (Chris): **make the core thing work well > breadth of capabilities.**

---

## 2. The mission(s) — one shape, two subjects

DAV produces the **same three outputs** — **gap analysis · current status · roadmap** — from any
subject evaluated against a reference model. Two subjects are in scope:

### 2.1 Primary mission — architecture validation
> For a set of **use cases**, determine whether the **DCM/UDLM architecture supports them**
> (**gap analysis**), report where the architecture stands today (**current status**), and turn the
> gaps into a prioritized **capability roadmap**.
- Reference model: the **DCM/UDLM architecture** (spec). Inputs: **use cases**.
- This is the priority; a feature that doesn't move a UC → gap → roadmap decision is not core.

### 2.2 Secondary mission — assessments (same shape)
> For an **engagement/organization**, determine where it stands against a **maturity framework**
> (**gap analysis** = findings vs target), render the present state (**current status** = the
> **Maturity Wall**), and turn the gaps into a prioritized **improvement roadmap**.
- Reference model: a **maturity framework** = a **base dataset of capabilities + a maturity rubric**
  (FlightPath, configurable). This is assessments' one *additional* dataset vs the primary mission — and
  per §6d it is **the same capability catalog** the architecture mission uses, with a maturity-level
  overlay (not a separate capability store). Inputs: **assessment material** (notes / PDF / image /
  structured info-dump).
- Same engine shape: *inputs × reference model → gaps → status + roadmap*. The "current status" surface
  is the existing **Maturity Wall** (heat-mapped current state); the roadmap is current → target — and
  can be projected as **capability** *or* **UC-enablement** (§6b), same as the primary mission.

**Why this is one system, not two:** both missions are `inputs × reference-model → (gaps, status,
roadmap)`. They **share** the ingest/validation gate, the project scope seam, the roadmap projection,
and the signal-over-noise bar. They **differ** only in the reference model (architecture spec vs
maturity framework), the input type (UCs vs assessment material), and the analyze logic (supports?
vs scores-against-framework). Keep them as **one pipeline with two configured subjects**, not two
codebases — that's the simplicity dividend.

Order of priority remains **primary first** (it's the A-now north star); the secondary mission rides
the same rails and must not fork them.

---

## 3. The pipeline — three verbs, in order

```
   INGEST            ANALYZE                    ROADMAP
 (pull UCs in,  →  (gap analysis:        →   (gaps → prioritized
  validate)         UC × architecture)        capability roadmap)
```

- **INGEST** — bring use cases *into* DAV (from a corpus repo/branch, bulk import, the editor, the
  recording/extraction path). **Validation happens here**, at the boundary. Output: stored, valid UCs.
- **ANALYZE** — run the gap analysis: for each UC, retrieve the relevant DCM/UDLM **architecture**
  (spec) and assess whether it's supported → emit **gaps** + capability mappings. (This is today's
  "run" / `dav-stage2` pipeline.)
- **ROADMAP** — project the confirmed gaps into a prioritized, consumable plan. **The analysis is the
  single source; the roadmap is a projection — and there is more than one** (see §6b): a
  **capability** roadmap *and* a **UC-enablement** roadmap (UC = the bridge to engineering).

### DECISION 3a — fix the vocabulary (the "ingest" overload)
"Ingest" currently means **three** different things; this is the single biggest source of confusion.
We split them:

| Today's name | What it does | New name |
|---|---|---|
| corpus *sync* / bulk import | pull UCs into DAV | **ingest** (the only meaning of the word) |
| `POST /api/runs`, "run", `trigger_run` | execute the gap analysis | **analyze** |
| "ingest loop" / `_ingest_run_analyses` | load analyze *results* into Postgres | **harvest** (an internal step of analyze, not a user verb) |

User-facing verbs are **Ingest** and **Analyze**. "Harvest" is internal plumbing. Rename touches the
API route (`/api/runs` → `/api/analyze`), the UI labels (run list → **Analyses**), and the docs.
(Keep a back-compat alias on the old route during transition.)

---

## 4. Core entities (minimal set for the mission)

| Entity | Role in the mission | Keep / change |
|---|---|---|
| **Project** | the unit of work + the **scope seam** (every row `project_id`-scoped) | **Keep** — central |
| **Corpus repo** (role=corpus) | source of UC files (repo + branch + **subpath**) | Keep; subpath is mandatory + curated (§6a) |
| **Spec repo** (role=spec) | the **architecture** analyzed against (DCM @ main, UDLM @ main) | Keep |
| **Use Case** | the unit being analyzed; lives in the UC store, validated | Keep; gains a **`purpose`** (§6a) |
| **Purpose** (UC attribute) | *why* the UC exists: `architecture-validation` (approved, gating) vs `feedback`/`candidate` vs `exploratory` | **New** (§6a) |
| **Scoping Set** | a named selection of UCs to analyze together | Keep |
| **Analysis** (was "run") | one execution: chosen UCs × architecture → gaps | Keep, rename |
| **Gap / capability mapping** | analyze output; the roadmap's raw material | Keep |
| **Roadmap** | prioritized projection of gaps | Keep |
| **Customer** | demand attribution (M:N) | Keep (soft); not core to the pipeline |
| **Tenant (schema-per-tenant)** | hard data isolation | **Remove** (see §5) |

Corpus and spec are the load-bearing distinction: **corpus = the questions (UCs); spec = the source of
truth (architecture).** Gap analysis is `corpus_UC × spec_architecture → gap`.

---

## 5. DECISION — isolation: project-scoped now, deployment-isolated later; **drop schema-per-tenant**

**Decision:** Collapse to a **single schema** with **`project_id` scoping** as the only isolation
mechanism for now. Remove the per-tenant schemas (`tenant_flightpath`, `tenant_default`,
`tenant_acme_val`) and the `search_path` runtime plumbing. Migrate the live data (`tenant_flightpath`)
back into `public`; drop the test tenants.

**Rationale:**
- Schema-per-tenant has cost real reliability (the Phase-2 boot crash class, `DAV_RUNTIME_SEARCH_PATH`
  juggling, run→session→project attribution guesswork) for **zero current benefit** — DAV is a
  single-operator tool with a handful of engagements, not yet onboarding isolated regulated clients.
- It is **false sovereignty**: a different Postgres schema in the *same instance / same backups / same
  process* is not the isolation a regulated/sovereign client actually requires. It carries the
  *complexity* of multi-tenancy without the *guarantee*.
- `project_id` already provides the logical separation the mission needs.

**When real isolation is needed:** isolate at the **deployment boundary** — a dedicated DAV instance
(own DB, egress, keys) per sovereign engagement. Simpler operationally *and* a stronger guarantee than
schema-per-tenant. The `project_id` model nests cleanly inside a per-deployment install.

**Cost (honest):** the collapse is a real one-time migration (move data out of `tenant_flightpath`,
strip search_path from runtime + the migration runner, retest). Worth it as a permanent simplification.

This decision **supersedes/closes** the open reconcile items #217 (single-vs-multi-tenant boundary)
and #199 (UC tenant-scoping) and reframes #200.

---

## 6. DECISION — validation: gate at ingest, with the engine's *real* loader

**Problem observed:** validity is checked by two divergent code paths — the engine's real loader
(`UseCase.from_dict` → `UseCaseMetadata(**data)`) and a separate API validator that only "mirrors"
it. They drift, so a UC can pass the mirror and still hard-fail at analyze time (this is exactly how
`metadata.note` slipped through and crashed UC-load mid-run).

**Decisions:**
1. **One validator = the engine loader.** Ingest validates by actually invoking the vendored engine
   `UseCase.from_dict`. Guarantee: **ingest-passes ⟺ analyze-loads.** No parallel "mirror" validator.
2. **Validate at the ingest boundary (synchronous gate).** On pull/import/save, each UC is either
   *accepted* or *quarantined with the exact reason* (`unknown metadata key: note`, `missing scenario`,
   …). A bad UC never silently becomes a run-time `<load-failed>`. "We know before we ingest."
3. **Background validation sweep.** Periodically re-validate the whole store/corpus, because validity
   drifts (engine schema changes, corpus branch changes, profile-vocab changes). Surfaces rot
   proactively into a **UC-health** view (generalizes the editor health-check #122; pairs with the
   freshness chip #117).
4. **Tolerant loader (defense in depth).** The loader filters/warns on unknown keys instead of
   crashing; one stray field degrades to a warning, never a hard failure. (And legitimate fields like
   `note` get added to the schema.)
5. **Schema as the one contract (direction, not this DR's deliverable).** Converge the editor
   validation, ingest validation, and engine loader onto a single derived schema (ties to #182).

---

## 6a. DECISION — UC **purpose**, the architecture's approved validation corpus, and gating
*(Chris refinement to decision #4, 2026-06-30)*

Decision #4 is **not** "make dcm/udlm spec-only." Each architecture must **own a corpus** of use cases
that live *in* its repo — these are the **approved** UCs for **continuous validation** and **potential
gating of the architecture itself** (a change to DCM/UDLM that regresses an approved UC is caught, and
can block). So an architecture repo is legitimately **both** `spec` (the architecture) **and** `corpus`
(its approved validation UCs) — what was wrong before was the *unscoped* corpus (empty subpath →
rglob'd the whole repo → loaded config files as UCs).

**Decisions:**
1. **Corpus subpath is mandatory and curated.** A `corpus`-role repo must declare a subpath pointing at
   a directory of *only* UC files (e.g. `<arch>/use-cases/`). No empty/whole-repo corpus. Ingest reads
   exactly that tree; non-UC files can't leak in. (Closes the 2026-06-30 misfire at the model level,
   not per-incident.)
2. **`purpose` becomes a first-class UC attribute** (recorded on each UC, validated, surfaced):
   - `architecture-validation` — an **approved** UC that validates (and may gate) its architecture.
   - `feedback` / `candidate` — proposed UC under review (e.g. the Piotr-feedback branch): analyzed for
     gaps, *not* gating, until promoted.
   - `exploratory` — ad-hoc/one-off analysis input.
   `purpose` is orthogonal to lifecycle state (`draft → approved → deprecated`). The **approved
   validation corpus** of an architecture = `purpose=architecture-validation` ∧ `lifecycle=approved`,
   living in that architecture's corpus subpath.
3. **Two consumption modes, one pipeline.** Analyze is the same (`UC × architecture → gaps`); what
   differs is the UC set and how the result is used:
   - **Continuous validation / gating** — the architecture's *approved* corpus runs on a cadence (and
     on architecture change); regressions are flagged and can gate.
   - **Feedback/roadmap** — candidate UCs (e.g. a branch) are analyzed → gaps → roadmap; good ones get
     **promoted** into the approved corpus (purpose flips `candidate → architecture-validation`,
     lifecycle → `approved`).
4. **Promotion is the bridge** between the two: a candidate UC that proves valuable is promoted into the
   architecture's approved corpus, after which it participates in continuous validation/gating.

**Implication:** the `corpus` role stays on dcm/udlm — but each is pointed at a curated approved-UC
subpath, and every UC carries a `purpose`. Validation (§6) enforces both: subpath-only ingest, and a
required, enumerated `purpose`.

---

## 6b. DECISION — roadmap is a *projection*; UC-enablement is the engineering bridge
*(Chris, 2026-06-30)*

The analysis is the **single source**; the **roadmap is a projection of it, and there is more than
one**. From the same gap↔capability↔UC graph, DAV projects (along with, or in place of, each other):

- **Capability roadmap** — grouped by **capability**: "build/raise capability C → enables UCs X, Y, Z."
  The architecture-program view.
- **UC-enablement roadmap** — grouped by **use case**: "to enable **UC X**, close gaps {a,b} → do work
  {build cap C, …}." **The UC is the bridge to engineering** — a concrete, shippable unit an engineer
  or stakeholder acts on, far more actionable than an abstract capability list. This is the form that
  feeds a **SOW** and **Jira/engineering hand-off**.

They are two **groupings of one graph**, pivoted via the **UC↔capability map**; both must stay
consistent with the same underlying gaps. The projection is **configurable** (capability, UC-enablement,
or both).

**Applies to both missions.** The assessment improvement roadmap can likewise be projected as
capability/maturity improvement **or** as UC-enablement ("raising maturity on these capabilities enables
these scenarios"). Generalizes the projection builder (#177); subsumes #255 (UC-centric eng roadmaps).

---

## 6c. DECISION (PROPOSED) — scope & selection: labels + selectors (the matrix)
*(Chris proposed tagging 2026-06-30; mechanics below for ratification)*

**Requirement:** compose a working scope across a **matrix** of {use cases, sets, customers, projects,
capabilities} where relationships are **M:N and bidirectional** (projects-within-customers *and*
customers-within-projects). Hardcoding each relationship doesn't scale.

**Decision:** every entity carries **labels (annotations)**, and scope is composed by **selectors with
positive *and* negative matching** — adopt **Kubernetes label-selector semantics** (`matchLabels` +
`matchExpressions`: `In / NotIn / Exists / DoesNotExist`); do not invent a grammar.

- **Entities stay first-class; membership is expressed as labels** (`customer/acme`, `project/rehydration`,
  `set/piotr-feedback`, `arch/dcm`). The matrix mapping (customer→UCs/projects and the reverse) becomes
  one query over the label graph — collapsing the bespoke customer×project / customer×user matrices into
  one mechanism.
- **A scoping set becomes a saved selector** (static member list = the pinned/degenerate case). Dynamic
  sets re-evaluate as labels change.
- **System-derived vs human labels** are distinguishable: derived labels (source repo/branch, `purpose`,
  `lifecycle`) are applied at **ingest** and may refresh on re-ingest; human labels (customer/project
  membership, ad-hoc) are sticky and never clobbered.
- **Lifecycle:** labels are attached/derived through ingest → edit → promotion; **selectors are evaluated
  at analyze time** to materialize the working set.
- **Separation of concerns:** **labels *select*; project + RBAC *authorize*.** A selector composes only
  within what the caller may access — it can never cross the §5 project scope seam.

Open: label key namespace/reserved prefixes; whether capabilities use the same selector surface (lean
yes); UI for building/saving selectors.

---

## 6d. DECISION (PROPOSED) — capability: the one shared spine (simplify)
*(Chris: "simplify the use and architecture of the capability" 2026-06-30)*

**Problem:** capability is modeled several overlapping ways — `capability_catalog`,
`capability_taxonomy_terms`, UDLM `capability_inventory`, and assessment `framework_capabilities` — that
have needed repeated reconciliation (#89/#90/#104). That duplication *is* the complexity.

**Decision (proposed):** **one capability catalog** is the shared spine for **both** missions, because
capability is the common currency: architecture gaps → capabilities to build (capability roadmap), and
assessments score **the same capabilities** against a maturity rubric.

- **Capabilities** = one entity (id, name, description, category). One catalog.
- A **maturity framework** is **not its own capability store** — it is a **selected capability set + a
  maturity rubric (levels/states)** layered on the catalog. FlightPath = curated capability set + rubric.
  (This is assessments' "additional base dataset", §2.2 — an *overlay*, not a parallel model.)
- The capability **method** (#132 Core/Supporting/Generic) and **map** (#88, UC↔capability) become
  attributes/views on the one catalog, not separate machinery.
- **Catalog scope:** one shared catalog, **labeled by architecture** (`capability tagged arch/dcm`) per
  §6c — the simpler default than per-architecture stores.

**Open (needs explicit go — touches the built Maturity Wall #147/#148):**
1. Collapse `capability_catalog` / `capability_inventory` / `framework_capabilities` into one entity?
2. Framework = capability-set + rubric overlay (not a separate capability store)?
3. Shared catalog labeled by architecture vs per-architecture catalog?

---

## 7. Explicitly deferred (not part of either mission)

To keep the missions working well, these are **not** part of this model and will be re-justified against
it later: the recording→UC pipeline, enhancement/PR workbench, customer-demand dedup, blueprint/linked
projects, the self-improvement A/B-on-prompts machinery, agent PAT surface. None are deleted; they're
simply not allowed to shape the core pipeline.

*(Note: **assessments / Maturity Wall** is **no longer deferred** — it is the §2.2 secondary mission. It
must ride the same ingest/analyze/status/roadmap rails as the primary, not fork them.)*

---

## 8. Consequences

**Positive:** one isolation model (project scoping); one validity definition (engine loader); clear
verbs (ingest / analyze / roadmap); a fail-fast quality gate so analyses only ever run on valid UCs;
far fewer failure modes and a much simpler thing to operate and reason about.

**Costs/risks:** the tenancy collapse migration; a deliberate rename pass (with back-compat aliases);
re-pointing existing docs/runbooks. All one-time.

---

## 9. Resolved (Chris, 2026-06-30)

1. **Tenancy collapse — YES.** Single schema + `project_id`; isolation-by-deployment later.
2. **Verbs — YES, everywhere incl. UI** ("Analyses"). Ingest / Analyze user-facing; Harvest internal.
   (Keep a back-compat alias on the old `/api/runs` route during transition.)
3. **Validation on ingest — QUARANTINE.** Invalid UCs stored as `invalid` + reason, shown in UC-health,
   excluded from analyze (not hard-rejected/dropped).
4. **Corpus role — KEEP on dcm/udlm, but curated.** Architectures own an approved validation corpus
   (continuous validation + gating). Fix is a *mandatory curated subpath* + a first-class UC `purpose`,
   not removing the role. See **§6a**.

---

## 10. Build sequence (ratified — slices)

1. **Loader tolerance** — filter/warn on unknown metadata keys; never hard-fail a UC on one stray key.
   (The broken UC's *content* is being fixed in a separate session; this is the tool-robustness fix so
   the failure *class* can't recur.)
2. **UC `purpose`** — add the enumerated, required attribute to the UC schema/loader; back-fill existing
   UCs (default `exploratory`; mark the architecture corpora `architecture-validation`).
3. **Ingest validation gate** — validate via the real engine loader; **quarantine** invalid UCs with
   reason; enforce mandatory curated corpus subpath (no whole-repo scan).
4. **Background validation sweep** → UC-health view (drift detection).
5. **Rename run→analyze** — *API alias DONE* (`/api/analyses…` additively aliases every `/api/runs…`
   route; old paths unchanged, no flag day). **Follow-ups:** UI relabel to "Analyses" (cosmetic; do
   with eyes on it — disruptive to a live session), then eventual deprecation of the `/api/runs` paths.
6. **Tenancy collapse** — migrate `tenant_flightpath` → `public`, strip `search_path` plumbing, drop
   test tenants.
7. **Corpus/spec hygiene** — set curated subpaths on every corpus repo (dcm/udlm = their approved
   `use-cases` trees; dcm-piotr = `dav/use-cases`); confirm spec roles unchanged.
8. **Continuous validation / gating** — schedule the approved corpus to analyze on cadence + on
   architecture change; surface regressions; wire the gate.
9. **Secondary mission on the same rails** — express **assessments** (§2.2) as a second *subject*
   over the unified pipeline: reference model = maturity framework (FlightPath), inputs = assessment
   material, current status = the existing Maturity Wall, output = improvement roadmap. Reuse
   ingest/validation/roadmap; **do not fork** the pipeline. (Builds on the existing Maturity Wall work.)
10. **Roadmap projections** (§6b) — add the **UC-enablement** projection alongside the capability
    roadmap, pivoting via the UC↔capability map; make the projection selectable; wire UC-enablement to
    SOW (#142) + Jira hand-off (#175).
11. **Scope: labels + selectors** (§6c, *after* ratification) — label model on all entities; K8s-style
    positive/negative selectors; scoping set → saved selector; selectors authz-bounded by project/RBAC.
12. **Capability: one shared spine** (§6d, *after* ratification) — collapse the parallel capability
    stores into one catalog; framework = capability-set + rubric overlay; method/map become views.
