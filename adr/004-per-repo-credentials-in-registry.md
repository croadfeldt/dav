# ADR-004 — Per-Repo Credentials in the Registry (Fernet-encrypted)

**Status:** Accepted
**Date:** 2026-05-27
**Author:** Chris Roadfeldt + Claude
**Supersedes:** the env-var `GITHUB_TOKEN` design described in M5 of #28

---

## 1. Context

[ADR-003](003-multi-repo-registry-and-mcp-source-of-truth.md) established
the `managed_repos` table as the source-of-truth for which repos DAV
operates on. M5 (#28) added PR-comment polling against repos with
`role=issue-source`, initially using a single cluster-wide
`GITHUB_TOKEN` env var sourced from the `dav-review-api-tokens` Secret.

That single-tenant shape is wrong for the same reason ADR-003 rejected
the ConfigMap-only design:

- One PAT shared across all tenants and all repos. Cannot scope access
  (read-only public mirror vs full read-write internal repo).
- No per-user / per-tenant audit. Rotation forces every tenant to use
  the new token.
- Webhook secret (M6) has the same shape — a per-repo value on the
  GitHub side, so storing one cluster-wide value collapses identities.

The question this ADR settles: **where do per-repo credentials live,
and how are they protected?**

## 2. Decision

Per-repo credentials are stored on `managed_repos` as
**Fernet-encrypted** column values:

- `github_pat_encrypted` — used by the poller (M5) and any future
  GitHub API consumer (e.g., a webhook self-setup helper)
- `github_webhook_secret_encrypted` — used by the M6 webhook endpoint
  to validate inbound HMAC signatures

Both columns are nullable TEXT, hold the Fernet token (URL-safe base64
string), and are **never** returned on HTTP GET responses. The API
exposes `has_github_pat: bool` and `has_github_webhook_secret: bool`
in the row dict so the UI can render "(set)" / "(none)" indicators
without seeing the value.

Operators set / rotate values via `POST /api/repos` or
`PUT /api/repos/{x}`. Explicit deletion: `DELETE /api/repos/{x}/secrets/github_pat`
(and `.../github_webhook_secret`). The UI surfaces three actions:
**Set** (when none), **Rotate** (when set; types a new value), **Clear**
(when set; explicit delete).

The Fernet key is operator-provided via the `DAV_FERNET_KEY` env var
sourced from a new `dav-fernet-key` Secret. The Secret is created by
Ansible from `vault_dav_fernet_key` (vaulted). If the key is missing
at API startup, the API logs a clear error and refuses to serve
encrypted-secret endpoints — read/list/etc. continue working, so
operators can recover without a hard down.

## 3. Alternatives Considered

### A. Cluster-wide env var (the M5 v1)

Single `GITHUB_TOKEN` env from the `dav-review-api-tokens` Secret.

**Rejected because:** see §1 — collapses all tenants into one identity,
no per-repo scoping, no per-tenant rotation.

### B. Per-tenant credentials table (separate from managed_repos)

`tenant_credentials(tenant_id, provider, token_encrypted)`. Poller
looks up by tenant.

**Rejected because:** coarser-grained than needed. Repos within a
tenant may need different tokens (e.g., a single tenant runs DAV
against both a public mirror and an internal-private fork). Co-locating
secrets on the repo row that uses them is simpler.

### C. Plaintext in DB

Encryption at rest is just RBAC at the DB layer. HTTP never returns
the values; DB pod requires creds.

**Rejected because:** defense in depth. DB dumps, logs that capture
SQL, or a future RLS misconfiguration would leak plaintext. Fernet
adds modest cost and removes that surface entirely.

### D. External secret manager (HashiCorp Vault / Sealed Secrets / cloud KMS)

Store nothing in DB; fetch from a secrets manager per-request.

**Deferred to v2** (not rejected). This is the eventual target —
HashiCorp Vault specifically. Reasons to land Fernet-in-DB first:

- Vault deployment + auth wiring is itself a multi-week project on a
  homelab cluster (Vault operator install, namespace bootstrap,
  Kubernetes auth method, policies per service-account, transit
  engine for the encryption-as-a-service pattern, optional Vault
  Agent injector, etc.). Not the right blocker for shipping PR-comment
  ingestion.
- The Fernet-in-DB approach gives us the right *boundary* (per-repo,
  per-tenant, never-via-HTTP) without the infra. Switching to Vault
  later is a localized refactor — see "Forward path" below.

**Forward path to Vault**: the v1 abstraction localizes all secret
fetch/encrypt/decrypt to two modules:

- `review-console/api/app/crypto.py` — Fernet wrapper
- `review-console/api/app/repos.py` — `get_repo_secrets()` and the
  encryption call sites in create/update

In the Vault-backed v2:

- `crypto.py` is replaced (or supplemented) by a `vault_client.py`
  that reads/writes via the Vault transit / KV engine
- `repos.py` stops storing encrypted values in DB columns; instead
  stores a Vault path reference (e.g., `secret/data/dav/repos/<uuid>/github_pat`)
  in `managed_repos`
- Callsites (`get_repo_secrets`, the create/update helpers) keep
  the same signatures — the storage backend swap is invisible to
  the poller and the webhook endpoint

The DB columns added by Migration 009 are not wasted: they survive
the swap as nullable legacy columns that can be migrated row-by-row
into Vault and then dropped in a subsequent migration.

## 4. Consequences

### Positive

- Per-repo, per-tenant credential boundary. Audit and rotation are
  scoped to where the credential is used.
- HTTP API never returns secret values — single, narrow leak surface
  (the DB pod with Fernet key in env).
- Webhook secret design is symmetric to the PAT design — operators
  manage both the same way through the same UI.
- Fernet is well-understood; the `cryptography` lib is stable and the
  Fernet token format (URL-safe base64) is friendly to PostgreSQL
  TEXT columns without escaping concerns.
- Migration path from env-var GITHUB_TOKEN (M5 v1) is one-way clean:
  the env var is unread post-refactor, so removing it from the
  Secret is operator hygiene, not a code dependency.

### Negative

- Operator burden: must generate + safeguard the Fernet key once.
  Losing it makes existing encrypted values unrecoverable (operator
  must re-enter every PAT + webhook secret).
- Adds a Python dep on `cryptography` (already common; well-maintained).
- The Fernet key is itself a cluster-wide Secret, which means rotating
  it is a coordinated operation (decrypt with old, re-encrypt with
  new, swap key). v1 punts on rotation tooling; deferred to a future
  ADR if/when actually needed.

### Operator transition (from M5's env-var design)

For operators who set `GITHUB_TOKEN` in `dav-review-api-tokens` per
the M5 commit:

1. Apply this change set; the env var is no longer read.
2. Provide `vault_dav_fernet_key` (generate via
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
3. Re-deploy: Ansible creates the `dav-fernet-key` Secret + adds
   `DAV_FERNET_KEY` to the review-api Deployment.
4. In the Repos UI, edit each `role=issue-source` repo and set the
   `GitHub PAT` field. Save → poller starts using it.
5. Optional: remove the orphan `GITHUB_TOKEN` key from
   `dav-review-api-tokens` (no longer read).

## 5. Implementation status

- Migration 009 adds `github_pat_encrypted` + `github_webhook_secret_encrypted` columns to `managed_repos`.
- `review-console/api/app/crypto.py` — Fernet wrapper; key from env at module load; clear error if missing.
- `repos.py` — `_row_to_dict` strips secrets + adds `has_*` flags; `get_repo_secrets(uuid)` for internal use; create/update accept new fields; `clear_repo_secret(uuid, field)` for explicit deletes.
- `github_client.py` — drops `_token()` env path; all list_* functions take an explicit `token` param.
- `pr_comments.py` poller — fetches per-repo PAT via `get_repo_secrets`; skips repos with no PAT (logs + records on poll_state); passes token to `github_client`.
- `main.py` webhook endpoint (M6) — looks up per-repo `github_webhook_secret` for HMAC validation; rejects with 400 if absent.
- Ansible — new `dav-fernet-key` Secret task; `DAV_FERNET_KEY` env on review-api Deployment.
- UI (M3 panel extended) — write-only Set / Rotate / Clear affordances for both fields.

## 6. Related

- ADR-003 — Multi-Repo Registry and MCP Source-of-Truth (this ADR extends the registry with per-repo credentials)
- `docs/review-console-design.md` — Operational design doc updated
- `specs/09-deployment-standards.md` — `DAV_FERNET_KEY` requirement, vault setup
