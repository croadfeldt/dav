"""DB-backed Personal Access Tokens (PATs) for non-interactive / agent auth.

An agent presents `Authorization: Bearer dav_pat_<secret>`. Only the sha256 of
the token is stored; on each request `get_user()` calls `resolve()` to map a
valid token -> the RBAC account email it acts as, and the normal RBAC applies.

A valid-token cache ({token_hash: (email, expires_epoch)}) is loaded from the DB
at boot and refreshed on mint/revoke — mirroring the `_approved_lower` pattern in
main.py — so the per-request check stays SYNC + fast and survives brief DB
downtime. Revocation = set revoked_at + refresh the cache.

Wiring (main.py), 4 hooks — see the integration note:
  1. MIGRATE_022_PATH + apply it alongside the other migrate_*.sql
  2. `import api_tokens` ; at startup: `await api_tokens.load_cache(pool)`
  3. in get_user(), right after the `_service_token_ok` check:
         pat_email = api_tokens.resolve(request.headers.get("Authorization", ""))
         if pat_email:
             return _canonical_identity(pat_email)
  4. the three /api/tokens endpoints (mint / list / revoke)
"""
import time
import hashlib
import secrets
import logging
from typing import Optional

log = logging.getLogger("dav-review-api.api_tokens")

TOKEN_PREFIX = "dav_pat_"

# token_hash -> (email, expires_epoch)  ; expires_epoch == 0.0 means "never"
_cache: dict[str, tuple[str, float]] = {}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate() -> tuple[str, str]:
    """Return (plaintext_token, token_hash). Plaintext is shown to the user once."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, _hash(token)


def resolve(authorization_header: str) -> Optional[str]:
    """SYNC. Given an Authorization header value, return the email the token acts
    as if it is a valid, cached, unexpired PAT; else None. Safe to call on every
    request (in-memory, constant-ish time)."""
    if not authorization_header:
        return None
    parts = authorization_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    tok = parts[1].strip()
    if not tok.startswith(TOKEN_PREFIX):
        return None
    ent = _cache.get(_hash(tok))
    if not ent:
        return None
    email, exp = ent
    if exp and exp < time.time():
        return None
    return email


async def load_cache(pool) -> int:
    """(Re)load the active-token cache from the DB. Call at boot + after any
    mint/revoke. Returns the number of active tokens cached."""
    global _cache
    new: dict[str, tuple[str, float]] = {}
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT token_hash, email, extract(epoch FROM expires_at) AS exp "
                "FROM api_tokens "
                "WHERE revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())")
    except Exception as e:  # keep last-known-good on a transient DB error
        log.warning("api_tokens.load_cache failed, keeping cache (%d): %s", len(_cache), e)
        return len(_cache)
    for r in rows:
        new[r["token_hash"]] = (r["email"], float(r["exp"]) if r["exp"] else 0.0)
    _cache = new
    log.info("api_tokens: cached %d active token(s)", len(new))
    return len(new)


async def mint(pool, email: str, label: str, created_by: str,
               expires_at=None) -> str:
    """Create a token for `email`. Returns the PLAINTEXT token (show once)."""
    token, h = generate()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO api_tokens (email, token_hash, label, created_by, expires_at) "
            "VALUES ($1,$2,$3,$4,$5)",
            email.strip().lower(), h, label or "", created_by or "", expires_at)
    await load_cache(pool)
    return token


async def listing(pool, email: Optional[str] = None) -> list[dict]:
    """List tokens (metadata only — never the secret)."""
    q = ("SELECT id, email, label, created_by, created_at, last_used_at, "
         "expires_at, revoked_at FROM api_tokens")
    args: list = []
    if email:
        q += " WHERE email=$1"
        args = [email.strip().lower()]
    q += " ORDER BY created_at DESC"
    async with pool.acquire() as conn:
        rows = await conn.fetch(q, *args)
    return [dict(r) for r in rows]


async def revoke(pool, token_id: int) -> bool:
    async with pool.acquire() as conn:
        res = await conn.execute(
            "UPDATE api_tokens SET revoked_at=now() "
            "WHERE id=$1 AND revoked_at IS NULL", token_id)
    await load_cache(pool)
    return res.rstrip().endswith("1")  # asyncpg returns "UPDATE <n>"
