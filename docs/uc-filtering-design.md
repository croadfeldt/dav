# Use-Case filtering, criteria & scoping — design

Status: **adopted 2026-06-30** · Tasks: #244 (filter design — UC list + views), #245 (UC
criteria), #246 (Scoping-Set criteria) · Related: #239 / #43 / #243 (scope-drives-views),
#111 (reusable list widget).

This is the living design for **how a user finds, narrows, and selects use cases (UCs)** in the
review console. It replaces the ad-hoc "filter wall" (one search box + seven dropdowns) on the Use
Cases list with a single, well-known paradigm, and pins down the *criteria* that a UC and a Scoping
Set are filtered by.

---

## 1. The paradigm — adopt, don't invent

Per the standards-adoption methodology (adopt a wide standard if it cleanly fits; supersede the
bespoke surface), the filter UX adopts two established, open paradigms rather than a DAV-invented one:

1. **Filtered-search qualifiers (GitHub / GitLab grammar)** — a single search box that accepts
   `key:value` tokens mixed with free text, e.g. `state:draft priority:high tag:billing secure`.
   This is the de-facto open standard for list filtering (GitHub issue search, GitLab filtered
   search bar). Bare words are a substring search over title / uuid / handle / tags.
2. **Attribute-value filter chips (PatternFly toolbar pattern)** — every active structured filter
   renders as a **removable chip**; an **"+ Filter ▾"** menu lets you pick an attribute then a value
   without knowing the grammar. PatternFly is Red Hat OSS and DAV is already PatternFly-styled
   (`pf-*`), so this is the on-brand, zero-new-dependency choice.

The two are the **same model from two ends**: the qualifier grammar is the keyboard path, the chip
menu is the pointer path, and both read/write one shared filter state. Nothing DAV-specific is
invented — only the *facet registry* (below) is ours, and it is data, not a paradigm.

### Non-goals
- No query language beyond `key:value` + free text (no boolean operators, ranges, or parentheses in
  slice 1 — GitHub-style flat AND of qualifiers is enough and is what users expect).
- No saved-query persistence in slice 1 (Scoping Sets already are the durable saved selection — §4).

---

## 2. Two kinds of filter — keep them distinct

A UC list filter is one of exactly two kinds. Conflating them is what made the old row confusing.

| Kind | What it answers | Mechanism | Examples |
|------|-----------------|-----------|----------|
| **Scope** | *Which population am I looking at?* | server-side — changes the fetch (`loadUCs`) | `source`, `repo`, `scope` (this-project vs apply-pool) |
| **Attribute** | *Within that population, which ones?* | client-side — narrows the loaded set (`renderUCList`) | `state`, `priority`, `assigned`, `health`, `tag`, free text |

**Scope** axes are the *population selector*. The long-term home for population selection is the
**masthead Project + Scope** control (#239/#43/#43) — see §5, slice 2. Until then they live in the
same bar as qualifiers (exactly as GitHub puts `repo:` next to `label:`), so there is one paradigm
regardless of where the default eventually comes from.

---

## 3. UC filter criteria — the facet registry (#244, #245)

These are the **canonical, complete** criteria a UC is filtered by. The registry is the single source
of truth: the parser, the chip renderer, and the "+ Filter" menu are all generated from it.

| Facet (`key`) | Kind | Values | Semantics |
|---------------|------|--------|-----------|
| *(free text)* | attribute | any string | substring over `title`, `uuid`, `handle`, `tags` |
| `state` | attribute | `draft` `ready` `in_review` `approved` `deprecated` `all` | lifecycle state. **Default (no chip): active** = hide `deprecated`. `all` includes deprecated. Corpus UCs (no lifecycle) are always shown. |
| `priority` | attribute | `critical` `high` `medium` `low` | roadmap priority band |
| `assigned` | attribute | `yes` / `no` (aliases of assigned / unassigned) | membership in ≥1 Scoping Set |
| `health` | attribute | `valid` / `invalid` | engine validation (#122) |
| `tag` | attribute | any existing tag | **exact** tag match (the menu lists tags present in the loaded set). Bare free text still does substring tag match. |
| `source` | **scope** | `managed` / `corpus` | provenance: DB-authored vs corpus-repo |
| `repo` | **scope** | dynamic — corpus namespaces (`repo@branch`) | one corpus repo/branch (#243) |
| `scope` | **scope** | *(default)* this project / `pool` | this project's UCs vs the tenant pool available to apply (#43) |

Rules:
- A facet is **active** iff its backing value is non-empty (the `state` "active" default is value-less
  → no chip). Active facets render as chips; the default state shows none.
- Qualifiers AND together (a UC must match every active facet).
- `sort:priority` is a **sort, not a filter** — it stays a separate toggle button, not a chip.

### UC criteria (#245), stated plainly
A use case is, for filtering/selection purposes, the tuple:
`{ title, uuid, handle, tags[], lifecycle_state, priority, source, namespace(repo@branch),
set_ids[], health }`. Everything the list filters on is one of these fields — there are no hidden
criteria. (The richer UDLM-native UC *content* model — entity/policy/provider/lifecycle primitives —
is tracked separately under the UDLM-native UC vocabulary work and is orthogonal to this list-filter
contract; when it lands, those primitives become additional facets in this same registry.)

---

## 4. Scoping-Set criteria (#246)

A **Scoping Set** is the *durable, named, explicit* counterpart to the *transient* filter bar:

| | Filter bar (this doc) | Scoping Set |
|---|---|---|
| Lifetime | transient (per view, not saved) | durable (named, persisted, shareable) |
| Membership | computed predicate over facets | **explicit** UC list (M:N `set_ids`) |
| Purpose | *find* UCs right now | *fix* a population for runs, scoping, and views |

Design decisions:
- A Scoping Set's membership is an **explicit set of UCs**, not a saved query. (A UC's tags/state can
  change; a Set is a deliberate, stable selection — that's why importance/coverage can be measured
  against it.) Saved *queries* are a possible future facet but are **not** what a Set is.
- The filter bar and Sets compose: filter to narrow, multi-select, **"Add to Set"** — the established
  "filter → select → act" flow. The `assigned` facet closes the loop (find UCs in no Set).
- A Set, once chosen in the masthead Scope, **scopes the data shown in every view** (#239), the same
  way `source`/`repo`/`scope` scope the UC list — Sets are first-class population selectors.

---

## 5. Rollout slices

- **Slice 1 (this change) — Use Cases list.** Replace the 7-dropdown row with the qualifier+chip bar.
  Implemented as an **adapter over the existing controls**: the seven `<select>`s + search input are
  retained *hidden* as the state store, so `loadUCs`/`renderUCList` read them unchanged; the new bar
  writes into them and dispatches their events. Zero change to filtering logic → low risk. Adds the
  `tag` facet (the one genuinely new criterion, answering "Tags?"). Also renames the **Authoring**
  domain to **Use Cases**.
- **Slice 2 — Scope → masthead.** Move the three *scope* facets (`source`/`repo`/`scope`) out of the
  per-list bar and into the masthead Project + Scope control, making it the content basis for Analyze,
  Roadmaps, and Capabilities too (#239/#43). The bar then carries only attribute facets.
- **Slice 3 — Reuse across views.** Generalize the chip bar + facet registry into the Results view and
  the Scoping-Sets palette (#244 "UC list **+ views**", #111 reusable widget).

## 5a. Project + Scope = the universal content basis (#239 / TODO1/3)

The masthead **Project** (hard data scope) + **Scope** (a Scoping Set) are the content basis for every
**consumer/projection** view — the surfaces that *read* evaluations, not the *authoring registries* that
*write* them. This is the established contextual-chrome rule (`_updateContextChrome`): the Scope chip
shows only on consumer views and is hidden on authoring views.

| Domain / view | Kind | Honors masthead Scope? |
|---|---|---|
| Analyze › Results | consumer | ✅ `scopeQuery()` |
| Analyze › Runs (analyses list) | working context | ➖ intentionally not scoped (a run is execution context, not a projection) |
| Roadmaps › Arch Review | consumer | ✅ `_activeScope` |
| Roadmaps › Enhancement / PR | consumer | ✅ `_activeScope` |
| Roadmaps › Engineering Roadmap (cap views) | consumer | ✅ `scopeQuery()` |
| Roadmaps › Engineering Roadmap (synthesized projection) | consumer | ✅ **fixed here** — `_loadRoadmapProjection` now passes `set_id`; `setScope` refreshes it |
| Capabilities › Cap Map | consumer | ✅ `set_id` |
| Capabilities › Catalog | **authoring registry** | ➖ intentionally not scoped (the capability registry, like the UC list — carries its own filters) |
| Coverage chip | consumer status | ✅ **added here** — `/api/freshness?set_id=…` |
| Assessments · Maturity Wall | consumer | ✅ `_activeScope` (assessment-specific scoping still open — see #200) |

Rule of thumb: **authoring registries (Use Cases, Catalog) are never scope-filtered** — they are where
you *curate* the corpus, and have their own in-view filter bars (§1–3). **Consumer views are always
scope-filtered** by Project + Scope. A Scoping Set chosen in the masthead is the content basis for all
of them at once. (Assessments scoping is deferred — its unit isn't a UC set; tracked as #200.)

## 6. Verification
- Drift guard (`node build.mjs --check`) + `eslint no-undef` + e2e boot-smoke (all roles) stay green.
- Every old filter still works (the hidden controls + their listeners are untouched).
- A user can reproduce any prior filter via either the menu (chips) or the qualifier grammar.
