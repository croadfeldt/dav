# DAV Review Console

Web UI + API for reviewing analyses produced by DAV. Browses the consumer's spec content, captures per-file review state (approved / needs-work / in-review), surfaces drift between reviewed content and current content, and triggers DAV pipeline runs from the UI.

## Components

- `api/` — FastAPI backend, asyncpg + Postgres for state, OAuth-proxy gated for SSO. Exposes `/files/`, `/reviews/`, `/runs/` etc.
- `ui/` — Static HTML/JS frontend. Served via NGINX with the OAuth proxy injecting `X-Forwarded-User`.
- `api/app/schema.sql` — Postgres schema (append-only review events + derived views).

## What it does

Two main flows:

**Review flow.**
- Operator browses files in the consumer spec repo (loaded from the `dav-spec` source content).
- Operator records reviews: `approved`, `in-review`, `needs-work`, `unreviewed`. Each review captures the file's content SHA at review time.
- The UI surfaces drift: if the file's SHA today differs from the SHA at review, the review is "drifted" (the operator's approval may no longer reflect current content).

**Pipeline trigger flow.**
- Operator picks a UC (or the whole corpus) and a mode (verification / reproduce / explore).
- The API creates a `PipelineRun` against `dav-stage2` with the selected params.
- The UI shows running and recently-completed PipelineRuns with status + duration.

## How DAV deploys it

The Ansible role at `../ansible/roles/dav/tasks/review_console.yaml` builds the API and UI as in-cluster images and deploys them as Kubernetes Deployments alongside a Postgres Deployment for state.

The OAuth integration uses OpenShift's `origin-oauth-proxy` sidecar in the UI pod. The API trusts the `X-Forwarded-User` header set by the proxy and uses it as the reviewer identity in audit-event records.

## Run locally (development)

The components can run outside OpenShift for development, but you'll need:

- A reachable Postgres (any 14+ instance; create a database matching the API's expected DSN)
- The OAuth proxy bypassed (set `ALLOW_ANON_WRITES=true` in the API env to use `anonymous` as the reviewer identity)

```bash
# Backend
cd review-console/api
pip install -r requirements.txt
DB_DSN=postgres://localhost/dav_review \
    ALLOW_ANON_WRITES=true \
    uvicorn app.main:app --port 8000

# Frontend (any static server works)
cd ../ui
python -m http.server 8001
# Configure the UI to talk to the local API, then open http://localhost:8001
```

## Files

- `api/Containerfile`, `ui/Containerfile` — container image specs
- `api/app/main.py` — FastAPI app, lifespan, route definitions
- `api/app/schema.sql` — Postgres schema
- `api/app/sources.py` — spec/corpus content sourcing logic
- `api/app/validations.py` — Tekton PipelineRun listing + status translation
- `api/app/corpus_loader.py` — loads files from a content tree into Postgres

## Notes

The API's startup applies `schema.sql` inside a transaction with an advisory lock to avoid concurrent-startup races. The schema is idempotent across restarts.

The `runs` API endpoints are guarded by `review_console_runs_trigger_enabled` (Ansible default `true`); set to `false` to deploy the console as read-only.
