"""PR comments — DB CRUD + GitHub poller.

The poller runs as a background asyncio task, wakes every
PR_COMMENTS_POLL_INTERVAL_SECONDS (default 300), enumerates all
managed_repos rows with role='issue-source', and ingests open-PR
comments from each into pr_comments. Upsert by (repo_uuid,
github_comment_id, github_comment_type) so re-polling is idempotent.

M6 (webhook receiver) will push individual comments via the same
upsert path; the poller becomes a fallback that catches anything
webhooks missed.

The Inbox API (M7) reads from pr_comments. The auto-draft endpoint
reuses the UC Assist plumbing to LLM-draft a UC YAML from a
comment's body + spec context.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import httpx

from . import github_client

log = logging.getLogger("dav-review-api.pr_comments")

ISSUE_SOURCE_ROLE = "issue-source"
COMMENT_TYPES = ("issue_comment", "pull_request_review_comment")

# Poll cadence. 300s (5 min) is a reasonable balance: catches comments
# within 5 min of being posted (worst case), uses ~12 GH API calls per
# repo per hour (well under the 5000/hr authenticated quota even with
# many repos and busy PRs).
POLL_INTERVAL_SECONDS = int(os.environ.get("PR_COMMENTS_POLL_INTERVAL_SECONDS", "300"))

# Initial delay before the first poll fires after startup — let migrations,
# seeding, and other lifespan work settle before hitting the network.
POLL_STARTUP_DELAY_SECONDS = int(os.environ.get("PR_COMMENTS_POLL_STARTUP_DELAY_SECONDS", "30"))

VALID_STATUSES = {"new", "dismissed", "drafted_to_uc"}


# ------------------------- Status constants -------------------------


def _parse_ts(s: Optional[str]) -> Optional[datetime]:
    """GitHub timestamps are RFC 3339 with Z. asyncpg wants aware datetimes."""
    if not s:
        return None
    # 2024-05-27T14:32:18Z → 2024-05-27T14:32:18+00:00
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        log.warning("github: cannot parse timestamp %r", s)
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------- DB helpers (read) -------------------------


def _row_to_dict(row: asyncpg.Record) -> dict:
    return {
        "uuid": str(row["uuid"]),
        "repo_uuid": str(row["repo_uuid"]),
        "tenant_id": row["tenant_id"],
        "github_comment_id": row["github_comment_id"],
        "github_comment_type": row["github_comment_type"],
        "pr_number": row["pr_number"],
        "pr_title": row["pr_title"],
        "pr_url": row["pr_url"],
        "author_login": row["author_login"],
        "author_url": row["author_url"],
        "body": row["body"],
        "comment_url": row["comment_url"],
        "status": row["status"],
        "status_changed_at": row["status_changed_at"].isoformat() if row["status_changed_at"] else None,
        "status_changed_by": row["status_changed_by"],
        "github_created_at": row["github_created_at"].isoformat(),
        "github_updated_at": row["github_updated_at"].isoformat(),
        "fetched_at": row["fetched_at"].isoformat(),
        "ingestion_source": row["ingestion_source"],
    }


async def list_comments(
    conn: asyncpg.Connection,
    *,
    status: Optional[str] = None,
    repo_uuid: Optional[str] = None,
    tenant_id: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    """List ingested PR comments with optional filters. Newest first."""
    where = []
    args: list = []
    if status is not None:
        if status not in VALID_STATUSES:
            raise ValueError(f"unknown status {status!r}; valid: {sorted(VALID_STATUSES)}")
        args.append(status)
        where.append(f"status = ${len(args)}")
    if repo_uuid is not None:
        args.append(repo_uuid)
        where.append(f"repo_uuid::text = ${len(args)}")
    if tenant_id is not None:
        args.append(tenant_id)
        where.append(f"tenant_id = ${len(args)}")
    args.append(limit)
    where_clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = await conn.fetch(
        f"SELECT * FROM pr_comments{where_clause} "
        f"ORDER BY github_updated_at DESC LIMIT ${len(args)}",
        *args,
    )
    return [_row_to_dict(r) for r in rows]


async def get_comment(conn: asyncpg.Connection, uuid: str) -> Optional[dict]:
    row = await conn.fetchrow(
        "SELECT * FROM pr_comments WHERE uuid::text = $1", uuid,
    )
    return _row_to_dict(row) if row else None


async def set_status(
    conn: asyncpg.Connection, uuid: str, new_status: str, changed_by: str,
) -> Optional[dict]:
    if new_status not in VALID_STATUSES:
        raise ValueError(f"unknown status {new_status!r}; valid: {sorted(VALID_STATUSES)}")
    row = await conn.fetchrow(
        "UPDATE pr_comments SET status = $1, status_changed_at = now(), "
        "status_changed_by = $2 WHERE uuid::text = $3 RETURNING *",
        new_status, changed_by, uuid,
    )
    return _row_to_dict(row) if row else None


# ------------------------- DB helpers (write — used by poller + webhook) -------------------------


async def upsert_comment(
    conn: asyncpg.Connection,
    *,
    repo_uuid: str,
    tenant_id: str,
    github_comment_id: int,
    github_comment_type: str,
    pr_number: int,
    pr_title: Optional[str],
    pr_url: Optional[str],
    author_login: str,
    author_url: Optional[str],
    body: str,
    comment_url: Optional[str],
    github_created_at: datetime,
    github_updated_at: datetime,
    ingestion_source: str,
) -> tuple[str, bool]:
    """Upsert a single comment. Returns (uuid, inserted).

    ON CONFLICT updates only the fields that can change at the source
    (body — comments are editable on GitHub — and github_updated_at).
    Status, status_changed_at, status_changed_by are NEVER touched on
    upsert: operator-curation state survives re-polls.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO pr_comments (
            repo_uuid, tenant_id,
            github_comment_id, github_comment_type,
            pr_number, pr_title, pr_url,
            author_login, author_url, body, comment_url,
            github_created_at, github_updated_at,
            ingestion_source
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
        )
        ON CONFLICT (repo_uuid, github_comment_id, github_comment_type)
        DO UPDATE SET
            body              = EXCLUDED.body,
            pr_title          = EXCLUDED.pr_title,
            github_updated_at = EXCLUDED.github_updated_at,
            fetched_at        = now()
        RETURNING uuid, (xmax = 0) AS inserted
        """,
        repo_uuid, tenant_id,
        github_comment_id, github_comment_type,
        pr_number, pr_title, pr_url,
        author_login, author_url, body, comment_url,
        github_created_at, github_updated_at,
        ingestion_source,
    )
    return str(row["uuid"]), bool(row["inserted"])


async def _record_poll_state(
    conn: asyncpg.Connection, repo_uuid: str, *,
    started_at: datetime, finished_at: datetime, ok: bool,
    error: Optional[str], comments_seen: int,
    newest_seen_updated_at: Optional[datetime],
) -> None:
    await conn.execute(
        """
        INSERT INTO pr_comment_poll_state (
            repo_uuid, last_poll_started_at, last_poll_finished_at,
            last_poll_ok, last_poll_error, comments_seen_total,
            newest_seen_updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (repo_uuid) DO UPDATE SET
            last_poll_started_at  = EXCLUDED.last_poll_started_at,
            last_poll_finished_at = EXCLUDED.last_poll_finished_at,
            last_poll_ok          = EXCLUDED.last_poll_ok,
            last_poll_error       = EXCLUDED.last_poll_error,
            comments_seen_total   = pr_comment_poll_state.comments_seen_total + EXCLUDED.comments_seen_total,
            newest_seen_updated_at = GREATEST(
                COALESCE(pr_comment_poll_state.newest_seen_updated_at, EXCLUDED.newest_seen_updated_at),
                COALESCE(EXCLUDED.newest_seen_updated_at, pr_comment_poll_state.newest_seen_updated_at)
            )
        """,
        repo_uuid, started_at, finished_at, ok, error,
        comments_seen, newest_seen_updated_at,
    )


# ------------------------- Poll one repo -------------------------


async def _poll_one_repo(
    conn: asyncpg.Connection, http: httpx.AsyncClient, repo: dict,
) -> dict:
    """Poll a single repo's open PRs for comments. Returns a summary dict."""
    repo_uuid = repo["uuid"]
    tenant_id = repo.get("tenant_id") or "default"
    started_at = _now()

    parsed = github_client.parse_owner_repo(repo["repo_url"])
    if not parsed:
        msg = f"cannot parse owner/repo from {repo['repo_url']!r}"
        log.warning("poll: %s — %s", repo["namespace"], msg)
        await _record_poll_state(
            conn, repo_uuid, started_at=started_at, finished_at=_now(),
            ok=False, error=msg, comments_seen=0, newest_seen_updated_at=None,
        )
        return {"repo_namespace": repo["namespace"], "ok": False, "reason": msg, "comments_seen": 0}

    owner, repo_name = parsed
    seen = 0
    inserted = 0
    updated = 0
    newest_updated: Optional[datetime] = None
    error: Optional[str] = None
    ok = True

    try:
        prs = await github_client.list_open_pull_requests(http, owner, repo_name)
        log.info("poll: %s/%s — %d open PR(s)", owner, repo_name, len(prs))

        for pr in prs:
            pr_number = pr["number"]
            pr_title = pr.get("title") or ""
            pr_url = pr.get("html_url") or ""

            for ctype, fetcher in (
                ("issue_comment", github_client.list_issue_comments),
                ("pull_request_review_comment", github_client.list_review_comments),
            ):
                try:
                    comments = await fetcher(http, owner, repo_name, pr_number)
                except github_client.GitHubError as e:
                    log.warning(
                        "poll: %s PR#%d %s — %s",
                        repo["namespace"], pr_number, ctype, e,
                    )
                    continue

                for c in comments:
                    cid = c["id"]
                    body = c.get("body") or ""
                    if not body.strip():
                        # Skip empty comments (rare but legal on GitHub)
                        continue
                    author = c.get("user") or {}
                    created_at = _parse_ts(c.get("created_at"))
                    updated_at = _parse_ts(c.get("updated_at"))
                    if not created_at or not updated_at:
                        continue
                    seen += 1
                    if newest_updated is None or updated_at > newest_updated:
                        newest_updated = updated_at
                    _, ins = await upsert_comment(
                        conn,
                        repo_uuid=repo_uuid,
                        tenant_id=tenant_id,
                        github_comment_id=cid,
                        github_comment_type=ctype,
                        pr_number=pr_number,
                        pr_title=pr_title,
                        pr_url=pr_url,
                        author_login=author.get("login") or "unknown",
                        author_url=author.get("html_url"),
                        body=body,
                        comment_url=c.get("html_url"),
                        github_created_at=created_at,
                        github_updated_at=updated_at,
                        ingestion_source="poller",
                    )
                    if ins:
                        inserted += 1
                    else:
                        updated += 1

    except github_client.GitHubError as e:
        ok = False
        error = str(e)
        log.warning("poll: %s — %s", repo["namespace"], e)
    except Exception as e:
        ok = False
        error = f"{type(e).__name__}: {e}"
        log.exception("poll: %s — unexpected error", repo["namespace"])

    finished_at = _now()
    await _record_poll_state(
        conn, repo_uuid, started_at=started_at, finished_at=finished_at,
        ok=ok, error=error, comments_seen=seen,
        newest_seen_updated_at=newest_updated,
    )
    return {
        "repo_namespace": repo["namespace"],
        "ok": ok,
        "comments_seen": seen,
        "inserted": inserted,
        "updated": updated,
        "newest_updated_at": newest_updated.isoformat() if newest_updated else None,
        "error": error,
    }


# ------------------------- Poll all repos -------------------------


async def poll_all_issue_source_repos(pool: asyncpg.Pool) -> dict:
    """Snapshot the list of role=issue-source repos and poll each. Returns a
    summary suitable for logging or API exposure."""
    from . import repos as _repos
    async with pool.acquire() as conn:
        repos_list = await _repos.list_repos(conn, role=ISSUE_SOURCE_ROLE)

    if not repos_list:
        return {"polled": 0, "repos": [], "skipped_reason": "no repos with role=issue-source"}

    if not github_client.has_token():
        log.warning(
            "poll: GITHUB_TOKEN not set; anonymous quota (60 req/hr per IP) "
            "will be exhausted quickly. Set the dav-github-pat Secret for "
            "production polling."
        )

    results = []
    async with httpx.AsyncClient() as http:
        for repo in repos_list:
            async with pool.acquire() as conn:
                # One transaction per repo so a single failure doesn't
                # roll back the others. _poll_one_repo doesn't open a
                # txn explicitly; statements run autocommit.
                results.append(await _poll_one_repo(conn, http, repo))

    return {
        "polled": len(repos_list),
        "ok_count": sum(1 for r in results if r["ok"]),
        "fail_count": sum(1 for r in results if not r["ok"]),
        "repos": results,
    }


# ------------------------- Background loop -------------------------


async def poller_loop(pool: asyncpg.Pool) -> None:
    """Forever-loop poll task. Started in lifespan, cancelled on shutdown."""
    log.info(
        "pr_comments poller starting (interval=%ds, startup_delay=%ds, "
        "token_present=%s)",
        POLL_INTERVAL_SECONDS, POLL_STARTUP_DELAY_SECONDS,
        github_client.has_token(),
    )
    try:
        await asyncio.sleep(POLL_STARTUP_DELAY_SECONDS)
        while True:
            try:
                summary = await poll_all_issue_source_repos(pool)
                if summary.get("polled"):
                    log.info(
                        "pr_comments poll: %d repo(s) — ok=%d fail=%d total_comments_seen=%d",
                        summary["polled"], summary["ok_count"], summary["fail_count"],
                        sum(r.get("comments_seen", 0) for r in summary["repos"]),
                    )
                else:
                    log.debug("pr_comments poll: %s", summary.get("skipped_reason"))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Never let the loop die from a single failed pass
                log.exception("pr_comments poll: unexpected error (%s); continuing", e)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        log.info("pr_comments poller stopping")
        return
