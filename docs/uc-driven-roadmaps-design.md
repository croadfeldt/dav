# UC-Driven Dual Roadmaps — Design / Requirements

**Status:** living design doc (started 2026-06-02). Captures the goal and model;
phasing is proposed, not committed. Build to this doc; update it as decisions land.

## 1. Goal

DAV started as a spec **gap-analysis** tool and is outgrowing that — it's becoming a
**gap-analysis / prioritization / design tool for architectures (and, later, code).**
The organizing principle that keeps it coherent as it grows:

> **Use Cases are the driver.** They drive the development of the architecture *and*
> the engineering work to build the product. The analysis is **one source of
> findings**; from it we surface **two distinct roadmaps for two distinct consumers.**

The confusion that prompted this doc: both roadmaps existed only in fragments, neither
was *surfaced as a roadmap*, and capability views were stranded inside the architecture
review — so they read as duplicate answers to one question. They are not duplicates.

## 2. The model

```
                 ┌─ gaps ─────────────► Architecture & Capability roadmap (Track 1)
   UCs ─► analysis ┤                     close spec gaps + identify/define capabilities
                 └─ capabilities ──┐          │ produces the canonical CAPABILITY CATALOG
                                   ▼          ▼
                          Engineering roadmap (Track 2)  ◄── UC priorities
                          build capabilities, dependency-ordered, UC-driven
```

- **One source of findings:** the per-UC analysis (verdicts, **gaps**, **capabilities_invoked**).
- **Two projections:** gaps → Track 1; capabilities → Track 2.
- **Shared currency:** *capabilities* are **identified in Track 1** and **built in Track 2.**
- **Single-source rule:** each projection drives exactly one roadmap for one consumer.
  Never co-locate the two roadmaps in one view (that was the original bug).

### Track 1 — Architecture & Capability roadmap
- **Consumer:** the architect evolving the spec.
- **Question:** "Where is the architecture deficient, and what capabilities do the UCs
  reveal that the spec must define/support?"
- **Inputs:** UCs + spec + analysis **gaps** + analysis **capabilities**.
- **Prioritization:** **gap-anchored** (severity + cross-UC frequency, weighted by the
  demand/foundational signal behind each gap).
- **Outputs:** (a) gap roadmap → Architectural Review / Enhancement Plan → **spec edits
  now, code-generation as a future target**; (b) the **canonical capability catalog**
  (new first-class output — see §4).

### Track 2 — Engineering roadmap
- **Consumer:** whoever sequences engineering work (Kevin/Piotr-style planning).
- **Question:** "What do we build, in what order, to satisfy the use cases?"
- **Inputs:** the **capability catalog** (from Track 1) + **UC priorities** + capability
  **dependencies** (foundational ordering).
- **Output:** a sequenced, dependency-ordered build roadmap — graphed for reading and
  **exportable** to slide decks, Jira, engineering reports (see §7).

## 3. End-to-end workflow — guided stages

The console must **walk the user through these stages in order**, each one surfacing its
*purpose (why)*, the *propose → curate action*, and the *expected output* that feeds the
next stage. A stage navigator should make "where am I / why / what will I get" obvious —
that explicit guided flow is the cure for the tool feeling like a grab-bag of tabs. The
flow is linear up to analysis, then **branches into the two tracks.**

**Stage 0 — Define & curate Use Cases** *(the driver)*
- **Why:** UCs drive both roadmaps; analysis quality is bounded by UC quality.
- **Action:** author/import UCs (propose: bulk-from-text); check **readiness** (#4) and
  fix; set **priority** (#1); group into a **Set** to scope the effort.
- **Output:** a ready, prioritized UC Set — the driver for everything downstream.

**Stage 1 — Analyze**
- **Why:** evaluate the UCs against the architecture spec to surface findings.
- **Action:** run DAV over the Set.
- **Output:** per-UC analysis — verdict, **gaps**, **capabilities** (the single source of findings).

**Stage 2 — Review findings**
- **Why:** understand and trust what surfaced before acting; catch shallow analyses.
- **Action:** inspect verdicts/gaps/capabilities; re-run thin ones (shallowness signal).
- **Output:** validated findings, ready to project into the two tracks.

— *flow branches* —

**Track 1 · Stage 3 — Prioritize gaps** *(gap-anchored)*
- **Why:** decide which spec deficiencies matter most (severity × cross-UC frequency,
  weighted by the demand behind each).
- **Action:** framework proposes a ranked gap worklist; architect reweights/pins/excludes.
- **Output:** a prioritized gap worklist (scoped to the Set).

**Track 1 · Stage 4 — Architectural review & design**
- **Why:** turn prioritized gaps into concrete architecture changes.
- **Action:** Architectural Review (narrative) → Enhancement Plan (spec patches) → PRs.
  *(code-generation is the future target of this stage.)*
- **Output:** spec edits / PRs, emitted **as tracked work items** (see §8).

**Track 1 · Stage 5 — Identify & curate capabilities → the catalog**
- **Why:** capability identification is Track 1's job; it produces the canonical catalog
  Track 2 depends on.
- **Action:** framework proposes capabilities (+ dependencies) from the analysis;
  architect confirms / renames / merges synonyms / defines.
- **Output:** the curated **capability catalog** (§4).

**Track 2 · Stage 6 — Sequence the build**
- **Why:** decide what to engineer first to satisfy the use cases.
- **Inputs:** catalog + UC priorities + capability dependencies (foundational first).
- **Action:** framework proposes a dependency-ordered sequence; architect adjusts.
- **Output:** the **engineering roadmap**, graphed.

**Track 2 · Stage 7 — Export & communicate**
- **Why:** hand the roadmap to planning/execution.
- **Action:** render the structured roadmap representation.
- **Output:** slide deck / Jira epics+stories / engineering report — emitted **as tracked
  work items** so delivery feeds back (§8).

**Stage 8 — Reconcile (close the loop)** *(cross-cutting; fires when a tracked output lands)*
- **Why:** keep the analysis in sync with the real target; prevent drift.
- **Action:** on spec-PR-merge / Jira-done / eng-PR-merge (auto where wired, manual
  otherwise), re-run the affected UCs and reconcile findings.
- **Output:** updated finding/work-item status (verified-resolved / still-open) and a drift
  report for anything stale.

## 4. The capability catalog (keystone)

Capabilities are currently **free-form strings the model coins per UC** — no controlled
vocabulary (unlike `provider_types` / `policy_modes` in the consumer profile). So today's
cross-UC capability aggregation silently miscounts synonyms; this is why the Capability
Map "didn't make sense." The engineering roadmap is **untrustworthy until capabilities
are canonical.**

Resolution falls out of the goal: **capability identification is Track 1's job.** The
architecture work produces and curates a **canonical capability catalog**; model-emitted
per-UC capabilities **resolve against it** rather than standing on their own. (Latent
support already exists: the engine's pass-1 emits `capabilities_observed` with
`id: <from spec>` + a `spec_ref`.)

- Catalog entry: id, name, definition, spec_ref(s), status (proposed/confirmed),
  dependencies on other catalog capabilities.
- **Hybrid build:** framework *proposes* capabilities from analysis; architect *curates* —
  confirm, rename, merge synonyms, define dependencies.
- Every aggregate capability view (demand, foundational, engineering roadmap) reads the
  **catalog**, not raw model strings — that's what makes the numbers trustworthy.

## 5. Sets as the prioritization primitive (generalized)

Today "Sets" group only UCs. Generalize to **working sets that can contain UCs, gaps,
and capabilities**, so you can carve a slice and build a roadmap from it (e.g. "Q3
cost-mgmt push" = these UCs + the capabilities they need + the gaps blocking them).

- A Set is the **scoping unit** for both roadmaps and for prioritization.
- Sets are **hybrid**: LLM proposes a candidate set; the human edits membership/ordering.
- Both roadmaps are generated **over a Set** (or the whole corpus).

## 6. Hybrid LLM + human workflow

Every stage is propose → curate, never fully automatic (matches DAV's existing ethos):

| Stage | Framework proposes | Architect disposes |
|-------|--------------------|--------------------|
| Findings | gaps + capabilities per UC (analysis) | — (raw material) |
| Capability catalog | candidate capabilities + dependencies | confirm / rename / merge / define |
| Sets | candidate groupings | edit membership + ordering |
| Prioritization | ranked worklist (gap- / capability-anchored) | reweight / pin / exclude |
| Roadmaps | draft narrative + ordering + graph | approve / adjust |

## 7. Outputs & integrations

- **Graphed roadmaps:** both roadmaps render as **readable graphs** (dependency DAGs,
  capability maps, sequencing timelines). Concrete formats **decided later** — the data
  model must not assume one renderer.
- **Engineering roadmap as an export surface:** must serialize to a **structured
  representation** that renders to multiple external consumers — **slide decks, Jira
  (epics/stories), engineering reports.** Implication: the roadmap is *data first*
  (capabilities, dependencies, sequence, UC drivers, priorities), with renderers on top —
  not a prose blob.

## 8. Closed-loop tracking & drift prevention

A finding isn't "done" when DAV recommends a fix — it's done when the fix **lands in the
target** (spec PR merged, Jira delivered) and a **re-evaluation confirms** it. If the
analysis lifecycle and the real artifacts drift apart, DAV's picture goes stale: it reports
gaps that were already closed, or misses changes made outside it. **That drift undermines
trust in both the tool and the architecture it evaluates — it's an existential risk, not a
nice-to-have.**

Every actionable output is therefore a **tracked work item** that joins a finding to its
real-world artifact and keeps them in sync:

- **Linkage:** work item ⇄ the finding(s) it addresses (gap / capability / UC) ⇄ the target
  artifact (spec PR, Jira epic/story, engineering PR/branch).
- **Status, synced from the target:** proposed → in-progress → merged/accepted → verified
  (or rejected / superseded). Pull state where we can — GitHub/GitLab PR state, via the
  same `pr_comments` poller + `managed_repos` credentials that already poll repos — and
  fall back to **manual status** where we can't yet (Jira at first). Manual is acceptable
  *as long as the link is explicit and trackable.*
- **Re-evaluation on acceptance (the loop closing):** when a Track-1 work item is accepted
  (spec PR merged), DAV **triggers a re-run** of the affected UCs, re-ingests, and
  reconciles — the gap becomes *verified-resolved* or *still-open* **from evidence**, not
  assumption. (Re-run → re-ingest already exists; this wires the trigger to acceptance.)
- **Drift surfacing:** show where the analysis is stale relative to outputs — a gap with a
  merged PR but no re-eval, an external change with no linked finding, a capability marked
  built whose UCs were never re-verified.

This makes **findings stateful over time** (open → addressed → verified) instead of
one-shot, and makes the work item the **single source for "what's the status of addressing
this finding"** — PR/Jira state lives there, nowhere else. Track 2 uses the same shape: a
capability → Jira/eng-PR work item whose synced status means "capability built" reflects
*delivered* reality, so the engineering roadmap shows true progress and a delivered
capability can trigger re-checking whether its UCs are now satisfiable.

## 9. Multi-user & multi-project (tenancy, roles, collaboration)

DAV must be **multi-user**, and **likely multi-project** (the consumer-agnostic design
already points this way; the DCM/cost-mgmt onboarding implies more than one target). This
is a **foundational, cross-cutting** concern — it scopes the data model and every surface —
so the cheap-but-critical move is to make new entities **tenancy-ready from birth** rather
than retrofit `project_id` later. Retrofitting tenancy is its own drift/debt.

**Project = a user-defined analysis scope.** A project is whatever set of information a user
groups to be **analyzed together** — one repo or many, one spec source or several — with its
UC corpus and consumer profile(s). The unifying criterion is **relatedness: everything in a
project is meant to be usable together by the gap analysis / capability mapping.** A project
owns its derived artifacts — runs, analyses, capability catalog, sets, roadmaps, work items.
The boundary is **not a fixed rule**: cost-mgmt *may* be its own project or folded in with
DCM — the user decides, based on whether they want them analyzed together. Everything the
next phases add — **catalog, generalized Sets, work items, roadmaps** — must carry a project
scope.

**Multi-user = identity + roles + attribution + collaboration.** Users and projects are
**many-to-many**: multiple users on one project, users each with their own project, or a
mix. Membership *and role* are **per project**.
- *Identity* already exists (oauth-proxy `X-Forwarded-User` → `get_user()`); attribution
  exists (`created_by` / `updated_by`, lifecycle `actor`). Make these first-class:
  ownership, assignment, "my work" filters, review queues.
- *Roles* give the hybrid propose→curate model real teeth: who authors UCs, who **curates
  the catalog**, who **approves** spec changes / roadmaps (the "architect disposes" seat),
  who triggers runs / opens PRs, who administers config/credentials. The existing lifecycle
  state machine (draft→ready→in_review→approved, with actors) is already the collaboration
  spine — extend it, don't reinvent.
- *Collaboration* — curation becomes multi-user with review/approval; needs basic
  concurrency safety (optimistic on edits).

**Pragmatic path:** bake tenancy into the schema/APIs **now** — `project_id` scope on new
tables, a `projects` + `project_members(user, role)` model, role checks at the
curate/approve seams. Since projects are user-defined, a basic **create / select project**
concept likely needs to be real early; **cross-project aggregation views can defer**. Ties
to the DCM **#7 multi-user auth** follow-up and the in-progress **multi-repo / multi-source**
work (managed_repos + sources), the multi-project scaffolding.

## 10. Single-source principles (the rules that prevent regression)

1. **One source of findings:** the ingested analysis. Everything else is a projection.
2. **One output per stage per track:** don't add a second view that re-answers a
   question an existing surface already owns. Enrich the owner instead.
3. **Two roadmaps, never co-located:** Track 1 and Track 2 live on separate surfaces.
4. **Catalog, not strings:** aggregate capability views read the canonical catalog.
5. **Propose → curate everywhere:** no fully-automatic roadmap; the human owns the cut.
6. **Guided flow:** every surface states its stage, why it exists, and its expected output.
7. **Close the loop:** outputs are tracked work items linked to their findings; acceptance
   triggers re-evaluation; findings are stateful (open → verified). The work item is the
   single source for output status. **Drift is the enemy.**
8. **Tenancy-ready:** every finding, capability, set, work item, and roadmap is scoped to a
   project and attributed to a user; curate/approve seams enforce roles. Bake it in from
   birth — retrofitting tenancy is drift.

## 11. What this means for what exists today

- **Architectural Review + Enhancement Plan** → Track 1 (gap roadmap). Keep; the healthy
  spine. Consider feeding demand/foundational signal in as *weighting context*.
- **Capability Map (#2) + Foundational (#3)** → not wrong, **mis-filed.** They belong to
  Track 2 (engineering roadmap), reading the **catalog**, not stranded in the review tab.
- **UC priority (#1)** → an input to Track 2's sequencing. ✓ already built.
- **UC readiness (#4)** → upstream quality gate on the driver (UCs). ✓ already built.
- **UC Sets** → generalize to UC/gap/capability working sets (§5).

## 12. Proposed phasing (sequence TBD with Chris)

- **Foundational (precedes / threads Phase 1) — tenancy-ready schema + roles.** Put a
  project scope on every new table (catalog, Sets, work items, roadmaps) and role checks at
  the curate/approve seams *before* building them, so they're multi-tenant from birth. Defer
  the full project-switching / cross-project UX until a second project lands. (Ties to DCM
  #7 multi-user auth + the in-progress multi-repo/source work.)
- **Phase 0 — Guided flow + surface the two tracks.** Implement the staged walkthrough
  (§3): a stage navigator with why/output per stage, and split Track 1 / Track 2 onto
  distinct surfaces. This is the de-confusion *and* the workflow articulation in one move;
  capability views are marked "provisional until catalogued."
- **Phase 1 — Capability catalog (keystone).** First-class, curatable canonical
  capabilities; analysis capabilities resolve against it. Unlocks trustworthy Track 2.
- **Phase 2 — Generalized Sets** (UC/gap/capability working sets) as roadmap scope.
- **Phase 3 — Roadmap generation:** hybrid propose/curate, graphed output, engineering
  roadmap **export** (slides / Jira / reports) over the structured representation.
- **Phase 4 — Closed-loop tracking (threads through; start with Track 1).** Tracked work
  items linking findings → PRs/Jiras; status sync (auto for GitHub/GitLab PRs via the
  existing poller, manual for Jira); **re-evaluation triggered on acceptance**; drift
  surfacing. The Track-1 spec-PR loop can land early — enhancement→PR + the repo poller
  already exist — with Jira/engineering sync following. This is what keeps the analysis
  honest over time; treat as high-priority, not last.

## 13. Open decisions (deferred)

- Graph/visualization formats for each roadmap.
- Canonical-capability mechanism details (catalog is Track-1-owned + spec-anchored; exact
  resolution of model strings → catalog ids: suggest + human-confirm vs auto-match).
- Export targets priority order (slides vs Jira vs report) and their schemas.
- Whether gaps also get a catalog/canonical identity or stay per-analysis.
- Stage-navigator UX: linear wizard vs. free navigation with progress indicators.
- Status-sync depth per target: GitHub/GitLab PR auto (existing poller) first; Jira and
  branches manual → automated later.
- Re-evaluation trigger policy: auto-run affected UCs on merge vs. queue for a human to
  launch; scope of the re-run (just the linked UCs vs. the whole Set).
- Out-of-band change detection: should DAV flag a target that changed with no linked work
  item (a fix made outside the loop)?
- ~~Project boundary granularity~~ — **resolved: project = a user-defined scope** (one or
  more repos/sources grouped by relatedness; the user draws the line). Remaining: what's
  **project-scoped vs. global** — repos / sources / UC corpus / catalog / roadmaps are
  per-project; model configs / credentials / MCP infra are likely shared. Confirm the split.
- Role model: the concrete roles (author / architect-curator / planner / reviewer / admin)
  and whether they derive from oauth-proxy groups, an external IdP, or DAV-managed.
- Cross-project coordination: the DCM team wanted cross-team coordination — is that
  cross-project *views*, or strictly per-project with a shared catalog vocabulary?
