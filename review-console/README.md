# DAV Review Console

Full operations frontend for DAV. Trigger pipeline runs, browse analysis results, manage use cases through their full lifecycle, organize them into named sets, and switch consumer configuration — all from a single web UI deployed in OpenShift.

The masthead carries two always-visible switchers (v0.10.0): a **project switcher** (sets the active project; sent on every request as the `X-DAV-Project` header) and a **system-wide run selector** that drives all analysis stages and mirrors the Runs list one-for-one. Multi-user access (LDAP approval + roles) and multi-project data tenancy are both opt-in — the console runs single-user with no role gating until LDAP is configured and enforced.

## Components

- `api/` — FastAPI backend (v0.10.0), asyncpg + Postgres for state, OAuth-proxy gated for SSO. Optional LDAP-backed multi-user approval + roles and multi-project data tenancy.
- `ui/` — Static single-page HTML/JS frontend (`ui/index.html`). Served via NGINX with the OAuth proxy injecting `X-Forwarded-User`.
- `api/app/schema.sql` — Postgres schema (corpus content cache + managed use cases + lifecycle events + UC sets + cached Review/Enhancement output + users + projects). Applied idempotently on boot, so it doubles as the migration script for v0.10.0 additions.
- `api/app/ldap_auth.py` — LDAP group → `users` table + in-memory approved-set sync; the access gate that backs multi-user mode (opt-in via `DAV_LDAP_ENFORCE`).
- `deploy/dav-ldap-secret.example.yaml` — example Kubernetes Secret for the optional LDAP config (mounted `envFrom` optional on the API deployment).

## Tabs

### Runs

Lists all PipelineRuns for the configured Tekton pipeline, sorted newest first. Shows run name, phase (Running / Succeeded / Failed / TimedOut / Cancelled), mode, triggered-by identity, start time, and duration.

**New Run** button opens a modal with full parameter control. The modal pre-populates from Config-tab values via `/api/sources` + corpus UC subpath auto-detection (`dav/use-cases` → `use-cases` → none), so what you see in the modal is what gets submitted. Editable per-run:
- Mode (verification / reproduce / explore)
- Sample count override
- Corpus subpath (auto-detected; editable)
- Corpus and spec repo URL / branch (pre-filled from Config)
- Inference endpoint and model (pre-filled from Config)
- Halt-on-error flag

Run creation is guarded by `DAV_TRIGGER_ENABLED` (Ansible default `true`); set to `false` for a read-only deployment.

**Cluster-wide energy chip** (Runs view top bar, right-aligned): `⚡ X.XX kWh total · last 24h Y · 7d Z · N runs`. Pulled from `/api/runs/stats` which sums `gpu_energy_joules` from finalized `run_sessions` rows and converts to kWh (÷ 3.6e6). Hidden until at least one run has reached terminal phase and been finalized.

**Click any PipelineRun row** to open the live run-detail drawer (slides in from the right, 960px wide). The drawer polls Tekton + metrics every 3 s, prompts/responses every 5 s, and stops on terminal phase (Succeeded / Failed / Cancelled / TimedOut).

The drawer has **four layout modes**, switched via the picker chip in the drawer header. The last choice sticks per browser; "set current as default" pins one as the auto-applied layout on next open.

| Mode | Best for | What changes |
|---|---|---|
| **Detailed** | Single-pane analysis, low monitor density | Full GPU + vLLM tile blocks, no scroll constraints. The original layout. |
| **Stacked + tails** | Watching an active run while keeping ambient stats nearby | Compact one-row stats (GPU + vLLM), tasks + prompts panels become fixed-height auto-scrolling tails (~240 px each) |
| **Side-by-side dense** | Wide screens; want tasks and prompts visible at once | Tasks and prompts render as a 2-column grid; compact stats row above |
| **Prompts dominant** | Debugging agent behavior — what's it asking, what's it being told | Thin stats; prompts panel takes ~560 px; tasks shrinks to ~140 px |

What's in each drawer (visibility varies by layout):
- **Session**: name, description, category, tags. On terminal phase: wall time, GPU energy (J/Wh/kWh auto-scaled), avg/peak power, total gen+prompt tokens.
- **UC progress (live)**: counter `X / N · A ok · B failed`, tri-color progress bar (green succeeded · red failed · pulsing blue active), currently-processing UC handle with elapsed time. Sourced from `<run_dir>/run-progress.yaml` (engine writes after each UC).
- **Pipeline tasks**: the 4-step Tekton ladder (cleanup-workspace → sync-spec ∥ sync-corpus → run-corpus) with per-task phase, duration, condition message. Failed tasks render inline failure block + "view logs" expander (tails the last 200 lines).
- **Prompts & responses (live)**: per-turn JSONL stream from `<run_dir>/turns/<uc_uuid>.seed-<N>.jsonl` — `start` (initial system + user prompts), `response` (model output + token usage), `tool` (mcp call name + args + result preview). Auto-scrolls to newest; ⤓ toggles auto-scroll; clear drops the visible buffer without resetting the file cursors. Bounded to ~400 records in the DOM.
- **GPUs (live)** (detailed mode): per-GPU tiles for AMD GPUs — GFX %, VRAM %, power (W), edge temp (°C), color-coded thresholds + bar visualizations. Plus the calibration note about AMD GFX activity latching at 100% on RDNA4 under light load.
- **Inference (vLLM, live)** (detailed mode): running/waiting requests, KV cache %, gen + prompt tokens/s, TTFT p95, and **session token totals** (delta from a baseline captured at trigger time in `run_sessions.baseline_*_tokens` — survives browser reload).
- **Params**: resolved Tekton parameters for this run.

Each live section shows a `· no change Xs` freshness indicator next to its title (amber > 30 s, red > 90 s) so it's obvious when the snapshot is stale vs. the workload is just idle. Values that change between polls briefly flash with the accent colour.

### Results

Browses analysis output directories from the shared workspace PVC (`dav-workspace`, mounted read-only at `/workspace`). The workspace is written by the Tekton pipeline tasks and read by the API at `/workspace/results/`.

Split three-panel layout:
1. **Run list** — all run directories sorted newest first, with run ID, mode, UC count, and success/failure counts from `run-summary.yaml`
2. **UC list** — use cases in the selected run, filterable by verdict (supported / partially\_supported / not\_supported / error)
3. **Analysis detail** — verdict, overall assessment, findings, gaps, recommendations, analysis metadata; handles all three modes (verification shows merged output, explore shows per-sample + variance, failures show error text)

Run directories in the workspace use the format `YYYY-MM-DDTHH-MM-SSZ-<7char-hash>`. These do not map directly to PipelineRun names; correlate by timestamp.

### Use Cases

Manages the full UC lifecycle. Two sources:

- **Managed** — UCs stored in Postgres (`managed_use_cases` table), authored or imported via the UI. Full CRUD: create from YAML editor, edit, delete. UUID is extracted from the YAML content's `uuid:` field.
- **Corpus** — UCs from the consumer's corpus repo, cloned at pod start. Read-only; can be cloned into Managed for customisation.

Each UC shows title, UUID, lifecycle state badge (managed), and tags. Opening a UC shows the full YAML, the lifecycle state machine, and set memberships.

#### Lifecycle state machine

Managed UCs move through a state machine: `draft → ready → in_review → approved → deprecated` (and `deprecated → draft` to reactivate). Transition buttons are shown context-sensitively in the UC detail panel based on current state. Every transition is recorded in `lifecycle_events` (actor, timestamp, optional notes) and shown as a history timeline in the detail panel.

Valid transitions:
```
draft       → ready
ready       → in_review | draft
in_review   → approved | ready
approved    → in_review | deprecated
deprecated  → draft
```

#### Import / export

**Export** (`↓ Export` dropdown, UC list panel): downloads all managed UCs (or a filtered subset) as `.tar.gz`, `.zip`, or `.tar`. Archive structure: `{lifecycle_state}/{set_name_or__ungrouped}/{uuid}.yaml`. The directory structure encodes both stage and set membership, enabling round-trip fidelity.

**Import** (`↑ Import` button, UC list panel): accepts `.tar.gz`, `.tgz`, `.tar`, or `.zip`. Parses the directory structure to determine target lifecycle state and set membership:
- New UCs are created at the encoded stage with an initial lifecycle event.
- Existing UCs have their YAML updated; if the encoded stage differs from current state, the import applies a transition (validated against the state machine). Invalid transitions are logged as errors but don't block the rest of the import.
- Set directories (anything except `_ungrouped`) are created on demand and the UC is added as a member.

Returns a summary: created / updated / transitioned / skipped / errors.

**Stage promotion via export/import:** export a set → (optionally rename top-level directories to change the target stage) → import on the target cluster. Each UC is transitioned to the encoded stage on import. For same-cluster promotion, use the Sets tab's "↑ Promote" button instead.

### Sets

Named collections of use cases. A UC can belong to multiple sets. Sets are the unit of scoped pipeline runs and bulk lifecycle operations.

Set list (left panel) shows name, description, and member count. Selecting a set opens the detail panel with:
- **Members list** — each member with source badge (managed/corpus) and a remove button
- **+ Add UC** — live-search dropdown across all UCs
- **▶ Run** — scopes a New Run to this set's corpus UC paths
- **↑ Promote** — bulk-transition all managed members in this set from one stage to another (state machine validated, atomic transaction, lifecycle audit events recorded)
- **↓ Export** — download this set's managed UCs as an archive (same format as UC list export)
- **Edit / Delete** — set metadata CRUD

### Config

Operational configuration across several categories (left-nav + right-content panes).

- **Pipeline Sources** — spec / corpus / inference sourcing. Source switching changes the spec and/or corpus repo URL + branch; updating triggers a ConfigMap write + Deployment rollout so the new content takes effect on the next pod start. The **Inference** source ConfigMap also holds the new-run **engine** default model (read by the Tekton pipeline) — this is deliberately *not* a `model_defaults` key.
- **AI Models** — one consistent "default model" selector per `model_defaults` key: **Arch Review**, **Enhancement**, **Evaluation**, and **UC Authoring** (all project-scoped, server-backed). `enhancement` falls back to `arch-review` when unset; `uc-authoring` is shared by the UC Assist panel, the UC Wizard, Bulk import, and inbox draft-uc. Each model-driven view also has a per-view **override** selector ("Use default — &lt;name&gt;"); blank sends no model so the endpoint resolves the Config default. See `docs/review-console-design.md` §Two-tier model selection.
- **Users & Access** (admin-only, v0.10.0) — LDAP status + Sync-now, per-user role editing (admin / editor / viewer), and **Projects** management (create / archive, member management from the LDAP-approved list with per-project roles). Hidden for non-admins.
- Plus the existing Repos, Shared credentials, MCP Integrations, and MCP refresh panels (see `docs/review-console-design.md` §Config tab).

## How DAV deploys it

The Ansible role at `../ansible/roles/dav/tasks/review_console.yaml` builds the API and UI as in-cluster images and deploys them as Kubernetes Deployments alongside a Postgres Deployment for state.

The API pod mounts:
- `dav-workspace` PVC at `/workspace` (read-only) — for results browsing. **Must be ReadWriteMany** (CephFS, NFS, EFS, etc.) — RWO causes Multi-Attach failure when pipeline pods land on a different node than the API pod. The role defaults to RWX and uses the cluster's default storage class; override `dav_workspace_pvc_storage_class` in `vars.local.yaml` if the cluster default is RWO. See `docs/operator-runbook.md` §0.0 for storage-class names by provider.
- `corpus` emptyDir at `/data` — historically pre-populated by a `git-clone-corpus` init container. **Post-M11a**: the init container is a graceful no-op when `dav-source-corpus` is in multi-source mode (legacy `repo_url`/`repo_branch` keys absent). Tekton's `dav-git-sync-multi-corpus` task is the source of truth for corpus content at run time; the API's directory-mode loader tolerates an empty `/data/repo`.
- `service-ca` ConfigMap at `/var/run/configmaps/service-ca` — OpenShift's service-CA bundle (auto-injected by the `service.beta.openshift.io/inject-cabundle` annotation). Used to verify the TLS cert of `thanos-querier` when the API queries Prometheus for run-detail metrics.

The OAuth integration uses OpenShift's `origin-oauth-proxy` sidecar in the UI pod. The API trusts the `X-Forwarded-User` header set by the proxy and uses it as the identity on managed-UC writes and run triggers.

**Cluster RBAC the role provisions** (in `ansible/roles/dav/templates/`):
- `dav-review-runs-trigger` Role (namespace-scoped) — create PipelineRun + read TaskRuns/Pods for the Runs tab
- `dav-review-sourcing` Role (namespace-scoped) — read/patch the three source ConfigMaps and the Deployments they target
- `dav-review-api-cluster-monitoring-view` **ClusterRoleBinding** — grants the API SA the cluster `cluster-monitoring-view` ClusterRole so it can query `thanos-querier` for GPU/vLLM metrics. Read-only; safe to bind to UI backends that only render metrics.

**Cluster-side prerequisites for the run-detail metrics** (not provisioned by this role — these are cluster-wide concerns):
- OpenShift user-workload monitoring enabled (it is by default on 4.18+)
- An AMD GPU metrics exporter publishing `gpu_gfx_activity`, `gpu_used_vram`, `gpu_average_package_power`, `gpu_edge_temperature` to cluster Prometheus. The AMD GPU Operator deploys this when `DeviceConfig.spec.metricsExporter.enable=true`. Recommended scrape `interval: 10s` for the run-detail UI freshness; the operator default of 60s is too coarse — the drawer polls every 3 s but Prometheus is the bottleneck.
- A vLLM (or other OpenAI-compatible) inference server publishing `vllm:num_requests_running`, `vllm:gpu_cache_usage_perc`, `vllm:generation_tokens_total`, etc. KServe auto-generates a ServiceMonitor for `InferenceService` resources when annotated with `monitoring.opendatahub.io/scrape=true`.

**LLM-bound endpoint timeouts (M12+).** The Route, oauth-proxy sidecar, and nginx sidecar are all configured for a 600s ceiling so long-running calls (`/api/uc-assist` for wizard generate/refine, `/api/use-cases/bulk-from-text` for transcript extraction) don't 502 mid-stream. Three hops, three different defaults to override: `haproxy.router.openshift.io/timeout` annotation on the Route, `--upstream-timeout=600s` on the oauth-proxy container args, and `proxy_read_timeout 600s` on the `/api/` location in the nginx ConfigMap. All three live in `ansible/roles/dav/templates/review-console-ui-*` and must move in lock-step.

**Post-M12 follow-up surface (briefly).** A handful of features sit on top of the M11/M12 base and are worth knowing exist:
- **Two-pass stage-2** is on by default (`DAV_STAGE2_TWO_PASS=1`): pass 1 explores the spec and emits a structured findings JSON; pass 2 starts fresh with the findings + MCP re-fetch + the canonical Analysis schema. Information-preservation guarantee — pass 2 can re-pull anything pass 1 compressed.
- **Infrastructure confidence** lives on `metadata.infrastructure_confidence` of every Analysis, persisted via migration 013 to `uc_analyses.infra_confidence_{label,score,signals,explanation,recommendations}`. Distinct from analytical confidence. UI surfaces as a colored chip on the UC detail Test history table; New Run modal renders a preflight banner when recent runs of a Set had compromised confidence.
- **Enhancement apply** turns the structured ENHANCEMENT blocks from `POST /api/enhancements` into real PRs (multi-PR auto-routed by namespace) against `role=enhancement-target` repos. See `POST /api/enhancements/apply` and `review-console/api/app/enhancement_apply.py`.
- **Auto-ingest loop** in `lifespan()` re-runs `_ingest_run_analyses` for every workspace `run-summary.yaml` not yet in `analysis_runs` — startup + every 5 min. Removes the manual `POST /api/analysis/ingest/{run_id}` step that historically left per-UC history (and run comparisons) blank.
- **Namespace as first-class field** on `run_sessions.{spec,corpus}_namespaces` and `uc_gaps.namespace` (migration 012). Cross-namespace drift warning on enhancement-apply when patches target a namespace outside the run's recorded scope.

**v0.10.0 highlights** (full detail in `docs/review-console-design.md`):
- **Two-tier model selection** — Config defaults per `model_defaults` key (`arch-review`, `enhancement`, `evaluation`, `uc-authoring`) + per-view overrides; resolution centralized in `_model_default_row`. `GET/PUT /api/model-defaults[/{key}]`.
- **Cached Review / Enhancement output** — `analysis_output_cache` table, write-through on success, `GET /api/analysis/output`, staleness flag when the run was re-ingested since.
- **Tab-close-resilient generation** — `/api/arch-review` + `/api/enhancements` run the LLM in a background task observed by the SSE response, so closing the tab no longer cancels it; a completion toast fires even after navigating away.
- **Enhancement rendering** — output rendered as markdown (same `mdToHtml` as Arch Review) while the raw structured `ENHANCEMENT` blocks remain intact for `enhancement_apply`'s PR parser.
- **Run management** — Runs tab gains Archive (`run_sessions.archived`) + Delete (DB + workspace dir + Tekton PipelineRun; RBAC grants `pipelineruns` `delete`), bulk multi-select, and text + phase filters.
- **Multi-user / LDAP** — optional approval + roles via the `dav-ldap` Secret; opt-in access gate (`DAV_LDAP_ENFORCE`); `users` table + 10-min LDAP sync; `/api/me`, `/api/ldap/*`, `/api/users/*`.
- **Projects + data tenancy** — `projects` / `project_members`; `project_id` scoping on UCs, runs, sets, results, and the output cache via the `X-DAV-Project` header.
- **`api()` 204 fix** — empty / `204 No Content` bodies now resolve to `null` instead of throwing spurious "failed" toasts.

## Run locally (development)

You'll need:
- A reachable Postgres 14+ instance
- A local directory at the path `DAV_WORKSPACE_PATH` containing `results/` in the run-directory layout, if you want to test the Results tab

```bash
# Backend
cd review-console/api
pip install -r requirements.txt
DATABASE_URL=postgres://localhost/dav_review \
    DAV_WORKSPACE_PATH=/path/to/your/workspace \
    DAV_TRIGGER_ENABLED=false \
    CORPUS_MODE=directory \
    CORPUS_DIR=/path/to/corpus/repo \
    uvicorn app.main:app --port 8000

# Frontend (any static server works)
cd ../ui
python -m http.server 8001
# Open http://localhost:8001 and point the console at the local API
```

## Key files

| Path | Purpose |
|------|---------|
| `api/app/main.py` | FastAPI app, lifespan, all route definitions (v0.10.0). Model-default resolution (`_model_default_row`), background-task generation registry (`_active_gen` / `_run_generation_bg` / `_ensure_generation` / `_observe_generation`), and project scoping (`_active_project_id`) all live here |
| `api/app/schema.sql` | Postgres schema: corpus file cache, `managed_use_cases`, `lifecycle_events`, `use_case_sets`, `use_case_set_members`, `run_sessions` (with token-counter baselines + finalized energy/token stats), `analysis_output_cache`, `users`, `projects`, `project_members`. Idempotent on boot (`CREATE … IF NOT EXISTS` + `ADD COLUMN IF NOT EXISTS` + backfills), so it doubles as the v0.10.0 migration |
| `api/app/ldap_auth.py` | LDAP group resolution → `users` + in-memory approved set; the access-gate logic and 10-min background sync |
| `api/app/results.py` | Scans workspace PVC for run summaries, analysis YAMLs, **per-UC progress** (`run-progress.yaml`), and **per-turn JSONL** (`turns/<uuid>.seed-<N>.jsonl`) with byte-offset cursor for tail polling |
| `api/app/validations.py` | Tekton PipelineRun listing, triggering, status translation, run-detail (TaskRun walk), and failed-task log tail |
| `api/app/sources.py` | Spec / corpus / inference sourcing — ConfigMap writes, branch enumeration, inference endpoint validation (`/models` probe + model-presence check) |
| `api/app/metrics.py` | Async Prometheus query proxy targeting `thanos-querier` via the SA bearer token + service-CA bundle. Curated `snapshot()` runs ~12 PromQL queries in parallel; grouped GPU rows + vLLM scalars |
| `api/Containerfile` | API container image spec |
| `ui/index.html` | Complete single-file UI (no build step) — includes the run-detail drawer, theming system, and modal flows |
| `ui/Containerfile` | UI/NGINX container image spec |

## Notes

- The API schema is applied idempotently at startup inside an advisory-lock transaction — safe to restart without schema drift. New columns use `ALTER TABLE ADD COLUMN IF NOT EXISTS` so the schema file doubles as a migration script.
- The `managed_use_cases` table stores the full YAML text; UUID, title, tags, and lifecycle_state are indexed fields extracted at write time.
- The `lifecycle_events` table is append-only — the current lifecycle state is the `to_state` of the most recent event per UC. Don't update rows; insert events.
- `python-multipart` is required for the import file-upload endpoint (`POST /api/import`).
- The Results tab and the Runs tab are not hard-linked. PipelineRun names (e.g. `dav-console-123456`) do not embed the run-directory ID (`2026-05-21T10-30-00Z-abc1234`). Correlate by timestamp when needed.
- **Multi-user / LDAP env vars** (all optional; supplied via the `dav-ldap` Secret, mounted `envFrom: optional` on the API deployment): `DAV_LDAP_URL`, `DAV_LDAP_BIND_DN`, `DAV_LDAP_BIND_PASSWORD`, `DAV_LDAP_USER_BASE`, `DAV_LDAP_GROUP_DN`, `DAV_LDAP_USER_ATTR`, `DAV_LDAP_MAIL_ATTR`, `DAV_LDAP_NAME_ATTR`, `DAV_LDAP_MEMBER_ATTR`, `DAV_LDAP_START_TLS`, `DAV_LDAP_ENFORCE`, `DAV_LDAP_BOOTSTRAP_ADMINS`. The access gate is a **no-op** until `DAV_LDAP_ENFORCE=true` AND LDAP is configured AND a sync has succeeded — so configuring LDAP can't accidentally lock anyone out. Bootstrap admins are always admin. With LDAP off, the console is single-user with no role gating. Requires `ldap3==2.9.1` (in `requirements.txt`). See `deploy/dav-ldap-secret.example.yaml` and `docs/operator-runbook.md`.
- **Multi-project** (v0.10.0): the active project rides on the `X-DAV-Project` request header (set by the masthead switcher); the server resolves it via `_active_project_id()` (header → membership-validated → `default`). UCs, runs, sets, results, and the output cache are scoped by `project_id`; capability `catalog` and `model_defaults` remain global for now.
