# DAV — active work (session checkpoint 2026-06-13)

## ✅ SHIPPED 2026-06-17 — #186 security remediation (P0 confirm + PAT-on-delete guard)
**Why:** finish the #186 remediation per the runbook — confirm the P0 work is actually in place and
close any remaining clearly-in-scope, low-risk code guard.
- **P0 confirmed present** (commit `0f18c0a`): esc() quote-escaping, `DELETE /api/credentials` auth,
  `/api/bundles/{bid}/attach` decorator on the real handler, `/api/analysis/roadmap` read guard,
  `GET /api/runs/{name}/turns` authenticated read guard, and the enhancement `target_path`
  traversal/extension/CI-file guard. Secret rotation (#190) reported done.
- **NEW guard [Chain C, PAT hardening]:** `DELETE /api/accounts/{reviewer}` now revokes the deleted
  account's PATs (`UPDATE api_tokens SET revoked_at=now()`) + reloads the token cache, so a deleted
  user can no longer regain access via a still-valid Personal Access Token. Break-glass default-admin
  stays a deactivate (the gate already enforces `enabled`).
- **Intentionally left for Chris** (need a decision / cluster eyes — not invented scope): global
  default-deny auth (P1.8, ~60 endpoints, needs persona walk-through), NetworkPolicy default-deny,
  Postgres TLS, DB-backup CronJob, MCP auth-LB + netpol, the broad exception-detail-leak sweep,
  tenancy/IDOR `project_id` columns, and the SSRF allowlist (touches live model-call paths).

## ✅ SHIPPED 2026-06-17 — #173 Created + last-modified timestamps in Authoring views
**Why:** authors need to see when a UC / Scoping Set was created and last touched, unobtrusively,
to gauge freshness and ownership. The data was already captured (`managed_use_cases` and
`use_case_sets` both carry `created_at`/`updated_at`, all writes set `updated_at=now()`) and the
API already exposes both on `/api/use-cases[/{uuid}]` and `/api/sets[/{id}]`. The UC **detail**
Provenance block already rendered created/updated by + timestamps. The only gap was the
**Scoping Sets** Authoring accordion, which showed description + member count but no timestamps —
added an unobtrusive `created … · updated …` metadata line (`fmtTs`, `--text-faint`, full
timestamps on hover) matching the existing metadata styling. UI-only change; no migration needed.

## 🔭 NEW EPIC 2026-06-13 — Maturity Wall: goal-driven, backward-chained assessment (#147)
Design + requirements: **`docs/maturity-wall-design.md`**. Models the Red Hat **FlightPath**
assessment (Function Appraisal maturity wall + per-phase recommended states + high-level
roadmap) as a first-class, **configurable** capability in the **Assessments** domain.
- **Organizing principle:** goal-driven, backward-chained — **Goals are the apex**; Desired
  State (target maturity per capability) → vs Current State (the assessment) → **Gap** →
  **Roadmap** (backward plan) → Swimlanes/Gantt → Execute (enhancement actions + Measure-By).
  This is the #120 outcomes-triangle made concrete. The **maturity wall is first-class and
  standalone** (valuable with no goals); goal↔maturity is **bidirectional** (goals drive
  desired-state; the assessment *informs* goals). Goals have **all 3 origins**
  (human/derived/customer); **themes group per-capability targets** with rollups.
- **Reuse:** builds on the existing assessment model (#91, migration 019) — `assessment_findings`
  already carries `category`/`capability_handle`/`maturity`/`catalog_capability_id`. Shares the
  capability spine with the architecture roadmap (#141) → maturity-gap becomes a free
  prioritization axis + a shared Gantt renderer (design §Bridges).
- **✅ SHIPPED slice 1 (schema, migration 021):** themes · goals · goal_targets · goal_measures ·
  assessment_frameworks (configurable 0–5 scale) · framework_categories (band + Inflection-Point)
  · framework_capabilities (catalog-linked) · framework_states · assessment_capability_scores
  (capability×state→0–5, source llm|human). Migration wrapped in try/except (can't crash boot);
  applied + verified via API boot logs. **Deferred (slice 1b):** FlightPath framework data seed +
  back-fill `current` from findings (separate verifiable pass).
- **✅ SHIPPED slice 1b (seed):** `maturity_seed.py` — the global `platform-maturity-v1` template
  (0–5 scale + 5 states + bands→categories→capabilities), idempotent, seeded on boot.
- **✅ SHIPPED slice 3 (UI):** Assessments → Maturity Wall heat-map + state switcher (reads
  `/api/assessments/{id}/maturity-wall?state=` / the framework skeleton).
- **✅ SHIPPED slice 2 (backend) 2026-06-17 (#149):** the write-side the UI consumes —
  - **Framework CRUD** (`app/maturity_scoring.py` + thin endpoints): `POST /api/assessment-frameworks`
    (project-scoped; `clone_from=<seed id>` deep-copies scale + states + categories + capabilities,
    reuse-first), `PUT`/`DELETE /api/assessment-frameworks/{id}`, and category / capability / state
    sub-resources (`…/categories[/{cid}]`, `…/categories/{cid}/capabilities`, `…/capabilities/{capid}`,
    `…/states[/{key}]`). **Seed templates (`project_id IS NULL`) are read-only** — projects clone +
    edit. All gated by `assessment.edit` in the owning project (`_gate_framework_edit`).
  - **`POST /api/assessments/{id}/score`** — LLM scoring through DAV's **existing** model call path
    (`_make_diagnosis_call_fn` over a `model_configs` row, resolved via the
    assessment-ingest → arch-review → evaluation default chain — the same path assessment-ingest uses).
    Reads findings + the linked framework, proposes 0–5 per capability × **target/desired** state,
    persists as `source='llm'`. **Never clobbers a `source='human'` cell** (curated scores are the
    truth — the conflict `DO UPDATE … WHERE source <> 'human'` enforces it). Returns
    `{proposed, written, skipped_human}`.
  - **`PUT /api/assessments/{id}/scores`** — human override of any cell(s) with **provenance**
    (`source='human'`, `updated_by`, `updated_at`); `maturity=null` deliberately clears to '-' Not
    Assessed. A human score always wins and survives the next LLM pass.
  - **Tests:** `test_maturity_scoring.py` (8) — maturity coercion, prompt build (targets-only +
    cap-id listing), response parse/validation (drops out-of-range / unknown-cap / non-target,
    strips code fences, rejects non-JSON), and the LLM-vs-human provenance rules via a fake conn.
    Route-shadow + migration-wiring guards pass (272 routes / 22 migrations).
- **NEXT:** per-phase targets · Recommendations-per-Phase · High-Level Roadmap Gantt · export
  (feeds SOW #142). Tasks #150+.

## ✅ SHIPPED 2026-06-13 — Roadmaps IA + Enhancement/PR Workbench (#140 · #138 · #145)
Deployed to ns `dav` (gate: compile · route-shadow 249 · UI e2e 60/0). Commits on
`feat/dcm-uc-prioritization`: `f2f5297` (IA + workbench + CI/CD design), `4051409` (#145 split),
`ebbd761` (scroll fix) — all pushed.
- **Roadmaps domain IA (#140):** four sub-tabs **Arch Review · Enhancement / PR · Cap Map · Roadmap**
  (relabeled `review`→Arch Review, `engineering`→Roadmap; added the `enhancement` view).
- **Enhancement / PR Workbench (#138):** new `#view-enhancement`, the "process enabler". Backend
  `POST /api/enhancements/preview` parses the plan (`enhancement_apply.parse_enhancement_blocks`) +
  routes each finding to its enhancement-target repo by `target:` namespace (read-only; returns
  `groups`/`unmatched`/`no_target`); `selected_ids` added to `POST /api/enhancements/apply` for
  selective submit. UI: per-repo PR groups, select **per finding / per PR / bulk**, expand to view
  patch+acceptance, **retarget** an unmatched namespace inline, **Submit selected → one PR per repo**
  (confirm + `project.enhance-pr`).
- **#145 — finished the split:** Enhancement Plan **generation moved out of Arch Review into the
  Enhancement/PR tab** (Step 1 · Enhancement Plan → Step 2 · Route → PRs); the superseded single-repo
  Create-PR form (`rpPrSection` + handlers) was removed from Arch Review. Arch Review = the review only.
- **Scroll fix:** `#view-enhancement` content sat in a plain centered div, but `.pf-view` is
  `overflow:hidden` (views need an inner `flex:1; overflow-y:auto` region like `.rp-output`) → content
  past the viewport was clipped. Wrapped it in a scroll region.
- **CI/CD design captured (#143):** `docs/cicd-design.md` (Tekton e2e, gate-as-merge-gate; needs
  Chris's webhook secret + registry creds + deploy branch).
- **NEXT (teed up, needs Chris's shape):** #141 proper roadmap creation tool (Roadmaps → Roadmap) +
  #142 SOW-from-roadmap (open-ended — proposed approach in the 2026-06-13 morning-review writeup, to
  design together before building). #139 push DCM/UDLM to RH in chunks.

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

## ✅ SHIPPED 2026-06-09 — catalog collapse + F7
- **Catalog collapse (task #90):** the duplicate `capability_inventory` (keystone draft)
  is **dropped**; the Capability entity **is** the existing `capability_catalog`, extended
  additively into the UDLM Knowledge family (migration 020 + schema.sql). `cap_key`=handle,
  `status`=lifecycle (+`observed`), `project_id` nullable (NULL=global observed). Existing
  Catalog CRUD untouched. Shared write path `upsert_observed_capability()`. See
  `docs/capability-catalog-design.md` → "SHIPPED STATE (2026-06-09)".
- **F7 — assessment ingestion:** `assessment_ingest.py` (parser registry: generic +
  automation adapter; `synthetic_fixture()` — NO confidential data), migration 019
  (`assessments` + `assessment_findings`), endpoints `POST /api/assessments/ingest` (body
  `{use_fixture:true}` for the synthetic), `GET /api/assessments`, `GET /api/assessments/{id}`
  (+ gap summary). Assessments nav tab (platform-admin) → list + ingest + per-assessment
  findings/gap view. Validated on ephemeral Postgres (drop+extend+nullable+legacy-CRUD
  compat+seed+resolve+ingest). **WORK/PERSONAL BOUNDARY honored** — generic mechanism +
  synthetic data only; real per-format parsers + engagement data go inside the work env.

## ✅ SHIPPED 2026-06-09 — F8 prompt management (foundation)
Per-project, per-stage prompt customization. Design: `docs/prompt-management-design.md`.
- **Schema:** `project_stage_context.section_overrides JSONB` (content = append context);
  new **`prompt.manage`** privilege seeded to project-admin/edit; **supersedes**
  `project.archreview.context` (rbac.py aliases old→new for back-compat).
- **Registry:** `prompts_registry.py` — stages + named base sections + `assemble()`.
  Stages: `stage2-analysis` (engine, **stored-held** — A/B before runtime enable),
  `arch_review` (console, **append-live**).
- **API:** GET `/api/prompts/stages`, GET `/api/prompts/project/{stage}` (customization +
  assembled preview); PUT `/api/stage-context/{stage}` extended (section_overrides, now
  gated on `prompt.manage`, **active-project** scoped — was a default-project bug).
- **UI:** Improve nav → **Prompts & Improvement** (tabs: Prompt management + existing
  diagnose/propose/experiments). Editor: stage picker → append box + per-section override
  + live assembled preview + Save.
- **HELD (needs Chris):** wiring section overrides to the **stage-2 engine** prompt
  (thread customization via Tekton param/env; section the base template). Byte-identical
  by default; any real stage-2 override is a prompt-quality change → A/B first.

Resume scratchpad for the current batch of asks. Survives chat-context loss.
Repo: `/Users/chris/git/dav`. Big single files: `review-console/api/app/main.py`
(~466KB), `review-console/ui/index.html` (~725KB), `review-console/api/app/schema.sql`.
Design doc: `docs/review-console-design.md` (keep in sync per house rule).

## F1 — Additional-context text section for UC creation (esp. bulk)
**Status: backend DONE, UI only remaining.**
- Backend already supports it: `UCBulkExtractIn.context` (main.py:3568, max 4000) →
  endpoint `POST /api/use-cases/bulk-from-text` (main.py:3743) → `uc_assist.extract_bulk(context=…)`
  (uc_assist.py:299/318) injects "Additional context:\n…" into `_BULK_SYSTEM_PROMPT`.
  Single-UC assist path also has `context` (uc_assist.py:159/180).
- **TODO:** add an "Additional context" `<textarea>` to the BULK UC IMPORT MODAL
  (index.html ~2251, "M12a / ADR-008") and pass its value as `context` in the
  bulk-from-text POST body. Check whether single-UC create already shows a context
  field to mirror its styling/labeling. Keep it optional, ≤4000 chars.

## F2 — "Test evaluation" → run the single open UC directly + relabel
**Status: not started.**
- Today `testRunUC(uuid, ucPath, title, branchOverride)` (index.html:7051) builds a
  one-UC filter then calls `openNewRun(...)` — which opens the New Run config page
  (the "full use case run documentation" the user does NOT want).
- Runs are actually triggered by `submitNewRun()` (index.html:5601) → `POST /api/runs`
  with payload incl. `uc_handles`/`uc_uuids`/`managed_uc_uuids` from `_pendingRunFilter`,
  `selection_mode:'individual'`, model/endpoint via `_resolveEndpointModel`, defaults.
- **TODO:** make the button **submit the run immediately** for just the open UC
  (build the minimal /api/runs payload from the UC's filter + current project defaults,
  POST, then jump to the run detail) instead of opening the modal. Keep a path to the
  full config for power users (maybe shift-click or a small "configure…" affordance).
- **Relabel** the two buttons `▶ Test evaluation` (index.html:6806 corpus/managed,
  index.html:8362 managed-direct) — proposed new text **"Run this UC as well"**
  (⚠ CONFIRM wording with user — odd for a single-UC action; they asked for it
  literally). Update the `title=` tooltips at 7076-7077 too.
- Batch sibling: `#ucSelTestBtn "▶ Test selected"` (index.html:1190) — leave unless asked.

## F3 — Audit log (who did what + login/logout/timeout)
**Status: not started. Largest item.** Related: task #78 (login history in Users & Roles).
- Auth surface: `POST /api/auth/login` (main.py:2526, sets session cookie ~2377),
  `POST /api/auth/logout` (main.py:2544, deletes cookie), `/api/auth/sso` (2551),
  `/api/me` (1472). Sessions: `local_auth.py` (`make_session`/`read_session`,
  HMAC-signed, expiry baked into token → "timeout" = token expired). LDAP path:
  `ldap_auth.py`. Auth middleware around main.py:855-945.
- **Design (proposed):**
  - `audit_log` table in schema.sql: id, ts, actor_email, actor_source(local/ldap/sso),
    project_id (nullable=global), action (verb), object_type, object_id, summary,
    ip, user_agent, outcome(success/denied/error), detail JSONB.
  - **Action capture:** a small helper `record_audit(conn, request, action, …)` called
    at mutating endpoints (run trigger, UC create/approve, RBAC change, repo/cred edit,
    project change, etc.). Prefer an explicit helper over blanket middleware so we log
    intent + object, not just method/path. Optionally a middleware fallback for
    coverage of all non-GET 2xx.
  - **Auth events:** record login(success/fail), logout, and session-timeout (emit when
    a request arrives with an expired/invalid session cookie that had been valid — detect
    in the auth dependency). Capture ip + user_agent.
  - **UI:** an "Audit" view (platform-admin = all; project scope = members' actions),
    filter by actor/action/object/date; reuse the Users & Roles area (#78). RBAC-gate it.
- **Open Qs for user:** retention window? per-project visibility rules? include read
  actions or mutations + auth only? PII/IP storage ok?

## F4 — DISCUSSION: DAV for consulting priorities / capabilities / roadmaps
**Status: discussion, not code.** User intuition: DAV could derive priorities/
capabilities/roadmaps for consulting engagements — strong correlation to DAV's existing
**dual-pipeline product goal** (see memory project_dav_product_goal: "UC-driven dual
pipeline — architecture gap analysis + engineering capability roadmap; single-source =
analysis, two projections"). Engagement artifacts (transcripts, requirement docs) →
bulk-extract UCs (F1!) → gap analysis vs a target spec → capability/priority roadmap.
This may reshape feature priorities. Capture the conversation outcome here.

## F5 — Graphical UC ↔ capability map (bidirectional)
**Status: CONFIRMED (user agreed 2026-06-08); bidirectional.** Visualize use-cases ↔
capabilities both directions: pick a UC → its demanded capabilities; pick a capability
→ the UCs that demand it. Doubles as a **F4 consulting deliverable / "second
projection"** (gap + roadmap made legible at a glance).
- **Data already exists** (mostly a viz task): `uc_capabilities` (bipartite UC↔capability
  edges, "UC demands capability X"; schema.sql:234) and `uc_capability_deps`
  (capability→capability deps; schema.sql:256). Endpoints:
  `/api/analysis/capability-density` (main.py:6206, demand per capability) and
  `/api/analysis/foundational-capabilities` (main.py:6275, dependency ranking +
  leverage). Analysis libs: `capability_density.py`, `capability_graph.py`.
- **TODO:** likely one new endpoint returning the bipartite edge list (uc_uuid ↔
  capability_id) for a run/set, then a UI graph/matrix. Options: force-directed
  bipartite graph, or a UC×capability matrix/heatmap (demand count = cell weight),
  with click-through both ways. Size capability nodes by demand density; flag
  foundational ones (high leverage). Scope to a run/set (data is per-run).
- **Open Qs:** graph vs matrix as primary view? scope to current Set or cross-run
  aggregate? include capability→capability dep edges in the same view or a layer toggle?

## F4 outcome — Holistic vision & pillar expansion (foundational, 2026-06-08)
See `docs/holistic-vision.md` (mirrored in dcm/ + udlm/). DAV generalizes from
"DCM gap-analysis" to **AD (Architectural Design) mode** — validate any spec/plan can
support a UC, and if not, why + how. Three pillars realize UCs: **Platform** (built =
AD mode), **People/Process** (new), **Enablement** (new). Same engine, different
evaluation target + ingestion per pillar. Consulting flow = consume our existing
**assessment outputs** → cross-pillar gap analysis → strategy + roadmap, all anchored
to **customer-agreed outcomes/execution/operational details**.

**Pillar-expansion backlog (feasibility-gated, not yet scheduled):**
- **F6 — Generalize evaluation target** ("spec" → assessment target; current-state vs
  target/reference; selectable per pillar). Prereq for People/Process + Enablement.
- **F7 — Assessment-output ingestion** (consume our existing assessments: automation
  strategy, platform, hybrid cloud, AI capability). The primary new ingestion path.
- **F8 — Value Stream Mapping ingestion** (People/Process current-state; flow/waste/
  handoffs → gaps).
- **F9 — People/Process pillar view** (evaluate UCs vs org/process current-state).
- **F10 — Enablement pillar view** (adoption/change/operationalization readiness).
- **F11 — Prioritization lens** (business value × effort × risk × time-to-value,
  elicited not hallucinated) layered on existing foundational-leverage ranking.
- **F12 — Report/export projection** — the client-facing deliverable.
- **AI strategy (standalone)** — develop our AI capability/strategy deliberately; it is
  both an assessment lens (F7) and its own strategy artifact. *(user action item)*

## F7 detail — assessment ingestion (decisions 2026-06-08)
- **Pilot = Automation assessment/strategy** (most data + usage). Data volume order:
  automation > hybrid-cloud > AI. **A generalized DCM strategy is the SUPERSET** across
  all of them.
- **Capability catalog ↔ DCM Taxonomy** — independent catalog, normalized TO the
  taxonomy, **back-fills the taxonomy where gaps exist** (catalog drives taxonomy
  completeness). Taxonomy = normalization authority (form); catalog = living inventory
  (substance). DCM superset → sub-domains {automation, hybrid-cloud, AI}, pillar-namespaced.
  Resolves the free-form-capability dependency. **Full design + schema sketch:
  `docs/capability-catalog-design.md`** (the keystone — build first).
- **WORK/PERSONAL BOUNDARY (critical):** real assessment output is **work-confidential**
  — it must be parsed **inside** the work env (Chris will move/run DAV inside for that).
  DAV stays OSS in personal. So **here we build the GENERIC mechanism only**: the
  assessment schema, a parser/mapper **interface** (dispatch by assessment type), the
  assessment-target abstraction, and a **synthetic/example** automation fixture. The
  real per-format parsers + confidential data are a drop-in **inside**. No confidential
  data in the OSS repo. See [[feedback_account_split]].
- **Fundamentals buildable here now:** (1) canonical capability catalog seeded from the
  DCM taxonomy (keystone); (2) `assessments` + `assessment_findings` schema
  (pillar/domain-aware, catalog-anchored); (3) generic import framework + type-dispatch
  parser interface + synthetic automation fixture; (4) map findings → UCs/capabilities/
  gaps so the existing engine consumes them; (5) F6 evaluation-target generalization.

## Suggested build order
F1 (small, UI) → F2 (medium, UX) → F3 (large, schema+API+UI) — but F4 discussion may
re-rank. Update `docs/review-console-design.md` + version on each shipped feature.
