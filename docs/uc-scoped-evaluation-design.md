# UC-scoped evaluation — result cache, freshness, and run-as-rebuild

## Context
Removing the masthead run selector (after the domain-shell + persona work) exposed that
the analysis/roadmap views were scoped by the **wrong object** — a *run* (an evaluation
artifact), not the demand. This doc settles the model: you **scope by a Use Case / UC Set**;
the result is a **per-UC fingerprinted cache** kept fresh against its project's repos; and a
"run" is just the **job that rebuilds the cache**. It is the consumer-side mechanics of the
persona paradigm (`ux-paradigm-design.md`): *consumers read outcomes, builders manage
evaluations*. The masthead's third citizen — a **freshness indicator** — is #112 done right.

## Decisions (Chris, 2026-06-11)
1. **Scope = UC / UC Set**, never a run. The run selector is retired for good; the consumer
   views' top context is a **scope picker** (UC / Set).
2. **A UC is tagged with the project that created it** — the existing `managed_use_cases.project_id`.
   No many-to-many, no reference/fork machinery now; cross-project reuse of a UC is a fork/copy later (#43).
3. **Result = a per-UC cache**, evaluated against *that UC's project's* repos. Project is implied
   by the UC, so it drops out of the key.
4. **Run = the rebuild job.** The Runs tab becomes the **freshness / job view** (operator/architect
   lens); consumer lenses just read the fresh cache.
4b. **Runs are eliminated as a scoping mechanism EVERYWHERE (Chris, 2026-06-11).** The *only*
   selectable scope is **UC / UC Set** (the selectable view); an Outcome is a named binding over a
   UC Set. A run is purely the *ingestion/evaluation event* that fills the per-UC cache — never a
   thing you pick. Every output view (Results · Architecture · Engineering · Cap Map) scopes by
   UC/Set and reads the **latest (or published) eval per UC** from the cache, regardless of which
   run produced it. The four run pickers (`rpRunSel`/`engRunSel`/`cmRunSel` + `resultsRunsPanel`)
   and the masthead run-selector are all gone. **The Runs view repurposes into a "UC ingestion
   audit"** — per-UC: last evaluated, by which run, fresh/stale, coverage — an operator/audit
   surface, not a selector. **Contract consequence:** a Set's results **span multiple runs** —
   latest-eval-per-UC means UC-A may come from run 5 and UC-B from run 7; a "result set" is no
   longer a single coherent run. That's the intent of run-agnostic scoping.
5. **Rebuild policy:** **lazy on-view + a manual "rebuild now"** to start; a change-queued worker later.
6. **Deliverable stability:** consumer lenses read the **last *published*** evaluation (pinned), not
   bleeding-edge — a stakeholder's view must not shift on a re-run. Rebuild → a *candidate* → publish.
7. **Masthead freshness chip:** coverage + freshness + drift; the popover shows **both** latest-vs-current
   and published-vs-current; **drift = git SHA-diff** across the project's repos, with **GitHub PR count**
   layered on where the poller has it.

## Scope = a Scoping Set (Chris, 2026-06-11)
**"Per-UC" is the compute/cache grain, NOT the scope.** Evaluation, fingerprint, and freshness are
per-UC because a UC is the atomic unit that gets evaluated — that's *how results are stored and kept
fresh*. **The selectable scope is a UC Set — the "Scoping Set" — which IS the scope definition** (a
single UC is a Set-of-one drill-in).

| Level | Grain |
|---|---|
| **What you select** | the **Scoping Set** (a UC Set); a single UC = a Set-of-one |
| **Results browse** | per-UC *within* the Scoping Set |
| **Roadmap / outcome requirements / combined outcomes** | **aggregate/synthesis over the Scoping Set** — per-UC is the evidence underneath |
| **Storage / freshness** | per-UC (atomic eval unit) |

So the **roadmap and output views synthesize over the Scoping Set**, never "per UC." The scope picker
selects a Scoping Set; Results lets you drill into any member UC's analysis; the synthesis views
(Architecture/Engineering, and the Customer/Stakeholder combined-outcome) aggregate across the Set.

**Implemented (2026-06-11): one shared masthead Scope selector, not per-view pickers.** The Scoping
Set is app-wide chrome alongside Project — `#globalScopeSel` (localStorage `davScope`) sets
`_activeScope`; `scopeQuery()` appends `?set_id=…`. Results, Cap Map, and Engineering all read it and
re-render on change (`setScope()`); the masthead freshness chip reflects the same scope. The former
in-view Set/run pickers are hidden. Authoring is split into **Use Cases · Scoping Sets · Discussion**:
the new **Scoping Sets** tab (`#view-scopingsets`) is the canonical set-management surface (the legacy
⚙ "Manage" modal redirects to it). Vocabulary: a *run* is surfaced as an **Ingestion** everywhere
user-facing (DB/API identifiers keep `run`).

## The model
```
scope (UC / Set, in the active project)
   → per-UC RESULT CACHE  (evaluated against the UC's project repos)
        fingerprint = hash(UC content + project repo SHAs[spec/arch/corpus] + eval config[model/prompt])
        fresh ⇔ fingerprint == current
   → derived projections:  outcome requirements (capabilities/arch the UCs require)  +  roadmap (path to close the gap)
RUN = the rebuild job that (re)computes cache entries.  Runs tab = freshness/job view.
```
- **Outcome requirements** and **roadmap** are *derived*, bottom-up, from the scoped UCs — not authored
  above them. A named "Outcome/Initiative" is just a **Set elevated with an outcome statement**
  (mechanically a Set), so there is no parallel object to maintain.
- **Invalidation by what changed:** UC edited → that UC · a project's spec/arch repo moves → *all* that
  project's entries · model/prompt/eval-config changes → entries on that config. Staleness is a
  **fingerprint compare (instant)**; rebuild is **gated** (policy 5) — never eager recompute on commit
  (an evaluation is an LLM/GPU run of hours, not a cheap cache fill).
- **Published vs latest:** each UC's cache holds a *latest* (newest rebuild) and a *published* (pinned)
  evaluation. Consumer lenses read published; builders see latest; the gap between them is the
  "should I re-publish" signal.

## Masthead freshness indicator (the real #112)
A **freshness/coverage health chip** — *status, not selection* — for the active project. One chip,
**persona-read**: a "go rebuild" prompt for the operator/architect, a confidence cue for the
customer/stakeholder ("as-of 3 days / 12 commits ago — refresh before the exec readout").

**Metrics — two axes + a leading signal:**
- **Coverage** — `ingested / total` UCs (how much demand has *ever* been evaluated).
- **Freshness** — # stale + **time stale** (`now − last_fresh_eval`).
- **Drift (leading indicator)** — **commits / PRs to the project's repos since the last eval.** The
  cheap signal that matters most: the spec moving 12 commits says the picture is drifting *before* you
  pay for a rebuild.

**Form:** collapsed chip with the one-line vital (`Analysis 47/52 · 3d · ↑12`) → expands to a popover
(reuse the who's-online popover pattern) with total / ingested / stale / last-run / **latest-vs-current**
+ **published-vs-current** drift, and a **"Rebuild now."** The popover is the entry to the operator
freshness/Runs view; per-UC drill-in lives there, masthead stays project-aggregate.

## Reuse (mostly connecting existing pieces)
- `managed_use_cases.project_id` — UC↔project tagging (exists).
- `managed_repos` per project — the "project defined repos" (exists).
- `analysis_output_cache` + `uc_analyses` / `uc_gaps` / `uc_capabilities` — the cache substrate + the
  data behind outcome-requirements/roadmap (exists).
- GitHub poller (`pr_comments`) — PRs-since (exists).
- #43 (UC↔project matrix) — the eventual cross-project reuse path.
- The genuinely **new** bits: the **input fingerprint** (incl. the run recording the **repo SHA it
  evaluated against**), the **staleness read**, and the **scope picker** that replaces the four
  bespoke run pickers (`rpRunSel`/`engRunSel`/`cmRunSel` + the hidden `resultsRunsPanel`).

## Build order (derived)
1. **Fingerprint + SHA capture** — runs record the repo SHAs + eval config they evaluated; store the
   fingerprint with each cached result.
2. **Staleness read** — fresh/stale per UC = fingerprint vs current; aggregate per project.
3. **Re-scope outputs to UC/Set (the restructure — Chris 2026-06-11).** Three coupled pieces:
   (a) a **latest-eval-per-UC backend** — given a UC/Set, return the newest cached eval per UC
   (`uc_analyses` `DISTINCT ON (uc_uuid) … ORDER BY ingested_at DESC`), regardless of run;
   (b) a **UC/Set scope picker** (the selectable view) replacing the four run pickers in
   Results · Architecture · Engineering · Cap Map, resolving to that latest-eval-per-UC;
   (c) **repurpose the Runs view → "UC ingestion audit"** (per-UC: last evaluated · by which run ·
   fresh/stale · coverage). Runs vanish as a scope everywhere.
4. **Masthead freshness chip + popover** (#112), reading the staleness + drift.
5. **Publish/pin** — candidate vs published evaluation; consumer lenses read published.
6. **Change-queued rebuild worker** — later optimization over lazy-on-view + manual.
7. **Combined-outcomes projection** (the triangle apex) — the Outcome object joins the UC eval cache
   (required capabilities) + the assessment cache (current maturity) on the capability catalog →
   prioritized gap + roadmap + value, for the Customer/Stakeholder lenses.

## Open / deferred
- Naming **outcome requirements** + **roadmap** as first-class *derived* projections (vs today's
  arch-review / engineering outputs) — surface them as the consumer-lens views.
- Cross-project UC reuse (#43 reference/fork) — out of scope until needed.
- Exact drift granularity (per-repo-role vs combined) — settle during build.

## Two pipelines, one substrate (Chris, 2026-06-11)
UC-driven and assessment-driven are **genuinely distinct pipelines** — they differ on **three
axes**, not just ingest — that happen to *share a substrate*. Don't merge them; project differently.

| Axis | **UC-driven** | **Assessment-driven** |
|---|---|---|
| **Starting point** | Use Cases — *desired* demand | an Assessment artifact — *observed* current-state, ingested |
| **Scoping** | UC / UC Set | Assessment / finding-set |
| **Direction** | forward: demand → arch gap → roadmap | diagnostic: current-state → maturity / gaps-vs-target → strategy |
| **Evaluation target** | UCs vs the **spec/architecture** | findings vs a **target/reference** (the F6 target generalization) |
| **Outputs** | outcome requirements + engineering roadmap | maturity + current-state gaps vs target + strategy/roadmap |
| **Persona home** | Architect · Engineer · Customer · Stakeholder | Assessor (+ consumer lenses) |

**Shared substrate (build once, parameterized by intake):** the **cache / fingerprint / freshness**
mechanism (`_eval_fingerprint` shape — UC content + repo SHAs on the arch side; assessment-artifact
hash + extraction config on the assessment side), the **capability catalog** as the common currency
both anchor gaps to, and the **persona-lens** model. **Pipeline-specific:** starting point, scope
object, and the output projections — so each lives under its own persona/domain, not a merged screen.

### The triangle — combined outcomes (Chris, 2026-06-11)
It's not two pipelines, it's a **triangle**; the third vertex is where the value lands.
```
                 COMBINED OUTCOMES  (the Outcome object — the synthesis)
            required − current → prioritized gap + roadmap + value
                  /                                    \
             desired                                current
          USE CASES                                ASSESSMENT
   (demand → required capabilities)      (current-state → capability maturity)
                  \____________ join on ______________/
                          CAPABILITY CATALOG (the shared currency)
```
- **Use Case** vertex → *required* capabilities. **Assessment** vertex → *current* capability
  maturity. **Combined Outcome** vertex → **required − current**, per capability → prioritized gap +
  roadmap + value (the consulting deliverable).
- **The capability catalog is the join key** — both pipelines anchor gaps to it, so the combined
  outcome is computable. This is *why* the catalog was built first (#89/#104).
- The **Outcome object** is this synthesis vertex: it references its **desired** side (UC sets →
  required caps) *and* its **current** side (assessment → maturity), and derives the combined outcome.
  **Customer/Stakeholder** personas live here; Architect/Engineer at the UC vertex, Assessor at the
  assessment vertex.
- Build-wise it's a **synthesis projection, not a new ingest** — reads both caches, joins on
  capability, ranks by gap × value. The step-1 substrate feeds all three vertices. *(Added to the
  build order as the combined-outcomes projection.)*

**The output is scoped too — by the Outcome (Chris, 2026-06-11).** Each vertex has its own subject,
so the **scope picker (step 3) is per-persona**:

| Vertex | Scope object | Persona |
|---|---|---|
| UC / architecture | **UC / UC Set** | Architect · Engineer |
| Assessment | **Assessment / finding-set** | Assessor |
| Combined outcomes | **Outcome / Initiative** = `{ desired: uc_set_ref · current: assessment_ref · statement }` | Customer · Stakeholder |

Selecting an Outcome resolves to its desired side (UC set → required caps) + current side (assessment
→ maturity), joins on the catalog, projects gap/roadmap/value. **Freshness composes** — a combined
Outcome is fresh iff *both* inputs are; the consumer chip ANDs them ("3 UCs stale · assessment 40d
old"), and the **combined fingerprint = `hash(uc-set fingerprint + assessment fingerprint)`**. The
**Outcome** is the only genuinely new object and is a *thin binding* — it references the two scopes;
the synthesis projection does the work. (Schema lands with step 7; the per-persona scope picker is
step 3.)

### Assessment ingest is multi-format (Chris, 2026-06-11)
The assessment vertex's **intake** must accept **JSON · YAML · PDF · Miro/whiteboard · photos**. The
unifier is the **extraction model** (#105's ingest-model): text artifacts (JSON/YAML/PDF) feed it as
text; images/Miro feed a **vision** model — same UDLM Assessment/Finding output, so everything
downstream (findings → capabilities → assessment cache → triangle) is format-agnostic, and the
fingerprint stays `hash(source artifact + extraction config)`. Status: JSON + PDF ✅ (#105); **YAML**
= a small add to the structured path; **photos (vision) + Miro** = #113 (needs the vision model wired
+ image/Miro routing).

## Versioning substrate — drift, publish/pin, queue, triangle (Chris decisions, 2026-06-11)
The next epic (#114 + #118–#120) rests on **one shared version model**, not four bolt-ons. Chris:
*"this is the version of the UCs and repo … both need to be versioned and if either changes, they are
stale … we need the version matrix tracking that roadmaps can be pinned to, but also allow per-UC
updates which once re-ingested, the roadmap can adjust too."*

**The version matrix.** Two versioned inputs per UC eval: the **UC content** (`uc_content_sha`) and the
**code it's evaluated against** (`source_repo_shas`, captured at ingest — step 1b). An eval is **current**
iff its `eval_fingerprint` still matches *both* current inputs. A change on **either axis → stale**
(this is the #114 decision: repo drift counts as stale, same axis as a UC edit). The matrix = UC × its
eval versions over time (each ingestion writes a new per-UC eval row at a fingerprint).

- **#114 drift = stale.** Staleness = `uc_content_sha changed OR captured source_repo_shas != current
  HEADs`. **Pass A — SHIPPED 2026-06-11:** `_current_project_repo_shas_cached` (120s TTL so the polled
  freshness endpoint doesn't hammer GitHub) + `_repo_drifted(captured, current)`; `/api/freshness`
  returns `stale_edited`/`stale_drifted` and `/api/results/uc-latest` returns per-UC `stale_edited`/
  `stale_drifted` (`stale = edited OR drifted`); freshness popover shows "(N edited · M code-drifted)",
  the audit `● stale · code` distinguishes drift, and drifted UCs already flow into the **▶ Ingest …**
  batch (failed/stale feed it). Degrades to no-drift if GitHub/token unavailable. **Pass B — TODO:** the
  **+N commits since eval** detail via a cached GitHub *compare* (base = captured SHA, head = current →
  `ahead_by`), shown in the popover.
- **#118 publish/pin = a published version, persona-driven.** Publish pins a **Scoping-Set evaluation
  snapshot** (the rollup of its members' pinned per-UC eval versions). **Consumer** personas
  (Customer/Stakeholder/Architect) read the *published* version by default; **builder** personas
  (Engineer) ride *latest*. No manual toggle — the persona selects it. The pin is a **living pin**: it
  tracks the matrix and advances a UC's cell when that UC is re-ingested *and approved* (below), rather
  than a hard freeze.
- **#119 queue = approval-gated re-ingestion.** Change detection (UC edit OR repo drift) **enqueues a
  debounced/coalesced rebuild proposal**; a human **approves** before it runs (GPU spend is
  deliberate). The Ingestions tab surfaces the pending queue (Approve / Dismiss). Approval is what moves
  a living pin forward. *No auto-run.*
- **#120 triangle apex = gap list + derived roadmap; the Outcome is its own scope unit.** A Combined
  Outcome `{statement, desired = ScopingSet, current = Assessment}` joins on the capability catalog →
  the **capability gap** (desired caps not satisfied by current) **plus a derived roadmap** to close it.
  Combined fingerprint = `hash(uc-set fp + assessment fp)`; freshness ANDs both inputs.

**Schema implication:** a `set_evaluation_version` concept (Scoping-Set fp + composing per-UC eval
versions → an immutable snapshot, with `published` flag + lineage) is the shared substrate for #118's
pin and #119's queue; #114 feeds its staleness inputs; #120's Outcome references two such versions
(one per side). Build order: **#114 (drift→stale) → #119 (queue) → #118 (publish/pin on the version
snapshot) → #120 (triangle on top).**

## Failure identification (#121, shipped 2026-06-11)
A UC can fall out of an ingestion four ways; the audit now makes all four visible (Chris,
2026-06-11 — "Capture + dropped-UC diff", Ingestion Audit hub):
- **engine-failed** — engine emitted `status='failed'`; reason = engine error / overall_assessment.
- **unreliable** — a *success* with `infra_confidence.label ∈ {low,compromised}` (soft note, not a failure).
- **dropped (`not_emitted`)** — **the keystone**: `_ingest_run_analyses` diffs the run's intended managed
  scope (`run_sessions.uc_state_snapshot`) minus what the engine emitted, and writes a **stub `failed`
  row** so a silently-dropped UC stops looking like "never attempted".
- **ingest-error** — file unparseable (phase reserved).

Capture: `uc_analyses += error_reason TEXT, error_phase TEXT` (`engine|analysis|ingest|not_emitted|
unreliable`). A `failed` latest row is **not coverage** — `/api/freshness` and `/api/results/uc-latest`
exclude it from `evaluated`/`ingested` (legacy `NULL` status still counts, so coverage doesn't regress)
and expose `failed` counts + `error_reason`/`error_phase`. Surface: the **UC Ingestion Audit** (Ingestions
tab) gains a **Failed** state + phase badge + inline reason, an **All/Failed/Stale** filter, a per-row
**↻ re-ingest**, and the batch action now reads "Ingest N needing evaluation" (failed feed it).
**Mirrored** (2026-06-11): the **Results** scoped list shows `✗ failed` + phase and its per-UC pane
renders a failure card (reason + phase + re-ingest); the **ingestion drawer** UC rows show a `✗ <phase>`
badge with the reason on hover, and `/api/results/{run_id}` appends dropped (`not_emitted`) UCs so the
drawer shows them too.

## Related
`ux-paradigm-design.md` (personas/lenses this serves) · `review-console-design.md` (§Navigation, the
run/results architecture) · `uc-driven-roadmaps-design.md` · #112 (masthead) · #43 (UC↔project matrix).
