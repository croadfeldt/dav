# AGENTS.md — DAV Review Console

> Nested cross-agent context file ([agents.md](https://agents.md) standard) for the **review-console**
> subtree. `CLAUDE.md` here is a symlink to this file. For operational detail see `README.md`; for the
> engine framework's design rationale see `../DAV-AI-PROMPT.md`; repo entry point is `../AGENTS.md`.

## What it is

The **operator-facing web app** for DAV: trigger pipeline runs, browse analysis results, manage use
cases through their lifecycle, organize them into sets, run assessments/maturity walls/roadmaps, and
administer access — all from one UI on OpenShift. It is the companion to the engine framework
(`../DAV-AI-PROMPT.md`); the engine runs the analysis, the console drives and surfaces it.

> **This doc supersedes the former `review-console/AI-PROMPT.md`**, which had gone badly stale (it
> described a ~1,400-line single-tenant SPA and tagged as "planned" much that has since shipped). The
> still-valid kernel of that doc is preserved below; the obsolete requirements narrative was removed.

## Stack

- **Backend:** `api/` — **FastAPI** (`api/app/main.py`, large), **asyncpg + Postgres** for state.
  Gated behind an **OAuth proxy** that injects `X-Forwarded-User`/`-Email` (SSO). Modular: auth
  (`ldap_auth.py`, `local_auth.py`, `api_tokens.py`), `rbac.py`, `audit.py`, `credentials.py`/`crypto.py`,
  model-assisted features (`uc_assist.py`, `arch_review.py`, `enhancement_apply.py`,
  `assessment_ingest.py`, `maturity_scoring.py`, `capability_*`), GitHub integration (`github_client.py`,
  `corpus_push.py`, `pr_comments.py`), `recording_worker.py` (separate deployment), `db_bootstrap.py`.
- **Frontend:** `ui/` — a **single-file, no-build** SPA (`ui/index.html`, served by NGINX behind the
  OAuth proxy). There is a small lint/e2e toolchain (`ui/lint.sh`, `ui/e2e.mjs`, `package.json`) — run
  those; there is no bundler/build step.
- **Deploy:** `deploy/` (OpenShift manifests, incl. `dav-db-backup.yaml`, `recording-worker.yaml`,
  `dav-ldap-secret.example.yaml`).

## Data model (load-bearing)

- **Append-only event log + derived views.** State changes are recorded as events
  (`review_events`/`lifecycle_events`) and read through derived views (`review_current`, `review_drift`,
  `file_current_status`). This event-sourced shape is deliberate — **don't replace it with in-place
  mutable rows.**
- **Analysis is ingested into Postgres** (`analysis_runs`, `uc_analyses`, `uc_gaps`, …) via
  `POST /api/analysis/ingest/{run_id}`, in addition to the workspace-PVC read path — this backs gap
  aggregation, trends, roadmap, and capability density.
- **Migrations are tracked.** Schema is applied via a migration runner (`db_bootstrap.py`) recording
  applied migrations in `schema_migrations`, plus per-tenant control/client schemas. **DO add a numbered
  migration** for schema changes — do **not** hand-edit a single `schema.sql` (the old doc said
  otherwise; that's wrong now).

## Auth, RBAC, tenancy

- **Multi-source identity:** OAuth-proxy header (`X-Forwarded-User`) for humans, **LDAP** approval +
  group→role sync (`DAV_LDAP_ENFORCE` opt-in), **local password** accounts, and **PAT bearer tokens**
  for agents/machines (first-class `agent` accounts). `ALLOW_ANON_WRITES` is a dev-only bypass.
  The oauth-proxy header is the **trust boundary** — hardening the `/api` relaxed-proxy seam matters
  (see repo task on `/api` defense-in-depth).
- **RBAC is roles + privileges, not scopes.** `rbac.py` + `rbac_roles`/`rbac_privileges`/bindings;
  agents get **role bindings exactly like a person** (no separate agent-permission system). The old
  doc's "scopes, not roles" locked decision was **reversed** — don't reintroduce a scope set.
- **Tenancy:** **project** is the scoping spine (sent as the `X-DAV-Project` header on every request);
  **customers** are first-class M:N demand attribution. A **multi-tenant substrate** (control/client
  schemas, schema-per-tenant bootstrap) exists and is opt-in. Note the open reconciliation flagged in
  `../DAV-AI-PROMPT.md`: the engine/Ansible layer still describes itself as single-tenant.

## Capability surface (UI tabs)

`runs`, `results`, `usecases`, `scopingsets`, `customers`, `projects`, `review`, `assess`, `maturity`,
`improve`, `engineering`, `enhancement`, `catalog`, `capmap`, `audit`, `inbox`, `config`. (Source of
truth is `ui/index.html`; the README's "Tabs" section documents each.)

## Run / test / conventions

- API deps in `api/requirements.txt`; UI checks via `ui/lint.sh` and `ui/e2e.mjs`.
- **Commits:** `--no-gpg-sign`, author `Chris Roadfeldt <chris@roadfeldt.com>`, trailer
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. GitHub `croadfeldt/dav`, branch `main`,
  subject-scoped PRs (see `../AGENTS.md` conventions).

## Genuinely still open / not built

Confirmed absent in code (don't assume these exist): a **webhook subsystem** (outbound subscriptions/
delivery/HMAC), **`/api/v1` versioning** + a published OpenAPI contract (all routes are unversioned
`/api/...`), **`Idempotency-Key`** handling on mutating POSTs, and **time-series** GPU/inference graphs
(metrics are point-in-time). The old doc listed several of these as "locked decisions" that never
shipped — treat them as open design questions, not commitments. **Tracked in [#4](https://github.com/croadfeldt/dav/issues/4)** (locked-decision reconciliation); the single-vs-multi-tenant boundary is **[#5](https://github.com/croadfeldt/dav/issues/5)**.
