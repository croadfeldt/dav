# DAV Review Console — Design & Feature Inventory

**Status:** Living document  
**Last updated:** 2026-05-25  
**Current version:** v0.9.15  
**Source:** `review-console/` (API: `api/`, UI: `ui/index.html`)

This document is the authoritative record of what the review console is, what each feature does, and how it hangs together technically. It exists so that:

- Changes can be checked against intent before implementation
- Features aren't accidentally regressed during refactors
- A new session can pick up without archaeology

Update this doc whenever a feature is added, changed, or removed.

## Design principles

- **Consistency** — UI/UX patterns and process flows must be consistent across all features. If a pattern is used in one place (model selector + Browse button, two-click delete, streaming output with status indicator), it must be applied the same way everywhere. Users should never need to learn a different interaction model for the same type of action.
- **Scope clarity** — configuration that affects all users belongs in Config and is stored server-side; personal preferences belong in localStorage and are never shown as shared settings.
- **Least surprise** — defaults are always visible and editable; overrides are always scoped to the current session and do not mutate the shared default.

---

## Goals

1. **Operational hub** — trigger DAV analysis runs, monitor them live, browse results.
2. **UC management** — full lifecycle for managed use cases (draft → approved → deprecated).
3. **Quality review** — architectural review and enhancement planning powered by configurable LLM.
4. **Integration surface** — MCP server registry, code repo configs, UC assist config.

Non-goals:
- Git push-back for managed UCs (deferred, see roadmap)
- Multi-consumer / multi-project switching (deferred, see roadmap)

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

**Run drawer:** Opens when a run row is clicked. Four layout modes (button in drawer header):
- `detailed` — full GPU/vLLM stat tiles + task list + prompts panel (default)
- `stacked-tails` — compact stats + task list + prompts panel stacked
- `side-by-side` — task list and prompts panel side by side
- `prompts-dominant` — thin stats header, prompts panel maximized

**Run drawer sections:**
- **Compact stats bar** — wall time, phase, GPU energy (kWh), token counts (gen/prompt), session delta tokens
- **GPU / vLLM tiles** — live Prometheus metrics: GFX activity, VRAM, power, gen tokens/s, prompt tokens/s, queue depth; polled every 3s while run is active
- **Tasks section** — Tekton TaskRun list with phase + log tail (last 200 lines via `GET /api/runs/{name}/logs?task=…`)
- **Prompts & responses section** — live tail of per-turn JSONL files written by the engine (see §Engine contract below). Polled every 5s. Expand/collapse per record; "expand all" / "collapse all" toggle persists in localStorage (`davPromptsDefaultMode`)
- **Review & Plan tab** — arch review (streaming) + enhancement planning (streaming); see §Review & Plan tab below

**Runs tab note:** Only runs triggered through the console appear here (they have `run_sessions` rows). CLI-triggered runs (`tkn pipeline start`) do not appear; their turns files are written but unreachable from the UI.

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
- **Sources** — spec repo URL/branch, corpus repo URL/branch. Updates `dav-source-spec` ConfigMap + rolls `dav-docs-mcp` deployment. Evaluation endpoint: selects inference model from `model_configs` dropdown (replaces free-text endpoint/model inputs); applied via `POST /api/sources/inference` after optional Test validation.
- **Model Endpoints** — all LLM endpoints in one `model_configs` table with per-endpoint use-flags: `use_arch_review` (default true) and `use_uc_assist` (default false, informational only — any enabled model can now be used for UC Assist). `api_key` masked on GET. All selectors across the console draw from the same enabled-model list via `_populateModelSel(selId, storageKey)`; selections persisted in localStorage per selector. Each selector has a **Browse…** button inline with the selector that opens the model browser overlay (see §Model browser below).
- **Default evaluation model** — project-scoped default for new analysis runs. Stored in `model_defaults` table (`key='evaluation'`). Set in Config → AI Models; applies to all users. New Run modal reads from `GET /api/model-defaults` on open and pre-selects this model when no user override is stored in localStorage. Only registered model_config rows can be set as project defaults (custom endpoint+model pairs are user-scoped only).
- **Default UC Assist model** — user-scoped personal default for UC Assist authoring. Stored in localStorage under `ucAssistModelId`. Configurable both in Config → AI Models and inline in the UC Assist panel (both selectors share the same storage key and mirror each other). Falls back to env-var config (`DAV_UC_ASSIST_*`) if no DB rows exist.
- **MCP Integrations** — registered MCP servers (`mcp_server_configs` table) with `use_uc_assist` flag. Health polled on demand. Servers flagged `use_uc_assist` displayed with amber badge.
- **Code Repositories** — git repos for branch/PR creation from enhancement findings (`code_repo_configs` table). Supports GitHub + GitLab.

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

Current: `"1.5"` — gap title field added, `spec_refs_missing` as list.

---

## DB migrations

Migrations run automatically at API startup before `schema.sql`. Each migration file is idempotent (safe to re-run).

| File | What it does |
|---|---|
| `migrate_002_model_configs.sql` | Renames `review_model_configs` → `model_configs`; adds `use_arch_review`/`use_uc_assist` flags; adds `use_uc_assist` to `mcp_server_configs`; migrates any `uc_assist_config` row into `model_configs`; drops `uc_assist_config` |
| `migrate_003_model_defaults.sql` | Creates `model_defaults` table for project-scoped model defaults |

---

## DB schema summary

| Table | Purpose |
|---|---|
| `managed_use_cases` | UC CRUD with lifecycle state |
| `lifecycle_events` | Audit trail for UC state transitions |
| `use_case_sets` | Named UC collections |
| `use_case_set_members` | UC set membership |
| `run_sessions` | Per-run metadata + resource stats (console-triggered runs only) |
| `analysis_runs` | Ingested run index |
| `uc_analyses` | Per-UC analysis results |
| `uc_gaps` | Per-gap records |
| `model_configs` | Centralized LLM endpoint registry; use-flags `use_arch_review`, `use_uc_assist` per row |
| `model_defaults` | Project-scoped model defaults keyed by pipeline type (`evaluation`); references `model_configs` |
| `mcp_server_configs` | MCP server registry; `use_uc_assist` flag per server |
| `code_repo_configs` | Git repos for PR creation |

---

## UI conventions

**Delete confirmation — `_armDeleteBtn(btn, action)`:** Native `confirm()` is suppressed by the OCP OAuth proxy, so all destructive actions use a two-click pattern instead. First click changes the button text to "Sure?" and adds a red outline; second click on the same button fires `action()`; clicking anywhere else resets the button. Used for: model endpoint delete, UC delete, UC set delete, MCP server delete, code repo delete. Any new destructive button must use this utility rather than `confirm()`.

**Model browser — `_openModelBrowser(selId, storageKey)`:** A shared fixed-position overlay that lets the user pick an endpoint from the registered model_configs URLs (or type a custom URL), probe it for available models via `GET /api/sources/inference/models?endpoint=...`, and select or manually enter a model ID. Clicking "Use this model" either selects a matching registered model_config (by id) or stores a custom `endpoint_url + model_id` pair in localStorage under `${storageKey}_ep` / `${storageKey}_mi` and sets the selector to a dynamically-added `__custom__` option.

Overlay background uses `var(--bg-panel)` — `var(--surface)` is not a defined theme variable and must not be used. The probe API returns `{reachable, models: [...], error, latency_ms}` (not a raw array); the probe handler reads `result.models`. Probing runs automatically on open and on every endpoint selector change via `_mbProbe()`; the "Probe for models" button re-triggers it manually.

**Custom model resolution — `_resolveEndpointModel(selId, storageKey)`:** Returns `{model_config_id}` for registered selections or `{endpoint_url, model_id}` for custom ones. Spread this into every API body that calls `/api/arch-review`, `/api/enhancements`, or `/api/uc-assist` instead of reading the selector value directly. All three API endpoints now accept either form (model_config_id OR endpoint_url+model_id).

**Storage-based resolution — `_resolveFromStorage(storageKey)`:** Reads model selection directly from localStorage, bypassing the DOM. Used where multiple selectors share the same storage key (e.g. UC Assist Config selector and panel selector) so the result is always consistent regardless of which selector the user last touched.

**`_populateModelSel(selId, storageKey)`:** When the stored value is `__custom__`, preserves the custom option if the endpoint+model no longer matches any registered row; upgrades to a registered id automatically if a new matching row is added.

**Model selector scope rules:**
- **Project-scoped** (DB, `model_defaults` table): evaluation model only. Set in Config → AI Models. Applies to all users. New Run modal reads this as its default; user can override per-session via localStorage (`nrLastModel`). Custom endpoint+model pairs cannot be project defaults.
- **User-scoped** (localStorage): arch review (`reviewLastModel`), enhancement (`enhanceLastModel`), UC Assist (`ucAssistModelId`), standalone review panel (`reviewLastModel`). Per-browser, not shared across users.

**UC Assist panel:** The UC Assist slide-in panel (in the UC editor modal) contains its own model selector (`ucAssistPanelModelSel`) using the same `ucAssistModelId` localStorage key as the Config selector. Both mirrors write to the same key; changing either one is reflected in the other on next populate. The compose textarea is 6 rows tall, user-resizable vertically, with ⌘↵ as the keyboard shortcut to send. `_ucAssistCheckAvail()` and `_ucAssistSend()` resolve the model via `_resolveFromStorage` to remain DOM-agnostic. When the panel is opened with no model selected, focus moves to the model selector automatically. The error message when no model is configured directs users to the inline selector, not Config.

**UC editor UUID auto-generation:** When the New UC modal is opened with no YAML content (fresh template), `crypto.randomUUID()` replaces the `uc-<your-uuid-here>` placeholder with a real UUID, preventing 409 collisions on repeated new-UC creation.

**Model selector layout:** All model selectors use an inline flex row: `[selector][Browse…]`. Selectors in narrow sidebar panels (e.g. Run Profile) also use this pattern for consistency. The option label is `${name} (${local|frontier})` unless `name` already contains the suffix (case-insensitive), in which case the suffix is omitted to avoid "Qwen3-32B (local) (local)" duplication.

**Empty api_key handling:** Local model endpoints (e.g. vLLM, llama.cpp) often have no API key. The Authorization header is only sent when `api_key` is non-empty; otherwise the request goes unauthenticated. Anthropic endpoints raise a clear error when the key is missing, since they always require one. (`uc_assist.py` matched `arch_review.py` for this behavior in v0.9.14.)

**UC Assist timeout:** Default httpx timeout in `uc_assist.chat()` is **300s** (was 60s, bumped in v0.9.15). Local 32B models doing multi-paragraph YAML drafting routinely exceed 60s — especially when current_yaml is large and the model has to revise rather than draft from scratch. The 5-minute ceiling matches what's reasonable for a single-turn assist response on local hardware; if a request takes longer than 5 minutes, something else is wrong (model unhealthy, GPU contention).

**UC modal layout:** The UC editor modal-body uses `display:flex; flex-direction:row` to put the YAML editor pane and the AI Assist panel side-by-side. The base `.modal-body` class is `flex-direction:column`, so the row direction must be set explicitly on the inline style — forgetting this stacks the panels vertically.

---

## Known limitations / future work

- **CLI-triggered runs** are not visible in the Runs tab (no `run_sessions` row). Turns files are written but unreachable from the UI.
- **Hard run/result linking** — PipelineRun name ↔ workspace run_id correlated by timestamp (±10 min). A `--run-label` param in the Tekton task would make this exact.
- **Managed UC git push-back** — managed UCs live only in Postgres; no mechanism to PR them back to the corpus repo.
- **Multi-consumer** — one console instance = one consumer. Switching requires redeploy.
- **Engine image rebuild** — the `dav-engine:latest` image must be rebuilt and redeployed after engine code changes. Console-triggered runs always use `dav-engine:latest` from the OCP image registry.
- **DCM / data model rework (planned)** — next major design effort. Scope to be defined in the next session (2026-05-26). Will revisit the underlying DAV domain capability model and the schemas / contracts it implies between engine, console, and consumer corpora.
- **Kick off evaluation of a new UC from the editor (planned, requested 2026-05-25)** — currently a new UC can be drafted but evaluating it requires switching to the New Run modal and selecting the UC by handle. There should be a "Run evaluation" action directly on the UC editor (or in the UC detail pane) that triggers a single-UC run against the current consumer using the project default evaluation model.
- **Push managed UCs back to the corpus as commit / PR (planned, requested 2026-05-25)** — managed UCs live only in Postgres today (noted in this section already). The new ask is an explicit "Push to corpus" action that creates a branch + commit + PR in the consumer's corpus repo (the URL/branch already configured per consumer in Config → Sources). Needs: a writeable token for the corpus repo, a path layout convention (dav/use-cases/ or use-cases/, auto-detected today on read), commit/PR metadata derived from the UC's `handle`, and probably a per-UC `synced_at` marker on the managed row.
- **Reorder top-level tabs: Use Cases + Sets first (planned, requested 2026-05-25)** — Use Cases and Sets are the *starting point* of the DAV workflow (you draft a UC → group into a Set → trigger a Run against the Set → review Results). Today the tab order is Runs / Results / Use Cases / Sets / Review & Plan / Config. Reorder to put Use Cases and Sets first so the IA reflects the actual workflow.
- **Human-readable name for UCs (planned, requested 2026-05-26)** — today the UC editor shows a YAML template where `scenario.description` is the only "title-ish" field, and the UC detail pane shows the UUID as the visible sub-heading. Users can't easily name a UC at create time. Surface an explicit **Name** input at the top of the UC editor (mapped to `scenario.description` or a new top-level `title:` field in the YAML), display the name prominently in the UC list and detail header, and demote the UUID to a smaller "ID" line. Applies to both hand-authored UCs and UC Assist–generated ones (the assist response should populate the Name field, not just the YAML body).

---

*Update this document when any feature is added, changed, or removed.*
