# DAV Review Console

Full operations frontend for DAV. Trigger pipeline runs, browse analysis results, manage use cases, and switch consumer configuration — all from a single web UI deployed in OpenShift.

## Components

- `api/` — FastAPI backend, asyncpg + Postgres for state, OAuth-proxy gated for SSO.
- `ui/` — Static single-page HTML/JS frontend (`ui/index.html`). Served via NGINX with the OAuth proxy injecting `X-Forwarded-User`.
- `api/app/schema.sql` — Postgres schema (corpus content cache + managed use cases).

## Tabs

### Runs

Lists all PipelineRuns for the configured Tekton pipeline, sorted newest first. Shows run name, phase (Running / Succeeded / Failed / TimedOut / Cancelled), mode, triggered-by identity, start time, and duration.

**New Run** button opens a modal with full parameter control:
- Mode (verification / reproduce / explore)
- Sample count override
- Corpus subpath override
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

Each UC shows title, UUID, source badge, and tags. Opening a UC shows the full YAML; managed UCs open in an editor modal.

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
| `api/app/main.py` | FastAPI app, lifespan, all route definitions |
| `api/app/results.py` | Scans workspace PVC for run summaries and analysis YAMLs |
| `api/app/validations.py` | Tekton PipelineRun listing, triggering, status translation |
| `api/app/sources.py` | Spec/corpus content sourcing and ConfigMap writes |
| `api/app/schema.sql` | Postgres schema (corpus file cache + `managed_use_cases` table) |
| `api/Containerfile` | API container image spec |
| `ui/index.html` | Complete single-file UI (no build step) |
| `ui/Containerfile` | UI/NGINX container image spec |

## Notes

- The API schema is applied idempotently at startup inside an advisory-lock transaction — safe to restart without schema drift.
- The `managed_use_cases` table stores the full YAML text; UUID, title, and tags are indexed fields extracted at write time.
- The Results tab and the Runs tab are not hard-linked. PipelineRun names (e.g. `dav-console-123456`) do not embed the run-directory ID (`2026-05-21T10-30-00Z-abc1234`). Correlate by timestamp when needed. Hard-linking is a future roadmap item.
