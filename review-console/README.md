# DAV Review Console

Full operations frontend for DAV. Trigger pipeline runs, browse analysis results, manage use cases through their full lifecycle, organize them into named sets, and switch consumer configuration — all from a single web UI deployed in OpenShift.

## Components

- `api/` — FastAPI backend (v0.4.0), asyncpg + Postgres for state, OAuth-proxy gated for SSO.
- `ui/` — Static single-page HTML/JS frontend (`ui/index.html`). Served via NGINX with the OAuth proxy injecting `X-Forwarded-User`.
- `api/app/schema.sql` — Postgres schema (corpus content cache + managed use cases + lifecycle events + UC sets).

## Tabs

### Runs

Lists all PipelineRuns for the configured Tekton pipeline, sorted newest first. Shows run name, phase (Running / Succeeded / Failed / TimedOut / Cancelled), mode, triggered-by identity, start time, and duration.

**New Run** button opens a modal with full parameter control:
- Mode (verification / reproduce / explore)
- Sample count override
- Corpus subpath override (can be pre-filled from a Set run)
- Corpus and spec repo URL / branch overrides
- Inference endpoint and model overrides
- Halt-on-error flag

Run creation is guarded by `DAV_TRIGGER_ENABLED` (Ansible default `true`); set to `false` for a read-only deployment.

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

Source switching: change the spec and/or corpus repo URL + branch. Updating triggers a ConfigMap write + Deployment rollout so the new content takes effect on the next pod start.

## How DAV deploys it

The Ansible role at `../ansible/roles/dav/tasks/review_console.yaml` builds the API and UI as in-cluster images and deploys them as Kubernetes Deployments alongside a Postgres Deployment for state.

The API pod mounts:
- `dav-workspace` PVC at `/workspace` (read-only) — for results browsing
- `corpus` emptyDir at `/data` — cloned from the corpus repo by an init container

The OAuth integration uses OpenShift's `origin-oauth-proxy` sidecar in the UI pod. The API trusts the `X-Forwarded-User` header set by the proxy and uses it as the identity on managed-UC writes and run triggers.

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
| `api/app/main.py` | FastAPI app, lifespan, all route definitions (v0.4.0) |
| `api/app/schema.sql` | Postgres schema: corpus file cache, `managed_use_cases`, `lifecycle_events`, `use_case_sets`, `use_case_set_members` |
| `api/app/results.py` | Scans workspace PVC for run summaries and analysis YAMLs |
| `api/app/validations.py` | Tekton PipelineRun listing, triggering, status translation |
| `api/app/sources.py` | Spec/corpus content sourcing and ConfigMap writes |
| `api/Containerfile` | API container image spec |
| `ui/index.html` | Complete single-file UI (no build step) |
| `ui/Containerfile` | UI/NGINX container image spec |

## Notes

- The API schema is applied idempotently at startup inside an advisory-lock transaction — safe to restart without schema drift. New columns use `ALTER TABLE ADD COLUMN IF NOT EXISTS` so the schema file doubles as a migration script.
- The `managed_use_cases` table stores the full YAML text; UUID, title, tags, and lifecycle_state are indexed fields extracted at write time.
- The `lifecycle_events` table is append-only — the current lifecycle state is the `to_state` of the most recent event per UC. Don't update rows; insert events.
- `python-multipart` is required for the import file-upload endpoint (`POST /api/import`).
- The Results tab and the Runs tab are not hard-linked. PipelineRun names (e.g. `dav-console-123456`) do not embed the run-directory ID (`2026-05-21T10-30-00Z-abc1234`). Correlate by timestamp when needed.
