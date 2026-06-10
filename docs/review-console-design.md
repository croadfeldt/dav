# DAV Review Console — Design & Feature Inventory

**Status:** Living document  
**Last updated:** 2026-06-09  
**Current version:** v0.19.0  
**Source:** `review-console/` (API: `api/`, UI: `ui/index.html`)

This document is the authoritative record of what the review console is, what each feature does, and how it hangs together technically. It exists so that:

- Changes can be checked against intent before implementation
- Features aren't accidentally regressed during refactors
- A new session can pick up without archaeology

Update this doc whenever a feature is added, changed, or removed.

## Design principles

- **Consistency** — UI/UX patterns and process flows must be consistent across all features. If a pattern is used in one place (model selector + Browse button, two-click delete, streaming output with status indicator), it must be applied the same way everywhere. Users should never need to learn a different interaction model for the same type of action. This extends to *typography and control affordances*: a section header is rendered one way everywhere (plain "Use Cases", never a mixed-case/italic "Use *cases*"); toolbar actions (Import / Export / Bulk / New) use one shared button scale (`.btn-sm`) and sit in a single `.toolbar-actions` cluster in the same position across views. The design-system layer (`.btn-sm`/`.btn-icon`/`.toolbar-actions`/`.view-title`/`.view-subtitle` + the `u-*` utility classes) is the canonical source — new views compose those classes rather than re-styling inline.
- **Standardization over customization** — prefer the standard, shared mechanism over a bespoke one-off, even when a special case would be marginally simpler. New behavior should reuse an existing abstraction and extend it for everyone rather than fork a private code path. *Example:* "All Use Cases" is modeled as a (synthetic) **set** so it flows through the exact same run / arch-review / readiness paths as any real set — rather than scattering `if isAllSelected` branches across the codebase. Minimizing custom code is a feature: it keeps the system legible and uniformly testable.
- **Whole-system reuse** — design with the whole system in mind and reuse existing data and sources before building new ones. Before adding a table, endpoint, or data feed, check whether an existing one already carries (or can carry) the information. Consistency and deliberate reuse beat parallel half-duplicated mechanisms.
- **Scope clarity** — configuration that affects all users belongs in Config and is stored server-side; personal preferences belong in localStorage and are never shown as shared settings.
- **Least surprise** — defaults are always visible and editable; overrides are always scoped to the current session and do not mutate the shared default.
- **Secure by construction** — every trust boundary is authenticated, and service-to-service calls use short-lived, identity-bound credentials, never shared static secrets. In-cluster components authenticate with their Kubernetes ServiceAccount projected token (validated via TokenReview), not a baked-in password; endpoints are defended in depth (auth gate + RBAC + NetworkPolicy). See §Service-to-service auth (engine → API).
- **Efficient use of screen real estate** — operational views (Runs detail, Results, Review & Plan) must use the available width and height. Stats that fit side-by-side should sit side-by-side at wide widths and stack only when the viewport forces it. Tail panes (live log-like streams) must bound their height so they never blow past the viewport — scroll *within* the pane, not the whole page. The Runs detail panel is the canonical example: GPU + Inference live tiles render as a 2-column grid at ≥1100px; Prompts/Tasks panes cap at `max-height: 48vh` and scroll internally.

---

## Goals

1. **Operational hub** — trigger DAV analysis runs, monitor them live, browse results.
2. **UC management** — full lifecycle for managed use cases (draft → approved → deprecated).
3. **Quality review** — architectural review and enhancement planning powered by configurable LLM.
4. **Integration surface** — MCP server registry, code repo configs, UC assist config.

Non-goals:
- Git push-back for managed UCs (deferred, see roadmap)
- Multi-consumer switching (deferred, see roadmap) — note: multi-**project** data tenancy shipped in v0.10.0 (see §Projects (multi-project) and §Data tenancy), but a single console instance still binds to one consumer's spec/corpus.

---

## Architecture

```
Browser (index.html SPA)
  └─ FastAPI (api/app/main.py)
       ├─ asyncpg → PostgreSQL (schema.sql)
       ├─ results.py → workspace PVC (/workspace/results/)
       ├─ validations.py → Tekton API (OpenShift)
       ├─ metrics.py → Prometheus (GPU + vLLM)
       ├─ arch_review.py → configured review model (streaming SSE)
       └─ uc_assist.py → UC assist model (streaming SSE)
```

The SPA is a single `index.html` with no build step. All state lives in JS globals; the API is called via a thin `api()` wrapper. Auth is handled externally (OCP Route + OAuth proxy).

---

## Tabs

### Runs tab

**Purpose:** Trigger and monitor Tekton PipelineRuns; view run metadata and live stats.

**Data sources:**
- `run_sessions` table (DB) — human metadata, resource stats, phase
- `GET /api/runs` — lists `run_sessions` rows (with live Tekton phase injected)
- `GET /api/runs/{name}` — single run detail (Tekton + DB merged)
- `GET /api/runs/{name}/status` — live phase polling
- `GET /api/runs/stats` — cluster-wide kWh / token totals

**Trigger form:** `POST /api/runs` → creates a `run_sessions` row and calls Tekton to start `dav-stage2` PipelineRun. Params: name, description, category, tags, mode (verification/reproduce/explore), inference model (selected from `model_configs` via dropdown — endpoint + model_id derived from the row), corpus subpath, repo overrides, sample count, halt-on-error.

**Per-run source filters** *(M11b + post-M12, multi-source mode only)*: when `/api/sources` reports `spec.multi_source` or `corpus.multi_source` is true, the modal renders a vertical checkbox list for each side instead of the legacy URL/branch inputs:

- **Corpus sources (per-run filter)** *(M11b)*: ticked namespaces flow through `RunTriggerIn.corpus_namespaces` → `_mk_pipelinerun` → PipelineRun `corpus-namespaces` param → `dav-git-sync-multi-corpus` Tekton task, which clones only the selected sources into `/workspace/corpus/<namespace>/`. **Hard enforcement** — unticked sources physically aren't in the workspace.
- **Spec sources (per-run filter)** *(post-M12)*: ticked namespaces flow through `RunTriggerIn.spec_namespaces` → `_mk_pipelinerun` → PipelineRun `spec-namespaces` param → `dav-run-corpus` task `DAV_SPEC_NAMESPACES_FILTER` env var → `engine.ai.prompts.build_stage2_system_prompt` appends a "spec source focus for this run" paragraph. **Soft enforcement** — the MCP itself still serves every spec namespace; the focus hint instructs the LLM to prefer the listed sources and note any cross-namespace lookups.

For both, all-checked ≡ null sent (no filter applied; full set used) — the default behavior matches pre-M11b runs.

**Run drawer:** Opens when a run row is clicked. Four layout modes (button in drawer header):
- `detailed` — full GPU/vLLM stat tiles + task list + prompts panel (default)
- `stacked-tails` — compact stats + task list + prompts panel stacked
- `side-by-side` — task list and prompts panel side by side
- `prompts-dominant` — thin stats header, prompts panel maximized

**Run drawer sections:**
- **Compact stats bar** — wall time, phase, GPU energy (kWh), token counts (gen/prompt), session delta tokens
- **GPU / vLLM tiles** — live Prometheus metrics: GFX activity, VRAM, power, gen tokens/s, prompt tokens/s, queue depth; polled every 3s while run is active. Each tile also carries inline SVG **sparklines** (GPU power/gfx, vLLM gen-tps/running) built from `GET /api/metrics/timeseries`. The timeseries is fetched once on drawer open and re-fetched every 60s for in-flight runs (`_rdSparklines` cache). Because the 3s metrics poll rebuilds the tile innerHTML wholesale, `renderRunDrawerMetrics()` must re-inject the cached sparklines (`_renderSparklines()`) at the end of every render — otherwise they flicker out between the 60s fetches on live runs (fixed `75065be`; completed runs don't poll so they were always stable)
- **Tasks section** — Tekton TaskRun list with phase + log tail (last 200 lines via `GET /api/runs/{name}/logs?task=…`)
- **Prompts & responses section** — live tail of per-turn JSONL files written by the engine (see §Engine contract below). Polled every 5s. Expand/collapse per record; "expand all" / "collapse all" toggle persists in localStorage (`davPromptsDefaultMode`)
- **Review & Plan tab** — arch review (streaming) + enhancement planning (streaming); see §Review & Plan tab below

**Runs tab note:** Only runs triggered through the console appear here (they have `run_sessions` rows). CLI-triggered runs (`tkn pipeline start`) do not appear; their turns files are written but unreachable from the UI.

**Run identity & per-UC counts:** every surface that names a run leads with the
user-given session name (`run_sessions.name`), falling back to the Tekton
PipelineRun name and finally the workspace `run_id`. The runs list shows per-UC
`succeeded/total` counts sourced from `run_sessions.uc_*` when set, else from the
ingested `analysis_runs` row (`total_ucs/successful/failed`) — the authoritative
fallback matters because a partial failure (e.g. 31/32 ok) still marks the
PipelineRun **Failed** (engine exits 1 if *any* UC fails); without counts the row
reads as "everything failed". `/api/analysis/runs` and
`/api/improvement-proposals` join `run_sessions` for `session_name` so analysis
panes never surface the raw PipelineRun name.

**Run management (v0.10.0):** the Runs list supports lifecycle management of runs.
- **Rerun** — opens New Run pre-loaded with the original run's **actual
  configuration**, sourced from the **server-stored trigger payload**
  (`run_sessions.trigger_payload`, persisted at trigger time — durable across
  Tekton PipelineRun pruning and independent of UI hydration state). The modal
  does not open until the config has loaded; if neither the stored payload nor
  live PipelineRun params exist (pre-upgrade run, pruned), the UI says so
  explicitly instead of silently opening defaults. Restored (legacy fallback =
  PipelineRun params): mode,
  sample count, model, corpus/spec repos+branches+subpath, halt-on-error,
  source-namespace narrowing, time allowed, category/description. UC scope is
  an **exact replay**: the original `uc-handles`/`uc-uuids`/`managed-uc-uuids`
  and corpus subpath are replayed verbatim (re-deriving scope from the live
  Set was tried and diverged — set-normalized handles, "." subpath, recomputed
  timeout). The Set is still resolved (by id, or by name for the synthetic
  "__all__" set whose lineage persists as set_id NULL) for display+provenance.
  Only the session name changes (`Rerun: <original>`).
- **Archive** — soft-hide via `run_sessions.archived`. Archived runs drop out of the default list but are retained.
- **Delete** — hard purge: DB rows + the workspace result directory + the Tekton PipelineRun. The role's RBAC grants `pipelineruns` `delete` for this.
- **Bulk operations** — multi-select with select-all / deselect-all; text + phase filters (the phase set includes Cancelled and TimedOut).

**System-wide run selector (masthead, v0.10.0):** a single run selector in the masthead drives every analysis stage. It mirrors the Runs list one-for-one; runs that have no ingested analysis appear disabled with their phase shown, so the operator always picks from the same canonical list regardless of which stage they're working in.

**`api()` 204 handling (v0.10.0):** the UI `api()` helper now treats `204 No Content` / empty response bodies as a successful `null` (previously it threw, producing spurious "failed" toasts on DELETE and other empty-body responses).

---

### Results tab

**Purpose:** Browse ingested analysis results; per-UC verdict, gaps, recommendations, spec refs; comparison between runs.

**Data sources:**
- `analysis_runs`, `uc_analyses`, `uc_gaps` tables (DB) — ingested after a run completes
- `GET /api/analysis/runs` — list ingested runs
- `GET /api/analysis/runs/{run_id}/ucs` — UC results for a run
- `GET /api/analysis/runs/{run_id}/ucs/{uuid}` — single UC detail (verdict, gaps, spec refs, etc.)
- `GET /api/analysis/compare/{run_id_a}/{run_id_b}` — verdict/gap delta between two runs

**Ingestion:** `POST /api/analysis/ingest/{run_id}` reads `analyses/*.yaml` from the workspace PVC and populates the DB. Auto-ingest is triggered when a run is selected in the Runs tab if its result isn't already in the DB.

**Per-UC gap display:** Each gap shows `title`, `description`, `severity`, `rationale`, `recommendation`, `spec_refs_consulted`, `spec_refs_missing`. These fields come from `uc_gaps` (ingested from engine output).

**Gap fields (as of prompt v1.5):**
- `title` — 3-7 word phrase naming the gap type (required, used as dedup key)
- `description` — prose description
- `severity` — `{label, score, band}` (minor/moderate/major/critical)
- `confidence` — `{label, score, band}` (low/medium/high)
- `rationale` — why this is a gap
- `recommendation` — what to do about it
- `spec_refs_consulted` — list of spec doc handles/sections the model checked
- `spec_refs_missing` — list of `doc-handle/section-title` strings for gaps in the spec; array, NOT prose

---

### Use Cases tab

**Purpose:** Full CRUD for managed UCs; view corpus UCs from git; UC Assist for NL-driven authoring.

**Managed UCs:** Stored in `managed_use_cases` table. Lifecycle: `draft → in-review → approved → deprecated`. Lifecycle events logged in `lifecycle_events`.

**UC sets:** Named collections of UCs (`use_case_sets` + `use_case_set_members`). Used for targeted runs.

**UC Assist:** NL prompt → YAML suggestion via configured model (any enabled model, or env-var fallback). The **Clear** button wipes both the conversation history and the compose textarea. When the assist panel is opened with no model selected, focus moves to the model selector so the user knows what to do first.

---

### Config tab

**Purpose:** Operational configuration — spec/corpus sources, review models, MCP servers, code repos, UC assist.

Two-pane layout: left nav (category list), right content (selected category).

**Categories:**
- **Shared credentials** *(M9, shipped)* — credentials registry view. Lists every shared credential by name, type, description, and "used by N repo(s)" chip. Add/edit/delete via `POST/PUT/DELETE /api/credentials`. Values are write-only (never returned by HTTP); rotation propagates to every dependent repo automatically per [ADR-005](../adr/005-shared-credentials-abstraction.md). Delete refuses with 409 + dependent repo list until references are reassigned. Forward path to HashiCorp Vault localized to this module + crypto.py.
- **Repos** *(M3 + M5b + M9 + M10 + M11a, shipped)* — managed_repos registry view. Lists every repo DAV operates on with namespace, URL/branch, roles (spec / corpus / issue-source / enhancement-target), tenant, and credential chips (🔑 PAT / 📬 WHK with `·s` suffix for shared). Add/edit/delete via `POST/PUT/DELETE /api/repos`. Per-repo credentials can be: linked to a shared credential (dropdown), set inline (collapsible details section), or cleared (button). Convert-inline-to-shared button surfaces on inline-only credentials. M11a adds per-role path overrides via `metadata.role_paths.{role}` — one row can serve multiple roles from different sub-paths (e.g., DCM uses `architecture/` for spec but `dav/use-cases/` for corpus). The registry is the source-of-truth per [ADR-003](../adr/003-multi-repo-registry-and-mcp-source-of-truth.md); both Sources panels below are read-only projections.
- **Sources — Architecture spec** *(M4, shipped — read-only)* — projected view of `managed_repos` rows with `role=spec`. Lists each source's namespace, URL, branch, root_path (resolved via `repos.resolve_root_path(repo, 'spec')`). Shows last-projected timestamp and rollout status. No editor inputs; an "↑ Manage in Repos" button scrolls to the Repos panel. If the ConfigMap is still in legacy single-source shape (pre-projection), the UI flags it and points the operator at the ↻ Project button.
- **Sources — Evaluation corpus** *(M11a, shipped — read-only)* — sibling projection of `managed_repos` rows with `role=corpus`, mirroring the spec panel post-refactor. Same multi-source ConfigMap shape (`sources:` YAML list with per-source namespace/URL/branch/root_path); per-row root_path resolved via `repos.resolve_root_path(repo, 'corpus')` so per-role overrides take effect. Unlike spec, corpus projection does **not** trigger a Deployment rollout — Tekton's `dav-git-sync-multi-corpus` task re-reads the ConfigMap at the start of every run.
- **Sources — Evaluation endpoint** — selects inference model from `model_configs` dropdown (replaces free-text endpoint/model inputs); applied via `POST /api/sources/inference` after optional Test validation.
- **Model Endpoints** — all LLM endpoints in one `model_configs` table with per-endpoint use-flags: `use_arch_review` (default true) and `use_uc_assist` (default false, informational only — any enabled model can now be used for UC Assist). `api_key` masked on GET. All selectors across the console draw from the same enabled-model list via `_populateModelSel(selId, storageKey)`; selections persisted in localStorage per selector. Each selector has a **Browse…** button inline with the selector that opens the model browser overlay (see §Model browser below).
- **Evaluation model** — project-scoped default for new analysis runs. Stored in `model_defaults` table (`key='evaluation'`). Set in Config → AI Models → Evaluation; applies to all users. New Run modal reads from `GET /api/model-defaults` on open and pre-selects this model when no user override is stored in localStorage. Only registered model_config rows can be set as project defaults (custom endpoint+model pairs are user-scoped only).
- **UC Assist model** — user-scoped personal default for UC Assist authoring. Stored in localStorage under `ucAssistModelId`. Configurable both in Config → AI Models → UC Assist and inline in the UC Assist panel (both selectors share the same storage key and mirror each other). The Config selector has an explicit Save button (UX consistency with the Evaluation and Arch Review pickers); both the Save button and the change handler write to the same localStorage key. Falls back to env-var config (`DAV_UC_ASSIST_*`) if no DB rows exist.
- **Arch Review model** — project-scoped default for `/api/arch-review` (Review & Plan tab). Stored in `model_defaults` table (`key='arch-review'`). Set in Config → AI Models → Arch Review. Filtered to model_configs rows with `use_arch_review=true`. `/api/arch-review` falls back to this default when the caller omits both `model_config_id` and `endpoint_url+model_id` — explicit overrides still win. The run drawer's Review & Plan tab still requires per-call selection today; the server-side fallback covers external callers and direct API usage.
- **Default model selectors (AI Models, v0.10.0)** — Config → AI Models now hosts a consistent "default model" selector for each model_defaults key: `arch-review`, `enhancement`, `evaluation`, and `uc-authoring`. These are all the **same** component (one shared selector + Browse button) and are all project-scoped + server-backed via the `model_defaults` table (see §Two-tier model selection). `enhancement` falls back to `arch-review` when unset; `uc-authoring` is shared by the UC Assist panel, the UC Wizard, Bulk import, and inbox draft-uc. The new-run **engine** default is deliberately NOT a model_defaults key — it lives in the Inference source ConfigMap (Config → Pipeline Sources → Inference) so the pipeline reads it directly.
- **Users & Access (v0.10.0, admin-only)** — multi-user / LDAP and Projects administration. See §Multi-user / LDAP and §Projects (multi-project). Surfaces LDAP status + Sync-now, per-user role editing, and project create/archive + per-project membership. Only rendered for admins.
- **MCP Integrations** — registered MCP servers (`mcp_server_configs` table) with `use_uc_assist` flag. Health polled on demand. Servers flagged `use_uc_assist` displayed with amber badge.
- **MCP refresh** *(post-M12, shipped)* — operational controls for keeping the bundled `dav-docs-mcp` server's served content fresh. The MCP holds spec/corpus content in memory at pod start (via its git-clone init container); without a refresh path, edits to spec or corpus repos are invisible until the next pod restart. Two surfaces:
  - **Scheduled refresh** — Ansible-templated CronJob `dav-docs-mcp-refresh` (default `17 * * * *`, hourly) restarts the MCP deployment by patching its pod-template `dav.io/restartedAt` annotation. Schedule, enabled flag, deployment name, and SA name come from inventory vars `dav_docs_mcp_refresh_*` so they're per-environment configurable. RBAC is intentionally narrow: ServiceAccount + Role granting only `apps/deployments` `get`+`patch` on the single deployment by `resourceName` (no rollouts subresource, no wildcard verbs). Schedule editing in the UI is a tracked follow-up — would need a reconciler updating the K8s CronJob `spec.schedule` from a DB row.
  - **Manual button** — Config → Pipeline Sources → MCP refresh panel renders a kv-grid with `last_refreshed_at` / `last_refreshed_by` / `last_refreshed_source` / `replicas` / `available` / `rollout_in_progress` + a `↻ Refresh now` button + a `Reload status` button. The Refresh now button confirms once, POSTs the trigger, and reloads status.
  - **API endpoints:**
    - `POST /api/mcp/refresh-now` — patches the MCP deployment's metadata annotations (`dav.io/last-refreshed-at`, `dav.io/last-refreshed-by`, `dav.io/last-refreshed-source=manual-ui-button`) AND its pod-template `dav.io/restartedAt` annotation (this is what `oc rollout restart` does under the hood, requiring only `apps/deployments` patch). Identifies the caller via `get_user(request)` (X-Forwarded-User from oauth-proxy). Returns `{ok, triggered_at, triggered_by}`.
    - `GET /api/mcp/refresh-status` — reads the MCP deployment and returns rollout state (`replicas`, `ready_replicas`, `updated_replicas`, `available`, `rollout_in_progress`) plus the metadata annotations from the most recent refresh.
  - **Why this shipped now:** the OSAC investigation surfaced a 25h-stale MCP pod whose served content was older than several rounds of corpus edits. The user's "this data should not be stale ever" framing pushed us from one-off manual restarts to a scheduled + on-demand pattern, with the manual button being the ops-day-saver when an operator needs the MCP fresh *right now*.
- ~~**Code Repositories**~~ — removed per [ADR-006](../adr/006-consolidate-code-repos-into-managed-repos.md). Enhancement PR target repos are now part of the **Managed repos** panel with `role=enhancement-target`. Provider (github/gitlab) lives in `metadata.provider` or is inferred from the URL. PAT is the same `github_pat` field used by other roles (per-repo inline or shared credential).

---

## Two-tier model selection (v0.10.0)

Every model-driven view resolves its model through a consistent two-tier pattern: a **project-scoped Config default** plus an optional **per-view override**.

**Tier 1 — Config defaults (server-backed).** Config → AI Models hosts one consistent "default model" selector component per `model_defaults` key:

| Key | Drives |
|---|---|
| `arch-review` | `/api/arch-review` (Review & Plan) |
| `enhancement` | `/api/enhancements`; **falls back to `arch-review`** when unset |
| `evaluation` | New Run modal's evaluation-model pre-fill |
| `uc-authoring` | UC Assist panel, UC Wizard (generate/refine), Bulk import, inbox draft-uc |

All four use the same selector + Browse component (UI consistency principle). The new-run **engine** default is intentionally **not** a `model_defaults` key — it lives in the Inference source ConfigMap (Config → Pipeline Sources → Inference), which the Tekton pipeline reads directly.

**Tier 2 — per-view overrides.** Each model-driven view carries an "override" selector whose first option is `Use default — <name>`. Leaving it blank sends **no** model in the request, so the endpoint resolves the Config default server-side. Picking a specific model sends an explicit `model_config_id` (or `endpoint_url`+`model_id` for a custom pair).

**Resolution order** (centralized in `_model_default_row(conn, *keys)`, applied by every model endpoint):

1. explicit `model_config_id` (per-call override)
2. explicit `endpoint_url` + `model_id` (custom pair)
3. project default(s) for the relevant `model_defaults` key(s)
4. (uc-authoring only) env-var fallback (`DAV_UC_ASSIST_*`)

**API:** `GET/PUT /api/model-defaults` (all keys) and `GET/PUT /api/model-defaults/{key}` (single key).

---

## Cached Review / Enhancement output (v0.10.0)

Successful Architectural Review and Enhancement Plan generations are cached and re-served, so reopening a run/UC doesn't re-spend inference. (This is the feature formerly sketched as "Phase B".)

**Table `analysis_output_cache`:** `(run_id, kind['review'|'enhancement'], scope['run'|'uc'], uc_uuid, content, model_label, source_ingested_at, project_id, created_by, created_at)`, with `UNIQUE(run_id, kind, scope, uc_uuid)`.

- **Write-through** on every successful generation (both `/api/arch-review` and `/api/enhancements`).
- `GET /api/analysis/output?run_id=&kind=&scope=&uc_uuid=` returns `{cached, content, model_label, created_at, stale}`.
- **Staleness** = the run was re-ingested since the cache row was written, i.e. `source_ingested_at` is older than the current `MAX(analysis_runs.ingested_at)` for that run. A stale entry is still returned, flagged `stale: true`.

**UI:** the Review & Plan tab auto-loads cached output on every run / scope / UC change. A cache chip shows `↻ cached <when> · <model>` (or `⚠ stale`). The Run buttons prompt the user to confirm replacing a cached result before regenerating.

---

## Tab-close-resilient generation + completion notify (v0.10.0)

`/api/arch-review` and `/api/enhancements` run the LLM in a **background asyncio task** rather than inline in the SSE handler. The SSE response only *observes* a shared output buffer, so closing the tab or navigating away no longer cancels the generation — it runs to completion and writes the cache regardless.

Machinery (all in the API): an `_active_gen` registry keyed by `(kind, scope, run_id, uc_uuid)`; `_run_generation_bg` runs the model; `_ensure_generation` starts-or-attaches; `_observe_generation` streams the shared buffer to a client. A **second** observer for the same key attaches to the already-running task instead of re-calling the model. Each entry self-cleans 45s after completion.

**UI:** a completion toast (`✓ Architectural Review ready` / `✓ Enhancement Plan ready`) fires even when the user has navigated to another view.

---

## Enhancement rendering as markdown (v0.10.0)

Enhancement output is rendered as **markdown** using the same `mdToHtml` typography as the Architectural Review. The renderer converts the structured `ENHANCEMENT` blocks into markdown for display **only** — the raw structured format is left intact so `enhancement_apply`'s PR parser still consumes the mechanical blocks (`target:`, `action:`, etc., see §Enhancement apply). The `/api/enhancements` system prompt requires each block flush-left (no indentation, plain-text labels) so both the human-readable render and the machine parse stay reliable.

---

## Run drawer — Review & Plan tab

Opened from the run drawer (tab at bottom). Scope is either `uc` (single UC) or `run` (all UCs in run).

**Architectural Review:** Streams from `GET /api/analysis/arch-review/{run_id}?scope=run` or `?scope=uc&uc_uuid=…`. Uses `arch_review.py` → configured review model → streaming SSE to UI. Supports both OpenAI-compatible and Anthropic providers. `<think>` blocks from reasoning models are passed through raw (no server-side stripping); the UI toggles their visibility client-side.

**Think-block indicator:** While the model is inside a `<think>…</think>` block (detected by comparing `lastIndexOf('<think>')` vs `lastIndexOf('</think>')` on the accumulated stream), the status element shows a pulsing "Thinking…" label. Reverts to "Generating…" once the closing tag arrives.

**Font controls:** A font bar above each stream panel provides A−/A+ buttons (11–22 px range) and a serif/sans/mono family picker. Settings applied via CSS custom properties (`--arch-font-size`, `--arch-font-family`) on `document.documentElement` and persisted in localStorage (`archFontSize`, `archFontFamily`).

**Enhancement Planning:** Same streaming pattern, different system prompt. Produces per-gap enhancement specs with implementation outline, acceptance criteria, dependencies/risks.

**PR/MR creation:** From an enhancement, can create a branch + PR/MR on a configured code repo. Calls `POST /api/code-repos/{id}/create-pr`.

**Copy prompt:** Copies the arch review or enhancement prompt to clipboard for use in Claude Code or claude.ai.

---

## Engine contract (what the engine must write)

The review console depends on the engine writing specific files to the workspace PVC. These are read-only from the API pod.

### Analysis YAML (`/workspace/results/{run_id}/analyses/{uc_uuid}.yaml`)

Must conform to `specs/07-analysis-output-schema.md`. Key fields consumed by the UI:
- `summary.verdict` — `supported | partially_supported | not_supported`
- `summary.notes` — prose assessment
- `gaps_identified[].title` — 3-7 word gap name (dedup key, required as of v1.5)
- `gaps_identified[].description`, `.severity`, `.confidence`, `.rationale`, `.recommendation`
- `gaps_identified[].spec_refs_consulted`, `.spec_refs_missing` (array of strings)
- `ensemble.votes`, `.n_samples`, `.dissent_factors` — shown in Results detail

### Run summary (`/workspace/results/{run_id}/run-summary.yaml`)

Written by `run_corpus.py` on completion. Fields consumed by the UI:
- `mode`, `started_at`, `finished_at`, `total_ucs`, `successful`, `failed`, `total_samples`

### Turns files (`/workspace/results/{run_id}/turns/{uc_uuid}.seed-{N}.jsonl`)

**Required for the Prompts & Responses panel in the run drawer.**

Written by `Stage2Agent._emit_turn()` in `engine/src/dav/ai/agent.py`. Each line is a JSON object with:
- `ts` — ISO8601 timestamp
- `turn` — integer turn index
- `kind` — one of: `start`, `response`, `tool`

Fields per kind:

**`start`** (emitted once at analysis begin):
```json
{"kind": "start", "uc_uuid": "...", "sample_seed": 42,
 "system_prompt": "...", "system_prompt_length": 1234,
 "user_prompt": "...", "user_prompt_length": 567,
 "max_tool_calls": 12}
```

**`response`** (emitted after each model response):
```json
{"kind": "response", "content": "...", "content_length": 890,
 "tool_call_count": 2, "tokens_used": 345, "tokens_total": 1200,
 "messages_in_context": 7}
```

**`tool`** (emitted for each tool call result):
```json
{"kind": "tool", "tool_name": "...", "args": {...},
 "ok": true, "result": "...", "result_length": 2000}
```

Fields larger than `DAV_TURNS_MAX_FIELD_BYTES` (default 256KB) are capped; capped fields get a companion `*_truncated: true` key.

The API discovers turns files via `results.list_turns_files(run_id)` and tails them incrementally via `results.tail_turns(run_id, file, since_offset)`.

**The UI polls `/api/runs/{name}/turns` every 5 seconds while the run drawer is open for an active run.** If the turns directory doesn't exist or is empty, the panel shows the placeholder message.

### Wiring (must remain intact)

```
run_corpus.run_one_uc()
  └─ runs_path = run_dir / "turns"
  └─ run_samples(..., turns_log_path=turns_path)
       └─ Stage2Agent(turns_log_path=per_sample_file)
            └─ _emit_turn(kind="start"|"response"|"tool")
```

If any link in this chain is broken (e.g., `turns_log_path` not passed, `_emit_turn` removed), the panel goes silent. Check this chain before refactoring any of these three files.

---

## Prompt versioning

`STAGE2_PROMPT_VERSION` in `engine/src/dav/ai/prompts.py` is bumped on any change to the system or user prompt. Stored in each analysis YAML under `metadata.prompt_version`. Used for cross-run comparability assertions.

Current: `"1.8"` — two-pass (explore → findings → synthesize) with per-pass system prompts. Earlier bumps: `1.5` gap title + spec_refs_missing, `1.6` cross-turn dedup nudge, `1.7` per-UC `spec_namespaces` hard scope.

The stage-2 system prompt also picks up a **per-run spec-source focus paragraph** when the engine sees `DAV_SPEC_NAMESPACES_FILTER` (set by the Tekton `dav-run-corpus` task from the `spec-namespaces` PipelineRun param; ultimately from the New Run modal's spec-source checkbox grid). The hint asks the LLM to prefer documents from the listed namespaces when grounding via MCP and to note any cross-namespace lookup in its analysis. This is **soft enforcement** — the MCP itself still holds every spec namespace — and is the spec-side analog of M11b's hard-enforced corpus filter (which physically constrains what `dav-git-sync-multi-corpus` clones).

**Cross-turn tool-call dedup (1.6)** — Stage2Agent tracks every successful `(tool_name, args_json)` pair in `self._call_history` for the duration of one UC sample. If the model emits the same call on a later turn, the engine short-circuits with a `⛔ DUPLICATE-CROSS-TURN` marker carrying the original turn number, `tool_call_id`, and a 400-char preview of the original result. Pairs with a stage-2 prompt nudge telling the model to scan its own prior calls before re-issuing. End of each `analyze()` emits a `kind="summary"` turn record (`cross_turn_duplicates_blocked`, `distinct_calls`, `section_title_misses`, `too_large_handles`, `total_tokens`) which the UI's prompts panel renders inline. Aggregating these into `run-summary.yaml` is a follow-up that requires touching `stage2_analyze` + `run_corpus.CorpusUcResult`.

**Per-UC `spec_namespaces` (1.7)** — UCs declare an optional top-level `spec_namespaces: [<ns>, ...]` field that overrides any run-wide focus. Three enforcement layers in the engine: (1) prompt — a "## Per-UC spec source scope (HARD)" paragraph appended to the system prompt; (2) `Stage2Agent._check_namespace_scope()` hard-rejects `get_document` / `get_document_section` for out-of-namespace handles with an `⛔ OUT-OF-SCOPE` marker before the MCP roundtrip; (3) summary record carries `out_of_scope_blocked` + `uc_spec_namespaces`. Search results aren't filtered (varies by MCP backend) but the prompt + per-call reject are the practical floor.

**Two-pass stage-2 (1.8, default-on)** — explores in pass 1, synthesizes in pass 2 with re-fetch fallback. Default behavior when `DAV_STAGE2_TWO_PASS != "0"`.
- **Pass 1**: same agent loop, but the system prompt is built from `build_pass1_findings_system_prompt()` and asks the model to emit a verbose structured FINDINGS JSON instead of the canonical Analysis (sections retrieved, capabilities observed, constraints quoted verbatim, cross-references, potential gaps, unresolved questions). The agent loop returns the raw findings string — no Analysis validation.
- **Pass 2**: fresh context. System prompt from `build_pass2_analysis_system_prompt()`. User prompt is the original UC + the findings JSON. MCP tools still available so pass 2 can re-fetch any spec section the findings compressed too aggressively. Emits the canonical Analysis via the existing `_parse_final` path.
- **State between passes**: dedup + anti-fishing reset; cross-turn-dup count and out-of-scope-blocked are cumulative across passes (agent-loop hygiene metrics, not per-pass exploration depth).
- **Turn records** carry a `pass: pass1 | pass2 | null` field so the UI prompts panel can render per-pass timelines.
- **Information-preservation guarantee**: pass 2's MCP access means nothing pass 1 compressed is irrecoverably lost.

**Dynamic max_tokens + context-overflow retry** — independent of the two-pass change but composes cleanly with it. Before every inference call the agent estimates the next prompt size from the last response's `prompt_tokens` plus a growth pad and computes `available_for_output = ceiling - estimated_prompt`. If less than `config.max_tokens`, requests only what fits AND drops tools (forcing final emission). Belt-and-suspenders: a 400 with `"maximum context length"` in the message gets parsed, retried once with `tools=None` and a `max_tokens` derived from the reported input-token count. Tracked on the summary as `budget_capped_turns` and `context_overflow_retries`. Operator-tunable: `DAV_MODEL_CONTEXT_LIMIT` (default 86016 = current vLLM `--max-model-len`) and `DAV_MODEL_CONTEXT_SAFETY` (default 256, tokenizer-drift cushion).

---

## Infrastructure confidence — distinct from analytical confidence

Every Analysis carries `metadata.infrastructure_confidence = {label, score, signals, explanation, recommendations}` computed by `Stage2Agent._compute_infrastructure_confidence()` at end-of-run from the same counters the summary record exposes. This answers **"did infrastructure constrain grounding?"** and is **distinct from analytical confidence** (`success_likelihood`, per-component `confidence`). A UC can be analytically high-confidence while infrastructure-compromised (model committed early due to budget pressure without enough exploration) and vice versa.

**Scoring** (start at 100, cap at 0):
- `context_overflow_retries × 30` — major; vLLM rejected a call mid-run
- `budget_capped_turns × 10` — forced commit due to dynamic max-tokens
- `cross_turn_dedup_rate > 20% → -10` — model losing track of prior calls
- `section_title_misses > 5 → -5` — model fishing for sections that don't exist
- `out_of_scope_blocked > 3 → -5` — frequent namespace-boundary attempts

**Labels**: `high ≥85 / medium ≥65 / low ≥40 / compromised <40`. Recommendations include "switch to long-context model" when overflow or cap counts are non-zero — pairs with the per-run model selector that already flows `inference_endpoint` + `inference_model` through `RunTriggerIn`.

**Persistence + surfacing** (migrate_013):
- `uc_analyses` gains `infra_confidence_{label,score,explanation}` + `infra_confidence_{signals,recommendations}` JSONB columns + index on label.
- `_ingest_run_analyses` reads `metadata.infrastructure_confidence` and persists.
- `GET /api/use-cases/{uuid}/runs` includes the per-row `infrastructure_confidence` object.
- `GET /api/runs/{name}/infra-confidence-aggregate` returns per-run label breakdown + deduplicated recommendations for the run drawer.
- `GET /api/runs/preflight-hint?set_id=X` returns a banner-shaped hint when ≥2 of the last N runs of the same Set had any UC at `low`/`compromised`. New Run modal renders the hint at top of step 1.

**UI surfacing**: per-UC chip in the Test history table on the UC detail panel (color-coded: green/amber/orange/red); tooltip carries explanation + signals + recommendations. Existing analyses ingested before migrate_013 show no chip (null label).

---

## Enhancement apply — turn findings into a PR

`POST /api/enhancements/apply` consumes the structured ENHANCEMENT blocks emitted by `POST /api/enhancements` (the system prompt for that endpoint was tightened in commit `50ee227` to demand mechanical patch blocks with `target:`, `action:`, `section_title:`, `position:`, a verbatim markdown content block, and `acceptance:` instead of prose). The applier:

1. Parses every ENHANCEMENT block via `enhancement_apply.parse_enhancement_blocks()` (tolerant — malformed blocks return with `parse_errors[]` populated rather than being silently dropped).
2. Groups by the namespace prefix on each `target:`.
3. For each namespace, looks up the `managed_repos` row with that namespace + `role=enhancement-target` (or uses an explicit `repo_overrides[ns]` mapping). Missing or PAT-less rows surface in `unmatched_namespaces[]`.
4. For each target file, fetches current content via `corpus_push.fetch_file_content()`, applies all of the namespace's patches in source order via `enhancement_apply.apply_enhancement()` (`add_section`, `update_section`, `new_document`; `replace_text` is NYI and surfaces as a warning), then pushes via `corpus_push.push_uc_to_github()`.
5. Opens **one PR per affected repo** — all sharing the same `branch_name` for cross-repo traceability.
6. Cross-namespace drift warning: if `payload.run_id` is set and `run_sessions.spec_namespaces` was non-empty for that run, any patch targeting an out-of-scope namespace gets a CROSS-NAMESPACE DRIFT warning (PR still opens; operator decides).

`corpus_push.push_uc_to_github` gained a `token_override` parameter so the applier authenticates with the per-repo PAT (`repos.get_repo_secrets`) instead of the legacy `DAV_CORPUS_PUSH_TOKEN` env var.

---

## Infrastructure: LLM-bound endpoint timeouts (M12+)

Three layers between the browser and the API each have their own default short timeout. For long LLM calls (`/api/uc-assist` wizard generate/refine, `/api/use-cases/bulk-from-text` transcript extraction), all three need to be lifted in lock-step or the slowest layer 502s mid-stream:

| Hop | Default | M12+ setting | Where |
|---|---|---|---|
| OpenShift Route (haproxy) | 30s | `haproxy.router.openshift.io/timeout: 600s` | `ansible/roles/dav/templates/review-console-ui-route.yaml.j2` |
| oauth-proxy sidecar | 30s | `--upstream-timeout=600s` | `ansible/roles/dav/templates/review-console-ui-deployment.yaml.j2` |
| nginx `/api/` location | 60s | `proxy_read_timeout 600s` | `ansible/roles/dav/templates/review-console-ui-nginx-cm.yaml.j2` |

The 600s ceiling matches the `httpx` timeout used by `uc_assist.extract_bulk`. Symptom of any hop being too short: the request returns 502 to the browser with no useful detail; the only readable signal is `oauth-proxy`'s `"http: proxy error: net/http: timeout awaiting response headers"` log line. When investigating future timeout-shaped issues, check oauth-proxy logs first — Route and nginx fail silently with generic 502s.

---

## Managed repos registry (M1+, ADR-003)

The `managed_repos` table is the source-of-truth for which repos DAV
operates on. Each row:

- `namespace` — URL-safe identifier; used as the MCP doc-handle prefix
  and as the clone directory name
- `repo_url`, `repo_branch` — git target
- `root_path` — optional subdirectory served as the source root (e.g.,
  `dcm` uses `architecture` so the MCP serves from `/data/dcm/architecture/*`)
- `roles[]` — open vocabulary (v1: `spec`, `corpus`, `issue-source`);
  one repo may carry multiple roles
- `tenant_id` — default `'default'`; multi-tenant filtering pathway
  (ungated in v1)
- `ingestion_config` — JSONB; per-role config (e.g., polling interval
  for issue-source)
- `metadata` — JSONB free-form
- audit: `created_at`, `created_by`, `updated_at`, `updated_by`

CRUD via `GET/POST/PUT/DELETE /api/repos` (Repos UI is M3). Role filter
on list: `GET /api/repos?role=spec`. Role vocabulary at
`GET /api/repos/roles/vocabulary`.

Seeding (first-run only): on startup, if `managed_repos` is empty, the
API reads the existing `dav-source-spec` and `dav-source-corpus`
ConfigMaps and inserts one row per declared source (multi-source list
or legacy single-source). Operators with existing config carry forward
without manual reseeding.

Projection contract (M2, shipped): the projector module
(`review-console/api/app/projector.py`) regenerates the `dav-source-spec`
ConfigMap `sources` field from the registry whenever a row with `role=spec`
changes (create / update / delete that touched the role) and triggers a
`dav-docs-mcp` rollout-restart so the init container re-clones. The
projector is idempotent: if the rendered sources YAML matches what's
already in the ConfigMap, no patch is written and no rollout is triggered.

The ConfigMap remains the transport mechanism for the MCP init container;
it is no longer the source-of-truth. Direct `oc edit` of the ConfigMap is
reverted on the next role=spec CRUD or by `POST /api/repos/project`
(manual reconcile endpoint).

The projector refuses to write an empty sources list — would crash the
MCP init at next start. If the operator deletes the last role=spec repo,
the ConfigMap is left untouched and a warning is logged. Create a
replacement repo first.

Corpus projection (role=corpus → `dav-source-corpus`) is not yet wired
because corpus is still single-source-only and consumed by the Tekton
pipeline (each run clones from the ConfigMap fresh, so no Deployment
rollout is needed). When corpus becomes multi-source-capable, the
project_corpus_sources sibling lands.

## PR-comment ingestion (M5+, ADR-003 extension)

managed_repos rows with `role=issue-source` are polled for PR comments
by a background task in the review-api process. Migration 008 adds:

- `pr_comments` — one row per ingested comment. Keyed by
  `(repo_uuid, github_comment_id, github_comment_type)` so re-polls and
  webhook replays upsert cleanly. `status` lifecycle: `new` →
  `dismissed` | `drafted_to_uc`. Curator state is preserved across
  re-polls (only body and timestamps update; status never gets reset).
- `uc_pr_comment_links` — provenance from a UC back to the PR
  comment(s) that drove its creation. Many-to-many.
- `pr_comment_poll_state` — one row per role=issue-source repo. Records
  last poll start/finish timestamps, success/error, comments seen,
  newest-seen watermark.

Poller (`review-console/api/app/pr_comments.py`):

- Async background task started in `lifespan` alongside the finalizer
- Runs every `PR_COMMENTS_POLL_INTERVAL_SECONDS` (default 300)
- Initial delay `PR_COMMENTS_POLL_STARTUP_DELAY_SECONDS` (default 30)
- For each role=issue-source repo: list open PRs → for each PR list
  `issue_comment` and `pull_request_review_comment` types → upsert
- One DB connection per repo (failures isolated)
- Logs `pr_comments poll: N repo(s) — ok=K fail=K total_comments_seen=N`
  per pass for visibility

GitHub client (`review-console/api/app/github_client.py`):

- httpx async, Bearer auth via `GITHUB_TOKEN` env
- Pagination via Link rel="next" (max 20 pages = 2000 items per call;
  safety cap with warning log)
- `parse_owner_repo(url)` derives owner+repo from the managed_repos
  row's `repo_url` (no need for the operator to type them twice)
- Anonymous mode supported but warns; 60 req/hr quota burns out fast
  for periodic polling

Operator setup for production polling (post-M5b, per ADR-004):

1. **Generate + set the Fernet encryption key** (one-time):
   ```
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   Add the output to `vars.local.yaml` as `vault_dav_fernet_key`, then
   `ansible-playbook --tags secrets` to create the `dav-fernet-key`
   Secret + `oc rollout restart deploy/dav-review-api`.
2. **Add the role + PAT per repo** via the Repos UI: edit each repo,
   check the `issue-source` role checkbox, paste a GitHub PAT into the
   "GitHub PAT" field (scope: `repo` for private, `public_repo` for
   public-only). Save. The poller picks up the repo + token within
   POLL_INTERVAL_SECONDS.
3. **Optional — webhook setup** (M6): for each role=issue-source repo,
   set a value in the "GitHub Webhook Secret" field, save, then in
   GitHub repo Settings → Webhooks add:
   - Payload URL: `https://dav-review.<cluster>/api/webhooks/github/pr-comments`
   - Content type: `application/json`
   - Secret: same value you typed into the UI
   - Events: Issue comments, Pull request review comments

Webhook events upsert via the same path as the poller with
`ingestion_source='webhook'`. The poller becomes a fallback for missed
deliveries. Both code paths validate the per-repo HMAC against
`managed_repos.github_webhook_secret_encrypted` (decrypted at request time).

## Webhook receiver (M6, ADR-004)

`POST /api/webhooks/github/pr-comments` on the review-console API
receives GitHub webhook events. oauth-proxy is configured to skip auth
on `/api/webhooks/` so GitHub doesn't see a redirect to log in.

Flow per request:

1. Read the raw body (HMAC validation needs the byte-exact body).
2. Check `X-GitHub-Event` — `ping` returns `{status:"pong"}`,
   unrecognized events return 200 + `ignored`.
3. Resolve the source repo by querying `managed_repos.repo_url =
   ANY([clone_url, html_url, git@github.com:owner/repo.git, ...])`.
4. If the row's roles don't include `issue-source`, return 200 + ignored.
5. If the row has no `github_webhook_secret_encrypted`, return 400 with
   operator-actionable text.
6. Decrypt the secret, compute `HMAC-SHA256(secret, body)`, compare
   constant-time against the `X-Hub-Signature-256` header. Mismatch → 403.
7. Parse the comment payload, upsert into `pr_comments` with
   `ingestion_source='webhook'`.

Webhook is primary (real-time); the poller catches anything the webhook
missed (network blip, secret rotation in flight, etc.).

The Inbox API (M7) reads from `pr_comments`. The Inbox UI (M8) lets
operators dismiss comments or draft a UC from one (LLM-assisted draft
via the UC Assist plumbing).

## Inbox tab (M7 + M8, top-level nav)

A new "📬 Inbox" top-level nav item exposes PR-comment curation.

API surface (M7):

- `GET /api/inbox?status=&repo_uuid=&tenant_id=&limit=` — list ingested
  comments. `status` defaults to `new`; pass `all` to disable the filter.
- `GET /api/inbox/{uuid}` — single comment enriched with `uc_links`
  (any UCs already drafted from this comment).
- `POST /api/inbox/{uuid}/status` — transition. Body:
  `{status: "new"|"dismissed"|"drafted_to_uc", uc_uuid?: str, notes?: str}`.
  When `status=drafted_to_uc`, `uc_uuid` is required and an entry is
  inserted into `uc_pr_comment_links` (idempotent on conflict).
- `POST /api/inbox/{uuid}/draft-uc` — calls `uc_assist.chat()` with a
  user message that frames the PR comment as scenario source material.
  Body: `{model_config_id?: int, endpoint_url?, model_id?}` (same
  resolution as `POST /api/uc-assist`). Returns
  `{explanation, yaml_suggestion, raw, comment}` for the UI editor.

UI flow (M8):

1. Operator opens Inbox tab → sees `new` comments in the left list,
   filtered chips (new / drafted / dismissed / all) + per-repo dropdown.
2. Click a row → detail panel right shows full body, GitHub deep-links,
   any existing UC drafts.
3. Click `✦ Draft UC (LLM)` → spinner; 30-60s later draft appears with
   explanation + YAML in a collapsible block. `⎘ Copy YAML` for fast
   paste; `↑ Switch to Use Cases` stashes the draft in sessionStorage
   and pivots to the UC editor.
4. After saving the UC, operator clicks the `drafted_to_uc` chip on the
   detail panel (or the API call from the editor's save handler) to
   record the link.

The Use Cases editor pre-population from sessionStorage is a polished
follow-up; v1 hands the operator a one-click clipboard copy + a clear
written workflow.

## DB migrations

Migrations run automatically at API startup before `schema.sql`. Each migration file is idempotent (safe to re-run).

| File | What it does |
|---|---|
| `migrate_002_model_configs.sql` | Renames `review_model_configs` → `model_configs`; adds `use_arch_review`/`use_uc_assist` flags; adds `use_uc_assist` to `mcp_server_configs`; migrates any `uc_assist_config` row into `model_configs`; drops `uc_assist_config` |
| `migrate_003_model_defaults.sql` | Creates `model_defaults` table for project-scoped model defaults |
| `migrate_015_improvement_proposals.sql` | Self-improvement loop (Phase 1): `run_diagnoses` (per-diagnosis taxonomy snapshot) + `improvement_proposals` (typed change proposals + review lifecycle) |
| `migrate_016_experiments.sql` | Self-improvement loop (Phase 2): `experiments` (A/B baseline-vs-candidate runs + scores + verdict) + `change_spec` column on `improvement_proposals` |

**v0.10.0 schema changes** are applied **idempotently on API boot via `schema.sql`** (the file doubles as a migration script: `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE … ADD COLUMN IF NOT EXISTS` + backfills), not as numbered migration files:

- **New tables:** `analysis_output_cache`, `users`. (`projects`, `project_members`, `capability_catalog`, `project_stage_context` are also created here if not already present.)
- **New columns:** `project_id` on `managed_use_cases`, `analysis_runs`, `run_sessions`, `use_case_sets`, and `analysis_output_cache`; `run_sessions.archived`. Existing rows are backfilled into the `default` project; `project_id` columns are indexed.
- **`model_defaults`** gains the `enhancement` and `uc-authoring` keys (data, not schema).

---

## Self-improvement loop — diagnose & propose (Phase 1, 2026-05-30)

Turns the manual "observe failure → root-cause → propose fix" loop into a feature. Full design + phased plan: [`dav-self-improvement-vision.md`](dav-self-improvement-vision.md). Modules: `failure_taxonomy.py` (classify a run's failures into typed signatures) + `diagnose.py` (signatures → ranked typed proposals, via a rules layer that encodes the OSAC 2026-05-29/30 fixes + an optional LLM second opinion). **Proposals are review artifacts — nothing is applied** (Phase 2 will gate applies).

- `POST /api/diagnose/{run_id:path}?use_llm=` — reads the run's durable failure artifacts (`failures/*.error.txt` + `run-summary.yaml`) from the workspace PVC, builds the failure taxonomy, runs the diagnoser, and persists a proposal batch. `use_llm` (default true) adds an LLM second opinion via the arch-review default model. Returns `{batch_id, taxonomy, proposals, llm_attempted, used_llm}`.
- `GET /api/diagnose/{run_id:path}` — the latest stored diagnosis (taxonomy + proposals + review status) for a run.
- `GET /api/improvement-proposals?status=&kind=&run_id=` — cross-run review queue.
- `POST /api/improvement-proposals/{id}/review` — accept/reject (review only; sets status + `reviewed_by/at`). Does **not** apply the change.

**Signature classes** (the failure taxonomy): `route_504`, `output_truncation`, `severity_reject`/`confidence_reject`, `score_out_of_band`, `context_overflow`, `budget_exhausted` (fishing), `tool_parse_error`, `inference_error`, `unknown`. **Proposal `kind`s**: `prompt | profile | route | tool | code | infra | data` — the kind gates how Phase 2 may apply it (prompt/profile = auto-with-revert; code/infra = human-gated). **Diagnoser guardrails** (in the LLM system prompt, from real incidents): classify the failure class before proposing prompt edits; never "harden the prompt to stop a behavior" (it backfires); respect `throughput × route_timeout` and `context × KV` ceilings. The LLM layer logs on failure and reports `llm_attempted` vs `used_llm` — a diagnostic tool must not hide its own failures. Pure-module regression coverage: `review-console/api/test_self_improvement.py`.

**UI — the "Improve" tab** (🩺, top-level nav): two-pane review queue built on the Inbox tab's list+detail+action idiom. Left: status-filtered proposal list (proposed/accepted/rejected/all) + a "diagnose a run" picker (recent runs, failed first; LLM-second-opinion toggle). Right: proposal detail (kind/target/confidence/source, proposed change, rationale, predicted effect, originating run) + Accept / Reject (review-only, two-click armed via `_armDeleteBtn`). The nav badge shows the unreviewed count. A run-drawer **"🩺 Diagnose"** button (`rdDiagnoseBtn`) runs the diagnoser in context. `/api/diagnose/{id}` accepts a workspace run_id OR a Tekton PipelineRun name (`_resolve_run_id` correlates the run's start time to a workspace results dir), so both entry points work with whatever id they hold.

**Phase 2 — A/B candidate experiments** (`experiment_eval.py`, migrate_016): the "always measure" gate as code. `POST /api/experiments` triggers a baseline + candidate run over the same eval set (`set_id` or `managed_uc_uuids`); the candidate's `change_spec` is applied as a **per-run override** — today `{type:'max_tokens', candidate:N}` via the Tekton task's `max-tokens` param (`validations._mk_pipelinerun`), so it's isolated: no `model_use_profiles` row, no `dav_stage2_max_tokens` deploy-var change, production + spamllm + `r9700-llm` untouched. (max_tokens can't ride a profile — `run_corpus.py:803` makes the task arg win.) The two arms are spaced 1.3 s apart to dodge the timestamp-derived run-name collision (409). `GET /api/experiments[/{exp_id}]` lazily scores both arms once they finish (`score_run` → `gate`), persists the verdict, and resolves dead/cancelled arms to `status='error'` (never stuck "running"). `gate()` returns **promote** only on a real success-rate improvement with **no new high-severity failure class** (the v1.9 lesson), **revert** on a regression or a new failure mode, **inconclusive** on a tie. `POST /api/experiments/{id}/promote` (verdict=promote only): max_tokens promotion is human-gated — it returns the exact `dav_stage2_max_tokens` deploy-var change + the engine-tag re-apply command, and flips the linked proposal to `applied`. UI: the Improve tab gains a Proposals|Experiments toggle, a "🔬 Run A/B" launcher on max_tokens proposals (candidate value + eval-set picker), and an experiment detail with the baseline-vs-candidate scorecard + verdict + Promote.

**Sampling-param experiments** extend the harness to temperature/top_k/top_p/min_p — the *runtime-applyable* case. The candidate applies its delta as a per-run `use-profile-json` override (merged onto the production profile via `_trigger_eval_run(profile_override=)`), so it's isolated like max_tokens. But **promote writes the production `model_use_profiles` row** (`_apply_sampling_promotion`, upsert mirroring `/api/models/{id}/profiles`) — runtime + reversible. `POST /api/experiments/{id}/revert` (`_revert_sampling_promotion`) restores the prior state (delete the row if none existed, else restore prior params; the before-state is captured in `change_spec.applied`). The experiment's `auto_promote` flag (default off, sampling only) makes `_maybe_score_experiment` auto-apply a winning verdict on scoring. UI: a `+ New A/B experiment` ad-hoc launcher (type → sampling param or max_tokens; param picker; candidate; eval-set; auto-promote toggle) + a ↩ Revert button on promoted sampling experiments.

**Phase 3 — continual scan** (`POST /api/self-improve/scan`): walks recent workspace runs (`results.list_runs()`), and for any that failed (`get_failures`) and lack a `run_diagnoses` row, runs the rules diagnoser and files proposals via the shared `_store_diagnosis()` helper. Rules-only + idempotent + bounded (`limit_runs`, `max_diagnose`). Driven by the Ansible-templated `dav-self-improve-scan` CronJob (`self-improve-scan-cronjob.yaml.j2`, defaults `dav_self_improve_scan_*`, every 6h, in-cluster curl — no RBAC) so the operator wakes to a triaged queue.

---

## Multi-user / LDAP — auth + roles (v0.10.0)

Identity still comes from the oauth-proxy (`X-Forwarded-User` / `X-Forwarded-Email`); LDAP decides **approval** and seeds roles. The console is no longer implicitly single-user, but multi-user gating is **opt-in** and cannot accidentally lock anyone out.

**Module:** `app/ldap_auth.py` (uses `ldap3`; added to `requirements.txt` as `ldap3==2.9.1`).

**`users` table:** `reviewer` (PK) · `email` · `display_name` · `role` (`admin` | `editor` | `viewer`) · `approved` · `source` (`ldap` | `bootstrap` | `manual`) · `last_seen`. A 10-minute background sync pulls the configured LDAP group → `users` + an in-memory approved set that **survives LDAP downtime** (the cached set keeps working if the directory is unreachable).

**Access gate (middleware):** rejects non-approved users, but **only** when all three hold: `DAV_LDAP_ENFORCE=true` AND LDAP is configured AND at least one sync has succeeded. Until then it's a no-op — so configuring LDAP can't lock out the operator before they've verified the user list. Bootstrap admins (via `DAV_LDAP_BOOTSTRAP_ADMINS`) are always admin. `require_role()` gates write endpoints; in single-user mode (LDAP off) there is **no** role gating.

**Endpoints:**
- `GET /api/me` now returns `role` / `is_admin` / `approved` / `ldap_enabled`.
- `GET /api/ldap/status`, `POST /api/ldap/sync` (manual sync).
- `GET /api/users`, `PUT /api/users/{reviewer}/role`.
- `GET /api/ldap/approved` (the approved set, used by the Projects member picker).

**UI:** Config → **Users & Access** (admin-only) shows LDAP status + Sync-now, and per-user role editing.

**Config (optional Kubernetes Secret `dav-ldap`):** mounted via `envFrom` (optional) on the API deployment. Example manifest: [`review-console/deploy/dav-ldap-secret.example.yaml`](../review-console/deploy/dav-ldap-secret.example.yaml). Env vars: `DAV_LDAP_URL`, `DAV_LDAP_BIND_DN`, `DAV_LDAP_BIND_PASSWORD`, `DAV_LDAP_USER_BASE`, `DAV_LDAP_GROUP_DN`, `DAV_LDAP_USER_ATTR`, `DAV_LDAP_MAIL_ATTR`, `DAV_LDAP_NAME_ATTR`, `DAV_LDAP_MEMBER_ATTR`, `DAV_LDAP_START_TLS`, `DAV_LDAP_ENFORCE`, `DAV_LDAP_BOOTSTRAP_ADMINS`.

**Rollout:** fill the Secret → `oc apply` → `oc rollout restart deploy/dav-review-api` → verify the user list in Config → Users & Access → only then set `DAV_LDAP_ENFORCE=true`. See the operator runbook for the step-by-step.

---

## Use-case git model — sharing, fork, reference (IN DESIGN, target v0.11)

**Problem:** UCs need to be shared across projects, both as live **references** and as divergent **forks**, with a **use-case-admin** capability that's assignable globally *and* per-project. Rather than build a bespoke Postgres share-matrix, we model this on git — which already solves reference (track an upstream ref), fork (branch/copy + PR back), permissions (forge ACLs), and history.

**Decided direction:**
- **Git is the source of truth; the DB is a projection/index** (the corpus is already synced-from-git this way). UC edits become commits — via commit-on-save or an explicit Publish step. `managed_use_cases` keeps a synced copy + git provenance.
- **DAV can host UC repos locally** ("localized git repos"), in addition to pointing at external forge repos (e.g. gitlab.roadfeldt.com). Backend is pluggable.
- **Layout is operator-chosen:** a project selects a **path/branch**, defaulting to **its own repo**. Repo-per-project is the default; path/branch-within-a-repo is allowed.

**Model:**
- UC row gains git provenance: `origin` (`local` | `reference` | `fork`), `source_repo_id` → `managed_repos`, `source_path`, `source_ref`, and for forks `upstream_repo_id`/`upstream_path`/`upstream_ref` (to compute drift + offer "open PR back").
- **Reference** = a read-only tracked source binding (re-syncs, like `consumer_spec_sources`). **Fork** = snapshot copy that diverges and can PR upstream. **Share INTO a project** = grant a reference, or open a PR — the **forge enforces the tenancy boundary**, not us.
- **The "matrix" UI** = *projects × UC-source bindings* (which repos/paths/refs each project subscribes to, reference vs fork), not a hand-rolled share table.
- **`GitProvider` abstraction** isolates the hosting choice: `ensure_repo(project)`, `read/commit/branch`, `fork`, `open_pr`, `register_reference`. Backends: external-URL, forge-API (gitlab/gitea), in-cluster host. The infra choice becomes config, not a fork in the code.
- **uc-admin** capability checked via one helper `_can_manage_uc_sources(user, project)` satisfied by **either** a global `uc-admin` role **or** a per-project `uc-admin` grant in `project_members`.

**Reuses (don't rebuild):** Push-to-corpus, Tekton git-sync, `managed_repos` + per-repo Fernet-encrypted PATs (ADR-004), namespaced multi-source sync, the projects/tenancy model.

**Resolved — hosting is an operator choice (reuse the corpus mechanism):** there is no single mandated backend. We keep today's corpus repo (`code_repo_configs` + git-sync + Push-to-corpus) as the default UC location and **generalize "the corpus repo" → "UC-location repos"**: operators may register additional repos (reusing `code_repo_configs` + ADR-004 per-repo encrypted PATs) and **assign where a project's/UC's use cases live** — the corpus, another registered repo, or a **DAV-localized PVC** (bare repo on a PVC) when they want DAV to store them. The PVC backend is fully self-contained but has **no native merge-request review UI**, so *reference* (re-syncing source binding) is the primary cross-project share path and *fork* is copy-with-provenance; **PR-back is offered only when the backend is a real forge** (e.g. gitlab). `GitProvider` backends: registered-repo (corpus model, exists) and localized-PVC.

**Phased delivery:**
1. **uc-admin capability** — ✅ **SHIPPED (v0.10.1).** `uc-admin` is accepted as a global role (`users.role`) and a per-project grant (`project_members.role`), validated via `_ASSIGNABLE_GLOBAL_ROLES` / `_ASSIGNABLE_PROJECT_ROLES` (role columns are free-text — no migration). `_can_manage_uc_sources(user, project_id?)` + the `require_uc_admin` dependency gate the forthcoming sharing endpoints; granting global `uc-admin` itself requires already holding it. `/api/me` returns `can_manage_uc_sources`; the role appears in the global Users and per-project member/invite dropdowns. Inert in single-user mode.
2. **UC-location repos** — ✅ **SHIPPED (v0.11).** Built on **`managed_repos`** (the live registry; `code_repo_configs` is migrated *into* it per ADR-006), not a new table. **`uc-store`** added to `repos.py` `VALID_ROLES` (open `roles TEXT[]`, no migration) marking a writable UC destination, co-assignable with other roles. **localized-PVC backend:** `POST /api/repos/pvc-local` (uc-admin) runs `git init --bare /uc-repos/<ns>.git` on a dedicated **RWX `dav-uc-repos` PVC** (mounted RW at `/uc-repos` on the API) and registers a `managed_repos` row (`metadata.provider='pvc-local'`, role `uc-store`, `repo_url=file:///uc-repos/<ns>.git`) — DAV-hosted storage, no git server. `_validate_repo_url` allows the constrained `file:///uc-repos/` prefix only (traversal-guarded). **Assignment:** `projects.uc_repo_uuid/uc_path/uc_branch` (per-project default) + `managed_use_cases.source_repo_uuid/source_path/source_ref` (per-UC override) via `GET/PUT /api/projects/{id}/uc-destination` and `PUT /api/use-cases/{uuid}/uc-destination` (uc-admin-gated; soft repo references, no FK). **UI:** per-project "UC store" picker in Config → Projects/Access + inline "let DAV host a new store" (pvc-local), gated on `can_manage_uc_sources`. *(Actual git read/write of UC content is Phase 3.)*
   - **Direction (per operator):** `uc-store` is intended to become the *universal* UC location; the existing `corpus` role stays as the transitional "must-have" set but is expected to be subsumed. **Purpose is expressed by tagging sets / individual UCs** (reusing the existing `tags` columns on `managed_use_cases` and `use_case_sets`), NOT by repo role — so a single `uc-store` repo holds everything and tags carry intent.
3. **Git round-trip** — commit-on-save / Publish; `origin` + provenance columns; DB-as-projection sync.
4. **Matrix UI + reference/fork actions** — projects × source-bindings, share(reference)/fork/PR-back, gated on the uc-admin capability.

---

## RBAC — accounts × roles × privileges (v0.13.0)

Replaced the single-`role`-per-user model with a matrix, modelled on OpenShift RBAC (roles = sets of privileges; accounts are *bound* to roles, optionally per-project). **Identity-source-agnostic:** a `users` row is one account whatever the auth source (internal password, LDAP, OCP); `source` is informational only, never an authorization input. A user may hold **many** roles.

**Scope model (3 classes — the only thing that differs between role classes):**
- **Platform** (≈ OpenShift cluster) — the platform itself: LDAP/SMTP/accounts/roles/repos. Bound globally.
- **Cross-project** — project-*related* but **not tied to a specific project** (e.g. **project creation**). Bound globally.
- **Project** (≈ OpenShift namespace) — one specific project's data/settings/members/deletion. Bound per-project.

Platform & Cross-project bindings are project-independent; Project bindings carry a `project_id`. Matrix compatibility is hierarchical: a role of a given scope may hold privileges of its scope or **narrower** (Platform ⊇ Cross-project ⊇ Project), so a Platform Admin can also carry Cross-project/Project privileges, a Cross-project role can carry Project privileges, a Project role only Project privileges.

**Schema:** `rbac_privileges`, `rbac_roles` (scope `platform`|`cross-project`|`project`, `is_system`), `rbac_role_privileges` (the matrix), `rbac_account_roles` (account × role × `project_id`; NULL project_id for platform/cross-project bindings), `rbac_group_role_mappings` (LDAP/OCP group→role, *structure only* — sync is a later slice, platform-admin managed). `users.enabled` is the gate flag; `users.default_project_id` the per-user default. An idempotent migration (re-run every boot, `ON CONFLICT DO NOTHING`) maps legacy `users.role=platform-admin` → Platform Admin and `project_members{admin,editor,viewer,uc-admin}` → Project {Admin,Edit,Viewer}; `project.create` is reclassified from platform → cross-project.

**Privileges (v0.14.0 — granular workflow + config):** `platform.admin` (**platform**); `project.create` (**cross-project**); all others **project**-scoped:
- *Baseline:* `project.data.read` (read/view all project data — gates every GET).
- *Project admin ops:* `project.settings`, `project.members`, `project.delete`.
- *Workflow / execution:* `project.usecases` (use-case + set CRUD/import/transition), `project.runs.manage` (archive/delete/rename runs), `project.runs.execute` (trigger a run), `project.archreview.execute`, `project.archreview.context` (edit stage context), `project.enhancement.execute`, `project.enhancement.pr` (open branches/PRs — external push), `project.catalog`.
- *Config registries (project-owned, strict isolation):* `project.models`, `project.integrations` (MCP), `project.repos`.

The legacy umbrella `project.data.write` is **retired** (removed from built-in roles; replaced by the granular keys). **Built-in roles:** Platform Admin (`platform.admin`+`project.create`); Project Admin (*all* project privileges); Project Edit (data.read + usecases + runs.manage + runs.execute + archreview.execute + archreview.context + enhancement.execute + catalog — **excludes** enhancement.pr, models/integrations/repos, settings/members/delete); Project Viewer (data.read). Built-ins are *retunable* but not deletable; custom roles compose any project privileges (e.g. a "Run Operator" = data.read + runs.execute). An idempotent boot backfill grants the full project-admin set to any role holding `project.settings`.

**Config tenancy + workflow authz (v0.14.0):** `model_configs`, `mcp_server_configs`, `managed_repos`, and `model_defaults` are now **project-owned** (NOT NULL `project_id`; name-uniqueness is per-project; existing rows backfilled to the DCM project). Every config + workflow endpoint is gated: *reads* on `project.data.read` (so editors/run-operators can still see model/MCP/repo lists for pickers), *mutations/execution* on the matching privilege, and write/execute paths additionally resolve the **target row's** `project_id` and require the privilege *in that project* (cross-project edit → 404 — closes a pre-v0.14 gap where any authenticated user could edit any project's data). Consumption paths (model selection for arch-review/enhancement/uc-assist/diagnose, MCP health) are scoped to the run's project (run-driven) or the active project (request-time), so a run only ever uses its own project's models/MCP. `platform.admin` is a superuser and bypasses project checks. All guards no-op in single-operator mode.

**Resolver (`rbac.py`):** `privileges_for(account, project_id)` = union of **platform + cross-project**-role privileges (everywhere) + **project**-role privileges scoped to that project. All legacy guards (`require_role`, `require_project_admin`, `require_uc_admin`, `_is_project_admin`, `_can_manage_uc_sources`) are reimplemented over it; new `require_priv(priv, project_id)`. **Platform admins** see all projects in the RBAC views and can grant themselves a project role, but project *data* still requires a project role. `project.create` (cross-project) / `project.delete` (project) gate project lifecycle.

**Escalation guard:** assigning a role (Accounts panel *and* project Members panel) is subset-checked — you may only grant a role whose privileges you already hold in that scope. Platform admins hold everything and bypass. So a Project Editor who also holds `project.members` can grant Edit/Viewer, not Admin.

**Gate / approval / session invalidation:** source-agnostic — "approved" == an *enabled* account. The gate re-validates the account on **every** request (in-memory enabled set, reloaded on every account/role change), so disabling or deleting a user cuts their existing sessions off on their **next** request (UI shows the login screen on a 401). JIT auto-provisioning (creating a role-less enabled account for a first-seen identity) applies **only to proxy-authenticated** (OCP/LDAP) identities — an internal session whose account was deleted/disabled is rejected, never re-created.

**Security (LB path):** the external MetalLB/nginx path has no oauth-proxy, so its `/api` location **clears** client `X-Forwarded-User`/`X-Auth-Request-*` headers — identity there comes only from the signed session cookie. (Closed a header-spoof auth bypass.)

**Default admin (dedicated break-glass):** `review_console_default_admin_email` is a **dedicated** account (e.g. `admin@dav.local`), kept distinct from any real person. The seed *ensures it exists* on every boot — created **deactivated** when a real platform admin already exists, password from `vault_review_console_default_admin_password` (else `changeme`). It is **never auto-disabled**; deleting it via the UI **deactivates** it instead. `reconcile_default_admin()` guarantees ≥1 enabled platform admin: if the count hits zero it **re-activates** the default and **notifies** (email + log + a `warning` the triggering API response carries to a UI toast). Run on boot and after every account/role change.

**Invites:** adding an account with **no password** (`POST /api/accounts`) auto-creates an activation invitation and emails the set-password link (copy-link fallback when SMTP is off); `POST /api/accounts/{x}/invite` re-sends for a password-less account; the per-project Members panel also invites with a project role. Link base = `DAV_PUBLIC_BASE_URL` (config-derived from hostname + custom port; see Deployment) → SMTP `base_url` → request Host. Accept (`/api/invites/{token}/accept`) sets the password, enables the account, and maps invite roles into `rbac_account_roles`.

**Endpoints:** `/api/accounts` (GET list-with-roles, POST create+invite, PATCH enable/disable+password, DELETE — self-delete blocked, default→deactivate), `POST /api/accounts/{x}/invite`; `/api/rbac/roles` + `/api/rbac/privileges` (GET), `POST/PUT/DELETE /api/rbac/roles`; `POST/DELETE /api/accounts/{x}/roles` (assign/revoke, escalation-guarded); `GET/POST/DELETE /api/projects/{id}/members` (RBAC project bindings — replaces the legacy `project_members` writes); `PUT /api/me/default-project`.

**UI:** Config → **Users & roles** (platform-admin) — add-account form, account list with enabled toggle + role chips (removable) + assign-role dropdown (platform & per-project), and a **Roles × Privileges matrix** (per-role privilege chips, scope-aware; create/delete custom roles). **Left-nav shortcuts** (privilege-gated): **Users & roles** (platform admin) and **Projects** (project admin / `project.create`). Account menu (top-right): self-service logout / change-password / appearance.

> **Next slice (in progress): a proper OpenShift-style RBAC management UI/UX** — no model change, just the interface: a **Roles** tab (catalog grouped by scope, per-role privilege matrix, create/clone/edit/delete) and a **Role bindings** tab (subject × role × scope/project, incl. the LDAP group mapper). The Projects section is the per-project lens on the same bindings.

---

## Service-to-service auth (engine → API) (v0.16.0)

The engine (the Tekton **run-corpus** task) materializes *managed* UCs at run start by fetching each from the console API (`GET /api/use-cases/{uuid}` against `dav-review-api.dav.svc:8000`). Once the API enforces auth (multi-user mode), that in-cluster call must authenticate too — otherwise the gate 401s it and the run silently drops every managed UC (the bug that made an "All Use Cases" run cover only the corpus-file matches, e.g. 7 of 32).

**Mechanism — Kubernetes ServiceAccount projected token + TokenReview (no shared secret):**
- The run-corpus Task mounts a **projected `serviceAccountToken`** volume for the run pod's SA (`system:serviceaccount:{ns}:pipeline` — the OpenShift Pipelines default), scoped to **audience `dav-api`**, ~1h TTL, auto-rotated by the kubelet, at `/var/run/secrets/dav/api-token`.
- The engine reads that file fresh per call and sends it as `Authorization: Bearer <jwt>` (`run_corpus.py`, env `DAV_API_TOKEN_PATH`).
- The API validates it via the **TokenReview** API (`validations.review_service_token`): the token must cryptographically authenticate, **carry the `dav-api` audience** (so a token minted for any other service can't be replayed here), **and** its SA username must be in `DAV_TRUSTED_SERVICE_ACCOUNTS`. A pass resolves to the synthetic identity **`system:engine`**, which bypasses the approval gate + project privilege checks **for that request only** (`main.py`: `_validate_service_token` runs once up front in `_approval_gate`, sets `request.state._svc_ok`; `get_user` / `require_priv` / `_require_priv_conn` / `require_role` honor it). Results are cached briefly by token digest so a multi-UC fetch issues one TokenReview, not N.
- **Why not a shared static secret:** short-lived + identity-bound beats a long-lived secret with nothing scoping *who* may present it. Nothing static is baked into an image or Secret to leak or rotate by hand.

**Supporting RBAC + network controls:**
- The API SA (`dav-review-api`) holds the built-in **`system:auth-delegator`** ClusterRole (the only grant needed to *create* TokenReviews; it can validate tokens but mint/escalate nothing). Bound via a namespaced-named ClusterRoleBinding so multiple DAV installs don't collide.
- **Defense-in-depth NetworkPolicy** (`dav-review-api-allow`): the API has **no direct external Route** — every legitimate caller (UI nginx proxy, oauth-proxy sidecar, engine run pods, webhook EventListener) lives in the `dav` namespace, so ingress on `:8000` is restricted to same-namespace pods. Kubelet health probes come from the node and are permitted by OVN-Kubernetes regardless.

**Verification (deploy-time):** from an in-namespace pod, a request to `/api/use-cases/{uuid}` with **no token → 401**, with a **valid pipeline-SA token (aud `dav-api`) → 200**, with a **wrong-audience token → 401**.

**Ansible:** `dav_api_token_audience` (default `dav-api`), `dav_pipeline_service_account` (default `pipeline`), `dav_api_networkpolicy_enabled` (default true); the auth-delegator binding ships in `review-console-self-test-rbac.yaml.j2`, the NetworkPolicy in `namespace.yaml`, the projected-token volume in `tekton-tasks/dav-run-corpus.yaml.j2`.

---

## Corpus-files cache reconciliation (v0.17.0)

The `files` table is the API's projection of the corpus — it backs **All-set
membership**, the capability **catalog**, and `/api/corpus`. Originally it was
pre-seeded at boot from a single-source clone (`/data/repo`). When DAV moved to
**multi-source corpus** (ADR-007/M11b: the engine clones each registered corpus
repo at run time), that pre-seed was disabled but **nothing replaced it** — the
cache became a **stale orphan**, carrying old single-source layouts
(`dav/`, `use-cases/`, `use_cases/`) while the engine ran the current `dcm`/`udlm`
repos. Symptom: an "All Use Cases" run showed 32 UCs in the set but only ran 30
(2 cached UCs no longer existed in the live corpus).

**Fix — `sync_corpus_files()` reconciles from the same source the engine uses.**
It clones each registered corpus repo (`managed_repos` where `roles @> {corpus}`,
reusing `walk_corpus` + `resolve_root_path(repo,'corpus')` + `crypto.decrypt` +
the `x-access-token` clone pattern), upserts every UC file with the path scheme
that **mirrors the engine's staging exactly** — `<namespace>/<path under the
corpus role's root_path>` (so `dcm` root_path `dav` → `dcm/use-cases/…`, `udlm`
→ `udlm/…`) — then **mark-and-sweeps**: `DELETE FROM files WHERE last_seen_at <
sync_start`. **Guard:** the sweep runs only when **every** repo cloned
successfully and ≥1 file loaded, so a transient clone failure never wipes the
cache; counts (seen / pruned / per-repo) are logged (no silent truncation).

**Triggers (all five):**
- **Boot + hourly** — `_corpus_sync_loop` (first pass ~20 s after start, then 1 h
  as a backstop for missed webhooks / out-of-band edits).
- **Webhook** — a GitHub `push` to a corpus repo (HMAC-validated against the
  repo's per-repo secret) → `sync_corpus_files`; add **Pushes** to the existing
  `/api/webhooks/github/pr-comments` URL's events.
- **Pre-run validation** — `set_corpus_subpath` calls `_ensure_corpus_fresh`
  (10-min gate) when a set is selected in New Run, so the UC count + membership
  are current before the run.
- **Manual** — `POST /api/corpus/resync` (platform-admin) + `GET
  /api/corpus/sync-status`; surfaced as **Config → Evaluation corpus → ↻ Resync
  corpus cache** with a freshness line.

Verified live: reconciled 195 stale rows → 63 accurate (`dcm` 12 + `udlm` 51, the
9 `dcm` UCs at `dcm/use-cases/…`); a planted ghost row was pruned (`pruned:1`);
idempotent.

---

## Run throughput & methodology (v0.18.0)

Profiling a verification run (qwen3-32b, N=3 ensemble, two-pass) surfaced where
the ~33 min/UC actually goes — and it is **not** the model "thinking":

| Signal | Observed | Read |
|---|---|---|
| prompt : generation tokens | **~26 : 1** | mostly re-submitted context (much served from vLLM prefix cache, so this overstates *compute*) |
| generation rate | **24.7 tok/s** | the binding constraint — decode of one sequence |
| TTFT p95 | 4.4 s/turn | prefill of the growing tail each turn |
| running / KV-cache | **1 / 5.8 %** | one sequence at a time; the GPU is ~idle |
| per ensemble sample | **18 turns · 17 tool calls · ~60k chars tool results · ~63k chars generated** | the agent loop drives the cost |

Decode math: ~18 turns × (4.4 s prefill + ~35 s decode of ~880 tokens at
24.7 tok/s) ≈ 12 min/sample; **× 3 samples run serially ≈ 36 min/UC** — matching
observation. So the run is **decode-bound on one sequence while the GPU sits at
running=1 / KV 5.8 %**.

**Levers (ranked by impact ÷ risk):**
1. **Concurrent ensemble samples** *(shipped, v0.18.0)* — the N samples are
   independent random-seed runs, so running them in parallel lets vLLM **batch**
   them on the idle GPU: ~3× the per-UC throughput with **no effect on results**.
   Wired as `sample-concurrency` (Tekton param → engine `--sample-concurrency`;
   API default `min(sample_count, DAV_MAX_SAMPLE_CONCURRENCY=4)`). The engine
   already supported it (`run_samples` ThreadPoolExecutor; per-sample MCP client,
   shared thread-safe inference client). *A/B (concurrency 1 vs 3) to quantify.*
   **UC-level concurrency** extends the same idea one level up: `uc_concurrency`
   on the trigger (`uc-concurrency` Tekton param → engine `--uc-concurrency`)
   runs whole UCs in parallel — UCs are independent agent loops (per-UC client
   factories; per-UC seeds derive from the UC uuid, so results are
   order-independent), and batched decode on the memory-bandwidth-bound R9700s
   scales aggregate tok/s nearly free. Effective in-flight requests =
   uc_concurrency × sample_concurrency (vLLM `max-num-seqs=32` is the ceiling).
2. **Cut the agent-loop tax** — 17 tool calls × a growing transcript drives the
   18 turns of decode. Tighter/fewer MCP queries, smaller tool results, or
   pruning old results between turns reduces turns → less decode. *Affects
   results — requires careful A/B.*
3. **Ensemble policy per job** — N=3 + two-pass is the high-assurance setting;
   N=1 / single-pass is 2–4× faster for routine sweeps.
4. **Decode speed** — 24.7 tok/s for a 32B is low; serving config / a smaller
   triage model are follow-ups.

Methodology principle: **results first, speed close behind** — a correct answer
that arrives too late to act on has little value. Throughput changes that don't
alter results (lever 1) ship first; anything that touches the analysis (lever 2)
is A/B'd for quality before adoption.

**Final-emit resilience:** early finals (the model emitting its analysis before
the budget-hit turn) are *unguided* — `guided_json` can't coexist with tool
definitions, so nothing constrains enums on that path (and Anthropic endpoints
never have guided decoding). Two layers keep a stray label from discarding a
complete analysis: (1) the prompt explicitly separates the axes' vocabularies
(severity = 5-word scale, confidence = exactly high/medium/low — "moderate is
not a confidence value"); (2) if final-JSON validation fails anyway, the agent
re-asks **once** with the guided schema attached ("fix the format, don't change
the findings") — on vLLM the schema's `enum` makes an illegal label
unrepresentable, so the **model** picks which legal label fits its own analysis.
Deliberately *not* done: silently aliasing `moderate→medium` in the validator —
a synonym guess by the engine could mislabel; the re-ask keeps the choice with
the model. Added after run `2026-06-06T23-04-55Z-5fae105`, where
`confidence: "moderate"` killed an otherwise-complete 358 s analysis.

---

## Deployment toggles — TLS + auth activation (v0.10.1)

All site-specific values live in `ansible/inventory/group_vars/all/vars.local.yaml`; framework defaults (all **off**) are in the `dav` role's `defaults/main.yaml`. Nothing here changes behavior until the operator opts in.

**TLS via cert-manager (Let's Encrypt).** When `review_console_cert_manager_enabled: true`, the `dav-review-ui` Route is annotated for the cert-manager **openshift-routes** controller (`cert-manager.io/issuer-name|kind|group`, plus optional `duration`/`renew-before`). The controller provisions a cert onto `spec.tls.{certificate,key}`. Because the Route is applied with **server-side apply** under `field_manager: ansible` and the template never sets the cert fields, the controller co-owns them — re-running the playbook does **not** clobber the issued cert. Optionally (`review_console_cert_manager_create_issuer: true`) the playbook creates the `Issuer`/`ClusterIssuer` itself from `review_console_letsencrypt_email` / `review_console_letsencrypt_server` with an HTTP-01 solver on ingress class `openshift-default` (template: `review-console-cert-issuer.yaml.j2`). Requires cert-manager core + the openshift-routes controller on the cluster.

**Multi-user auth activation.** Two flags, meant to be flipped together:
- `review_console_require_auth: true` → sets `DAV_REQUIRE_AUTH=true` on the API → enforces app sessions, the approval gate, and role scoping (otherwise single-user passthrough).
- `review_console_relaxed_proxy: true` → the oauth-proxy adds `--skip-auth-regex` for `/`, `/api/`, assets, so app-native (internal/invited/LDAP) users reach the DAV login + `/api/auth/*` instead of bouncing off OCP login. `/sso` stays OCP-protected for OCP-oauth bootstrap.

The **dedicated** break-glass platform-admin (`review_console_default_admin_email`, keep it distinct from a real person) is *ensured to exist* on every boot — created **deactivated** when a real platform admin already exists, password from `vault_review_console_default_admin_password` (else `changeme`), forced to change on first login. See the RBAC section for the never-auto-disable / re-activate-on-zero-admins invariant.

**Outbound link base URL:** `DAV_PUBLIC_BASE_URL` (env on the API) is used to build invite links. It's config-derived: explicit `review_console_public_base_url`, else `https://{review_console_hostname}` + the LoadBalancer port **when the LB is enabled and the port is non-standard** (no `:443`/`:80` ever appended). The LB's nginx also forwards `Host $http_host` (not `$host`) so the custom port survives as a runtime fallback.

**Rollout:** set the flags in `vars.local.yaml` → `ansible-playbook playbook.yaml --tags review-console` → log in as the default admin → change password → create projects + assign users roles. Flip `require_auth` only after confirming the admin works (it strictly enforces approval).

### External hosting on a custom port — MetalLB LoadBalancer (v0.10.2)

For hosting the console off-cluster on a dedicated IP + custom port (e.g. `dav.roadfeldt.com:8843`), a MetalLB `LoadBalancer` Service bypasses the OCP router. Internal DNS for the hostname points at the MetalLB IP, and the firewall forwards the WAN port to the same IP:port — so both paths share `review_console_loadbalancer_port`. Mirrors the proven frc-scheduler-server pattern.

- **Path:** `MetalLB IP:8843 → Service dav-review-ui-lb → nginx :9443 (TLS) → /api → dav-review-api`. **oauth-proxy is NOT in this path** — external users authenticate app-natively, so the playbook **asserts `review_console_require_auth: true`** (otherwise `/api` would be exposed). The OCP Route stays alongside on 443.
- **nginx TLS:** a second `server{}` block on the in-pod TLS port (`review_console_loadbalancer_tls_port`, default 9443) is contributed at the **http** level (`/opt/app-root/etc/nginx.d/`, via `dav-review-ui-nginx-tls` CM) and `include`s the same `nginx.default.d` locations as the `:8080` server — identical behavior over TLS. Cert mounted from the Secret at `/etc/nginx/tls`.
- **Cert (DNS-01):** because the hostname now resolves to the MetalLB IP (not the router), HTTP-01 can't validate — so a cert-manager `Certificate` (`review-console-ui-public-cert.yaml.j2`) uses **DNS-01**, **referencing** the existing DNS-01 ClusterIssuer (`review_console_loadbalancer_cert_issuer = letsencrypt-prod`, shared with frc-scheduler-server). The playbook waits for the Secret before rolling the deployment. **Never recreate that shared issuer:** keep `review_console_cert_manager_create_issuer: false`, and the create-issuer task is additionally guarded to create-only-if-absent (its template is HTTP-01, which would otherwise clobber the DNS-01 ClusterIssuer of the same name and break every app that uses it). Since the issuer is DNS-01, the route-based cert path validates too, but it's redundant when the LB is the live path.
- **Renewal:** nginx reads the cert at startup, so a weekly `dav-review-cert-renewal` CronJob (own SA + minimal deployments-patch Role) does a rolling restart to pick up renewed certs (toggle `review_console_loadbalancer_cert_renewal_restart`).
- **IP:** explicit `review_console_loadbalancer_ip` (→ `metallb.universe.tf/loadBalancerIPs`) or a `review_console_loadbalancer_pool` (→ `metallb.universe.tf/address-pool`). All resources are removed when `review_console_loadbalancer_enabled` is false.

### Namespace egress firewall (v0.14.0)

As DAV opens to other users, an OVN `EgressFirewall/default` in the `dav` namespace (`dav_egress_firewall_enabled`, in `tasks/namespace.yaml`) restricts what the (shared) dav pods may reach **outside the cluster**: a tight set of `/32`s plus the public internet, **denying the rest of RFC1918** (lateral homelab access). Ordered (first match wins; no match = allow, so the internet stays reachable):
1. **Allow** the cluster pod (`10.128.0.0/14`) + service (`172.30.0.0/16`) overlay networks.
2. **Allow** the cluster's own **node InternalIPs as `/32`s** — **discovered at deploy time** (`kubernetes.core.k8s_info kind=Node` → InternalIP → `/32`, `dav_egress_discover_nodes: true`). These are required *only so the pod can reply to kubelet liveness/readiness probes* — the kubelet initiates from the node, and OVN's broad Deny ACL otherwise drops the reply packets, crash-looping the pod (exit 137). The pod does **not** initiate connections to nodes; this is reply traffic. Discovery keeps the list tight **and** correct across node scaling, instead of opening the whole `10.0.0.0/24` (which would expose the rest of that critical subnet — the API VIP `.60`, gateway, etc. stay **denied**).
3. **Allow** the configured endpoint `/32`s (`dav_egress_allow_cidrs`): `10.0.0.70/32` (default ingress → `*.apps.ocp.roadfeldt.com` — MCP servers + `*.apps` models), `10.0.90.20/32` (`*.llm` router → local models), `10.10.90.4/32` (`buddy` SMTP).
4. **Deny** `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`.

Driven by `dav_egress_cluster_cidrs` / `dav_egress_node_cidrs` (auto-filled) / `dav_egress_allow_cidrs` / `dav_egress_deny_cidrs` — add a `/32` to `dav_egress_allow_cidrs` whenever a new internal API/MCP endpoint is configured. **Verified:** only the node `/32`s + the three endpoint `/32`s are reachable on `10.0.0.0/24`; `10.0.0.1`/`.50`/`.60` and other homelab IPs (`10.0.90.18`, `10.10.90.9`) time out, while the pod stays `1/1` with 0 restarts.

**Architectural limit (single shared pod):** per-project *network* egress is **not** enforceable here — all projects share the pod's network identity. Per-project MCP/model isolation is enforced at the **app layer** (the pod only ever dials the *active project's* configured endpoints — see Config tenancy in the RBAC section). This firewall is a coarse namespace-wide backstop. Verified: the pod reaches the allowlisted ingress/models/SMTP + internet + DB, and an un-allowlisted homelab IP (e.g. `10.0.90.18`, `10.10.90.9`) times out.

### MCP server authentication (v0.15.0)

MCP servers can require a **bearer token**. `mcp_server_configs.auth_token_encrypted`
(Fernet, masked on GET as `has_auth_token`) holds a per-server token; the
`/api/mcp-servers` create/update endpoints accept `auth_token` (blank-on-update
preserves), and the health poll sends `Authorization: Bearer <token>` over a
**TLS-verified** connection. `dav-docs-mcp` self-registers on boot
(`_seed_docs_mcp`) from `DAV_DOCS_MCP_URL`/`DAV_DOCS_MCP_TOKEN` (env ← vault). The
server-side hardening (its own internal MetalLB IP `10.0.90.23` + DNS-01 TLS + an
nginx bearer-token sidecar, FastMCP bound to `127.0.0.1`, public Route removed)
is authored in ansible **default-off** (`dav_docs_mcp_lb_enabled`) pending a
watched rollout — see [security-audit.md](security-audit.md) H7. The unused
`openshift-mcp` / `frc-scheduler-mcp` seeds were removed.

### Security hardening (v0.15.0)

A full security sweep ([security-audit.md](security-audit.md)) found and fixed a
**live auth bypass** (relaxed-proxy `/api/` trusted a client-spoofable
`X-Forwarded-User` — the nginx `/api/` location now clears those headers in
relaxed mode, identity = signed session cookie), an **unauthenticated
cross-project export** (`/api/export`), a cluster of **read endpoints that
authenticated but didn't authorize** (`use-cases/{uuid}`, `sets/{id}`, `catalog`,
`credentials`, `stage-context` — now gate on `project.data.read` + the
membership-validated active project), two **path traversals**
(`results._safe_under` containment), a git **arg-injection** + PAT-in-stderr
leak, an **archive-bomb** DoS cap on import, and the MCP poll's `verify=False`.
Outbound **email** now includes mandatory `Date`/`Message-ID` headers (amavis
was quarantining DAV mail as `BAD-HEADER`).

### Presence gauge (v0.15.0, platform-admin)

The masthead shows a live **"N online · M active"** chip **for platform admins
only**. The `_approval_gate` records a per-identity last-seen on every
authenticated request (in-memory; single API replica). `GET /api/presence`
(platform-admin gated) returns `{online, active}`: *online* = a tab seen in the
last 2 min (any request, incl. background polls); *active* = a real, non-poll
request in the last 5 min (background pollers like `/api/me`, `/api/runs`,
health are excluded from "active"). The UI polls it every 45 s when the caller is
a platform admin. **Who's-online popover (v0.16.0):** clicking the chip opens a
popover listing who's online (from `GET /api/presence?detail=1`, which returns the
per-user list); while open it **live-refreshes every 4 s** so it reads as a
real-time view. `_startPresence(platAdmin)` (from `_applyAccessVisibility`) gates
the whole feature on platform-admin.

### Outbound email headers (v0.15.0)

`_smtp_message()` builds every outbound message (invites, notifications, SMTP
test) with `From`/`To`/`Subject` **plus `Date` and `Message-ID`** — both
mandatory under RFC 5322. Omitting them caused content filters (amavis
`bad_header`) to quarantine DAV's mail (`BouncedOutbound`/`BAD-HEADER-0`).

---

## Projects (multi-project) (v0.10.0, RBAC-membership in v0.13.0)

The console carves data into **projects**. **Membership is now RBAC** — a user is a member of a project iff they hold a project-scoped role on it (`rbac_account_roles`), *not* the legacy `project_members` table (which is migrated from + no longer authoritative). A `default` project is seeded; existing data was backfilled into it.

**Visibility (v0.13.0):**
- **Top-bar switcher** (`GET /api/projects/mine`) — **only** projects you're a member of, for **everyone including platform admins** (they add themselves to projects to access data). Returns your resolved default project.
- **RBAC views** (`GET /api/projects`) — **all** projects for platform admins (to assign/move/delete anywhere); **member-only** for everyone else. `my_role`/`member_count` come from RBAC.
- **Data scoping** (`_active_project_id`) — the `X-DAV-Project` header is honored only when you're a member of it; else your default project.

**Per-user default project:** `users.default_project_id`. On login the switcher lands on it; auto-set to a member project when unset. A ☆/★ next to the switcher sets it (`PUT /api/me/default-project`).

**Lifecycle:** `POST /api/projects` (gated on `project.create`; creator is granted Project Admin via RBAC); `PATCH /api/projects/{id}` (rename/archive); `POST /api/projects/{id}/move-data` (platform admin — reassigns all project-scoped rows incl. capability-catalog, collision-safe, to another project); `DELETE /api/projects/{id}` (gated on `project.delete`/platform.admin; refuses `default` and any project that still holds content — move/remove its data first).

**Members panel (RBAC, v0.13.0):** Config → Projects → a project's **Members** = its project-role bindings (a user may appear once per role), add/remove via `rbac_account_roles`, escalation-guarded, with email-invite. Same model as the Accounts panel.

---

## Data tenancy — scope data to project (v0.10.0)

`project_id` was added to `managed_use_cases`, `analysis_runs`, `run_sessions`, `use_case_sets`, and `analysis_output_cache` (the capability `catalog` already had it). Existing rows are backfilled into `default` and the column is indexed. Children (`uc_analyses` / `uc_gaps` / `uc_capabilities` / set members) inherit project via their FK to the parent.

Scoping is applied via `_active_project_id()`:

- **UCs** — list + create scoped to the active project.
- **Runs** — list scoped (a run shows under its session's project; orphan Tekton runs fall under `default`); trigger sets `run_sessions.project_id`; ingest inherits that onto `analysis_runs`.
- **Results** — the workspace list is filtered by the run's project.
- **Sets** — list + create scoped.

**Remaining hardening (noted, not yet done):** per-project capability `catalog` and `model_defaults` (still global today); and strict project checks on individual *detail* endpoints — these are reachable by id today regardless of project, which is acceptable under the approved-user trust model but should be tightened.

---

## DB schema summary

| Table | Purpose |
|---|---|
| `managed_use_cases` | UC CRUD with lifecycle state (carries `project_id`) |
| `lifecycle_events` | Audit trail for UC state transitions |
| `use_case_sets` | Named UC collections (carries `project_id`) |
| `use_case_set_members` | UC set membership |
| `run_sessions` | Per-run metadata + resource stats (console-triggered runs only); carries `project_id` + `archived` |
| `analysis_runs` | Ingested run index (carries `project_id`) |
| `uc_analyses` | Per-UC analysis results |
| `uc_gaps` | Per-gap records |
| `analysis_output_cache` | Write-through cache of generated Review / Enhancement output; `UNIQUE(run_id, kind, scope, uc_uuid)`; carries `project_id` + staleness via `source_ingested_at` (v0.10.0) |
| `model_configs` | Centralized LLM endpoint registry; use-flags `use_arch_review`, `use_uc_assist` per row |
| `model_defaults` | Project-scoped model defaults keyed by pipeline type (`arch-review`, `enhancement`, `evaluation`, `uc-authoring`); references `model_configs` (v0.10.0 adds `enhancement` + `uc-authoring`) |
| `mcp_server_configs` | MCP server registry; `use_uc_assist` flag per server |
| `users` | Source-agnostic **accounts**: email/identity, `password_hash` (argon2), `enabled` (gate flag), `default_project_id`, `source` (informational); legacy `role` kept for back-compat only (v0.13.0) |
| `user_invitations` | Tokened account-activation / project invites (set-password link) |
| `rbac_privileges` | Privilege vocabulary (matrix columns), each `scope` platform/project (v0.12.0) |
| `rbac_roles` | Roles (groups of privileges), `scope` platform/project, `is_system` (v0.12.0) |
| `rbac_role_privileges` | Role × privilege matrix (v0.12.0) |
| `rbac_account_roles` | Account × role × `project_id` bindings (NULL project = platform) (v0.12.0) |
| `rbac_group_role_mappings` | LDAP/OCP group → role mappings (structure; sync is a later slice) (v0.12.0) |
| `app_settings` | In-app platform settings (LDAP/SMTP), secret fields Fernet-encrypted |
| `projects` | Multi-project tenancy roots; `default` always seeded (v0.10.0) |
| ~~`project_members`~~ | **Deprecated** — migrated into `rbac_account_roles`; membership is now RBAC (v0.13.0) |
| `capability_catalog` | Capability taxonomy (carries `project_id`) |
| `project_stage_context` | Per-project, per-stage context overrides |
| ~~`code_repo_configs`~~ | Folded into `managed_repos` with `role=enhancement-target` per [ADR-006](../adr/006-consolidate-code-repos-into-managed-repos.md) (M10). Table left in place for one release cycle; `/api/code-repos/*` endpoints return 410 Gone with the new path. |

---

## UI conventions

**Delete confirmation — `_armDeleteBtn(btn, action)`:** Native `confirm()` is suppressed by the OCP OAuth proxy, so all destructive actions use a two-click pattern instead. First click changes the button text to "Sure?" and adds a red outline; second click on the same button fires `action()`; clicking anywhere else resets the button. Used for: model endpoint delete, UC delete, UC set delete, MCP server delete, code repo delete. Any new destructive button must use this utility rather than `confirm()`.

**Modal stacking z-index:** Every `.modal-overlay` shares `z-index: 200` by default; the later-in-DOM modal wins when two are open. When a secondary editor opens from inside a list/manager modal (e.g., `setModal` or `addMemberModal` opened from inside `manageSetsModal`), bump that secondary's z-index above the parent — currently both lift to `210` via an explicit `#setModal.modal-overlay, #addMemberModal.modal-overlay { z-index: 210; }` rule. New "modal opened from inside another modal" cases should follow the same pattern (add the id to that selector) rather than rearranging DOM order.

**Model browser — `_openModelBrowser(selId, storageKey)`:** A shared fixed-position overlay that lets the user pick an endpoint from the registered model_configs URLs (or type a custom URL), probe it for available models via `GET /api/sources/inference/models?endpoint=...`, and select or manually enter a model ID. Clicking "Use this model" either selects a matching registered model_config (by id) or stores a custom `endpoint_url + model_id` pair in localStorage under `${storageKey}_ep` / `${storageKey}_mi` and sets the selector to a dynamically-added `__custom__` option.

Overlay background uses `var(--bg-panel)` — `var(--surface)` is not a defined theme variable and must not be used. The probe API returns `{reachable, models: [...], error, latency_ms}` (not a raw array); the probe handler reads `result.models`. Probing runs automatically on open and on every endpoint selector change via `_mbProbe()`; the "Probe for models" button re-triggers it manually.

**Custom model resolution — `_resolveEndpointModel(selId, storageKey)`:** Returns `{model_config_id}` for registered selections or `{endpoint_url, model_id}` for custom ones. Spread this into every API body that calls `/api/arch-review`, `/api/enhancements`, or `/api/uc-assist` instead of reading the selector value directly. All three API endpoints now accept either form (model_config_id OR endpoint_url+model_id).

**Storage-based resolution — `_resolveFromStorage(storageKey)`:** Reads model selection directly from localStorage, bypassing the DOM. Used where multiple selectors share the same storage key (e.g. UC Assist Config selector and panel selector) so the result is always consistent regardless of which selector the user last touched.

**`_populateModelSel(selId, storageKey)`:** When the stored value is `__custom__`, preserves the custom option if the endpoint+model no longer matches any registered row; upgrades to a registered id automatically if a new matching row is added.

**Model selector scope rules:**
- **Project-scoped** (DB, `model_defaults` table): evaluation model (`key='evaluation'`) and arch-review model (`key='arch-review'`). Set in Config → AI Models. Applies to all users. New Run modal reads the evaluation default; `/api/arch-review` falls back to the arch-review default when no per-call override is supplied. User can override per-session via localStorage (`nrLastModel`, `reviewLastModel`). Custom endpoint+model pairs cannot be project defaults.
- **User-scoped** (localStorage): arch review per-call selection (`reviewLastModel`), enhancement (`enhanceLastModel`), UC Assist (`ucAssistModelId`), standalone review panel (`reviewLastModel`). Per-browser, not shared across users.

**UC Assist panel:** The UC Assist slide-in panel (in the UC editor modal) contains its own model selector (`ucAssistPanelModelSel`) using the same `ucAssistModelId` localStorage key as the Config selector. Both mirrors write to the same key; changing either one is reflected in the other on next populate. The compose textarea is 6 rows tall, user-resizable vertically, with ⌘↵ as the keyboard shortcut to send. `_ucAssistCheckAvail()` and `_ucAssistSend()` resolve the model via `_resolveFromStorage` to remain DOM-agnostic. When the panel is opened with no model selected, focus moves to the model selector automatically. The error message when no model is configured directs users to the inline selector, not Config.

**UC editor UUID auto-generation:** When the New UC modal is opened with no YAML content (fresh template), `crypto.randomUUID()` replaces the `uc-<your-uuid-here>` placeholder with a real UUID, preventing 409 collisions on repeated new-UC creation.

**Model selector layout:** All model selectors use an inline flex row: `[selector][Browse…]`. Selectors in narrow sidebar panels (e.g. Run Profile) also use this pattern for consistency. The option label is `${name} (${local|frontier})` unless `name` already contains the suffix (case-insensitive), in which case the suffix is omitted to avoid "Qwen3-32B (local) (local)" duplication.

**Empty api_key handling:** Local model endpoints (e.g. vLLM, llama.cpp) often have no API key. The Authorization header is only sent when `api_key` is non-empty; otherwise the request goes unauthenticated. Anthropic endpoints raise a clear error when the key is missing, since they always require one. (`uc_assist.py` matched `arch_review.py` for this behavior in v0.9.14.)

**UC Assist timeout:** Default httpx timeout in `uc_assist.chat()` is **300s** (was 60s, bumped in v0.9.15). Local 32B models doing multi-paragraph YAML drafting routinely exceed 60s — especially when current_yaml is large and the model has to revise rather than draft from scratch. The 5-minute ceiling matches what's reasonable for a single-turn assist response on local hardware; if a request takes longer than 5 minutes, something else is wrong (model unhealthy, GPU contention).

**UC modal layout:** The UC editor modal-body uses `display:flex; flex-direction:row` to put the YAML editor pane and the AI Assist panel side-by-side. The base `.modal-body` class is `flex-direction:column`, so the row direction must be set explicitly on the inline style — forgetting this stacks the panels vertically.

**UC Name / title field (v0.9.17):** UCs carry a human-readable name in the YAML's top-level `title:` field. The UC editor surfaces it as a dedicated **Name** input above the YAML editor. On save, the input value is injected into the YAML's `title:` line via `_injectTitleIntoYaml()` so the two halves of the editor never disagree. On open, `_extractTitleFromYaml()` pulls the current value into the input. The API's `_derive_uc_title()` helper prefers top-level `title:` > `scenario.description` > `handle` > `uuid`, with a 120-char cap. UC list and detail render the title prominently; UUID and handle move to a small monospaced ID line. UC Assist's system prompt teaches the model to populate `title:`, and the "Apply to editor" button syncs the title from the assistant's YAML into the Name input.

**Default Set (v0.9.18):** `use_case_sets` has an `is_default` boolean column with a partial unique index enforcing at most one default row. The Sets detail header has a **★ Set as default** / **Clear default** toggle; the Sets list shows a `DEFAULT` badge on the chosen one. The New Run modal — when opened with no explicit set/subpath context (e.g. the top-bar "+ New run" button) — checks for a default Set and pre-fills `corpus_subpath` from its common path prefix, with a banner reading "Pre-filled from default Set …". Calls that already carry their own context (`runSet`, the Re-analyze flow) bypass this. Endpoints: `PUT /api/sets/{id}/default` (set), `DELETE /api/sets/{id}/default` (clear). Migration 004 adds the column + partial unique index.

**Test evaluation from UC (v0.9.19):** UC detail pane gets a **▶ Test evaluation** button and a **Test history** section.
- **Button** — enabled for corpus UCs only. Calls `testRunUC(uuid, path, title)` which computes the narrowest directory containing the UC's path (`_narrowestSubpath`) and opens the New Run modal pre-filled with that as `corpus_subpath`, plus a session-name pre-fill of `test: <title>`. Managed UCs see the button disabled with a tooltip explaining they need to be promoted to the corpus first (Push to corpus, planned). The engine still runs everything under `corpus_subpath`, so a "single UC test" may include siblings in the same directory — true per-UC engine filtering is a future engine change.
- **Test history section** — async-loaded by `loadUCTestHistory(uuid)` after the detail pane renders. Calls `GET /api/use-cases/{uuid}/runs?limit=10` and renders each run as a row showing verdict (color-coded), run_id, wall time, gap count, and timestamp. Clicking a row routes through `_openUCRunResult` which switches to the Results tab, selects the run, then opens the per-UC analysis — works for both managed and corpus UCs since the join key is `uc_uuid`.

This is one half of the **Run test evaluation from UC** pipeline item — the single-UC path. The multi-select-from-list path lands in the upcoming UC/Sets merge work (the unified list makes batch selection natural).

**Merged UC/Sets view (v0.9.20):** The standalone Sets tab is gone; Sets are now a left-rail filter on the unified **Use Cases** tab. Three-column layout:
- **Left rail (Sets filter):** "All UCs" + "(No set)" + one item per Set with member count + DEFAULT badge. Clicking filters the UC list. Above the rail: **+ New** (creates a Set) and **⚙ Manage** (opens the Manage Sets modal). State: `activeSetId` is either `null` (All), `'__none__'` (UCs with no set), or a numeric set id.
- **Middle (UC list):** unchanged shape; gains a header **active-set banner** when a Set filter is applied — shows `Set: <name> (N UCs)` with ▶ Run / ⚙ Manage / × Clear buttons.
- **Right (UC detail):** unchanged; the **Sets** chip strip now filters the list in place (clicking a chip calls `selectSet(id)` rather than navigating to the gone Sets view).
- **Manage Sets modal:** the new home for what used to be the Sets-detail-pane actions — per-Set Edit / ▶ Run / ★ Default / ↑ Promote / ↓ Export / + UC / × Delete. Reuses existing endpoints; no API changes for management itself.
- **API:** `GET /api/use-cases` now returns `set_ids: [int]` on each row so the client-side set filter doesn't need an N+1 lookup. The set→members join is computed once per list call.
- **Nav:** the Sets nav item is removed. Existing in-app links that did `switchView('sets')` are repointed to the in-place `selectSet()` filter — no tab switch needed.

Why this shape: Sets are functionally tags on UCs. Treating them as a sibling tab doubled UI surface for what's really a UC property. The merge also makes batch selection a natural next step (multi-select UC rows → "Add to set" / "Run test eval on selected") without inventing a new screen.

**UC ↔ Set membership editing (v0.9.21):** The merged view was missing the discoverable affordances the old Sets detail pane provided for adding/removing members. Three new paths to manage membership:
1. **Drag a UC row onto a Set in the left rail** — the UC list item is `draggable=true` and stashes the UC reference in `dataTransfer` as `application/x-dav-uc`; Set rail items are drop targets that highlight on dragover and `POST /api/sets/{id}/members` on drop.
2. **+ Add to Set picker on the UC detail** — a dashed `+` chip in the Sets section opens an in-place popover listing every Set with current memberships pre-checked. Clicking toggles membership (add or remove). Outside-click dismisses.
3. **Per-chip × remove** — each Set chip in the UC detail has an inline × that removes that single membership without confirmation (set memberships are cheap; full Set delete still uses the two-click arm pattern).

All three paths share `_addUCToSet` / `_removeUCFromSet` / `_toggleUCSetMembership` helpers, which re-fetch sets + UCs and re-render the Manage Sets modal so all surfaces stay consistent.

**"All Use Cases" — synthetic, immutable set (v0.16.0; id reworked 2026-06-07):** "All" is exposed as a first-class **set** (reserved id **`"__all__"`**, name "All Use Cases") so it runs / arch-reviews / reports through the *exact same* paths as any real set — the *standardization over customization* principle in practice, instead of `if isAll` branches scattered everywhere. It is **dynamic + immutable**: its membership is computed on read as **every** managed UC (from the DB) **+ every** corpus UC (parsed from the files table), deduped — `_all_set_members(conn, pid)`. `list_sets` prepends it (with a live member count); `get_set("__all__")` returns `{**_all_set_dict(...), members}`. All mutation endpoints (rename/delete/default/add/remove member) reject it via `_reject_all_set_edit` — you can run it and make *temporary* per-run tweaks, but never edit, filter, or persist changes to it. `set_corpus_subpath` / `promote_set_members` / `set_readiness` special-case the sentinel to resolve against the full membership. Because both kinds run, an All-set run materializes managed UCs via the engine→API fetch (see §Service-to-service auth) **and** filters the corpus to its members — so "All" actually means all. **Why a string sentinel:** the original reserved id was `0`, which is falsy in JS — it repeatedly caused silent breakage (`if (!setId)` guards degraded All-set flows to Full-corpus; `Number('__all__')`-style coercions are now also guarded). `set_id` path params are `str` server-side; `_real_set_id()` int-validates real ids after the `_is_all_set()` branch (which still accepts legacy `0`/`"0"` for back-compat). UI excludes it from real-set-only dropdowns via `s.id !== ALL_SET_ID`. **Persisted lineage:** `run_sessions.set_id` is a FK to `use_case_sets`, and the synthetic set has no such row — so an All-set run records `set_id = NULL` with `set_name = "All Use Cases"` carrying the lineage (the server coerces the sentinel → NULL on insert). On read, name-only set lineage is resolved back to the live set by name (rerun, run detail). The legacy "Full corpus" run option is reworded **"Full corpus (all corpus UCs, unfiltered)"** to disambiguate it from "All Use Cases".

**Design-system layer + view-header consistency (v0.16.0):** a shared CSS layer enforces the Consistency principle on controls + typography: `.btn-sm` (the single small-button scale used for all toolbar actions), `.btn-icon` (icon-only), `.toolbar-actions` (the standard right-aligned action cluster), `.view-title`/`.view-subtitle` (page headers), `.panel-title` (section headers), and a `u-*` utility layer. The Use Cases toolbar (Import / Export / Bulk / New) was normalized onto `.btn-sm` in one `.toolbar-actions` cluster; section headers across views were unified to plain **"Use Cases"** / **"Run Results"** / **"PR Comments"** / **"Capability Catalog"** as `.panel-title` (eliminating the mixed-style "Use *cases*" that rendered the second word italic + lowercase). New views compose these classes rather than restyling inline. A `<meta name="dav-build">` stamp (set by the UI Containerfile via `sed __DAV_BUILD__`) is surfaced in the account menu so a stale browser cache is obvious at a glance; nginx serves `index.html` with `Cache-Control: no-cache, must-revalidate`.

**Run identifiers — session names everywhere (v0.9.22):** Run dropdowns and the Results-tab run list now show the human-readable session name (entered in the New Run modal as "Name"), not just the workspace `run_id` string. `GET /api/results` enriches each row with `session_name` / `session_description` / `session_category` by joining `analysis_runs` (which carries the Tekton `run_name`) to `run_sessions`. The workspace `run_id` remains the canonical key (used in URLs, history rows, etc.) and is shown as a small mono-spaced sub-line so reviewers can correlate when they need to. Result-list filter now matches name / description / category in addition to run_id. The Review & Plan and comparison dropdowns format options as `<session name> (<run_id prefix>…)` with the full run_id in the `title` for hover.

**Push to corpus as PR (v0.9.23):** Managed UCs can be pushed to the consumer's corpus repo as a GitHub PR — closes the loop from "drafted in the console" to "shipped to the engine's source of truth."

- **Migration 005** adds `corpus_pr_url`, `corpus_pr_state` (`open|merged|closed`), `corpus_commit_sha`, `corpus_synced_at`, `corpus_synced_by`, `corpus_synced_path`, `corpus_branch` to `managed_use_cases`.
- **`corpus_push.py`** module: GitHub-first provider via the REST API (Contents + Refs + Pulls), no `git` binary needed in the container. Detects host from the configured corpus URL; raises clear errors for non-GitHub hosts (gitlab.*, etc.). Auth: `DAV_CORPUS_PUSH_TOKEN` env var (PAT with `repo` scope) — set in the consumer Secret per [[feedback-consumer-config]].
- **API endpoints:**
  - `GET /api/corpus-push/status` — returns `{configured, corpus_url, host, env_var}`. UI calls this once per UC-tab session to decide button state.
  - `POST /api/use-cases/{uuid}/push-to-corpus` — opens or refreshes a PR. Strategy: resolve base-branch HEAD SHA → ensure side branch exists → write file via Contents API (creates a commit) → open PR if one isn't already open from this branch. Idempotent on re-push (same branch is reused; PR auto-updates with new commits).
  - File path: `<corpus_subpath>/<handle>.yaml` (subpath auto-detected from disk: `dav/use-cases/` or `use-cases/`); falls back to `<uuid>.yaml` when the UC has no handle. Branch name: `dav-push/<uuid-prefix>`.
- **UI:** UC detail header gets a state-aware button —
  - Never pushed → `↑ Push to corpus` (disabled with tooltip when host/token missing)
  - PR open → linked badge `⇡ PR open` + `↻ Update PR`
  - PR merged → `✓ merged`
- **Hands-off note:** the Secret carrying `DAV_CORPUS_PUSH_TOKEN` is updated by the user in the consumer namespace per [[feedback-consumer-config]]; the console only reads it from the API pod's env.

**Manage Sets — per-member × (v0.9.23):** Each set row in the Manage Sets modal now has an expander (▶) that fetches the set's members and renders them with a per-UC × to remove. The user reported the modal was the natural place to remove members and the UC chip × wasn't discoverable enough.

**Engine-side UC filter (v0.9.24):** Sets and single-UC test runs now scope the engine to *exactly* the selected UCs, not the directory they sit in. Fixes the "Run Set ended up running the full corpus" bug — Sets were scoped by directory prefix, which collapsed to the corpus root when members were scattered.

End-to-end change across engine, Tekton, and console:

- **Engine (`run_corpus.py`)** — two new optional CLI args: `--uc-handles` and `--uc-uuids`, both comma-separated. After `gather_corpus`, each YAML is parsed and kept only if its `handle:` or `uuid:` is in the filter set (OR semantics). When neither flag is set, behavior is unchanged (whole subpath runs). Logs the before/after counts so operators can see what got filtered.
- **Tekton Task `dav-run-corpus`** — two new params `uc-handles` and `uc-uuids` (string, default `""`). Wired into `OPTIONAL_ARGS` so the engine receives them when non-empty.
- **Tekton Pipeline `dav-stage2`** — same two params declared at pipeline level and passed through to the task.
- **Console API** — `RunTriggerIn` adds `uc_handles: list[str] | None` and `uc_uuids: list[str] | None`. `_mk_pipelinerun` serializes them as comma-joined PipelineRun params. Backwards-compatible: when omitted, no params are added and the engine runs the whole subpath as before.
- **Console UI** — new `_pendingRunFilter` global; populated by `runSet` (reads Set members, prefers `handle` per member, falls back to `uuid`; skips managed members that aren't in corpus yet), `testRunUC` (single UC's handle/uuid), and the default-Set fallback in `openNewRun`. `submitNewRun` passes the lists through. Banner shows "(engine-filtered to exactly these UCs)" so reviewers know the scope. `closeNewRun` clears the filter so it doesn't leak across modal openings.

**Deploy ordering matters** for backwards compatibility:
1. Engine image rebuild first (new `--uc-handles` arg accepted, old runs still work).
2. Tekton Task + Pipeline re-applied via `ansible-playbook --tags engine,tekton` (declares the new params with default `""`).
3. Console rebuild + rollout (now safe to send `uc_handles` in PipelineRun specs since the Pipeline accepts the param).

Reverse order would break: an old Pipeline rejects a PipelineRun specifying an unknown param.

**Lifecycle gating (v0.9.25):** The `draft → ready → in_review → approved → deprecated` state machine is now load-bearing for two operations:

1. **Approve transition (`* → approved`)** requires at least one passing run on file — `uc_analyses.status='success' AND verdict IN ('supported', 'partially_supported')`. The lifecycle modal pre-checks this when opened for an approve transition: shows a green "✓ N passing runs on file" banner when good, or a red "⚠ No passing runs" banner with an **override checkbox + required reason** when not. Override is recorded as `[OVERRIDE: no passing run] <reason>` in the lifecycle event, and the modal's notes label switches to "REQUIRED when overriding."
2. **Push to corpus** requires `lifecycle_state == 'approved'`. The button on the UC detail header is disabled with an explanatory tooltip when the UC is in any other state; a small **⚠ Force push** button next to it lets reviewers override with a confirmation prompt. Force-pushed UCs get an explicit note in the PR body so the corpus reviewer can see what happened.

API:
- `LifecycleTransitionIn` gains `override: bool = False`. `POST /api/use-cases/{uuid}/transition` returns **409** when approving without a passing run and no override; **400** when override is set but notes are empty.
- `PushToCorpusIn` gains `override: bool = False`. `POST /api/use-cases/{uuid}/push-to-corpus` returns **409** when the UC isn't approved and override isn't set.

Why this is soft (override available) rather than hard: the design note from 2026-05-26 — "Mitigation for trivial UCs: warn + override with a reason." A reviewer who needs to ship a one-line glossary UC shouldn't be blocked by the "needs a passing run" rule; they record the override + reason and move on. The override is visible in lifecycle history and in any pushed PR, so the trail is preserved.

**Multi-select batch test from UC list (v0.9.26):** Each UC list row now has a checkbox; the engine-side filter (v0.9.24) made this trivial. When ≥1 UC is checked, a sticky toolbar appears above the list with `N selected · ▶ Test selected · ★ Add to Set… · ×`. Actions:
- **▶ Test selected** — opens the New Run modal with `uc_handles` / `uc_uuids` built from the corpus members of the selection; subpath set to the narrowest dir that contains all selected paths. Managed UCs in the selection are skipped (with a clear banner note) since they're not in the corpus repo.
- **★ Add to Set…** — popover lists every Set; clicking one adds all selected UCs to it. Reports `N added · M already in set · K failed` in a single toast.
- **× Clear** — uncheck everything.
- Checkbox state lives in `_selectedUCs` (a module-level `Set` of UUIDs); `renderUCList` honors it across re-renders, and stale UUIDs (no longer in `allUCs`) are pruned automatically. The toolbar's "Test selected" button auto-disables when the selection contains zero corpus UCs.

**Push-then-test chain + Set-filtered list × (v0.9.27):** Two related affordances:

1. **Push & test (managed UCs)** — new `↑↦▶ Push & test` button on managed UCs that haven't been pushed. Single click does both: opens a PR on a side branch (using the override path so it doesn't trip the approval gate, since this loop exists precisely to validate UCs *before* approval), then immediately opens the New Run modal scoped to the resulting PR branch + the UC's handle. After push, the regular **▶ Test evaluation** button on the same UC stays enabled and re-tests on the same branch — useful for iteration. Implementation: `pushAndTestUC` → `POST /push-to-corpus` with `override:true` → re-fetch UC → call `testRunUC(uuid, path, title, branchOverride)`. `openNewRun` accepts an optional `branchOverride` that pre-fills `nrCorpusBranch` after `loadNewRunDefaults` populates the form.
2. **Per-row × on the UC list when filtered to a Set** — when `activeSetId` is a number, each UC row shows a small red × at the right edge. Click removes the UC from *that* set in-place without leaving the list (calls the existing `_removeUCFromSet`, which also refreshes the rail counts and the Manage modal). Click is `stopPropagation`'d so it doesn't also trigger row select. Hidden when "All UCs" or "(No set)" is the active filter.

**Run-Set gate on zero corpus members (v0.9.28):** Real bug fix. When a Set contained only managed UCs (`corpus_count=0`), the engine filter built by `_filterFromSetMembers` came back `null` (it skips managed members since they're not in the corpus). `openNewRun` then ran with an empty subpath that auto-detected to `dav/use-cases`, and with no engine filter, it processed the whole corpus subtree — the opposite of what the banner promised. Now `runSet` refuses to open the modal when `corpus_count===0`, with a toast pointing the user at the fix: push managed UCs to corpus first (via **↑↦▶ Push & test** on each one), then re-run the Set. The toast distinguishes the all-managed case ("push first") from the truly-empty Set case ("add UCs first"). *(Superseded by v0.9.29 — managed UCs can now be tested directly without push.)*

**Managed-UC test eval — engine fetches from console API (v0.9.29):** Closes the "test before promote" loop. Managed UCs can now be run through the same gap analysis as corpus UCs without being pushed to the corpus repo first. End-to-end change across engine, Tekton, and console:

- **Engine (`run_corpus.py`)** — two new args: `--managed-uc-uuids` (comma-separated UUIDs) and `--console-api-url` (e.g. `http://dav-review-api.dav.svc.cluster.local:8000`). After `gather_corpus`, the engine fetches each UUID via `GET {console_api_url}/api/use-cases/<uuid>`, extracts `yaml_content`, writes to a temp dir (`/tmp/dav-managed-ucs-XXXX/<uuid>.yaml`), and appends those paths to `corpus_files`. From that point on, materialized managed UCs are indistinguishable from corpus UCs — same filters, same processing, same `uc_analyses` rows. The empty-corpus error is now lenient when managed UCs are being added (it's fine if the corpus_path has nothing as long as there are managed UCs to fetch).
- **Tekton Task `dav-run-corpus`** — two new params `managed-uc-uuids` and `console-api-url` (the latter defaulting to the in-cluster Service DNS at template-render time). Engine call gets both when `managed-uc-uuids` is non-empty.
- **Tekton Pipeline `dav-stage2`** — declares the same params, passes through.
- **Console API** — `RunTriggerIn.managed_uc_uuids: list[str] | None`. `validations._mk_pipelinerun` serializes the UUIDs comma-joined as the `managed-uc-uuids` param plus `console-api-url` derived from `DAV_CONSOLE_INTERNAL_URL` env var (default: in-cluster Service DNS for the API).
- **Console UI** —
  - `testRunUC` now handles three shapes: corpus UC (existing handle filter), managed UC never-pushed (new — fetched via API), managed UC already pushed (PR-branch test path retained as a secondary action).
  - "▶ Test evaluation" is **active for all managed UCs** now, not just pushed ones. The button calls `testRunUC` with no path → triggers the managed-fetch flow.
  - "↑↦▶ Push & test" stays as a *secondary* action (small btn-sm) when push is configured — useful when the reviewer specifically wants the test recorded against a corpus path.
  - `_filterFromSetMembers` now collects managed UUIDs into a third bucket (`managed`); previously managed members were silently dropped. Set runs and batch test selections now run their managed members alongside corpus members.
  - `runSet` no longer refuses on `corpus_count===0` (v0.9.28 gate); it only refuses on a truly empty Set. The banner cleanly shows the split `N corpus + M managed (fetched from API)`.
  - `submitNewRun` includes `managed_uc_uuids` in the payload when `_pendingRunFilter.managed` is non-empty.

Lifecycle interaction: managed UCs can be tested in any state (draft, ready, in_review). The resulting `uc_analyses` rows are visible in **Test history** on the UC detail. The existing **approval gate** (v0.9.25) still requires at least one passing run to move to `approved`, so the test-before-approve loop now actually works for managed UCs — previously it was a chicken-and-egg situation (couldn't approve without a test, couldn't test without push, couldn't push without approval). The **push gate** (also v0.9.25) still requires `approved` state, so pre-promotion test results don't bypass the approval workflow.

**Deploy ordering** (same as v0.9.24):
1. Engine + Tekton via `ansible-playbook --tags engine,tekton` (engine binary accepts new flags; Pipeline + Task declare new params with empty defaults).
2. Console rebuild + rollout (now safe to send `managed_uc_uuids` in PipelineRun specs).

**Runs detail layout (v0.9.30):** Re-ordered + densified per the design principle above. New section order in `rdRunBody`:
1. Session (identifying info)
2. UC progress (live)
3. **GPUs + Inference** in a `.rd-stats-grid` — 2-column grid at ≥1100px, stacks at narrower widths
4. Pipeline tasks (collapsible tail, `max-height: 48vh`)
5. Prompts & responses (collapsible tail, `max-height: 48vh`)
6. Params

Stats now sit ABOVE the tail panes (was the reverse). Prompts/Tasks panes cap their height so the page doesn't grow past the viewport — scroll happens within each pane.

**Note on the 4-layout drawer (v0.9.3):** The original slide-out run-detail drawer carried a 4-layout picker (Detailed / Stacked+tails / Side-by-side dense / Prompts dominant). When the drawer was refactored into the inline run-detail panel in v1.0 (commit `1f2c429`), the layouts came along as a *single* fixed layout. v0.9.30 brings back two of the four (Detailed + Side-by-side dense — the latter is now automatic via the `.rd-stats-grid` media query). The other two (Stacked+tails, Prompts dominant) can be reintroduced as a picker if needed; they're not gone-on-principle, just unrendered.

**Push-to-corpus token wiring (v0.9.30):** The API Deployment now does `envFrom: secretRef: dav-review-api-tokens, optional: true`. The user creates the Secret out-of-band with the GitHub PAT (and any other future runtime tokens) and the API picks it up on next rollout. Documented inline in the Deployment template comment.

Setup:
```sh
# 1. Create a GitHub PAT with `repo` scope (or `public_repo` if the corpus is public):
#    https://github.com/settings/tokens
# 2. Create the Secret in the dav namespace:
oc create secret generic dav-review-api-tokens -n dav \
  --from-literal=DAV_CORPUS_PUSH_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxx
# 3. Restart the API so the new env var lands in the container:
oc rollout restart deploy/dav-review-api -n dav
```

To rotate later: `oc set data secret/dav-review-api-tokens DAV_CORPUS_PUSH_TOKEN=ghp_…` + restart.

**Bug fixes — selection authority + onclick HTML escape (v0.9.32):** Implements R1 from the canonical requirements section above. Two real bugs were keeping selection-based runs from working as documented:

1. **Engine ran the whole corpus when a Set with only managed UCs was triggered.** Root cause: when the console sent `managed_uc_uuids` but no `uc_handles`/`uc_uuids`, the engine's filter block didn't fire (`if handles_filter or uuids_filter:` was False), so `corpus_files` retained the full `gather_corpus` result. The materialized managed UCs were appended, then everything ran. Fix: the engine now treats *any* non-empty selection (handles, uuids, OR managed_uuids) as "explicit selection mode" and clears `corpus_files` of unrequested corpus content. Materialized managed UCs are added **after** the filter, so they're always included when listed. Per R1: "selection is authoritative — when any filter is non-empty, the engine runs only what's listed."
2. **`▶ Test evaluation` (and several other) onclick handlers silently did nothing.** Root cause: `JSON.stringify(value)` produces `"..."` (with double quotes), and embedding that inside a double-quoted HTML attribute (`onclick="...${JSON.stringify(x)}..."`) terminates the attribute at the first inner `"`. Fix: new `attrJson(v)` helper wraps `JSON.stringify` and replaces each `"` with `&quot;`; the browser decodes the entity when it reads the attribute value for execution, so the JS sees the right quotes. Applied to all 8 onclick sites that embedded JSON.

**UC validity — template UUID prefix + UC Assist schema hint corrected (v0.9.33):** Two bugs the engine validation exposed after R1's selection-authority fix actually started running managed UCs end-to-end.

1. **UUID missing `uc-` prefix.** The engine validates `uuid` starts with `uc-`. `openUCModal` was generating `crypto.randomUUID()` (which produces a plain UUID like `f3b64dda-…`) and **replacing** the template's `uc-<your-uuid-here>` placeholder text — dropping the `uc-` prefix in the process. Fix: replace only the `<your-uuid-here>` substring so the `uc-` prefix stays as static template text.
2. **UC Assist schema hint had wrong enum values.** `_UC_SCHEMA_HINT` in `uc_assist.py` listed placeholders (`single_with_deps`, `multi_eligible`, `human-authored` as a mode, etc.) that don't exist in the DCM consumer profile. UC Assist was confidently generating UCs that fail engine validation. Rewrote the hint with the actual DCM enums for `lifecycle_phase`, `resource_complexity`, `policy_complexity`, `provider_landscape`, `governance_context`, `failure_mode`, `actor.profile`, `generated_by.mode`, and `generated_by.source` — plus the `uc-` uuid prefix rule and a "DO NOT invent values" warning. (Single-consumer hardcoded for now; making this dynamic via consumer profile fetch is a follow-on once we have more than one consumer.)

Follow-on: the API should pre-validate UC YAML on create/update against the same engine schema so authors see errors in the editor instead of at run time.

**R4 implementation — UC Assist prompt capture (v0.9.34):** Implements R4 from the requirements section. Prompts the user sends during a UC Assist session are tracked in a module-level `_ucAssistPrompts` array (reset on panel open). When the user clicks **↑ Apply to editor** on any assistant turn, `_injectAssistPromptsAsComment` prepends a comment block to the YAML:

```yaml
# UC Assist prompts (this UC was iterated from these messages):
#   1. <first prompt>
#   2. <second prompt>
#
title: ""
uuid: uc-...
...
```

The block is recognized by its header, so re-applying a later turn (or applying multiple times in a session) replaces the existing block rather than stacking duplicates. The engine's `safe_load` ignores comments, so this has zero impact on validation. The UC Assist system prompt was updated to instruct the model to preserve any existing `# UC Assist prompts:` comment block when refining, so iterating on an already-applied UC keeps the original provenance.

Future enhancement: also surface a structured form under `metadata.assist_prompts` for queryability, and a "Replay these prompts against a different model" action on the UC detail.

**R2 implementation — result lineage + state (v0.9.35):** Implements R2 from the canonical requirements section.

- **Migration 006** adds `run_sessions.set_id` / `set_name` / `selection_mode` / `uc_state_snapshot` (JSONB map `{uuid: lifecycle_state}` captured at trigger time) and `uc_analyses.lifecycle_state_at_run` / `source_kind`.
- **Trigger** (`POST /api/runs`): `RunTriggerIn` accepts `set_id`, `set_name`, `selection_mode`; before inserting into `run_sessions`, the API snapshots the lifecycle_state of every referenced managed UC (lookups by `managed_uc_uuids` + `uc_uuids` from the DB) and writes it as JSONB.
- **Ingest** (`POST /api/analysis/ingest/{run_id}`): correlates workspace `run_id` → `run_sessions.run_name` via the ±15-min `started_at` window, populates `analysis_runs.run_name`, pulls the `uc_state_snapshot`, and writes each UC's `lifecycle_state_at_run` + `source_kind` into `uc_analyses` rows.
- **Results enrichment** (`GET /api/results/{run_id}`): per-UC `lifecycle_state_at_run` / `source_kind` and run-level `set_id` / `set_name` / `selection_mode` / `session_name` are merged onto the response.
- **UI lineage threading**: every run-trigger path (`runSet`, `testRunUC`, `_batchTestSelectedUCs`, the default-Set fallback in `openNewRun`) now passes `{ set_id?, set_name?, selection_mode }` through a new `_pendingRunLineage` global; `submitNewRun` includes the lineage in the POST payload. `selection_mode` defaults to `'corpus'` when no filter is active, `'selection'` for ad-hoc multi-select, `'set'` for Set runs, `'individual'` for single-UC test.
- **UI surface**: the persistent Results header now has a "Lineage:" row showing the Set (clickable to switch to the UC tab filtered to that Set) + the selection mode. Each UC row in the result list shows a small color-coded `LIFECYCLE_STATE_AT_RUN` badge for managed UCs (draft / ready / in_review / approved / deprecated) — reviewers can tell at a glance which results came from a pre-promotion test vs an approved UC.

What it doesn't (yet) do: enforce R3 (block enhancement PR generation when any source UC is non-approved). That's the next commit.

**R3 implementation — approval gate before enhancement PR (v0.9.36):** Implements R3 from the canonical requirements section. Both client AND server enforce.

- **`PrCreateIn`** gains `override: bool = False` and `override_reason: Optional[str] = None`.
- **`POST /api/pr/create`** queries `uc_analyses` for the run's source UCs and collects those whose `source_kind == 'managed'` AND `lifecycle_state_at_run != 'approved'`. If any exist and `override` is false → **409** with a structured detail: `{detail: 'approval_gate', message, non_approved: [...], hint}`. If `override=true` but `override_reason` is empty → **400**. When override is used, the PR body gets a blockquote callout listing the non-approved UCs, the override user, and the reason — visible to the corpus reviewer.
- **`api(path, opts)`** UI helper now attaches `status` and parsed `body` to thrown `Error` so callers can branch on structured 409s.
- **New `createPrWithApprovalGate(payload)`** UI wrapper: posts the payload; on 409 with `approval_gate`, opens the approval-gate modal listing the offending UCs (color-coded state badges), requires a non-empty reason, then re-posts with `override=true` + reason. Cancel returns `null` and the caller surfaces "Cancelled."
- **Both PR-create button paths** (`rpPrCreateBtn` in the run-detail Review pane, and the matching button in the Review & Plan view) now route through the wrapper — single chokepoint, consistent UX.

Corpus-source UCs are deliberately not gated (no lifecycle on those). Future enhancement: also gate Arch Review generation similarly.

**Pre-flight UC validation (v0.9.37):** Catches engine validation errors at save time instead of at run time — surfaced after the previous version's bug where an existing UC with bad enum values + missing `uc-` prefix kept failing the engine even after the template/Assist were fixed.

- **`_validate_uc_yaml(parsed)`** in `main.py` mirrors the engine's validation against the DCM consumer profile: uuid `uc-` prefix; `generated_by.mode` / `source` enums; `actor.profile` / `scenario.profile` / `dimensions.*` against the DCM enum sets (`_DCM_*` constants); required non-empty fields (`scenario.description`, `intent`, `actor.persona`, `success_criteria` non-empty list).
- **`create_use_case` and `update_use_case`** now call `_validate_uc_yaml` after parsing. On failure they return **400** with a structured detail: `{detail: 'uc_validation_failed', message, errors: [...]}`.
- **`POST /api/use-cases/validate`** — standalone lint endpoint that returns `{ok, errors}` without saving. Used by the UC editor's new **✓ Validate** button (in the modal footer) so authors can check before clicking Save.
- **UI** — `saveUC` parses the structured 400 and renders the error list under the status line in red; `validateUC` shows a green ✓ when the YAML passes or the same red list when it doesn't.
- Hardcoded to DCM constants (single-consumer install). Future enhancement when multi-consumer ships: fetch enums from the active consumer profile.

What this doesn't do (yet): retroactively fix existing bad UCs. The user's path for a single bad UC is delete + recreate; a `/api/use-cases/{uuid}/repair-uuid` endpoint that cascades the rename across set members + lifecycle events + uc_analyses is a noted follow-on if more accrue.

**Auto-navigate to the new run on trigger (v0.9.38):** After `submitNewRun` succeeds, the UI now switches to the Runs tab, refreshes the run list so the new PipelineRun is in `allRuns`, then calls `selectRun(name)` so the run detail pane opens automatically. Users watching for run progress no longer have to manually navigate and click — the live polling starts immediately on the just-triggered run.

**Anti-fishing for MCP tool calls (v0.9.39):** Two layered fixes after observing the model wasting 23 of 26 tool calls on a single run by retrying the same `section_title` across every document, ignoring the "Available sections" list the MCP server returned each time.

1. **MCP server (`dav-docs-mcp/server.py`)** — both `get_document` and `get_document_section` now return forceful directives when the request is malformed:
   - **Section not found** response gets a `⚠ Section '<X>' NOT FOUND in '<doc>'.` header plus a "REQUIRED NEXT ACTION (pick exactly one)" block explicitly forbidding the model from retrying the same `section_title` in a different document. The list of actual sections in the queried doc still follows.
   - **Document too large** response gets a `⚠ DOCUMENT TOO LARGE TO RETURN IN FULL` header plus a "DO NOT call `get_document(...)` again — you will get this same response" directive pointing at `get_document_section`.
2. **Engine (`dav.ai.agent`)** — per-run state tracking:
   - `_section_title_misses` dict counts misses per `section_title`. On the 3rd+ miss with the same title, the tool response is prepended with a `⛔ ANTI-FISHING STOP` directive forcing a `search_docs(query=<DIFFERENT keywords>)`.
   - `_too_large_handles` set tracks docs already returned as too large. A 2nd `get_document(handle)` call on a seen handle gets the same `⛔` prepend forcing a section call.
   - State resets at the start of each `analyze()` so it's per-sample, not global.

The prepend approach is more reliable than relying on the buried system-prompt directive because the model reads tool responses fresh each turn, while system prompts can be skimmed. The original tool response is preserved after the directive for context.

**Tekton task already wires the MCP server URL via `mcp-url` param**, so no infra change is needed beyond the engine + MCP image rebuilds (`ansible-playbook --tags engine,mcp`).

**Auto-follow tail panes — shared behavior (v0.9.40):** Standardized auto-scroll-on-new-content for any tail pane (Prompts & Responses today; future streams reuse). One helper `_setupAutoFollow(scrollEl, btn, get, set)` owns the full contract:

- **Scroll-away pauses** — scrolling more than 24px above the bottom flips the state to OFF; the button repaints to the inactive (outline + grey) style.
- **Scroll-back resumes** — scrolling all the way back to the bottom flips the state ON again; button repaints to the active (filled accent background) style.
- **Click toggles** — same paint logic; resume also snaps to the bottom immediately.
- **Visual convention** — `_renderAutoFollowBtn(btn, on)`: ON = accent fill + panel-bg text + accent border; OFF = transparent + faint text + border. Tooltip rewrites to reflect state.

Replaces the prior single-purpose toggle that only flipped `color` between accent and faint and didn't react to user scrolling. Future tail panes should call `_setupAutoFollow(...)` with their state get/set closures.

**Anti-fishing threshold tightened to 2 (v0.9.41):** Empirical: v0.9.39 reduced tool-call waste by 22% across a 3-sample run, but the engine still allowed 4 attempts of the same `section_title` (counter increments on each miss; 3rd miss got the prepend, 4th was the call after the prepend). User asked for tighter gating — threshold dropped from 3 to 2 misses. Now the 2nd attempt of the same `section_title` returns the STOP directive, capping fishing at roughly 1 useless retry instead of 3.

**In-turn tool-call dedup (v0.9.43):** A run after v0.9.41 hit the model's 32K context limit and 400'd. Root cause: the model was emitting the **same tool call 4-8 times in a single response** (turn 9 fired `get_document_section` with identical args 8 times). The engine executed each one, each adding a 1-3 KB tool response to the context. Anti-fishing per-miss counters can't help because all duplicates fire before any new response updates the count.

Fix: in-turn dedup keyed on `(tool_name, json.dumps(args, sort_keys=True))`. The first occurrence in a response actually executes; every subsequent identical call returns a short `⛔ DUPLICATE-IN-TURN` marker pointing at the first `tool_call_id` ("STOP emitting duplicate tool calls in one response — wait for the result of one call before deciding what to call next"). The OpenAI tool-call protocol requires one response per `tool_call_id`, so the marker satisfies that constraint without re-running the call or eating real context. JSONL emit gets a `dedup_of: <first_id>` field so the UI can later distinguish dedup'd entries.

This alone should cut context growth dramatically. If runs still hit the limit, the follow-on is a soft cap that forces a final response when input tokens cross e.g. 24K.

**Runs list/detail — scope + counts (v0.9.44):** The Runs tab list rows were anonymous beyond name + mode; reviewers couldn't tell what Set/scope was running or how it finished without drilling in. Same for the Run-detail Session section.

- **`GET /api/runs`** SELECT now pulls `set_id` / `set_name` / `selection_mode` / `uc_total` / `uc_succeeded` / `uc_failed` from `run_sessions` and joins them onto each list row.
- **Runs list row** gains a scope sub-line (`⊞ <SetName> · <selection-mode-label>`) and, when the run has finalized, a counts segment (`N/M ok` in green or accent when there are failures, `K fail` in red).
- **Run-detail Session section** gains two new kv rows: `scope` (Set chip + mode, Set chip clickable to filter the UC tab to it) and `UC counts` (same color logic as the list row), inserted above the existing `category` row.

`run-detail` endpoint already does `SELECT *` from `run_sessions`, so the new columns flow through automatically — no separate API change beyond `/api/runs`.

**Parallel-tool-call disable + wrap-up nudge (v0.9.45):** v0.9.43's dedup prevented the context-window crash but a new failure surfaced — the model emitted the same tool_call 1→3→7→15 times across turns 2-10, eventually consuming the 30-turn budget without producing a usable final JSON.

Two fixes:

1. **`parallel_tool_calls: false`** added to the OpenAI-compatible request body (`client.py`) whenever `tools` is set. vLLM honors the flag — the model is constrained to one tool_call per response, which physically prevents the parallel-duplicate explosion at the source. Backends that ignore the flag (older vLLMs, OpenAI's own) are no worse off than today.
2. **Wrap-up nudge** in tool responses (`agent.py`) — on the last 3 turns before the tool-call budget, every tool response gets prepended with `⚠ WRAP-UP: only N tool-call turn(s) left … synthesize your final JSON analysis on your NEXT response …`. The budget-hit turn already strips tools entirely (existing behavior); this gives the model 3 turns of warning to converge.

Together: #1 kills the duplicate explosion that was the root cause; #2 is the safety net so a model that's still indecisive at turn 28 gets a clear "wrap it up now" signal before tools are forcibly removed.

**Live runs list (v0.9.42):** Runs list now polls every 5s while the Runs tab is the active view. Triggers from outside the UI (direct API call, CLI, webhook, etc.) appear in the list within one poll cycle without a manual refresh; in-flight phase transitions (Pending → Running → Succeeded/Failed) repaint live. The poll self-gates on `document.visibilityState === 'visible'` and the tab being active, so a hidden tab or a user on a different view doesn't burn requests. Implementation: `_startRunsListPoll` / `_stopRunsListPoll` are toggled by `switchView`.

**Results tab — persistent run-summary header (v0.9.31):** Same shape as the Runs detail v0.9.30 work: stats stay above the output, output bounded to the panel. Previously `renderRunSummaryHeader` rendered the run-level stats into `analysisDetail`; picking a UC replaced them with the per-UC analysis and the run context disappeared. Now a dedicated `runResultsHeader` strip sits above `analysisDetail`, populated on `selectRunResult` and never overwritten by per-UC rendering. Compact single-row layout: session name + run_id + mode on the left; `N/M UCs (X%) · failed · samples · ⏱ wall · finished_at` on the right. Wraps at narrow widths. `analysisDetail` gets `flex:1; overflow-y:auto; min-height:0` so the per-UC content scrolls *within* the panel — the page never grows beyond the viewport.

---

## UC test execution requirements (canonical, 2026-05-26)

The console must let reviewers kick off evaluations at three granularities, all flowing through the same gap-analysis pipeline.

**R1 — Three selection granularities, one pipeline:**

1. **Set-level** — clicking ▶ Run on a Set executes **exactly** the UCs in that Set. Corpus members run via handle/uuid filter; managed members are materialized from the console API. The rest of the corpus is NOT run.
2. **Selection-within-Set** — when a Set is active in the rail, the multi-select toolbar's ▶ Test selected runs **only** the checked UCs (subset of the Set or any other subset the user has selected).
3. **Individual UC** — the ▶ Test evaluation button on a UC detail runs only that UC.

All three send the same shape to the engine (handles + uuids + managed_uuids). Different selection scope; identical execution path; identical result schema. **Selection is authoritative — when any filter is non-empty, the engine runs only what's listed, never the whole corpus subpath.**

**R2 — Results must carry lineage and state:**

Each run records, beyond what's already captured (`run_id`, `triggered_by`, timestamps, totals):

- `set_id` + `set_name` — the Set (if any) the run was triggered for
- `selection_mode` — `set` | `selection` | `individual` | `corpus` (full)
- Per-UC `lifecycle_state_at_run` — the UC's lifecycle state at trigger time, snapshotted so a reviewer can later distinguish "this was approved when tested" from "this was a pre-promotion test in draft"
- Per-UC `source` (`corpus` | `managed`) — already implicit; surfaced explicitly in result UI

The Results tab surfaces these so a reviewer can see provenance without leaving the view.

**R3 — Approval gate before architecture enhancements:**

The Plan enhancements / Generate enhancement PR flow must:

1. **Inspect** the lifecycle state of every UC referenced in the results being used
2. If any UC is **not in `approved` state** (`draft` / `ready` / `in_review` / `deprecated`), show a warning modal listing the non-approved UCs and require explicit confirmation before proceeding with PR generation
3. **Enforce** both client-side (the warning + confirmation) AND server-side in the enhancement PR endpoint (defense in depth — pure UI gating is bypassable)
4. The confirmation override is recorded in the resulting PR body with the list of non-approved UCs and the user who confirmed

**R4 — UC Assist prompts must be captured on the UC:**

Every prompt text sent to UC Assist during the session that produced an applied YAML is preserved with the UC. Goals:

- **Provenance** — a future reviewer can see exactly what the author asked for
- **Reproducibility** — the same prompts can be re-played against a different model later to compare
- **Trail** — when a reviewer questions a UC, the conversation that birthed it is right there

Capture target: a YAML comment block at the top of the file (e.g. `# UC Assist prompts:` followed by numbered prompt lines). Comments survive `_yaml.safe_load` (engine ignores them) and survive round-trips through the editor. A future structured form under `metadata.assist_prompts` may be added, but the comment block is the immediate, no-schema-change capture.

When UC Assist refines an existing UC, the system prompt must instruct the model to preserve any existing comment-block prompt history at the top of the YAML.

Implementation status of these requirements lives in the per-version notes below.

---

## Planned design: UC review pipeline (2026-05-26)

Today the UC lifecycle states (`draft → ready → in_review → approved → deprecated`) exist in the schema but are just labels — they don't gate anything. The plan is to make them load-bearing so authoring, testing, reviewing, and shipping a UC become one coherent pipeline instead of four disconnected features.

### State semantics (planned)

| State | Who acts | What's allowed | What's gated |
|---|---|---|---|
| `draft` | author | free editing, repeated UC Assist iterations, no test runs required | cannot be added to a default Set; cannot be pushed to corpus |
| `ready` | author signals "please review" | edits locked or warned | enables the **Run test evaluation** button on the UC |
| `in_review` | reviewer | triggers test runs (single UC or multi-select queue); attached run results render inline on the UC | edits require state-back-to-draft |
| `approved` | reviewer | UC is shippable | requires ≥1 passing run attached + a reviewer note; **enables Push to corpus** |
| `deprecated` | anyone | UC is excluded from default Sets but kept for history | — |

The gate at `approved` is intentional: nothing leaves the console for the corpus repo without a passing run on record. Mitigation for trivial UCs — keep the gate soft (warn + override with a reason) rather than hard.

### Sets as the partitioning mechanism

Sets are the natural unit of "what gets run together," and they replace any need for private UC repos.

- **Default Set** — a new `is_default` boolean on `use_case_sets` (max one default per consumer). The New Run modal pre-populates from the default Set; runs scheduled outside the UI pull from the default Set when no explicit Set is named.
- **Edge-case Sets** — named Sets (`edge-case-fringe`, `regression-2026-rev2`, etc.) that only run when explicitly selected. Edge-case UCs can stay managed-only (never pushed to corpus) or be pushed into a corpus subpath — orthogonal axis.
- **Approval bar tightened** — once `is_default` exists, "approved" should require a passing run **on the default Set's profile**, not just any run anywhere. That's the meaningful bar.

### Test-from-UC flow (consolidates the open requests)

This pipeline absorbs the per-feature asks from 2026-05-25 and 2026-05-26 into one design:

- **Run test evaluation from the UC** (single or multi-selected) — only enabled when state is `ready` or `in_review`. Triggers the same backend pipeline as a Sets-based run but with an ad-hoc UC list. Result attaches to each UC's history.
- **See results inline on the UC** — reviewer reads verdict / gaps / findings without leaving the UC tab. The Run tab still exists for full-run views; the UC tab gets a focused per-UC slice.
- **Reorder top-level tabs: Use Cases + Sets first** — matches the actual workflow (UC → Set → Run → Result), and makes the review pipeline discoverable as the primary path.
- **Human-readable Name field on UCs** — a prerequisite: reviewers and authors need to identify UCs by name, not UUID. Maps to `scenario.description` or a new top-level `title:` field; UC Assist responses populate it explicitly.
- **Push to corpus as commit / PR** — appears only when state is `approved`. Creates a branch + commit + PR; records `synced_at` + `synced_commit_sha` + `corpus_pr_url` on the managed row.

### Why this shape (vs. private UC repos)

Private repos were considered and rejected. The "edge cases shouldn't run as part of the standard suite" need is solved by Sets, not by visibility. The "drafts shouldn't be acted on" need is solved by the lifecycle state machine, not by visibility. Adding privacy on top would compound RBAC + UI complexity without solving a real problem in the current single-operator / homelab usage; revisit if the console becomes multi-tenant.

### Migration

Existing managed UCs are all currently in `draft` (or whatever lifecycle was last set). They keep their state. The pipeline only activates new gating going forward — no retroactive enforcement. Existing Sets keep working; the new `is_default` flag defaults to `false` everywhere until someone picks one.

---

## Known limitations / future work

- **CLI-triggered runs** are not visible in the Runs tab (no `run_sessions` row). Turns files are written but unreachable from the UI.
- **Hard run/result linking** — PipelineRun name ↔ workspace run_id correlated by timestamp (±10 min). A `--run-label` param in the Tekton task would make this exact.
- **Managed UC git push-back** — managed UCs live only in Postgres; no mechanism to PR them back to the corpus repo.
- **Multi-consumer** — one console instance = one consumer. Switching requires redeploy.
- **Engine image rebuild** — the `dav-engine:latest` image must be rebuilt and redeployed after engine code changes. Console-triggered runs always use `dav-engine:latest` from the OCP image registry.
- **DCM / data model rework (planned)** — next major design effort. Scope to be defined in the next session (2026-05-26). Will revisit the underlying DAV domain capability model and the schemas / contracts it implies between engine, console, and consumer corpora.
- **UC review pipeline (planned, 2026-05-25 / 2026-05-26)** — full design captured above in **Planned design: UC review pipeline**. Bundles five previously per-feature requests into one coherent lifecycle: (1) human-readable Name field, (2) Run test evaluation from UC editor/detail with inline results, (3) multi-select UC list → batch test run, (4) Default Set + `is_default` flag for partitioning, (5) Push to corpus as commit/PR gated on `approved` state with a passing run attached. Replaces the need for private UC repos — Sets do the partitioning, lifecycle state does the gating.
- ~~**Reorder top-level tabs: Use Cases + Sets first**~~ — done in v0.9.16; default landing tab is now Use Cases. New order: Use Cases / Sets / Runs / Results / Review & Plan / Config.

---

## v0.19.0 — Capability catalog, assessments, prompt management, A/B (2026-06-09)

Five features shipped + deployed 2026-06-09. Validated post-deploy by the in-pod harness
`api/validation/qa_validate.py` (**25/25 PASS**). Detailed designs:
`docs/capability-catalog-design.md`, `docs/prompt-management-design.md`,
`docs/blueprint-projects-design.md`, `udlm/`.

**1. Capability catalog — one UDLM table.** The keystone draft created a parallel
`capability_inventory`; it duplicated the app's existing `capability_catalog`. Collapsed
into ONE table (migration 020 + schema.sql): the Capability entity **is**
`capability_catalog`, extended additively into the UDLM Knowledge family — `cap_key` =
handle, `status` = lifecycle (`confirmed`/`suggested`/`rejected` curated **+ `observed`**),
`depends_on`/`spec_refs` reused, plus `family`, `normalized_to_term_id` (→
`capability_taxonomy_terms`), `normalization_status`, `created_via`, `evidence`,
`provenance`, `classification`, `domain_prefix`. `project_id` relaxed to **nullable** (NULL
= global observed; curated stays project-scoped, existing Catalog CRUD untouched). Shared
write path `capability_catalog.upsert_observed_capability()`. Endpoints
`GET /api/capabilities/{stats,taxonomy,catalog,normalize}`, `POST .../{resolve-uc-capabilities,reseed}`.

**2. Assessment ingestion (F7).** UDLM Knowledge family Assessment + Finding (migration
019). `assessment_ingest.py`: parser registry (generic + automation adapter), ingest →
findings land on `capability_catalog` as OBSERVED (normalized onto the taxonomy or flagged
as a gap) + gap summary; **synthetic fixture, no confidential data** (real parsers/data
live inside the work env). Endpoints `POST /api/assessments/ingest` (`{use_fixture:true}`),
`GET /api/assessments[/{id}]`. **Assessments** nav tab (platform-admin).

  *Finding model (Chris 2026-06-10):* two independent dimensions — **state** = disposition
  (`present` | `partial` | `absent` = asked/no capability | `n/a` = not asked / not
  applicable), and **maturity** = pure rating **1..5** (NULL = none): 1 Minimal, 2 Basic,
  **3 Capable = engagement target** (satisfies the technical requirements), 4 Above, 5 Best
  (4/5 are more process than technical, lower ROI). Findings carry a **`category`**; the
  detail view groups capabilities by category, each anchoring a vertical list of colored
  capability pills. Maturity color: 1 red → 2 dark-gold → 3 green (target, ringed) → 5 deep
  green, white text + dark outline for contrast on any background; N/A neutral/dashed. Gap
  summary adds a maturity-vs-target rollup. `assessment_findings.category` + the `n/a` state
  are added by idempotent ALTERs in migration 019.

**3. Prompt management (F8).** Per-project, per-stage prompt customization (additional
context + section overrides). `project_stage_context.section_overrides JSONB` added;
`prompts_registry.py` = stage/section registry + `assemble()`. **New `prompt.manage`
privilege** (seeded to project-admin/edit; **supersedes** `project.archreview.context` —
`rbac.privileges_for` aliases the old grant → new). The **Improve** nav became **Prompts &
Improvement** (tabs: Prompt management + diagnose/propose/experiments); editor = stage
picker → additional-context box + per-section override + live assembled preview. Stages:
`stage2-analysis` (engine, **stored-held** — overrides stored/previewable but NOT applied
at runtime pending A/B), `arch_review`, `enhancement`. Endpoints `GET /api/prompts/stages`,
`GET /api/prompts/project/{stage}`, `PUT /api/stage-context/{stage}` (now
`prompt.manage`-gated + **active-project** scoped — fixed a prior default-project bug).

**4. Review/Enhancement split.** Enhancement reads its own `enhancement` stage context
(was the shared `arch_review`); schema.sql one-time idempotent copy of existing
`arch_review` content into `enhancement`. Independently customizable.

**5. Static A/B backported into the experiments framework.** Reuses the engine's semantic
Analysis comparator (`engine/src/dav/evaluator/compare.py`) — **no fork**: it is **vendored
into the API image at build time** (`ansible/.../review_console.yaml` → `app/_vendor/`,
gitignored). `analysis_compare.py` runs it **server-side** (analyses stay on the
run-workspace PVC; only the diff crosses to the browser). `POST
/api/experiments/static-compare` compares two existing runs (equivalent/changed + per-UC
severity, recorded in the `experiments` table); `_maybe_score_experiment` also attaches a
`semantic_diff` dimension to launched (dynamic) experiments. UI: "+ Static A/B" form +
`_renderSemanticDiff` in experiment detail. Verified on real runs.

**RBAC note (extends v0.14.0 privileges):** add `prompt.manage` (project-scoped) — edit
per-project prompt customizations for all stages; supersedes `project.archreview.context`.

**HELD:** wiring the **stage-2 engine** prompt to per-project overrides (an append-only
`DAV_STAGE2_EXTRA_CONTEXT` engine seam) — byte-identical by default; any real stage-2
change is A/B-validated (the static comparator is the measurement tool) before runtime
trust. See `docs/prompt-management-design.md`.

---

*Update this document when any feature is added, changed, or removed.*
