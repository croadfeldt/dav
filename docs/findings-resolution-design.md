# Findings & Resolution — DAV's systemic Find → Track → Organize → Enable loop

**Status:** Design / requirements (living doc — build to it, update after changes). Authored 2026-06-19.
**Motivating trigger:** reviewing the feedback on our `dcm-project` PRs (e.g. machacekondra's
*"`depends_on` says **what** depends on what, but not **WHY** — and the WHY is what builds the graph"*,
the "missing `/info` discovery endpoint", "meta-providers are bad UX"). We want DAV to **find, track,
organize, and help enable solutions** for questions like these — and to do it the way DAV does everything:

> **DAV solves problems architecturally and systemically. Point solutions are limited.**
> **DAV assists with solving architectural and systemic findings — its primary purpose — but is not
> limited to that.** (operator, 2026-06-19)

**Primary goal — change submission via ADRs.** The point of the loop is to **submit changes to the architecture
*as* ADRs**: a finding → a proposed resolution → **validated** (against use cases) → accepted → it *drives* the
change (a spec/enhancement PR), with the ADR as the durable why. The ADR is the **unit of change proposal**, not
just a retrospective note — the same shape as a dcm-project **enhancement proposal** / a KEP, and exactly what
DAV's **Enhancement/PR Workbench (#138)** already surfaces. **DAV is where ADRs are authored, validated, and
submitted** (most relevant); **DCM consumes them for change-tracking + drift** (its ADRs are the change log; the
`OBSERVED` state flags when the architecture has drifted from a decision — the ADR-009/monolith case). Acceptance
is the quality gate: *you validate before you submit.*

**Scope.** Architectural/systemic findings (anchored to capabilities, the spec, dependencies) are the
**primary** and best-developed case — but the Finding→Resolution spine is **general**: a finding may be
about process, enablement, operations, or anything else, and resolve to a decision/action that isn't a
spec change. Just as DCM was "always just the first instantiation" of DAV's AD mode (`holistic-vision.md`),
the architecture finding is the first instantiation of the resolution loop, not its boundary. The anchor
generalizes from "capability" to "any resolution target across the three pillars (Platform / People-Process
/ Enablement) — or none." The systemic discipline (unify, anchor, resolve-once, project) is what's universal;
the *kind* of finding is open.

Pairs with: `holistic-vision.md` (the realization model), `capability-method-design.md` (capabilities =
the stable nouns), `uc-driven-roadmaps-design.md` (projections), `review-console-design.md` (the console +
the `pr_comments` ingest that already exists), `dav-self-improvement-vision.md` ("Framework proposes;
architect disposes").

---

## 1. Why this is *not* a "PR-comment tracker"

DAV already ingests PR comments (`pr_comments`, `pr_comment_poll_state` GitHub poller, `uc_pr_comment_links`,
a `status` lifecycle). Building a bespoke "review-comment tool" on top of that would be a **point solution** —
exactly what we don't want. A PR review comment is just **one source of one thing DAV already has a name for:
a Finding** — an externally-surfaced observation that points at the architecture and needs resolving. So does
an `assessment_finding`. So does a `uc_gap`. They differ only in **where they came from** and **what they were
evaluated against** — which is precisely the variability the Holistic model already absorbs:

> *"Each pillar is a different view of mostly the same data (UCs ↔ gaps ↔ capabilities). The same gap-analysis
> engine evaluates every pillar; what changes is the evaluation target and the ingestion method. The output
> shape is always the same: gaps → prioritized capabilities → strategy + roadmap."* — `holistic-vision.md`

The systemic move is therefore: **unify everything that needs resolving onto one Finding spine, anchor each to
the stable nouns (capabilities / decisions), and resolve via durable, reusable DecisionRecords** — instead of a
status field per comment. A review question becomes a first-class citizen of the same loop that already turns
gaps into roadmap.

**The gap this closes:** today a comment is a row with a `status`. There is no durable **Resolution** that (a)
connects back to the architecture element it concerns, (b) is **reusable** (one answer closes many like it), and
(c) records the **WHY**. That missing WHY is *literally the feedback we received*. Making the WHY a first-class,
capability-anchored artifact in DAV is both the fix for this feature and the answer to the reviewer.

---

## 2. The systemic frame (one diagram in words)

```
  SIGNALS                FINDINGS                ARCHITECTURE ANCHOR         RESOLUTION                PROJECTIONS
  (any source)           (unified spine)         (the stable nouns)          (durable decision)        (existing)
  ───────────            ───────────────         ───────────────────         ──────────────────        ──────────
  PR review comments ─┐                       ┌─ capability               ┌─ DecisionRecord (the WHY) ─┐
  assessment findings ─┼─► Finding ──organize─┼─ capability_dependency ───┼─ action: answer/clarify/   ├─► roadmap
  uc_gaps             ─┤   (source, body,     ├─ spec section             │   change-spec/defer/wontfix│   (uc-driven)
  customer mtg / rec  ─┤    provenance,        ├─ use case                 ├─ closes N findings (M:N) ──┼─► enablement
  issues / manual     ─┘    classification)    └─ theme                    └─ artifact: proposal/PR ────┘   pillar
        │                        │                       │                          │                        │
        └──── FIND ──────────────┴──── TRACK ────────────┴──── ORGANIZE ────────────┴──── ENABLE ────────────┘
                      (LLM-assisted at every hop; Framework proposes, architect disposes)
```

Same engine, same output shape as the rest of DAV. The four verbs map to the four hops.

---

## 3. The four verbs (the capability)

### 3.1 FIND — surface signals from any source, normalize to Findings
- **Sources (pluggable, generalized — not GitHub-only):** GitHub PR review comments + issues (poller exists);
  assessment ingest (exists, #91/#105); `uc_gaps` from evaluations (exists); meeting/recording transcripts
  (#176/#180); manual entry. New sources are *adapters*, not new subsystems — aligns with **#153 single smart
  ingest** and **#177 configurable pipeline**.
- **LLM extraction + classification on ingest:** each signal → a Finding with a **type**
  (`question | objection | gap | risk | nit | duplicate | already-addressed`) and a one-line normalized
  statement. Multi-comment threads collapse to one Finding with the thread retained.
- **De-dup / cluster on the way in** (reuse the `customer-demand-dedup` machinery, #181): near-identical
  questions across PRs/customers attach to the *same* Finding so we resolve once. This is the first place
  "systemic" beats "point".
- **Provenance preserved:** repo/PR/file/line/author/thread/SHA (have most of it in `pr_comments`); for other
  sources, the analogous origin. Field-level provenance per UDLM (#191).

### 3.2 TRACK — one lifecycle, one source of truth, governed
- **Shared Finding lifecycle** (supersedes the per-comment `status`): `new → triaged → anchored → drafting →
  proposed → resolved → (verified) | deferred | wontfix | duplicate`. Versioned on the existing evaluation/
  versioning substrate (#114–120); every transition in the **audit log** (#87/#103).
- **One queryable backlog across sources** — "what's open against capability X / spec Y / this PR / this
  customer", not three disjoint lists. Signal-over-noise: a verdict + the few drivers, never a dump.
- **Freshness/Staleness** reused (#114–117): when the underlying spec/capability changes, anchored findings can
  re-evaluate ("is this still open?").

### 3.3 ORGANIZE — anchor to the stable nouns, roll up to themes
- **Anchor each Finding to ≥1 resolution target.** **P1 scope = `capability` / `capability_dependency`
  (`uc_capability_deps`) / spec section only** (architecture findings). The general model also recognizes two
  non-architecture anchor families for later: a **value-stream** (the *value* anchor, People-Process pillar) and
  an **enablement** target — and **enablement is upstream of value** (you enable, which realizes value). A
  finding may also anchor to a `use_case`, a `theme`, or **none**. The anchor — not the source — is how findings
  are organized. This is the systemic hinge: capabilities
  are the stable nouns (capability method), so organizing by them makes the architectural backlog *durable* across
  PRs, customers, and time; non-architectural findings organize by their pillar/target the same way.
- **Cluster + theme roll-up** reuses `themes` and the roadmap grouping: many findings → a theme → a roadmap line.
- **Coverage view:** which capabilities/dependencies attract the most open findings = where the architecture is
  least settled = where to spend. (Mirrors the gap-density / foundational-leverage prioritization already in the
  model.)

### 3.4 ENABLE solutions — resolution-assist → durable decision → close many → project
- **LLM resolution-assist (proposes):** given a Finding + retrieved context (the anchored capability, the spec,
  prior DecisionRecords), the engine drafts: a **resolution**, an **action type**
  (`answer-as-is | clarify-doc | change-spec | defer | wontfix`), an **already-addressed citation** if a prior
  decision covers it, and the **affected capabilities/deps**. Reuses the eval engine + prompt management (#92) +
  A/B experiments (#94). The engine *proposes*.
- **Resolution validation — test it against use cases (with variance) [core].** A candidate resolution is
  **evaluated, not just drafted.** The evaluation phase (a) selects the **applicable use cases** — relevance is
  determined *at evaluation time*, not pre-bound — and (b) **generates UC variants with variance** (coverage +
  adversarial, the UC-generation C-modes) to stress it; then runs the **same evaluation engine** with the
  **target = the proposed resolution** instead of the architecture spec. A resolution **passes only if it
  holds**: it supports the UCs it claims, **regresses no previously-supported UC**, and survives the adversarial
  variants. Those per-UC verdicts + regressions are the **evidence** the architect disposes on. *Framework
  proposes — and now proves — architect disposes.* (Side benefit: variance UCs that prove useful feed back into
  the corpus as new coverage — resolution validation *produces* ground truth.)
- **Architect disposes:** human edits/accepts in the **Enhancement/PR Workbench (#138)** (extended to inbound
  findings) — the existing per-PR + per-finding console; ties to **#179 review & commenting**.
- **Resolution = a durable, capability-anchored DecisionRecord (the WHY).** This is the payoff: the answer is
  not a reply, it's a decision bound to the capability/dependency it justifies — queryable, reusable, and the
  literal answer to "you need the WHY". `improvement_proposals` (#015) is the partial precedent; promote it to a
  first-class **DecisionRecord** (rationale, action, links, version).
- **One resolution closes many** (M:N Finding↔Resolution): resolving "depends_on needs WHY" once closes that
  comment *and* the class — and pre-empts re-asking, because the decision is discoverable.
- **Close the loop into existing projections:** a resolution can (a) spawn an `improvement_proposal` / PR if a
  change is needed, (b) feed the **roadmap** and the **Enablement pillar** view (gap-rich → enablement), (c)
  **post back to the source** (GitHub reply) — **human-gated**, via the outbound queue (#97). Never auto-post.

---

### 3.5 Evaluation is a shared primitive — gap analysis *and* resolution validation (*and* other uses)
DAV's core loop is **"evaluate use case(s) against a target."** The Holistic model already varies the *target*
by pillar; this generalizes it one step further: the target is **parameterized** and chosen **per evaluation
phase** —

| Evaluation phase | Use cases (selected + generated-with-variance) | Target | Purpose |
|---|---|---|---|
| Architecture review | corpus UCs for the scope | the **spec / architecture** | **gap analysis** (original mode) |
| Resolution validation | applicable UCs + variance (coverage/adversarial) | a **proposed resolution** | **does the resolution hold?** |
| (other appropriate uses) | selected at phase time | a candidate (provider, design, change…) | as defined |

One engine, many purposes — **resolutions are validated by the very mechanism that finds the gaps.** "Which use
cases apply, and to what" is **determined during the evaluation phase** (relevance selection + variance
generation), not pre-bound to the UC. This is the same insight that lets the engine serve every pillar
(`holistic-vision.md`), extended so the *target* can be a resolution. No separate validation engine, no fork.

### 3.6 The DecisionRecord — the durable write-up of a resolution (confirmed first-class)
**DecisionRecord is the established Decision Record / ADR concept — *adopted*, not a DAV invention.** It is the
**Decision Record (DR)** (of which an **ADR — Architecture Decision Record — is the architecture-scoped kind**),
defined as a member of the UDLM **Knowledge entity-type family** (`udlm/entities/knowledge-family.md` §4.5)
alongside `Capability` / `TaxonomyTerm` — so the **WHY lives in the substrate** and any realization (DCM, DAV,
peers) carries decision provenance natively, paired with `universal-audit` + field-level provenance. We adopt the
ADR *format + lifecycle by reference* and keep the **prose `rationale` body first-class** (the structure is an
envelope, not a replacement). DAV **realizes** it (per #106, all of DAV operates via UDLM); the curation lifecycle
(`PROPOSED → UNDER_REVIEW → CANONICAL`) is ADR status, and **validation gates `CANONICAL` only where use cases are
applicable** (a non-testable decision, e.g. a naming choice, can be CANONICAL without it — compatible with ordinary ADRs).

**Yes: a DecisionRecord *is* the write-up of the resolution process** — not a one-line answer. It is the durable,
versioned artifact that captures *how and why a finding (or class of findings) was resolved*, so the reasoning is
auditable, reusable, and queryable. Think "ADR, but generated-with-assist and **validation-backed**." It records:

- **The question/finding(s)** it resolves (M:N — one record can close a class).
- **The anchor** — the capability / dependency / spec section (P1) it is a decision *about*.
- **The analysis** — options considered, what the LLM proposed, what was retrieved (prior decisions, spec).
- **The validation evidence** — the use cases it was tested against (submitted + variance), the per-UC verdicts,
  any regressions, adversarial survivals. *A DecisionRecord is only "accepted" with passing validation attached.*
- **The decision + the WHY** — the chosen resolution and its **rationale** (this is the "you-need-the-WHY"
  payload, bound to the capability it justifies).
- **The action** — `answer-as-is | clarify-doc | change-spec | defer | wontfix` + any spawned
  `improvement_proposal` / PR / commit link.
- **Provenance + lifecycle** — author(s) (human + which model/prompt version proposed it), version, supersedes/
  superseded-by, comm-back status (was it posted to the source).

Because it's anchored to the capability and carries its validation, the DecisionRecord set becomes DAV's
**queryable "why the architecture is the way it is" store** — the systemic asset. New/duplicate findings can be
auto-matched to an existing DecisionRecord ("already-addressed → cite it"), which is what makes resolution scale
instead of repeating.

---

### 3.7 Who produces and consumes ADRs — and the validation DAV must do
The ADR/DR record type earns its fields only if real producers write them and real consumers read them. Mapping
both also pins down **the validation DAV owns** — which is the whole reason DAV is central here.

**Producers (what writes an ADR):**
- **P1 — the Findings & Resolutions loop (primary):** finding → resolution-assist draft → **UC-validation gate** →
  architect accepts → ADR emitted `CANONICAL`. Automated-assist, human-disposed.
- **P2 — direct human authoring:** the architect writes an ADR proactively (no finding) — e.g. a design choice.
  DCM's existing 17 hand-written ADRs are this; they import as DecisionRecords (backfill).
- **P3 — agent first-pass (PAT):** an agent drafts candidate ADRs/triage; human approves.
- **P4 — supersession:** a drift signal or new information triggers a new decision that **supersedes** an old ADR.

**Consumers (what reads an ADR):**
- **C1 — the capability graph ("the WHY on the edge"):** "why does capability X `depend_on` Y?" reads the ADR
  `about` that edge. (The literal answer to the `depends_on`-WHY feedback.)
- **C2 — already-addressed matching (dedup):** a new finding searches existing ADRs → "already decided, cite it" →
  close without re-deciding. DAV consumes its own output.
- **C3 — continuous re-validation (drift) — the validation DAV must do:** ADRs are **not write-once.** When the
  spec/capability an ADR is `about` changes (a freshness signal, #114–120), DAV **re-runs that ADR's validation
  UCs** against current state. Pass → still `CANONICAL`; **fail → premises drifted → `OBSERVED` ≠ `CANONICAL` →
  the ADR becomes a *new Finding* ("ADR-009's '9 services' no longer holds") and re-enters the loop.** This is
  what would have caught the stale ADR-009/monolith README automatically.
- **C4 — humans / re-engagement:** read the narrative to understand or answer "why" to the dcm-project team.
- **C5 — realizations (DCM):** the ADRs governing their architecture; implementation-vs-decision drift.
- **C6 — roadmap / SOW:** `change-spec` ADRs project into roadmap items / SOW (existing projection).

**The two validation duties DAV owns** (everything else is produce/consume plumbing):
1. **Produce-time gate:** no ADR reaches `CANONICAL` without passing UC validation *where UCs apply* (§3.4).
2. **Consume-time re-validation:** ADRs are continuously re-checked against the moving architecture; drift demotes
   them and spawns a finding. **This is DAV's job because DAV owns the UC-evaluation engine + the freshness
   substrate** — re-validating an ADR is just re-evaluating its UCs, reusing #114–120, no new machinery.

**Why this pins the data model (and the trim):** the producer/consumer flows require exactly — and only — the
**non-duplicative slice**: the `about` anchor (so a spec change *triggers* re-validation), `decides`→Finding(s)
(dedup/close), and the **UC-refs / validation_evidence** (so the drift check is *re-runnable*), plus the
`CANONICAL`↔`OBSERVED` lifecycle. The change-trace, provenance origin, and supersede storage are **reused** from
Universal Audit + field-level provenance + versioning; the narrative **is** an ADR (reused). Nothing produces or
consumes a redefinition of those — confirming the trim.

## 4. Data model (reuse-first)

| Concept | Reuse (exists) | Add |
|---|---|---|
| Signal ingest | `pr_comments`, `pr_comment_poll_state`, poller; assessment ingest; `uc_gaps` | source adapters; on-ingest classify |
| **Finding** (spine) | logically unify `pr_comments` + `assessment_findings` + `uc_gaps` behind a shared view/interface | `finding_type`, normalized statement, lifecycle `status`, cluster_id |
| Architecture anchor | `capability_catalog`, `uc_capabilities`, **`uc_capability_deps`**, `themes`, `managed_use_cases` | `finding_anchor` (finding ↔ element, M:N, polymorphic element_ref) |
| **Resolution / DecisionRecord** | `improvement_proposals` (partial) | `resolution` (rationale, action_type, version, comm-back status) + `resolution_finding` (M:N) + anchor to capability/decision |
| Engine | eval engine, prompt mgmt (#92), experiments (#94), provenance (#191) | a **resolution-assist** stage + prompts |
| Console | **Enhancement/PR Workbench (#138)**, #179 review/commenting, audience lenses | inbound-findings view + resolution editor |
| Governance | `audit_log` (#87), tenancy (customers/projects), PAT agents (#167) | transitions audited; agent first-pass triage |
| Outbound | outbound message queue (#97) | GitHub reply adapter (human-gated) |

**Note (don't over-build):** the Finding "unify" can be **logical** first — a shared `resolvable` interface /
view over the three existing tables + a common `finding_anchor` and `resolution` — rather than a physical table
merge. Physical consolidation only if/when it earns it. (Whole-system reuse > parallel mechanisms, but migration
cost is real.)

---

## 5. What makes it systemic (the guarantees)

1. **One spine, many sources.** A review comment, a gap, an assessment finding, a customer question all enter the
   *same* loop and the *same* backlog. No per-source silo. (Adding a source = an adapter.)
2. **Anchored to stable nouns.** Findings organize by capability/dependency/decision, not by PR. The backlog
   survives PRs closing, customers rotating, and specs versioning.
3. **One-resolves-many.** A DecisionRecord closes a *class* of findings and pre-empts the next instance — the
   opposite of answering each comment.
4. **The WHY becomes data.** Resolutions are capability-anchored decisions → DAV becomes the queryable system of
   record for *why the architecture is the way it is*. (Directly answers the `depends_on`-WHY feedback; pairs with
   **#126** eliciting `depends_on` edges — now the edges carry their rationale.)
5. **Same engine, same projections.** Resolution-assist is the eval loop with a different target; output flows to
   the existing roadmap + enablement views. Nothing parallel.
6. **Dogfoods DCM's own ideals (#184):** source = a **provider**; findings = **UDLM entities**; resolution = a
   governed, audited, provenance-tracked pipeline. DAV applying DCM to itself.

---

## 6. Requirements

**FIND**
- **RF-1** Ingest signals from pluggable sources (GitHub PR comments/issues [have], assessments [have], gaps
  [have], recordings, manual) via adapters, not bespoke subsystems.
- **RF-2** On ingest, LLM classifies each signal (`finding_type`) and emits a normalized one-line statement;
  threads collapse to one Finding.
- **RF-3** De-dup/cluster near-identical findings across sources (reuse demand-dedup) so a class resolves once.
- **RF-4** Preserve full provenance (origin, author, file/line/thread, SHA; field-level per UDLM).

**TRACK**
- **RT-1** One shared Finding lifecycle + status across all sources; supersedes the per-comment `status`.
- **RT-2** Every transition versioned + audited; sliceable by source, capability, PR, customer, theme.
- **RT-3** One cross-source backlog query ("what's open against X"); freshness re-checks on spec/capability change.

**ORGANIZE**
- **RO-1** Anchor each Finding to ≥1 resolution target (M:N): primary = capability / capability_dependency /
  spec section; also People-Process / Enablement targets, UC, theme, or none. Architecture is primary, not the
  boundary.
- **RO-2** Cluster + roll up to themes/roadmap lines; expose a capability-coverage ("most-contested") view.

**ENABLE**
- **RE-1** LLM resolution-assist proposes resolution + action_type + already-addressed citation + affected
  capabilities (Framework proposes).
- **RE-2** Human dispositions in the workbench; nothing auto-resolves or auto-posts (architect disposes).
- **RE-3** Resolution is a durable, capability-anchored **DecisionRecord** (rationale + action + version) — the WHY.
- **RE-4** One Resolution may close many Findings (M:N); duplicates close with the class.
- **RE-5** A Resolution can spawn an `improvement_proposal`/PR, feed roadmap + enablement projections, and
  (human-gated) post back to the source via the outbound queue.
- **RE-6** Every candidate resolution is **validated by use-case evaluation** before acceptance: applicable UCs
  (submitted **and** generated-with-variance — coverage + adversarial) are evaluated against the resolution on
  the **same engine** as gap analysis. Pass = supports claimed UCs **and** regresses no previously-supported UC.
- **RE-7** UC application + purpose are **determined per evaluation phase** (relevance selection + variance
  generation), not pre-bound. The evaluation target is parameterized: `architecture/spec` (gap analysis) |
  `resolution` (validation) | other — one engine, no fork.
- **RE-8** A DecisionRecord is the **validation-backed write-up of the resolution process** (finding(s) →
  analysis → validation evidence → decision + WHY → action + anchor + provenance/version); it accepts only with
  passing validation attached; new/duplicate findings auto-match to existing DecisionRecords.

**CROSS-CUTTING**
- **RX-1** Signal over noise — every surface changes a decision; verdict + drivers, not a dump.
- **RX-2** Governed: tenancy-scoped, audited, agent-capable (PAT) for first-pass triage.
- **RX-3** No point solutions — a new signal type or output is an adapter/projection on the spine, not a fork.

---

## 7. Plan (phased — each a focused session; smallest high-value first)

**P1 — Spine + capability anchor + assist + validation + DecisionRecord (MVP, highest leverage).**
Scope locked to **PR/Spec findings, capability/dep/spec anchors only.** Add `finding_anchor`
(Finding ↔ capability/dep/spec), a **first-class `DecisionRecord`** (§3.6), and the M:N links, over the
*existing* `pr_comments` (logical spine, no table merge). Wire the **resolution-assist** stage (draft + anchor
proposal + already-addressed) **and the resolution-validation gate** — evaluate applicable + variance UCs
against the candidate resolution on the shared engine; a DecisionRecord only accepts with passing validation
attached. Surface in the Enhancement/PR Workbench (#138). **Seed corpus:** backfill the `dcm-project` PR
feedback (website-#8; machacekondra/gabriel) as the first Findings and take the **`depends_on`-WHY** one
end-to-end — drafted, UC-validated, recorded — as the worked example. *Proves the whole loop incl. validation.*

**Executable validation set (this capability dogfooded).** This design is itself expressed as 7 analytical use
cases — DAV evaluating DAV — in the self-eval corpus `examples/dav-self/dav/use-cases/findings_resolution/`
(`uc-fr-001`…`007`), grouped by the **"Findings & Resolutions (self-eval)"** scoping set (loader:
`examples/dav-self/load-into-dav.sh`; auto-syncs once #42's git→DB round-trip lands). They trace the operator
goal — *submit changes to the architecture via ADRs* — across FIND→ENABLE→SUBMIT→ORGANIZE→CONSUME and map 1:1 to
the §6 requirements (incl. `uc-fr-003` = the RE-6/7 validation gate, `uc-fr-006` = decision-drift / DCM
change-tracking). Running this set against this document is the standing gap check on the capability.

**P2 — Systemic close.** M:N one-resolves-many + clustering (dedup #181); auto-match new findings to existing
DecisionRecords ("already-addressed → cite"); the capability **coverage / "most-contested"** view; project
resolutions into the **roadmap** + (later) the **Enablement** pillar. Joins #126 (the `depends_on` edges now
carry their WHY).

**P3 — Sources + round-trip.** Generalize ingest adapters (issues, recordings, manual) behind #153/#177;
human-gated GitHub post-back via #97; agent (PAT) first-pass triage.

**P4 — Logical→physical unify (only if earned).** Collapse `pr_comments`/`assessment_findings`/`uc_gaps` onto the
shared spine physically; retire the per-source code paths (standardization-over-customization).

---

## 8. Connections (so this doesn't fork the system)
Builds on / consolidates: **#179** (UC review & commenting), **#138** (Enhancement/PR Workbench), **#126**
(`depends_on` edges — now WHY-bearing), **#177** (configurable pipeline), **#153** (smart ingest), **#97**
(outbound queue), **#181** (dedup), **#114–120** (versioning/freshness), **#106** (UDLM data model), **#184**
(DCM ideals on DAV), **#163** (re-engage — the resolved decisions are what we bring back to the team).

## 9. Decisions (resolved 2026-06-19)
1. **Scope of first build → DECIDED:** limit to **PR / Spec findings with capability anchors** (architecture).
   Non-architecture sources/targets are later phases.
2. **Non-architecture anchors → DECIDED:** the two other anchor families are **value** (value-streams,
   People-Process) and **enablement**, where **enablement is upstream of value**. (Modeled now, built later.)
3. **DecisionRecord → DECIDED first-class, AND a UDLM record type.** It *is* the durable write-up of the
   resolution process (finding → analysis → validation evidence → decision + WHY → action), validation-backed.
   Defined in the substrate as a **UDLM Knowledge-family entity type** (`udlm/entities/knowledge-family.md` §4.5)
   so the WHY is universal; DAV realizes it. See §3.6. (operator, 2026-06-19: "tracking the why… belongs in udlm
   as a record type".)
8. **Name/standard → DECIDED: adopt ADR/DR, don't invent.** A `DecisionRecord` *is* the established **Decision
   Record** (ADR = the architecture-scoped kind); we adopt the ADR format + lifecycle by reference. **Coherence
   with UDLM verified** — it's `Data`/Knowledge; ADR's "supersede, don't edit" = UDLM immutability + `supersedes`;
   ADR status = the curation lifecycle; `OBSERVED` adds decision-drift. Only caveat: the four-state names carry
   provisioning connotation, reinterpreted as `UNDER_REVIEW` for curated artifacts. No structural incompatibility.
   Prose body stays first-class; validation gates only where UCs apply. (operator, 2026-06-19.)
4. **Resolution validation → DECIDED core:** every candidate resolution is validated by evaluating use cases
   (submitted **and** generated-with-variance) against it, on the shared engine; UC application + purpose chosen
   per evaluation phase. See §3.4 + §3.5.
5. **House name → DECIDED: "Findings & Resolutions"** (its own console domain; extends the Enhancement/PR
   Workbench #138 rather than forking).
6. **Post-back → DECIDED: human-gated** (DAV drafts, human approves, posts via outbound queue #97). GitHub first.
7. **Finding unify → logical first (shared view/interface over `pr_comments`/`assessment_findings`/`uc_gaps`),
   physical only if earned.** (Standing recommendation; revisit at P4.)
8. **(above) Adopt ADR/DR, don't invent; coherence with UDLM verified.**
9. **Standards-adoption + consolidation → DECIDED (operator, 2026-06-19, "save this methodology"):** applied the
   UDLM adoption decision procedure (`udlm/design-principles/adopted-standards.md` §1b; mirrored as
   [[feedback_standards_adoption_methodology]]). Outcomes:
   - **Adopt the ADR / MADR format** for the DR fields (don't roll our own field set).
   - **Consolidate `improvement_proposals` → Resolution/DecisionRecord, target-parameterized.** The
     self-improvement `improvement_proposals` (trigger `signature_class` → `rationale` → `proposed_change` +
     `target` → `predicted_effect`/`evidence` → `status`/review) is **the same shape** as an architecture
     resolution; differs only by *target* (DAV knob vs spec/capability). One Resolution model, target chosen per
     evaluation phase (§3.5). **Net-negative on bespoke surface area** — retire the divergence, don't add a third.
   - **SARIF** (OASIS findings standard) does **not** cleanly fit architecture findings (code/file-shaped) →
     **adopt its principles** (rule · result · level · location), not the schema.
   - **`Antipattern`** (UDLM Knowledge) is related but not the same (a standing rule, not a point decision) →
     **don't force**; an ADR may *produce* one.

_All settled per operator review 2026-06-19; this section is the decision log going forward._
