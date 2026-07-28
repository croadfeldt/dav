## 🔭 CURRENT EPIC — UX paradigm → persona shell → UC-scoped evaluation
Design settled 2026-06-11. Docs: **`ux-paradigm-design.md`** (personas as lenses over constant
objectives) + **`uc-scoped-evaluation-design.md`** (scope = UC/Set → fingerprinted per-UC result
cache, rebuilt on change → derived outcome requirements + roadmap; run = rebuild job; masthead
freshness chip = #112).
- ✅ **SHIPPED:** the app-wide **domain shell** (left domain rail + top sub-tab strip), the
  **persona switcher** (generalizes focus; default-by-RBAC, switchable, orthogonal to view-mode),
  the **bundles** manager (#107 4c/4d), and **run-selector removal** (masthead → read-only run
  status). Run-selector audit fixed Engineering (`engRunSel` writes the shared run) + Results copy
  (→ Runs tab) as interim unbreaks.
- ✅ **SHIPPED:** step 1a (eval fingerprint) + 1b-capture (repo HEAD SHAs at ingest, guarded); step 2
  (`/api/freshness`); step 4 (masthead freshness chip + live run chip #112, pulse-on-attention only).
- 🆕 **EPIC — Customer demand & compatibility-aware UC dedup (design `customer-demand-dedup-design.md`,
  2026-06-12):** new paradigm — **Customer is a first-class entity, orthogonal to Project (M:N)** (DCM =
  1 project/many customers; Assessments = customer-focused/many projects); **importance = DISTINCT
  customers** (anti-poisoning: same customer asking 10× ≠ 10× importance); dedup-on-ingest is a
  **disposition** (skip / import / **bump** = log a request on the canonical UC / **increase & adapt**),
  gated by a **semantic-similarity** score + a **compatibility** score.
  **Phase 1 SHIPPED:** per-customer demand log (`uc_customer_requests`, text `customer` = forward-compat
  seam to the entity) + denormalized `managed_use_cases.customer_requests`; list badge `👥 distinct·total`
  (multi-tenant highlighted) + UC-detail **Customer demand** panel (rollup, per-customer chips, attributed
  log w/ log+delete); `GET/POST/DELETE /api/use-cases/{uuid}/customer-requests`; `?sort=demand` +
  `distinct_customers` on the list. **Phases 2–4 in design** (Customer entity + M:N + embeddings index →
  compatibility score → New-Ingestion warn-and-confirm disposition). Open decisions in the design doc.
- ✅ **SHIPPED — Architecture roadmap → set scope + capability descriptiveness + bug fixes (2026-06-12):**
  the **Architecture** tab (`#view-review`) was the last surface on the old run paradigm — its
  Full-run/This-UC picker is **retired**; it now scopes to the **masthead Scoping Set** (latest eval
  per UC, may span runs; no version comparison). Backend adds a `scope='set'` path across
  arch-review / enhancements (+ their prompt-export siblings), `/api/analysis/output` (cache keyed by
  a synthetic `set:<id>` run token), and `/api/pr/preview` (gap context aggregated over the set), via
  shared helpers `_set_token`/`_parse_set_token`/`_set_label`/`_set_latest_analyses`. **Capability
  descriptiveness:** Engineering + Cap Map read terse because set-scoped mode dropped the `usage`
  gloss — now `capability-density` (set mode) and `uc-capability-map` (both modes) carry a
  representative `usage` sentence; Engineering already renders it, Cap Map shows it on header hover.
  **Catalog suggestions 500 fixed:** `/api/catalog/suggestions` hard-coded `$2` in the no-run_id
  branch (IndeterminateDatatypeError) → per-branch scoped subquery; also exclude already-cataloged
  caps by **normalized `cap_key`** not the raw string. **Focus-reset bug fixed:** window refocus →
  `loadMe` → `_applyPersona` re-rendered the rail (clearing `.active`) then auto-homed to Use Cases;
  now it re-derives the current domain from `_curView` and only homes when there's genuinely none.
- ✅ **SHIPPED — step 3, the outcomes restructure (decision 4b):** **runs are eliminated as a scope
  everywhere**; the only selectable scope is the **Scoping Set** (= UC/UC Set, the scope definition);
  a run is the *ingestion event*. (3a) latest-eval-per-UC backend → (3b) scope picker replacing the 4
  run pickers → **(consolidated) one shared masthead `Scope` selector** (`#globalScopeSel`, next to
  Project; localStorage `davScope`) drives Results/CapMap/Engineering via `_activeScope`/`scopeQuery()`;
  the per-view pickers are retired (a Set's results span multiple runs) → (3c) **Runs view → "UC
  ingestion audit"** with **▶ Ingest N un-evaluated / stale** (also one-click from the masthead
  freshness popover via `ingestStaleUCs()`).
- ✅ **SHIPPED — vocabulary + Authoring IA (2026-06-11):** **run → "Ingestion"** across the UI
  (masthead chip, `+ New Ingestion`, Ingestions tab/list/metadata; DB/API identifiers unchanged);
  **Use Case sets → "Scoping Sets"** sweep (UI + API user-facing); **Authoring split into three
  sub-tabs** — **Use Cases** · **Scoping Sets** (new `#view-scopingsets`, canonical set-management
  surface; the legacy ⚙ modal now redirects here) · **Discussion** (was Inbox); Inbox rows now show
  **repo · ⎇ branch**.
- ✅ **SHIPPED — route-shadow fix + guard (2026-06-11):** `/api/results/uc-latest` and
  `/api/runs/preflight-hint` were unreachable (declared after `{run_id}`/`{name}` siblings →
  misleading 404s). Both moved above their param siblings; **`check_routes.py`** added + wired into
  the deploy (`review_console.yaml`) so any future shadow fails the play pre-build.
- ✅ **SHIPPED — Scoping Sets two-pane manager + total run→Ingestion sweep (2026-06-11):** the
  Scoping Sets tab is a **two-pane** page — **left** = a static, filterable full Use Case list (drag
  source; filters = search · **Unassigned/assigned** · source · lifecycle-state; each row shows its
  set-membership chips), **right** = the **vertical Scoping Set accordion** (`_renderSetMgmtInto`,
  expandable members + full management). **Drag a UC from the left onto a set to add** it (reuses the
  `application/x-dav-uc` payload + `_addUCToSet`). The **Use Cases tab dropped its Scoping Sets rail**
  (Use-Cases-only now; set management lives on the Scoping Sets tab). **run→Ingestion is now total**
  (static markup + JS toasts/confirms/banners; only code identifiers + Tekton `PipelineRun` proper
  nouns remain). New Ingestion gains **"Stale / un-ingested"** + **"Unassigned"** scope options; the
  **ingest-stale** actions (audit + freshness popover) open New Ingestion **pre-selected to Stale /
  un-ingested** (UCs needing evaluation, via `/api/results/uc-latest`).
- ✅ **SHIPPED — masthead Unassigned scope + pill fixes (2026-06-11):** **Unassigned (no Scoping Set)**
  added to the masthead **Scope** dropdown — resolved at the single `_resolve_scope_uc_uuids` choke
  point (`NOT EXISTS` membership), with the 3 capability endpoints' `set_id` relaxed `int→str` to accept
  the sentinel (numeric guards kept on the legacy run-scoped paths). The masthead **Ingestion pill** is
  fixed: a **persistent adaptive heartbeat** (7s active / 30s idle, kicked at boot) keeps it live on every
  tab + during drawer watching; numbers **labelled** (`<N> ingestions · done/total UC · ✓ ✗`, tooltip);
  the **label flips to "Active"** while running (avoids "Ingestion … ingestions").
- ✅ **SHIPPED — #121 UC ingestion failure capture + Audit + mirror (2026-06-11):** `uc_analyses +=
  error_reason, error_phase`; ingest captures reason/phase + does the **dropped-UC diff** (intended scope
  − emitted → stub `not_emitted` failed rows). `/api/freshness` + `/api/results/uc-latest` exclude failed
  from coverage (legacy NULL kept) and return failure data. **Ingestion Audit** = Failed state + phase +
  reason + All/Failed/Stale filter + per-row re-ingest; **mirrored** in Results (failure card) + the
  ingestion drawer (badge + appended dropped UCs). Validated on ephemeral PG. See
  `uc-scoped-evaluation-design.md` → "Failure identification (#121)".
- ✅ **SHIPPED — #114 drift=stale Pass A (2026-06-11):** captured `source_repo_shas` != current repo
  HEADs ⇒ stale; cached HEAD resolve (120s TTL); `/api/freshness` + `/api/results/uc-latest` return
  `stale_edited`/`stale_drifted`; popover + audit show the breakdown. Pass B (+N commits) = TODO.
- ✅ **SHIPPED — #122 UC validation + health/repair (2026-06-11):** root-caused today's run
  `dav-stage2-console-213152` (9/23 failed) = **9 managed UCs missing a top-level `handle:`** → engine
  loader `KeyError: 'handle'`. Fixes: (1) `_validate_uc_yaml` now **requires handle** (the gap that let
  them save); (2) `_derive_uc_handle` (`managed/{profile}/{slug-of-title}`); (3) `POST
  /api/use-cases/{uuid}/repair` (backfill handle + save) + `GET /api/use-cases/health` (per-project
  validity); (4) UC editor **⚕ Repair** button, UC-list **⚠ invalid** badge + header **⚕ Repair N**
  (repair-all). The 9 existing UCs are flagged + one-click repairable.
- ▶ **Engine follow-ups (separate engine repo):** UC loader should derive a handle instead of
  `KeyError`, and **preserve the uuid on load-failure** (currently records `<load-failed>`, so #121
  can't attribute load-fails to a specific UC). Also: the 2026-06-06 stage-2 failure was `invalid
  confidence label 'moderate'` (model synonym not in {high,low,medium}) — loosen/normalize the label set.
- ▶ **Remaining:** #114 Pass B (+N commits), step 5 publish/pin (#118), step 6 queued
  worker (#119), step 7 combined-outcomes (triangle apex, #120), Outcome object.
- **Deferred:** outcome-requirements/roadmap as named derived projections; the **Outcome/Initiative**
  = a Set elevated with an outcome statement; cross-project UC reuse (#43 fork).

