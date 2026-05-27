# DAV Review Console — Design & Feature Inventory

**Status:** Living document  
**Last updated:** 2026-05-25  
**Current version:** v0.9.45  
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
- **Efficient use of screen real estate** — operational views (Runs detail, Results, Review & Plan) must use the available width and height. Stats that fit side-by-side should sit side-by-side at wide widths and stack only when the viewport forces it. Tail panes (live log-like streams) must bound their height so they never blow past the viewport — scroll *within* the pane, not the whole page. The Runs detail panel is the canonical example: GPU + Inference live tiles render as a 2-column grid at ≥1100px; Prompts/Tasks panes cap at `max-height: 48vh` and scroll internally.

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
- **Repos** *(M3, shipped)* — managed_repos registry view. Lists every repo DAV operates on with namespace, URL/branch, roles (spec / corpus / issue-source), and tenant. Add/edit/delete via `POST/PUT/DELETE /api/repos`. The registry is the source-of-truth per [ADR-003](../adr/003-multi-repo-registry-and-mcp-source-of-truth.md); the Sources panel below is now a read-only filtered view over it (M4). Corpus panel pending similar refactor (corpus is still single-source-only).
- **Sources — Architecture spec** *(M4, shipped — read-only)* — projected view of `managed_repos` rows with `role=spec`. Lists each source's namespace, URL, branch, root_path. Shows last-projected timestamp and rollout status. No editor inputs; an "↑ Manage in Repos" button scrolls to the Repos panel. If the ConfigMap is still in legacy single-source shape (pre-projection), the UI flags it and points the operator at the ↻ Project button.
- **Sources — Evaluation corpus** — single-source today (editable inline). Updates `dav-source-corpus` ConfigMap + rolls `dav-review-api`. Becomes a read-only projection when corpus gains multi-source support and the projector wires a `project_corpus_sources` sibling.
- **Sources — Evaluation endpoint** — selects inference model from `model_configs` dropdown (replaces free-text endpoint/model inputs); applied via `POST /api/sources/inference` after optional Test validation.
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

*Update this document when any feature is added, changed, or removed.*
