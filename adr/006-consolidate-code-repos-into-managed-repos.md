# ADR-006 — Consolidate code_repo_configs into managed_repos

**Status:** Accepted
**Date:** 2026-05-27
**Author:** Chris Roadfeldt + Claude
**Extends:** [ADR-003](003-multi-repo-registry-and-mcp-source-of-truth.md),
[ADR-005](005-shared-credentials-abstraction.md)

---

## 1. Context

[ADR-003](003-multi-repo-registry-and-mcp-source-of-truth.md) established
`managed_repos` as **the** registry of repos DAV operates on, with
`roles[]` distinguishing purpose. M5/M6 added `issue-source` as a role
for PR-comment ingestion; the design intent was that any new "DAV
operates on this repo for purpose X" would land as another role.

But `code_repo_configs` — the table behind the Config → Code repositories
panel, used by enhancement PR creation — predates ADR-003 and was left
as a parallel registry. This means:

- Two places to register a repo for two different DAV behaviors
- Two places to enter the same PAT (and `code_repo_configs.token` is
  plaintext; no Fernet, no rotation, no [ADR-005](005-shared-credentials-abstraction.md)
  sharing)
- Operators must remember which registry to edit for which behavior
- Future "DAV does X with this repo" features face the same choice:
  add a column to `code_repo_configs`, add a column to `managed_repos`,
  or add a third registry

The question this ADR settles: **does enhancement PR creation belong
in managed_repos as another role, or does it stay a separate registry?**

## 2. Decision

Enhancement PR creation moves to `managed_repos` as the
**`enhancement-target`** role. `code_repo_configs` is migrated row-by-row
into `managed_repos` (matching by `repo_url`; merging roles when the
row already exists) and is then deprecated. Its `token` column is
encrypted into `managed_repos.github_pat_encrypted` during the
migration so [ADR-004](004-per-repo-credentials-in-registry.md) /
[ADR-005](005-shared-credentials-abstraction.md) credential resolution
applies uniformly.

The provider field (`github` / `gitlab`) lives in
`managed_repos.metadata.provider`. When absent, the enhancement code
infers from `repo_url` (host = `github.com` → github; host contains
`gitlab` → gitlab). This handles the common case without operator
configuration; the metadata override is for edge cases (self-hosted
git servers, etc.).

`code_repo_configs` is **not dropped in this migration**. It stays as
a read-only table for one release cycle so an operator can compare
state if anything looks wrong. A subsequent migration drops it once
the consolidation has soaked.

The Config → "Code repositories" UI panel is removed. Enhancement PR
target selection (currently a dropdown of `code_repo_configs`) becomes
a dropdown of `managed_repos` filtered to `role=enhancement-target`.

## 3. Alternatives Considered

### A. Keep both registries, document the split

Leave `code_repo_configs` alone; let it grow as a separate concept.

**Rejected because:** violates ADR-003's "one registry" intent. The
operator burden compounds (two PAT entries, two rotations, two UI
panels). Future "DAV does Y with this repo" features get pulled in
either direction with no principled answer.

### B. Replace managed_repos with code_repo_configs (the inverse)

`code_repo_configs` could absorb roles + multi-source projection.

**Rejected because:** the managed_repos schema is the
ADR-003-shaped + ADR-005-shared one (UUID-addressable, tenant-scoped,
shared credentials via FK, ingestion_config JSONB). `code_repo_configs`
is older and simpler. Moving the more-developed schema is wasted
churn; folding the simpler one in is mechanical.

### C. Per-purpose credentials on managed_repos

A separate column for the enhancement-write PAT (vs the polling-read
PAT) so operators can use a low-scope read-only token for polling and
a high-scope write token for enhancement creation.

**Deferred** to a future ADR if the workload demands it. For v1, the
existing `github_pat` field is used for both polling and enhancement.
Most operators use one PAT with both scopes (`repo` covers both); the
power-user case of split scopes can land as a follow-up that adds a
sibling FK column without breaking the common case.

## 4. Consequences

### Positive

- One registry. Operators add a repo once; check roles for what DAV
  does with it.
- Shared credentials apply uniformly. The same PAT used for polling
  also works for enhancement creation (when scoped `repo`).
- Migration 011 is non-destructive (additive: new role + token
  migration). Rollback = drop the role from existing repos; legacy
  `code_repo_configs` is untouched.
- Adding a future role (e.g., `webhook-target` for outbound events,
  `mirror-target` for sync) is one entry in `repos.py.VALID_ROLES`
  plus consuming-side code — no new tables, no new UI panels.

### Negative

- Breaking change for any external caller of `/api/code-repos`.
  Mitigation: the endpoints stay as 410 Gone with a Location header
  pointing at `/api/repos?role=enhancement-target` (or just removed
  if no external callers known).
- `PrCreateIn.repo_config_id: int` → `PrCreateIn.repo_uuid: str` is a
  breaking change for the PR creation endpoint. Mitigation: the only
  caller is the in-tree UI, which migrates atomically.
- Migration 011 must run with Fernet available to migrate tokens. If
  Fernet is missing, the migration creates managed_repos rows with
  empty tokens and logs a warning; operator re-enters via the Repos UI.

## 5. Migration

### Forward (Migration 011)

For each row in `code_repo_configs`:

1. Search `managed_repos` for a row with matching `repo_url` (any of
   `repo_url`, `clone_url`-equivalents).
2. If found:
   - Add `enhancement-target` to `roles[]` (no-op if already present).
   - Merge `metadata.provider = code_repo_configs.provider`.
   - Migrate `token` → `github_pat_encrypted` (Fernet) only if the
     managed_repos row has no PAT yet; otherwise leave the existing
     credential in place and log that the code_repo_configs token
     wasn't migrated.
3. If not found:
   - INSERT a new `managed_repos` row with:
     - `namespace` derived from `code_repo_configs.name` (slugified)
     - `repo_url`, `repo_branch = default_branch`
     - `roles = ['enhancement-target']`
     - `metadata = {provider: ...}`
     - `github_pat_encrypted = Fernet(token)` if token + Fernet present
     - `display_name = code_repo_configs.name`

Migration is idempotent — re-running detects already-migrated rows by
the `enhancement-target` role + matching `repo_url` and skips.

### Operator transition

After migration, the Config → "Code repositories" panel is gone. The
managed_repos list shows the migrated rows with the `enhancement-target`
role chip. Editing one updates URL/branch/PAT through the same form as
any other repo. Rotating the PAT via a shared credential propagates to
both polling (if also `issue-source`) and enhancement creation.

### Rollback

Drop the `enhancement-target` role from `repos.py.VALID_ROLES` and
revert the API + UI changes. The legacy `code_repo_configs` table is
intact and resumes serving the old endpoints (until a future migration
drops it). Tokens in code_repo_configs were not touched by the
migration.

## 6. Implementation status

- Migration 011 folds rows; idempotent.
- `repos.py.VALID_ROLES` += 'enhancement-target'.
- `main.py` enhancement endpoint rewritten to look up by
  `managed_repos.uuid` via `repos.get_repo()` + `repos.get_repo_secrets()`.
  `PrCreateIn.repo_uuid: str` replaces `repo_config_id: int`.
- `/api/code-repos*` endpoints return 410 Gone with a hint pointing at
  `/api/repos?role=enhancement-target`.
- UI Config → "Code repositories" panel + nav link removed.
- UI PR creation form's repo dropdown switches to
  `/api/repos?role=enhancement-target`.

## 7. Related

- [ADR-003](003-multi-repo-registry-and-mcp-source-of-truth.md) — the
  registry this consolidates into
- [ADR-004](004-per-repo-credentials-in-registry.md) — per-repo PAT
  encryption (applied to migrated tokens)
- [ADR-005](005-shared-credentials-abstraction.md) — shared credentials
  (enhancement creation can reuse the polling PAT or its own)
- `docs/review-console-design.md` — Code repositories section removed;
  enhancement creation now under the unified Repos panel
