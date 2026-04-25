"""DCM Review Console API.

Thin FastAPI over Postgres. Auth is expected to be terminated upstream
(oauth-proxy sidecar), which injects X-Forwarded-User / X-Forwarded-Email.

Corpus seed modes (env: CORPUS_MODE):
  directory (default)  walk CORPUS_DIR, include/exclude filters applied
  file                 read CORPUS_PATH as a JSON array [{path, content}]
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .corpus_loader import walk_corpus, parse_patterns
from . import validations
from . import sources

log = logging.getLogger("dav-review-api")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())

DB_DSN = os.environ["DATABASE_URL"]
CORPUS_MODE = os.environ.get("CORPUS_MODE", "directory").lower()
CORPUS_DIR = os.environ.get("CORPUS_DIR", "/data/repo")
CORPUS_PATH = os.environ.get("CORPUS_PATH", "/etc/dav-review/corpus.json")
CORPUS_INCLUDE = parse_patterns(os.environ.get("CORPUS_INCLUDE"))
CORPUS_EXCLUDE = parse_patterns(os.environ.get("CORPUS_EXCLUDE"))
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
ANON_REVIEWER = os.environ.get("ANONYMOUS_REVIEWER", "anonymous")
ALLOW_ANON_WRITES = os.environ.get("ALLOW_ANON_WRITES", "false").lower() == "true"

STATUSES = {"unreviewed", "in-review", "needs-work", "approved", "stale"}

pool: Optional[asyncpg.Pool] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    log.info("Connecting to Postgres...")
    pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=8, command_timeout=30)
    async with pool.acquire() as conn:
        log.info("Applying schema...")
        await conn.execute(SCHEMA_PATH.read_text())
        await _seed_corpus(conn)
    log.info("Ready.")
    yield
    await pool.close()


async def _upsert_file(conn: asyncpg.Connection, path: str, content: str) -> None:
    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    size = len(content.encode("utf-8"))
    parts = path.split("/")
    folder = "/".join(parts[:-1]) if len(parts) > 1 else "/"
    await conn.execute(
        """
        INSERT INTO files(path, content, content_sha256, size_bytes, folder,
                          first_seen_at, last_seen_at)
        VALUES ($1, $2, $3, $4, $5, now(), now())
        ON CONFLICT (path) DO UPDATE SET
          content = EXCLUDED.content,
          content_sha256 = EXCLUDED.content_sha256,
          size_bytes = EXCLUDED.size_bytes,
          last_seen_at = now()
        """,
        path, content, sha, size, folder,
    )


async def _seed_corpus(conn: asyncpg.Connection) -> None:
    if CORPUS_MODE == "directory":
        root = Path(CORPUS_DIR)
        if not root.exists():
            log.warning("CORPUS_DIR %s does not exist; skipping seed", CORPUS_DIR)
            return
        log.info("Seeding corpus from directory %s", CORPUS_DIR)
        n = 0
        for entry in walk_corpus(root, CORPUS_INCLUDE, CORPUS_EXCLUDE):
            await _upsert_file(conn, entry["path"], entry["content"])
            n += 1
        log.info("Seeded %d files from directory", n)
    elif CORPUS_MODE == "file":
        corpus_file = Path(CORPUS_PATH)
        if not corpus_file.exists():
            log.warning("CORPUS_PATH %s does not exist; skipping seed", CORPUS_PATH)
            return
        with corpus_file.open() as f:
            corpus = json.load(f)
        log.info("Seeding %d corpus files from %s", len(corpus), CORPUS_PATH)
        for entry in corpus:
            await _upsert_file(conn, entry["path"], entry["content"])
    else:
        log.error("Unknown CORPUS_MODE=%s; skipping seed", CORPUS_MODE)


app = FastAPI(title="DCM Review API", version="0.1.0", lifespan=lifespan)

_cors = os.environ.get("CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors.split(",")] if _cors else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_user(request: Request) -> str:
    user = (
        request.headers.get("X-Forwarded-User")
        or request.headers.get("X-Forwarded-Email")
        or request.headers.get("X-Auth-Request-User")
        or request.headers.get("X-Auth-Request-Email")
    )
    if user:
        return user
    if ALLOW_ANON_WRITES:
        return ANON_REVIEWER
    raise HTTPException(status_code=401, detail="reviewer identity not provided")


# ------------------------- Models -------------------------


class ReviewIn(BaseModel):
    file_path: str
    status: str = Field(..., description="one of the allowed status values")
    notes: Optional[str] = ""


class HandoffRequest(BaseModel):
    file_paths: list[str]
    title: str = "DCM Corpus Review — Handoff"
    action: str = (
        "Please review the following files against the current DCM V1 "
        "architecture. Identify gaps, stale references, inconsistencies with "
        "the Data / Provider / Policy abstractions, and recommend concrete "
        "updates or deprecations."
    )
    include_content: bool = True
    include_notes: bool = True


class SelfTestRunIn(BaseModel):
    branch: Optional[str] = None
    commit_sha: Optional[str] = None
    inference_endpoint: Optional[str] = None
    test_count: Optional[str] = None


class SourceApplyIn(BaseModel):
    repo_url: str = Field(..., min_length=1, max_length=512)
    repo_branch: str = Field(..., min_length=1, max_length=256)


# ------------------------- Probes -------------------------


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/readyz")
async def readyz():
    if pool is None:
        raise HTTPException(503, "pool not initialized")
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"ok": True}


# ------------------------- Identity -------------------------


@app.get("/api/me")
async def me(request: Request):
    try:
        return {"reviewer": get_user(request), "authenticated": True}
    except HTTPException:
        return {"reviewer": None, "authenticated": False}


# ------------------------- Corpus -------------------------


@app.get("/api/corpus")
async def list_corpus():
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
              f.path, f.folder, f.size_bytes, f.content_sha256,
              fcs.status         AS status,
              fcs.reviewer       AS latest_reviewer,
              fcs.reviewed_at    AS latest_reviewed_at,
              (SELECT COUNT(*) FROM review_current rc WHERE rc.file_path = f.path)
                                 AS review_count,
              EXISTS(
                SELECT 1 FROM review_current rc
                WHERE rc.file_path = f.path
                  AND rc.file_sha256_at_review IS DISTINCT FROM f.content_sha256
              )                  AS has_drift
            FROM files f
            LEFT JOIN file_current_status fcs ON fcs.file_path = f.path
            ORDER BY f.path
            """
        )
        return [
            {
                **dict(r),
                "latest_reviewed_at": r["latest_reviewed_at"].isoformat()
                if r["latest_reviewed_at"] else None,
            }
            for r in rows
        ]


@app.get("/api/corpus/{file_path:path}")
async def get_file(file_path: str):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM files WHERE path = $1", file_path)
        if not row:
            raise HTTPException(404, "file not found")
        reviews = await conn.fetch(
            """
            SELECT reviewer, status, notes, reviewed_at, file_sha256_at_review,
                   (file_sha256_at_review IS DISTINCT FROM $2) AS is_drifted
            FROM review_current
            WHERE file_path = $1
            ORDER BY reviewed_at DESC
            """,
            file_path, row["content_sha256"],
        )
        return {
            "path": row["path"],
            "content": row["content"],
            "content_sha256": row["content_sha256"],
            "size_bytes": row["size_bytes"],
            "folder": row["folder"],
            "first_seen_at": row["first_seen_at"].isoformat(),
            "last_seen_at": row["last_seen_at"].isoformat(),
            "reviews": [
                {**dict(r), "reviewed_at": r["reviewed_at"].isoformat()}
                for r in reviews
            ],
        }


# ------------------------- Reviews -------------------------


@app.post("/api/reviews")
async def post_review(payload: ReviewIn, request: Request):
    if payload.status not in STATUSES:
        raise HTTPException(400, f"invalid status; must be one of {sorted(STATUSES)}")
    reviewer = get_user(request)
    async with pool.acquire() as conn:
        file_row = await conn.fetchrow(
            "SELECT content_sha256 FROM files WHERE path = $1", payload.file_path
        )
        if not file_row:
            raise HTTPException(404, "file not found")
        existing = await conn.fetchval(
            "SELECT 1 FROM review_current WHERE file_path = $1 AND reviewer = $2",
            payload.file_path, reviewer,
        )
        action = "update" if existing else "review"
        await conn.execute(
            """
            INSERT INTO review_events(
              file_path, reviewer, action, status, notes, file_sha256_at_review
            ) VALUES ($1, $2, $3, $4, $5, $6)
            """,
            payload.file_path, reviewer, action, payload.status,
            payload.notes or "", file_row["content_sha256"],
        )
    return {"ok": True, "action": action, "reviewer": reviewer}


@app.delete("/api/reviews/{file_path:path}")
async def clear_review(file_path: str, request: Request):
    reviewer = get_user(request)
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM review_current WHERE file_path = $1 AND reviewer = $2",
            file_path, reviewer,
        )
        if not exists:
            return {"ok": True, "noop": True}
        await conn.execute(
            "INSERT INTO review_events(file_path, reviewer, action) "
            "VALUES ($1, $2, 'clear')",
            file_path, reviewer,
        )
    return {"ok": True}


# ------------------------- History -------------------------


@app.get("/api/history")
async def history(
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    reviewer: Optional[str] = None,
    file_path: Optional[str] = None,
):
    clauses, args, argnum = [], [], 0

    def add_clause(sql: str, val):
        nonlocal argnum
        argnum += 1
        clauses.append(sql.format(argnum))
        args.append(val)

    if reviewer:
        add_clause("reviewer = ${}", reviewer)
    if file_path:
        add_clause("file_path = ${}", file_path)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, file_path, reviewer, action, status, notes, created_at
            FROM review_events
            {where}
            ORDER BY created_at DESC
            LIMIT ${argnum + 1} OFFSET ${argnum + 2}
            """,
            *args, limit, offset,
        )
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM review_events {where}", *args
        )
    return {
        "total": total,
        "events": [
            {**dict(r), "created_at": r["created_at"].isoformat()} for r in rows
        ],
    }


# ------------------------- Dashboard -------------------------


@app.get("/api/dashboard")
async def dashboard():
    async with pool.acquire() as conn:
        summary = await conn.fetchrow(
            """
            WITH fcs AS (SELECT * FROM file_current_status)
            SELECT
              (SELECT COUNT(*) FROM files)                          AS total,
              (SELECT COUNT(*) FROM fcs WHERE status='approved')    AS approved,
              (SELECT COUNT(*) FROM fcs WHERE status='in-review')   AS in_review,
              (SELECT COUNT(*) FROM fcs WHERE status='needs-work')  AS needs_work,
              (SELECT COUNT(*) FROM fcs WHERE status='stale')       AS stale,
              (SELECT COUNT(*) FROM files)
                - (SELECT COUNT(*) FROM fcs)                        AS unreviewed,
              (SELECT COUNT(*) FROM review_drift WHERE is_drifted)  AS drifted_reviews,
              (SELECT COUNT(DISTINCT reviewer) FROM review_events)  AS reviewers
            """
        )
        by_folder = await conn.fetch(
            """
            SELECT
              f.folder,
              COUNT(*)                                                       AS total,
              COUNT(*) FILTER (WHERE fcs.status='approved')                  AS approved,
              COUNT(*) FILTER (WHERE fcs.status='in-review')                 AS in_review,
              COUNT(*) FILTER (WHERE fcs.status='needs-work')                AS needs_work,
              COUNT(*) FILTER (WHERE fcs.status='stale')                     AS stale,
              COUNT(*) FILTER (WHERE fcs.status IS NULL)                     AS unreviewed
            FROM files f
            LEFT JOIN file_current_status fcs ON fcs.file_path = f.path
            GROUP BY f.folder
            ORDER BY f.folder
            """
        )
        recent = await conn.fetch(
            """
            SELECT file_path, reviewer, action, status, created_at
            FROM review_events
            ORDER BY created_at DESC
            LIMIT 10
            """
        )
        reviewers = await conn.fetch(
            """
            SELECT reviewer,
                   COUNT(*) AS events,
                   MAX(created_at) AS last_active
            FROM review_events
            GROUP BY reviewer
            ORDER BY last_active DESC
            LIMIT 20
            """
        )
    return {
        "summary": dict(summary) if summary else {},
        "by_folder": [dict(r) for r in by_folder],
        "recent": [
            {**dict(r), "created_at": r["created_at"].isoformat()} for r in recent
        ],
        "reviewers": [
            {**dict(r), "last_active": r["last_active"].isoformat()}
            for r in reviewers
        ],
    }


# ------------------------- Handoff -------------------------


@app.post("/api/handoff")
async def build_handoff(req: HandoffRequest):
    if not req.file_paths:
        raise HTTPException(400, "file_paths is empty")
    async with pool.acquire() as conn:
        files = await conn.fetch(
            """
            SELECT f.*, fcs.status, fcs.reviewer, fcs.reviewed_at
            FROM files f
            LEFT JOIN file_current_status fcs ON fcs.file_path = f.path
            WHERE f.path = ANY($1::text[])
            ORDER BY f.path
            """,
            req.file_paths,
        )
        notes_rows = await conn.fetch(
            """
            SELECT file_path, reviewer, status, notes, reviewed_at
            FROM review_current
            WHERE file_path = ANY($1::text[])
            ORDER BY file_path, reviewed_at DESC
            """,
            req.file_paths,
        )
    notes_by_path: dict[str, list] = {}
    for r in notes_rows:
        notes_by_path.setdefault(r["file_path"], []).append(dict(r))

    from datetime import datetime
    lines = [f"# {req.title}", ""]
    lines.append(f"**Generated:** {datetime.utcnow().isoformat()}Z  ")
    lines.append(f"**Files:** {len(files)}  ")
    lines.append("")
    lines.append("## Context")
    lines.append("")
    lines.append(
        "This handoff originates from the DCM Review Console. The files below "
        "have been reviewed against the DCM (Data Center Management) "
        "architecture — a Red Hat FlightPath framework for sovereign private "
        "cloud management, built on the **Data**, **Provider**, and **Policy** "
        "abstractions connected by a policy-driven event loop."
    )
    lines.append("")
    lines.append("## Requested action")
    lines.append("")
    lines.append(req.action)
    lines.append("")
    lines.append("## Files under review")
    lines.append("")
    for f in files:
        lines.append("---")
        lines.append("")
        lines.append(f"### `{f['path']}`")
        lines.append("")
        status = f["status"] or "unreviewed"
        line = f"**Status:** {status}"
        if f["reviewer"]:
            line += f" · {f['reviewer']}"
        if f["reviewed_at"]:
            line += f" · {f['reviewed_at'].isoformat()}"
        lines.append(line)
        lines.append("")
        if req.include_notes:
            file_notes = notes_by_path.get(f["path"], [])
            for n in file_notes:
                if n["notes"]:
                    lines.append(f"**Notes from {n['reviewer']} ({n['status']}):**")
                    lines.append("")
                    for nl in n["notes"].split("\n"):
                        lines.append(f"> {nl}")
                    lines.append("")
        if req.include_content:
            lines.append("**Content:**")
            lines.append("")
            lines.append("```")
            lines.append(f["content"])
            lines.append("```")
            lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"*End of handoff. {len(files)} file(s) packaged.*")
    markdown = "\n".join(lines)
    return {"markdown": markdown, "length": len(markdown), "files": len(files)}


# ------------------------- Self-Test Trigger -------------------------


@app.get("/api/self-test/status")
async def runs_status():
    """Is the self-test trigger feature available? Used by UI to hide tab."""
    return {
        "enabled": validations.ENABLED,
        "available": validations.is_available(),
        "pipeline_name": validations.PIPELINE_NAME,
        "namespace": validations.NAMESPACE,
        "default_branch": validations.DEFAULT_BRANCH,
    }


@app.post("/api/self-test/run")
async def self_test_run(payload: SelfTestRunIn, request: Request):
    if not validations.ENABLED:
        raise HTTPException(403, "self-test trigger disabled")
    reviewer = get_user(request)
    try:
        result = validations.trigger_run(
            triggered_by=reviewer,
            branch=payload.branch,
            commit_sha=payload.commit_sha,
            inference_endpoint=payload.inference_endpoint,
            test_count=payload.test_count,
        )
        return {"ok": True, "pipelinerun": result}
    except Exception as e:
        log.exception("self-test trigger failed")
        raise HTTPException(500, f"trigger failed: {e}")


@app.get("/api/self-test/runs")
async def self_test_runs(limit: int = Query(20, ge=1, le=100)):
    if not validations.ENABLED:
        return {"runs": [], "enabled": False}
    try:
        runs = validations.list_recent(limit=limit)
        return {"runs": runs, "enabled": True}
    except Exception as e:
        log.exception("list self-test runs failed")
        raise HTTPException(500, f"list failed: {e}")


# ------------------------- Sourcing -------------------------


@app.get("/api/sources")
async def sources_state():
    """Return current state of both spec and corpus sourcing."""
    try:
        if not sources.is_available():
            raise HTTPException(
                503, "sources not available (ConfigMap or RBAC missing)"
            )
        return {"sources": sources.get_all_sources_state()}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("sources state read failed")
        raise HTTPException(500, f"read failed: {e}")


@app.get("/api/sources/branches")
async def sources_branches(repo_url: str = Query(..., min_length=1)):
    """List branches for a GitHub repo. 5-minute in-memory cache."""
    try:
        branches = sources.list_branches(repo_url)
        return {"repo_url": repo_url, "branches": branches}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.exception("branch listing failed")
        raise HTTPException(500, f"listing failed: {e}")


@app.post("/api/sources/{kind}")
async def sources_apply(kind: str, payload: SourceApplyIn, request: Request):
    """Apply a new repo+branch to spec or corpus sourcing.

    Patches the ConfigMap, mirrors annotations to the Deployment, and
    triggers a rolling restart. Returns the resulting state including
    rollout progress.
    """
    if kind not in sources.SOURCES:
        raise HTTPException(400, f"unknown source kind: {kind}")
    reviewer = get_user(request)
    try:
        new_state = sources.apply_source(
            kind=kind,
            repo_url=payload.repo_url,
            repo_branch=payload.repo_branch,
            applied_by=reviewer,
        )
        return {"ok": True, "state": new_state}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.exception("sources apply failed")
        raise HTTPException(500, f"apply failed: {e}")


@app.get("/api/sources/{kind}")
async def sources_kind_state(kind: str):
    """Return current state for a single source kind (poll-friendly)."""
    if kind not in sources.SOURCES:
        raise HTTPException(400, f"unknown source kind: {kind}")
    try:
        return {"state": sources.get_source_state(kind)}
    except Exception as e:
        log.exception("source kind state read failed")
        raise HTTPException(500, f"read failed: {e}")
