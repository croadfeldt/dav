"""Audit log — who did what + auth events. See migrate_018_audit_log.sql.

Mutating API calls are auto-captured by a dedicated middleware (fire-and-forget,
so audit never adds request latency). Auth events (login / logout / session
timeout) are recorded explicitly. Reads are not audited. Failures here are
swallowed — auditing must never break a request.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

log = logging.getLogger("dav.audit")

# Collapse ids/uuids/hashes in a path so the action label is stable.
_ID_RE = re.compile(r"/(?:[0-9a-fA-F-]{8,}|\d+)(?=/|$)")

# Non-GET traffic to these prefixes is noise, not a user action.
_SKIP_PREFIXES = ("/api/presence", "/healthz", "/readyz", "/livez", "/metrics",
                  "/api/runs/status", "/api/auth")  # auth events recorded explicitly


def action_label(method: str, path: str) -> str:
    """Stable, readable action from method + path (ids → {id})."""
    collapsed = _ID_RE.sub("/{id}", path or "")
    return f"{(method or '').lower()}:{collapsed}"


def should_audit(method: str, path: str) -> bool:
    """True for mutating API calls worth recording."""
    if method in ("GET", "HEAD", "OPTIONS"):
        return False
    if not (path or "").startswith("/api/"):
        return False
    return not path.startswith(_SKIP_PREFIXES)


def outcome_for(status_code: int) -> str:
    if status_code >= 500:
        return "error"
    if status_code in (401, 403):
        return "denied"
    if status_code >= 400:
        return "failure"
    return "success"


async def record(pool, *, action: str, actor: Optional[str] = None,
                 actor_source: Optional[str] = None, method: Optional[str] = None,
                 path: Optional[str] = None, object_type: Optional[str] = None,
                 object_id: Optional[str] = None, project_id: Optional[int] = None,
                 outcome: str = "success", status_code: Optional[int] = None,
                 ip: Optional[str] = None, user_agent: Optional[str] = None,
                 summary: Optional[str] = None, detail: Optional[dict] = None) -> None:
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO audit_log
                   (actor, actor_source, action, method, path, object_type, object_id,
                    project_id, outcome, status_code, ip, user_agent, summary, detail)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)""",
                actor, actor_source, action, method, path, object_type, object_id,
                project_id, outcome, status_code, ip, (user_agent or "")[:512],
                summary, json.dumps(detail or {}),
            )
    except Exception:
        log.exception("audit record failed (non-fatal)")


async def query(conn, *, actor: Optional[str] = None, action: Optional[str] = None,
                outcome: Optional[str] = None, since=None, limit: int = 200,
                before_id: Optional[int] = None) -> list[dict]:
    where: list[str] = []
    args: list = []

    def add(cond: str, val) -> None:
        args.append(val)
        where.append(cond.format(len(args)))

    if actor:
        add("lower(actor) LIKE ${}", f"%{actor.lower()}%")
    if action:
        add("action LIKE ${}", f"%{action}%")
    if outcome:
        add("outcome = ${}", outcome)
    if since is not None:
        add("ts >= ${}", since)
    if before_id:
        add("id < ${}", before_id)

    sql = ("SELECT id, ts, actor, actor_source, action, method, path, project_id, "
           "outcome, status_code, ip, summary FROM audit_log")
    if where:
        sql += " WHERE " + " AND ".join(where)
    args.append(min(int(limit or 200), 1000))
    sql += f" ORDER BY id DESC LIMIT ${len(args)}"
    rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]
