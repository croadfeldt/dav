# ADR-005 — Shared Credentials Abstraction

**Status:** Accepted
**Date:** 2026-05-27
**Author:** Chris Roadfeldt + Claude
**Extends:** [ADR-004](004-per-repo-credentials-in-registry.md)

---

## 1. Context

[ADR-004](004-per-repo-credentials-in-registry.md) added per-repo
Fernet-encrypted credentials on `managed_repos`: each repo carries its
own `github_pat_encrypted` and `github_webhook_secret_encrypted` column.

That model works for one or two repos, but breaks down quickly when an
operator has many repos owned by the same GitHub identity:

- The same PAT must be entered N times.
- PAT rotation requires N updates with drift risk (one miss leaves a
  stale PAT silently in place).
- No audit / lineage between "this is my GitHub bot identity" and
  "these are the repos that use it" — it's just N opaque blobs.

A typical multi-repo deployment (e.g., upstream + multiple downstreams
all belonging to the same owner) hits this immediately.

The question this ADR settles: **how do operators manage a single
credential used across multiple managed_repos rows?**

## 2. Decision

Add a `credentials` table where each row is a named, typed,
Fernet-encrypted secret. Add nullable FK columns to `managed_repos`
that reference credentials. Resolution order: shared credential (if FK
set) > inline value (existing ADR-004 column) > none.

Schema (migration 010):

```sql
CREATE TABLE credentials (
    id              SERIAL PRIMARY KEY,
    uuid            UUID UNIQUE DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    credential_type TEXT NOT NULL,   -- 'github_pat', 'github_webhook_secret', ...
    value_encrypted TEXT NOT NULL,   -- Fernet (same crypto.py)
    description     TEXT,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      TEXT NOT NULL DEFAULT 'system',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by      TEXT NOT NULL DEFAULT 'system',
    UNIQUE (tenant_id, credential_type, name)
);

ALTER TABLE managed_repos
    ADD COLUMN github_pat_credential_id            INTEGER REFERENCES credentials(id) ON DELETE SET NULL,
    ADD COLUMN github_webhook_secret_credential_id INTEGER REFERENCES credentials(id) ON DELETE SET NULL;
```

`get_repo_secrets()` (the internal helper the poller and webhook use)
prefers the FK if set; falls back to the inline column; returns None
otherwise. This is a non-breaking change: every existing inline value
keeps working without operator intervention.

API surface:

| Endpoint | What |
|---|---|
| `GET /api/credentials?type=&tenant_id=` | List metadata; value never returned. Each row includes `used_by_repos` count + uuid list. |
| `GET /api/credentials/{uuid_or_name}` | Single with full `used_by_repos` list |
| `POST /api/credentials` | Create. `value` field is write-only; encrypted at write. |
| `PUT /api/credentials/{uuid}` | Rotate the value (single update propagates to every dependent repo) or update name/description/metadata |
| `DELETE /api/credentials/{uuid}` | Refuses with 409 if any repo references it — operator must reassign or null the FKs first. The body of the 409 includes the dependent repo list so the UI can render them. |

Repo CRUD endpoints (`POST/PUT /api/repos[/{x}]`) gain two optional
fields: `github_pat_credential_id`, `github_webhook_secret_credential_id`.
Setting either to a credential UUID/name links; setting to `null`
unlinks (without clearing the inline column).

UI:

- **New Config category: "Shared credentials"** above Repos
- List per credential: name + type + description + "used by N repo(s)" chip
- Add / Edit (rotate the value with the same Set/Rotate/Clear write-only
  pattern as ADR-004 inline credentials) / Delete
- **Repo edit form**: PAT field becomes a dropdown of existing
  `github_pat` credentials + `+ Create new...` + `(inline value)` option
- Same for webhook secret
- **Migration affordance**: a "Convert to shared credential" button on
  each repo row that still has an inline PAT — creates a credential from
  the inline value, sets the FK, clears the inline. One-shot per repo.

## 3. Alternatives Considered

### A. Tenant-default credentials with per-repo override

`tenant_credentials(tenant_id, type, value_encrypted)`. Poller looks up
per-repo first, falls back to tenant default.

**Rejected because:** rigid — assumes one credential per type per tenant
at the default level. The user's actual workflow has multiple identities
in play (e.g., a personal PAT for some repos, an org bot PAT for others).
Named credentials handle this naturally.

### B. Credential reference inside ingestion_config JSONB

`managed_repos.ingestion_config.github_pat_credential = "my-github-pat"`.
Looked up by name at runtime.

**Rejected because:** loses FK integrity (deletion of a credential
leaves dangling string references; no `ON DELETE SET NULL` semantics).
FK columns are cheap and correct.

### C. Build directly against HashiCorp Vault now

Skip the intermediate `credentials` table; go straight to Vault.

**Deferred** for the same reasons as ADR-004 §D: Vault deployment is
multi-week infra and not the current blocker. The shared-credentials
abstraction is itself the right Vault-shaping intermediate step: each
`credentials` row maps cleanly to a future Vault KV path, and the FK
columns become Vault path references when the swap happens.

## 4. Consequences

### Positive

- One credential per identity, rotated in one place.
- Audit clarity: "this PAT is used by N repos" is a query, not a manual
  inventory.
- Vault-shaping: when ADR-004 §D's forward path is taken, this is the
  obvious migration target — `credentials` becomes a thin wrapper over
  Vault KV.
- Non-breaking: existing inline credentials keep working; operators
  migrate at their own pace via the UI affordance.

### Negative

- One more table + endpoints to keep in sync as new credential types
  land (e.g., when SSH keys or cloud-provider creds get added).
- Deletion semantics are slightly more involved: must check FKs before
  delete. The 409-with-list pattern handles this cleanly but is a
  pattern operators must learn.

## 5. Migration & rollback

### Adoption (forward)

Operators with inline PATs (post-ADR-004) see no change until they
choose to migrate. The UI's "Convert to shared credential" button:

1. Reads the inline encrypted value
2. Creates a new `credentials` row (operator picks the name; type is
   pre-filled)
3. Decrypts inline value, re-encrypts (same key — no-op crypto but
   keeps the boundary clean) into the new row
4. Sets the FK on the repo
5. Clears the inline encrypted column

After conversion, the repo references the shared credential exclusively.
Rotating the credential updates every linked repo at once.

### Rollback

`DELETE FROM credentials` (where the FK is set) triggers `ON DELETE SET
NULL`, returning the repos to inline-only mode. If the inline value was
cleared during the migration, the repo loses its credential entirely
and the operator re-enters it via the existing inline path.

A future operator who wants to drop the abstraction entirely can:
1. For each repo with a credential FK, copy the credential's plaintext
   into the repo's inline column
2. Null the FKs
3. Drop the `credentials` table

This is reversible without data loss as long as the Fernet key is
unchanged.

## 6. Implementation status

- Migration 010 creates `credentials` + adds FK columns to `managed_repos`.
- `review-console/api/app/credentials.py` — CRUD + lookup helpers; never
  returns plaintext via HTTP; same Fernet wrapping as inline.
- `repos.py.get_repo_secrets()` updated: FK → credential decrypt → fallback
  to inline → None.
- `main.py` gains `GET/POST/PUT/DELETE /api/credentials`; existing
  `POST/PUT /api/repos[/{x}]` accept `github_pat_credential_id` +
  `github_webhook_secret_credential_id`.
- UI: new Shared credentials section in Config; dropdown integration in
  the Repos form; "Convert to shared" migration button on each repo row
  with inline values.

## 7. Related

- [ADR-003](003-multi-repo-registry-and-mcp-source-of-truth.md) —
  Multi-Repo Registry (the substrate this extends)
- [ADR-004](004-per-repo-credentials-in-registry.md) — Per-Repo
  Credentials (the inline storage this builds on; rejected env-var
  approach; Vault forward path deferred)
- `docs/review-console-design.md` — Operational design doc updated with
  the Shared credentials section
- `specs/09-deployment-standards.md` — credentials are still encrypted
  with `DAV_FERNET_KEY`; no new env var or Secret
