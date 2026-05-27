"""Shared credentials registry — CRUD + lookup over the credentials table.

Per ADR-005. Each credentials row is a named, typed, Fernet-encrypted
secret that multiple managed_repos rows can reference via FK. Same
Fernet wrapping as ADR-004's inline columns (DAV_FERNET_KEY shared).

API contract: HTTP endpoints never return the plaintext value. Internal
callers (poller, webhook receiver, repo-secret resolver) use
`get_credential_secret(uuid_or_name, credential_type)` to decrypt.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import asyncpg

from . import crypto as _crypto

log = logging.getLogger("dav-review-api.credentials")

# v1 closed vocabulary — adding a type requires no migration; just add
# it to this set and surface a UI affordance. Keep it small + closed so
# the dropdown stays sensible.
VALID_TYPES = {"github_pat", "github_webhook_secret"}

DEFAULT_TENANT = "default"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def _validate_name(name: str) -> None:
    if not name or not _NAME_RE.match(name):
        raise ValueError(
            f"invalid credential name {name!r}: must be lowercase "
            "alphanumeric with hyphens, 1-63 chars, not starting with hyphen"
        )


def _validate_type(credential_type: str) -> None:
    if credential_type not in VALID_TYPES:
        raise ValueError(
            f"unknown credential_type {credential_type!r}; "
            f"valid: {sorted(VALID_TYPES)}"
        )


def _parse_jsonb(value) -> dict:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _to_jsonb(value: Optional[dict]) -> str:
    return json.dumps(value if value is not None else {})


def _row_to_dict(row: asyncpg.Record) -> dict:
    """Convert a credentials row to a JSON-serialisable dict.

    NEVER includes value_encrypted or the plaintext. The plaintext is
    only available via get_credential_secret() for internal use.
    """
    return {
        "uuid": str(row["uuid"]),
        "name": row["name"],
        "credential_type": row["credential_type"],
        "description": row["description"] or "",
        "tenant_id": row["tenant_id"],
        "metadata": _parse_jsonb(row["metadata"]),
        "created_at": row["created_at"].isoformat(),
        "created_by": row["created_by"],
        "updated_at": row["updated_at"].isoformat(),
        "updated_by": row["updated_by"],
    }


# ------------------------- CRUD -------------------------


async def list_credentials(
    conn: asyncpg.Connection,
    credential_type: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> list[dict]:
    """List credentials with optional filters. Includes a `used_by_repos`
    count for each row so the UI can show "used by N repo(s)" chips
    without an extra round-trip per row.
    """
    if credential_type is not None and credential_type not in VALID_TYPES:
        raise ValueError(f"unknown credential_type {credential_type!r}")
    where = []
    args: list = []
    if credential_type is not None:
        args.append(credential_type)
        where.append(f"credential_type = ${len(args)}")
    if tenant_id is not None:
        args.append(tenant_id)
        where.append(f"tenant_id = ${len(args)}")
    where_clause = (" WHERE " + " AND ".join(where)) if where else ""

    rows = await conn.fetch(
        f"""
        SELECT c.*,
               (
                 SELECT COUNT(*) FROM managed_repos r
                 WHERE r.github_pat_credential_id = c.id
                    OR r.github_webhook_secret_credential_id = c.id
               ) AS used_by_count
        FROM credentials c
        {where_clause}
        ORDER BY c.credential_type, c.name
        """,
        *args,
    )
    out = []
    for r in rows:
        d = _row_to_dict(r)
        d["used_by_count"] = int(r["used_by_count"])
        out.append(d)
    return out


async def get_credential(
    conn: asyncpg.Connection,
    uuid_or_name: str,
    credential_type: Optional[str] = None,
) -> Optional[dict]:
    """Fetch one credential by UUID or name. Optionally constrain by type
    (lets the operator/UI disambiguate when names collide across types).
    Returns the row dict + a `used_by_repos` list of {uuid, namespace,
    role: 'pat' | 'webhook_secret'} entries.
    """
    if credential_type is not None:
        row = await conn.fetchrow(
            "SELECT * FROM credentials "
            "WHERE (uuid::text = $1 OR name = $1) AND credential_type = $2 LIMIT 1",
            uuid_or_name, credential_type,
        )
    else:
        row = await conn.fetchrow(
            "SELECT * FROM credentials "
            "WHERE uuid::text = $1 OR name = $1 LIMIT 1",
            uuid_or_name,
        )
    if not row:
        return None
    out = _row_to_dict(row)
    deps = await conn.fetch(
        """
        SELECT uuid::text AS uuid, namespace,
               CASE
                 WHEN github_pat_credential_id            = $1 THEN 'pat'
                 WHEN github_webhook_secret_credential_id = $1 THEN 'webhook_secret'
               END AS used_as
        FROM managed_repos
        WHERE github_pat_credential_id = $1
           OR github_webhook_secret_credential_id = $1
        ORDER BY namespace
        """,
        row["id"],
    )
    out["used_by_repos"] = [dict(d) for d in deps]
    out["used_by_count"] = len(out["used_by_repos"])
    return out


async def create_credential(
    conn: asyncpg.Connection,
    *,
    name: str,
    credential_type: str,
    value: str,
    description: Optional[str] = None,
    tenant_id: str = DEFAULT_TENANT,
    metadata: Optional[dict] = None,
    created_by: str = "system",
) -> dict:
    """Insert a new credential. `value` is plaintext; encrypted at write.
    Raises ValueError on validation failure or name collision (per the
    UNIQUE (tenant_id, credential_type, name) constraint).
    """
    _validate_name(name)
    _validate_type(credential_type)
    if not value:
        raise ValueError("value is required (cannot be empty)")
    if not tenant_id:
        tenant_id = DEFAULT_TENANT

    value_enc = _crypto.encrypt(value)  # raises CryptoUnavailableError if no key

    try:
        row = await conn.fetchrow(
            "INSERT INTO credentials "
            "(name, credential_type, value_encrypted, description, tenant_id, "
            " metadata, created_by, updated_by) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $7) RETURNING *",
            name, credential_type, value_enc, description, tenant_id,
            _to_jsonb(metadata), created_by,
        )
    except asyncpg.UniqueViolationError as e:
        raise ValueError(
            f"credential {name!r} (type={credential_type}) already exists "
            f"in tenant {tenant_id!r}"
        ) from e
    return _row_to_dict(row)


async def update_credential(
    conn: asyncpg.Connection,
    uuid_or_name: str,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    value: Optional[str] = None,
    metadata: Optional[dict] = None,
    updated_by: str = "system",
) -> Optional[dict]:
    """Update fields on an existing credential. `value` is plaintext;
    encrypted at write. credential_type and tenant_id are immutable
    through this endpoint.

    Returns the updated row, or None if no credential matched.
    """
    existing = await get_credential(conn, uuid_or_name)
    if not existing:
        return None

    set_clauses = []
    args: list = []

    if name is not None:
        _validate_name(name)
        args.append(name)
        set_clauses.append(f"name = ${len(args)}")
    if description is not None:
        args.append(description)
        set_clauses.append(f"description = ${len(args)}")
    if value is not None:
        if not value:
            raise ValueError("value cannot be set to empty; DELETE the credential instead")
        args.append(_crypto.encrypt(value))
        set_clauses.append(f"value_encrypted = ${len(args)}")
    if metadata is not None:
        args.append(_to_jsonb(metadata))
        set_clauses.append(f"metadata = ${len(args)}::jsonb")

    if not set_clauses:
        return existing

    args.append(updated_by)
    set_clauses.append(f"updated_by = ${len(args)}")
    args.append(existing["uuid"])

    try:
        row = await conn.fetchrow(
            f"UPDATE credentials SET {', '.join(set_clauses)} "
            f"WHERE uuid::text = ${len(args)} RETURNING *",
            *args,
        )
    except asyncpg.UniqueViolationError as e:
        raise ValueError(
            f"credential name {name!r} already in use for this type+tenant"
        ) from e
    return _row_to_dict(row) if row else None


class CredentialInUseError(Exception):
    """Raised when delete is attempted on a credential with FK dependents.

    The API translates this to a 409 with the dependent_repos payload so
    the UI can render which repos need to be reassigned first.
    """
    def __init__(self, dependents: list[dict]):
        super().__init__(
            f"credential is referenced by {len(dependents)} repo(s); "
            "reassign or null those references before deleting"
        )
        self.dependents = dependents


async def delete_credential(
    conn: asyncpg.Connection, uuid_or_name: str,
) -> bool:
    """Delete a credential. Raises CredentialInUseError if any repos
    reference it (ON DELETE SET NULL would silently unlink, which is
    the wrong default — operator should make the unlink explicit).
    """
    existing = await get_credential(conn, uuid_or_name)
    if not existing:
        return False
    if existing["used_by_repos"]:
        raise CredentialInUseError(existing["used_by_repos"])
    result = await conn.execute(
        "DELETE FROM credentials WHERE uuid::text = $1 OR name = $1",
        uuid_or_name,
    )
    return result.endswith(" 1") or result.startswith("DELETE 1")


# ------------------------- Internal lookups -------------------------


async def get_credential_secret(
    conn: asyncpg.Connection,
    credential_id: Optional[int] = None,
    uuid_or_name: Optional[str] = None,
    credential_type: Optional[str] = None,
) -> Optional[str]:
    """Decrypt + return the plaintext value of a credential. Internal
    use only — never exposed via HTTP.

    Look up by either:
      - credential_id (INTEGER) — when the caller has the FK
      - uuid_or_name + optional credential_type — convenience for tools

    Returns None if no credential matches. Raises CryptoUnavailableError
    if the row exists but cannot be decrypted (key missing / changed).
    """
    if credential_id is not None:
        row = await conn.fetchrow(
            "SELECT value_encrypted FROM credentials WHERE id = $1",
            credential_id,
        )
    elif uuid_or_name is not None:
        if credential_type:
            row = await conn.fetchrow(
                "SELECT value_encrypted FROM credentials "
                "WHERE (uuid::text = $1 OR name = $1) AND credential_type = $2 LIMIT 1",
                uuid_or_name, credential_type,
            )
        else:
            row = await conn.fetchrow(
                "SELECT value_encrypted FROM credentials "
                "WHERE uuid::text = $1 OR name = $1 LIMIT 1",
                uuid_or_name,
            )
    else:
        raise ValueError("provide either credential_id or uuid_or_name")

    if not row:
        return None
    return _crypto.decrypt(row["value_encrypted"])


async def resolve_credential_id(
    conn: asyncpg.Connection, uuid_or_name: str, credential_type: str,
) -> Optional[int]:
    """Look up a credential's integer id from its UUID or name. Used by
    the repos endpoints to translate the operator-friendly identifier
    into the FK they store.
    """
    row = await conn.fetchrow(
        "SELECT id FROM credentials "
        "WHERE (uuid::text = $1 OR name = $1) AND credential_type = $2 LIMIT 1",
        uuid_or_name, credential_type,
    )
    return row["id"] if row else None
