"""Managed repos registry — CRUD over the managed_repos table.

The managed_repos table is the first-class source-of-truth for which repos
DAV operates on. Each row carries a namespace (URL-safe identifier used as
the doc-handle prefix in multi-source MCP and as the clone directory name),
a clone URL + branch, an optional root_path subdirectory, and a roles[]
array indicating what the repo is used for.

Roles in v1:
  - 'spec'         — served by dav-docs-mcp (projected into dav-source-spec
                     ConfigMap by sources.py / M2)
  - 'corpus'       — cloned by the pipeline at run start (UCs read from here)
  - 'issue-source' — polled / webhook'd for PR comments (M5/M6)

The dav-source-spec and dav-source-corpus ConfigMaps become projections
over this table: filter rows by role, render as the ConfigMap's `sources`
YAML list. Projection logic lands in M2.

Seeding: on first startup, if the registry is empty, we read the existing
dav-source-spec / dav-source-corpus ConfigMaps and create one managed_repos
row per source declared there. This preserves the operator's existing
configuration without manual reseeding after upgrade.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import asyncpg
import yaml

from . import credentials as _credentials
from . import crypto as _crypto

log = logging.getLogger("dav-review-api.repos")

# Per-repo credential columns supported by the registry.
# Keep this list small + closed — adding a credential type involves a
# migration (new column), a UI affordance, and per-callsite handling.
SECRET_FIELDS = ("github_pat", "github_webhook_secret")

# v1 closed vocabulary. Open for extension — adding a role here is the only
# change needed to make the registry accept it.
VALID_ROLES = {"spec", "corpus", "issue-source"}

# Match the migration's CHECK constraint exactly.
_NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$")


def _validate_namespace(namespace: str) -> None:
    if not namespace or not _NAMESPACE_RE.match(namespace):
        raise ValueError(
            f"invalid namespace {namespace!r}: must be lowercase alphanumeric "
            "with hyphens, 2-63 chars, not starting or ending with hyphen"
        )


def _validate_repo_url(repo_url: str) -> None:
    if not repo_url or not repo_url.startswith(("http://", "https://", "git@")):
        raise ValueError(f"invalid repo_url: {repo_url!r}")
    if len(repo_url) > 512:
        raise ValueError("repo_url too long (max 512)")


def _validate_branch(branch: str) -> None:
    if not branch or any(c.isspace() for c in branch):
        raise ValueError(f"invalid branch: {branch!r}")
    if len(branch) > 256:
        raise ValueError("branch too long (max 256)")


def _validate_roles(roles: list[str]) -> list[str]:
    if not isinstance(roles, list):
        raise ValueError(f"roles must be a list, got {type(roles).__name__}")
    unknown = [r for r in roles if r not in VALID_ROLES]
    if unknown:
        raise ValueError(
            f"unknown role(s) {unknown}; valid roles are {sorted(VALID_ROLES)}"
        )
    # Dedupe while preserving order
    seen = set()
    out = []
    for r in roles:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


DEFAULT_TENANT = "default"


def _parse_jsonb(value) -> dict:
    """asyncpg returns JSONB columns as raw strings unless a codec is
    registered globally. Parse here to keep the rest of the module simple.
    Accepts dict (already parsed), str (JSON to parse), or None.
    """
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    # Unexpected type — return empty rather than raise.
    return {}


def _row_to_dict(row: asyncpg.Record) -> dict:
    """Convert a managed_repos row to a JSON-serialisable dict.

    NEVER returns encrypted credential columns. Exposes `has_*` flags
    indicating whether ANY source (shared credential FK OR inline column)
    has a value, plus `*_source` indicating which one wins per ADR-005
    resolution order ('shared' | 'inline' | None).

    If the row was fetched with the credential-uuid sub-queries
    (via _select_repos / _select_repo_one), the linked credentials are
    surfaced as `github_pat_credential` and `github_webhook_secret_credential`
    objects ({uuid, name}). Otherwise those keys are None — the integer
    FK is still available for callers that need it.
    """
    has_pat_inline = bool(row["github_pat_encrypted"])
    has_pat_shared = row["github_pat_credential_id"] is not None
    has_ws_inline = bool(row["github_webhook_secret_encrypted"])
    has_ws_shared = row["github_webhook_secret_credential_id"] is not None

    def _cred_obj(uuid_key: str, name_key: str):
        # Sub-query columns are absent if the SELECT didn't request them
        try:
            u = row[uuid_key]; n = row[name_key]
        except (KeyError, IndexError):
            return None
        if u and n:
            return {"uuid": u, "name": n}
        return None

    return {
        "uuid": str(row["uuid"]),
        "namespace": row["namespace"],
        "display_name": row["display_name"] or row["namespace"],
        "repo_url": row["repo_url"],
        "repo_branch": row["repo_branch"],
        "root_path": row["root_path"],
        "roles": list(row["roles"] or []),
        "tenant_id": row["tenant_id"],
        "ingestion_config": _parse_jsonb(row["ingestion_config"]),
        "metadata": _parse_jsonb(row["metadata"]),
        "has_github_pat": has_pat_inline or has_pat_shared,
        "has_github_webhook_secret": has_ws_inline or has_ws_shared,
        # Per ADR-005: shared FK wins; inline is fallback
        "github_pat_source": "shared" if has_pat_shared else ("inline" if has_pat_inline else None),
        "github_webhook_secret_source": "shared" if has_ws_shared else ("inline" if has_ws_inline else None),
        "github_pat_credential_id": row["github_pat_credential_id"],
        "github_webhook_secret_credential_id": row["github_webhook_secret_credential_id"],
        "github_pat_credential": _cred_obj("github_pat_credential_uuid", "github_pat_credential_name"),
        "github_webhook_secret_credential": _cred_obj("github_webhook_secret_credential_uuid", "github_webhook_secret_credential_name"),
        "created_at": row["created_at"].isoformat(),
        "created_by": row["created_by"],
        "updated_at": row["updated_at"].isoformat(),
        "updated_by": row["updated_by"],
    }


# Common SELECT with credential ref sub-queries. Caller appends WHERE clauses
# and ORDER BY. Sub-queries are cheap (PK indexed lookup on FK).
_REPO_SELECT_WITH_CREDS = """
    SELECT mr.*,
           (SELECT uuid::text FROM credentials WHERE id = mr.github_pat_credential_id) AS github_pat_credential_uuid,
           (SELECT name        FROM credentials WHERE id = mr.github_pat_credential_id) AS github_pat_credential_name,
           (SELECT uuid::text FROM credentials WHERE id = mr.github_webhook_secret_credential_id) AS github_webhook_secret_credential_uuid,
           (SELECT name        FROM credentials WHERE id = mr.github_webhook_secret_credential_id) AS github_webhook_secret_credential_name
    FROM managed_repos mr
"""


# ------------------------- CRUD -------------------------


async def list_repos(
    conn: asyncpg.Connection,
    role: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> list[dict]:
    """List managed repos.

    Optional filters:
    - `role`: only repos that carry this role in their roles[] array.
    - `tenant_id`: only repos in this tenant. If omitted, ALL tenants
      are returned (intended for operator/admin use; per-tenant UI views
      pass tenant_id explicitly).
    """
    if role is not None and role not in VALID_ROLES:
        raise ValueError(f"unknown role: {role!r}")

    where = []
    args: list = []
    if role is not None:
        args.append(role)
        where.append(f"${len(args)} = ANY(roles)")
    if tenant_id is not None:
        args.append(tenant_id)
        where.append(f"tenant_id = ${len(args)}")
    where_clause = (" WHERE " + " AND ".join(where)) if where else ""

    rows = await conn.fetch(
        f"{_REPO_SELECT_WITH_CREDS} {where_clause} ORDER BY namespace ASC",
        *args,
    )
    return [_row_to_dict(r) for r in rows]


async def get_repo(conn: asyncpg.Connection, uuid_or_namespace: str) -> Optional[dict]:
    """Fetch one repo by UUID or namespace (UI/operator convenience)."""
    row = await conn.fetchrow(
        f"{_REPO_SELECT_WITH_CREDS} "
        "WHERE mr.uuid::text = $1 OR mr.namespace = $1 LIMIT 1",
        uuid_or_namespace,
    )
    return _row_to_dict(row) if row else None


async def create_repo(
    conn: asyncpg.Connection,
    *,
    namespace: str,
    repo_url: str,
    repo_branch: str = "main",
    display_name: Optional[str] = None,
    root_path: str = "",
    roles: Optional[list[str]] = None,
    tenant_id: str = DEFAULT_TENANT,
    ingestion_config: Optional[dict] = None,
    metadata: Optional[dict] = None,
    github_pat: Optional[str] = None,
    github_webhook_secret: Optional[str] = None,
    github_pat_credential_ref: Optional[str] = None,
    github_webhook_secret_credential_ref: Optional[str] = None,
    created_by: str = "system",
) -> dict:
    """Insert a new managed repo. Returns the created row as a dict.

    Optional `github_pat` and `github_webhook_secret` are Fernet-encrypted
    at write time (ADR-004 inline). Optional `*_credential_ref` accept a
    credential UUID or name and link via FK (ADR-005 shared). If both
    inline and credential_ref are provided for the same field, the FK
    wins at read time per get_repo_secrets resolution order.
    """
    _validate_namespace(namespace)
    _validate_repo_url(repo_url)
    _validate_branch(repo_branch)
    roles = _validate_roles(roles or [])
    root_path = (root_path or "").strip("/")
    if not tenant_id:
        tenant_id = DEFAULT_TENANT

    # Encrypt inline now so a Fernet misconfiguration is caught before INSERT
    pat_enc = _crypto.encrypt(github_pat) if github_pat else None
    secret_enc = _crypto.encrypt(github_webhook_secret) if github_webhook_secret else None

    # Resolve credential refs (UUID or name) to integer FKs
    pat_cred_id = None
    if github_pat_credential_ref:
        pat_cred_id = await _credentials.resolve_credential_id(
            conn, github_pat_credential_ref, "github_pat",
        )
        if pat_cred_id is None:
            raise ValueError(
                f"github_pat credential {github_pat_credential_ref!r} not found"
            )
    ws_cred_id = None
    if github_webhook_secret_credential_ref:
        ws_cred_id = await _credentials.resolve_credential_id(
            conn, github_webhook_secret_credential_ref, "github_webhook_secret",
        )
        if ws_cred_id is None:
            raise ValueError(
                f"github_webhook_secret credential {github_webhook_secret_credential_ref!r} not found"
            )

    try:
        row = await conn.fetchrow(
            "INSERT INTO managed_repos "
            "(namespace, display_name, repo_url, repo_branch, root_path, "
            " roles, tenant_id, ingestion_config, metadata, "
            " github_pat_encrypted, github_webhook_secret_encrypted, "
            " github_pat_credential_id, github_webhook_secret_credential_id, "
            " created_by, updated_by) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, "
            " $10, $11, $12, $13, $14, $14) "
            "RETURNING uuid",
            namespace, display_name, repo_url, repo_branch, root_path,
            roles, tenant_id, _to_jsonb(ingestion_config), _to_jsonb(metadata),
            pat_enc, secret_enc,
            pat_cred_id, ws_cred_id,
            created_by,
        )
    except asyncpg.UniqueViolationError as e:
        raise ValueError(
            f"namespace {namespace!r} is already in use by another repo"
        ) from e
    # Re-fetch with credential sub-queries so credential refs populate
    return await get_repo(conn, str(row["uuid"]))


_SENTINEL_UNLINK = object()  # marker for "explicitly null the FK" on update


async def update_repo(
    conn: asyncpg.Connection,
    uuid_or_namespace: str,
    *,
    repo_url: Optional[str] = None,
    repo_branch: Optional[str] = None,
    display_name: Optional[str] = None,
    root_path: Optional[str] = None,
    roles: Optional[list[str]] = None,
    ingestion_config: Optional[dict] = None,
    metadata: Optional[dict] = None,
    github_pat: Optional[str] = None,
    github_webhook_secret: Optional[str] = None,
    github_pat_credential_ref=None,           # str | _SENTINEL_UNLINK | None
    github_webhook_secret_credential_ref=None,  # str | _SENTINEL_UNLINK | None
    updated_by: str = "system",
) -> Optional[dict]:
    """Update one or more fields on an existing repo. Namespace is immutable.

    Returns the updated row, or None if no repo matched.
    """
    existing = await get_repo(conn, uuid_or_namespace)
    if not existing:
        return None

    set_clauses = []
    args: list = []

    if repo_url is not None:
        _validate_repo_url(repo_url)
        args.append(repo_url)
        set_clauses.append(f"repo_url = ${len(args)}")
    if repo_branch is not None:
        _validate_branch(repo_branch)
        args.append(repo_branch)
        set_clauses.append(f"repo_branch = ${len(args)}")
    if display_name is not None:
        args.append(display_name)
        set_clauses.append(f"display_name = ${len(args)}")
    if root_path is not None:
        args.append((root_path or "").strip("/"))
        set_clauses.append(f"root_path = ${len(args)}")
    if roles is not None:
        validated_roles = _validate_roles(roles)
        args.append(validated_roles)
        set_clauses.append(f"roles = ${len(args)}")
    # tenant_id is intentionally NOT settable through update_repo: moving
    # a repo between tenants is a higher-privilege operation that lands
    # as its own dedicated transfer_repo endpoint when multi-tenant
    # filtering ships.
    if ingestion_config is not None:
        args.append(_to_jsonb(ingestion_config))
        set_clauses.append(f"ingestion_config = ${len(args)}::jsonb")
    if metadata is not None:
        args.append(_to_jsonb(metadata))
        set_clauses.append(f"metadata = ${len(args)}::jsonb")
    # Per-repo credentials: set/rotate via PUT. Pass plaintext; encrypted
    # at write. To clear, use clear_repo_secret() (DELETE endpoint) —
    # passing None here means "don't touch", not "delete".
    if github_pat is not None:
        args.append(_crypto.encrypt(github_pat))
        set_clauses.append(f"github_pat_encrypted = ${len(args)}")
    if github_webhook_secret is not None:
        args.append(_crypto.encrypt(github_webhook_secret))
        set_clauses.append(f"github_webhook_secret_encrypted = ${len(args)}")
    # Credential FK changes:
    #   None                     → don't touch
    #   _SENTINEL_UNLINK          → set FK to NULL (explicit unlink)
    #   str (UUID or name)        → resolve + set FK
    if github_pat_credential_ref is _SENTINEL_UNLINK:
        set_clauses.append("github_pat_credential_id = NULL")
    elif github_pat_credential_ref is not None:
        cid = await _credentials.resolve_credential_id(
            conn, github_pat_credential_ref, "github_pat",
        )
        if cid is None:
            raise ValueError(f"github_pat credential {github_pat_credential_ref!r} not found")
        args.append(cid)
        set_clauses.append(f"github_pat_credential_id = ${len(args)}")
    if github_webhook_secret_credential_ref is _SENTINEL_UNLINK:
        set_clauses.append("github_webhook_secret_credential_id = NULL")
    elif github_webhook_secret_credential_ref is not None:
        cid = await _credentials.resolve_credential_id(
            conn, github_webhook_secret_credential_ref, "github_webhook_secret",
        )
        if cid is None:
            raise ValueError(f"github_webhook_secret credential {github_webhook_secret_credential_ref!r} not found")
        args.append(cid)
        set_clauses.append(f"github_webhook_secret_credential_id = ${len(args)}")

    if not set_clauses:
        # No-op update — return existing without touching updated_at.
        return existing

    args.append(updated_by)
    set_clauses.append(f"updated_by = ${len(args)}")

    args.append(existing["uuid"])
    row = await conn.fetchrow(
        f"UPDATE managed_repos SET {', '.join(set_clauses)} "
        f"WHERE uuid::text = ${len(args)} RETURNING uuid",
        *args,
    )
    if not row:
        return None
    return await get_repo(conn, str(row["uuid"]))


async def delete_repo(
    conn: asyncpg.Connection, uuid_or_namespace: str
) -> bool:
    """Delete a managed repo. Returns True if a row was deleted, False otherwise.

    Caller is responsible for projection-side effects (regenerating
    dav-source-spec ConfigMap, etc.).
    """
    result = await conn.execute(
        "DELETE FROM managed_repos WHERE uuid::text = $1 OR namespace = $1",
        uuid_or_namespace,
    )
    # asyncpg returns "DELETE <n>"
    return result.endswith(" 1") or result.startswith("DELETE 1")


# ------------------------- Per-repo secrets (internal use) -------------------------


async def get_repo_secrets(
    conn: asyncpg.Connection, uuid_or_namespace: str,
) -> Optional[dict]:
    """Fetch + decrypt a repo's stored credentials. Internal use only —
    never exposed via HTTP endpoints.

    Resolution order (per ADR-005):
      1. Shared credential via FK (github_pat_credential_id / github_webhook_secret_credential_id)
      2. Inline encrypted column (ADR-004 fallback)
      3. None

    Returns a dict with keys `github_pat` and `github_webhook_secret`
    (each None if not set). Returns None if the repo doesn't exist.

    Raises CryptoUnavailableError from the crypto module if a non-NULL
    encrypted value cannot be decrypted (key missing / changed). Callers
    log a clear "re-enter the credential" hint.
    """
    row = await conn.fetchrow(
        """
        SELECT github_pat_encrypted,
               github_webhook_secret_encrypted,
               github_pat_credential_id,
               github_webhook_secret_credential_id
        FROM managed_repos
        WHERE uuid::text = $1 OR namespace = $1 LIMIT 1
        """,
        uuid_or_namespace,
    )
    if not row:
        return None

    # Prefer the shared-credential FK over the inline column.
    pat = None
    if row["github_pat_credential_id"] is not None:
        pat = await _credentials.get_credential_secret(
            conn, credential_id=row["github_pat_credential_id"],
        )
    if pat is None:
        pat = _crypto.decrypt(row["github_pat_encrypted"])

    webhook_secret = None
    if row["github_webhook_secret_credential_id"] is not None:
        webhook_secret = await _credentials.get_credential_secret(
            conn, credential_id=row["github_webhook_secret_credential_id"],
        )
    if webhook_secret is None:
        webhook_secret = _crypto.decrypt(row["github_webhook_secret_encrypted"])

    return {
        "github_pat": pat,
        "github_webhook_secret": webhook_secret,
    }


async def convert_inline_to_shared(
    conn: asyncpg.Connection,
    uuid_or_namespace: str,
    field: str,
    credential_name: str,
    description: Optional[str] = None,
    updated_by: str = "system",
) -> dict:
    """Migrate a repo's inline encrypted credential to a shared credentials
    row (ADR-005 §5 adoption). Decrypts the inline value, creates a new
    credentials row, sets the FK, clears the inline column. One-shot.

    Raises ValueError if the repo has no inline value for this field, or
    if the credential name collides with an existing one.
    """
    if field not in SECRET_FIELDS:
        raise ValueError(f"unknown field {field!r}; valid: {sorted(SECRET_FIELDS)}")
    inline_col = f"{field}_encrypted"
    fk_col = f"{field}_credential_id"
    cred_type = field  # 'github_pat' or 'github_webhook_secret'

    row = await conn.fetchrow(
        f"SELECT uuid, namespace, tenant_id, {inline_col}, {fk_col} "
        "FROM managed_repos WHERE uuid::text = $1 OR namespace = $1 LIMIT 1",
        uuid_or_namespace,
    )
    if not row:
        raise ValueError(f"repo {uuid_or_namespace!r} not found")
    if row[fk_col] is not None:
        raise ValueError(
            f"repo {row['namespace']} already references a shared credential "
            f"for {field}; nothing to convert"
        )
    if not row[inline_col]:
        raise ValueError(
            f"repo {row['namespace']} has no inline {field} to convert"
        )

    plaintext = _crypto.decrypt(row[inline_col])
    if plaintext is None:
        raise ValueError(
            f"inline {field} for repo {row['namespace']} could not be decrypted "
            f"(Fernet key changed?)"
        )

    new_cred = await _credentials.create_credential(
        conn,
        name=credential_name,
        credential_type=cred_type,
        value=plaintext,
        description=description or f"Migrated from inline {field} on repo {row['namespace']}",
        tenant_id=row["tenant_id"] or DEFAULT_TENANT,
        created_by=updated_by,
    )
    # Find the integer id (create_credential returns the dict without id)
    cid = await _credentials.resolve_credential_id(
        conn, new_cred["uuid"], cred_type,
    )
    await conn.execute(
        f"UPDATE managed_repos "
        f"SET {fk_col} = $1, {inline_col} = NULL, updated_by = $2 "
        f"WHERE uuid::text = $3",
        cid, updated_by, str(row["uuid"]),
    )
    return {
        "repo": await get_repo(conn, str(row["uuid"])),
        "credential": new_cred,
    }


async def clear_repo_secret(
    conn: asyncpg.Connection, uuid_or_namespace: str, field: str,
    updated_by: str = "system",
) -> Optional[dict]:
    """Explicitly remove a repo's credential for the given field.

    Clears BOTH the inline encrypted column AND the shared-credential FK
    in one operation — semantically "this repo no longer has any
    credential of this type". The shared credential row itself is NOT
    deleted (other repos may reference it; deletion is a separate
    DELETE /api/credentials/{uuid} flow that refuses if dependents exist).

    `field` must be one of SECRET_FIELDS ('github_pat', 'github_webhook_secret').
    Returns the updated row dict, or None if the repo doesn't exist.
    """
    if field not in SECRET_FIELDS:
        raise ValueError(
            f"unknown secret field {field!r}; valid: {sorted(SECRET_FIELDS)}"
        )
    inline_col = f"{field}_encrypted"
    fk_col = f"{field}_credential_id"
    row = await conn.fetchrow(
        f"UPDATE managed_repos SET {inline_col} = NULL, {fk_col} = NULL, "
        f"updated_by = $1 "
        "WHERE uuid::text = $2 OR namespace = $2 RETURNING uuid",
        updated_by, uuid_or_namespace,
    )
    if not row:
        return None
    return await get_repo(conn, str(row["uuid"]))


# ------------------------- Seeding -------------------------


async def seed_from_existing_configmaps(
    conn: asyncpg.Connection,
    spec_sources_yaml: Optional[str] = None,
    spec_legacy_url: Optional[str] = None,
    spec_legacy_branch: Optional[str] = None,
    corpus_url: Optional[str] = None,
    corpus_branch: Optional[str] = None,
) -> int:
    """First-run seed: populate managed_repos from the existing source
    ConfigMaps so the operator's current config carries forward into the
    registry without manual reseeding.

    Caller passes the ConfigMap contents (since this module avoids importing
    the kubernetes client directly — the caller does that). All args are
    optional; the function inserts whatever it has.

    Returns the number of rows inserted. If the registry already has any
    rows, this is a no-op (returns 0) — we don't overwrite operator-managed
    state on later startups.
    """
    count = await conn.fetchval("SELECT COUNT(*) FROM managed_repos")
    if count > 0:
        return 0

    inserted = 0

    # Spec multi-source: parse YAML list and insert one row per source
    if spec_sources_yaml:
        try:
            sources = yaml.safe_load(spec_sources_yaml) or []
        except Exception as e:
            log.warning("seed: failed to parse spec sources YAML: %s", e)
            sources = []
        for src in sources:
            ns = src.get("namespace")
            url = src.get("repo_url")
            branch = src.get("repo_branch", "main")
            root = (src.get("root_path") or "").strip("/")
            if not ns or not url:
                continue
            try:
                # If this repo is also the corpus, we'll add 'corpus' role
                # in the next block. For now mark it as spec only.
                await create_repo(
                    conn,
                    namespace=ns,
                    repo_url=url,
                    repo_branch=branch,
                    root_path=root,
                    roles=["spec"],
                    created_by="seed:configmap",
                )
                inserted += 1
                log.info("seeded spec source: ns=%s url=%s branch=%s", ns, url, branch)
            except ValueError as e:
                log.warning("seed: skipping spec source ns=%s: %s", ns, e)

    # Spec legacy single-source: insert as ns='spec' if no multi-source
    if not spec_sources_yaml and spec_legacy_url and spec_legacy_branch:
        try:
            await create_repo(
                conn,
                namespace="spec",
                repo_url=spec_legacy_url,
                repo_branch=spec_legacy_branch,
                roles=["spec"],
                created_by="seed:configmap:legacy",
            )
            inserted += 1
            log.info("seeded legacy spec single-source: %s", spec_legacy_url)
        except ValueError as e:
            log.warning("seed: skipping legacy spec source: %s", e)

    # Corpus (single-source today). If the URL matches an existing spec
    # entry (by URL — namespace may differ), add 'corpus' to its roles.
    # Otherwise insert as its own ns='corpus' entry.
    if corpus_url and corpus_branch:
        existing = await conn.fetchrow(
            "SELECT uuid, namespace, roles FROM managed_repos "
            "WHERE repo_url = $1 LIMIT 1",
            corpus_url,
        )
        if existing:
            existing_roles = list(existing["roles"] or [])
            if "corpus" not in existing_roles:
                existing_roles.append("corpus")
                await conn.execute(
                    "UPDATE managed_repos SET roles = $1, updated_by = $2 "
                    "WHERE uuid = $3",
                    existing_roles, "seed:configmap", existing["uuid"],
                )
                log.info(
                    "seeded corpus role onto existing repo ns=%s url=%s",
                    existing["namespace"], corpus_url,
                )
        else:
            try:
                await create_repo(
                    conn,
                    namespace="corpus",
                    repo_url=corpus_url,
                    repo_branch=corpus_branch,
                    roles=["corpus"],
                    created_by="seed:configmap",
                )
                inserted += 1
                log.info("seeded corpus single-source: %s", corpus_url)
            except ValueError as e:
                log.warning("seed: skipping corpus source: %s", e)

    return inserted


# ------------------------- Helpers -------------------------


def _to_jsonb(value: Optional[dict]) -> str:
    """asyncpg + jsonb prefers explicit JSON strings to avoid implicit casts."""
    import json
    return json.dumps(value if value is not None else {})
