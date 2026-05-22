"""DAV Console API.

Thin FastAPI over Postgres + Kubernetes + workspace PVC.
Auth is terminated upstream (oauth-proxy sidecar) which injects
X-Forwarded-User / X-Forwarded-Email headers.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import tarfile
import unicodedata
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import asyncpg
import yaml as _yaml
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .corpus_loader import walk_corpus, parse_patterns
from . import validations
from . import sources
from . import metrics
from . import results as _results
from . import uc_assist

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
VALID_MODES = {"verification", "reproduce", "explore"}

UC_STATES = {"draft", "ready", "in_review", "approved", "deprecated"}

# Curated run categories surfaced in the New Run modal. Sticking to a
# closed set so we can build filter chips + analytics later without
# normalisation grief. Add entries here; UI picks them up automatically.
RUN_CATEGORIES = [
    "regression",
    "baseline",
    "exploration",
    "production-validation",
    "debug",
    "ad-hoc",
]
VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft":      {"ready"},
    "ready":      {"in_review", "draft"},
    "in_review":  {"ready", "approved"},
    "approved":   {"in_review", "deprecated"},
    "deprecated": {"draft"},
}

pool: Optional[asyncpg.Pool] = None


async def _finalizer_loop():
    """Background task: find run_sessions rows whose PipelineRun has reached
    terminal phase but stats were never finalized (user never opened the
    drawer post-completion). Trigger lazy finalization for them so the
    cluster kWh chip + per-run energy stats catch up.

    Runs every 60 s. Idempotent — once finalized_at is set, the row is
    skipped on subsequent passes.
    """
    import asyncio
    while True:
        try:
            await asyncio.sleep(60)
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT run_name FROM run_sessions "
                    "WHERE finalized_at IS NULL "
                    "AND created_at < now() - interval '2 minutes' "
                    "ORDER BY created_at DESC LIMIT 20"
                )
            for r in rows:
                try:
                    detail = validations.get_run_detail(r["run_name"])
                    if detail.get("phase") in TERMINAL_PHASES:
                        await _maybe_finalize_session(detail)
                except KeyError:
                    # PipelineRun expired/deleted before finalize; mark as
                    # finalized to stop trying. Use a sentinel value of
                    # phase='expired' so it's distinguishable from real
                    # finalizations.
                    try:
                        async with pool.acquire() as conn:
                            await conn.execute(
                                "UPDATE run_sessions SET phase='expired', finalized_at=now() WHERE run_name=$1",
                                r["run_name"],
                            )
                    except Exception:
                        pass
                except Exception as e:
                    log.info("background finalize for %s deferred: %s", r["run_name"], e)
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.warning("finalizer loop hiccup: %s", e)


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
    import asyncio
    finalizer_task = asyncio.create_task(_finalizer_loop())
    yield
    finalizer_task.cancel()
    try:
        await finalizer_task
    except Exception:
        pass
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


app = FastAPI(title="DAV Console API", version="0.8.3", lifespan=lifespan)

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
    title: str = "DAV Corpus Review — Handoff"
    action: str = (
        "Please review the following files against the current DAV architecture. "
        "Identify gaps, stale references, and recommend concrete updates."
    )
    include_content: bool = True
    include_notes: bool = True


class RunTriggerIn(BaseModel):
    mode: str = "verification"
    sample_count: Optional[int] = None
    corpus_subpath: Optional[str] = None
    corpus_repo_url: Optional[str] = None
    corpus_repo_branch: Optional[str] = None
    spec_repo_url: Optional[str] = None
    spec_repo_branch: Optional[str] = None
    inference_endpoint: Optional[str] = None
    inference_model: Optional[str] = None
    halt_on_error: bool = False
    # User-facing session metadata (persisted to run_sessions)
    name: str = ""
    description: str = ""
    category: str = "ad-hoc"
    tags: list[str] = []
    # Legacy params kept for backward compat with self-test UI
    branch: Optional[str] = None
    commit_sha: Optional[str] = None


class ManagedUCIn(BaseModel):
    yaml_content: str
    tags: list[str] = []


class LifecycleTransitionIn(BaseModel):
    to_state: str
    notes: Optional[str] = None


class SetIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""


class SetMemberIn(BaseModel):
    uc_uuid: str
    uc_source: str = "managed"
    uc_handle: Optional[str] = None
    uc_path: Optional[str] = None


class SetPromoteIn(BaseModel):
    from_state: str
    to_state: str
    notes: Optional[str] = None


class SourceApplyIn(BaseModel):
    # Repo sources (spec, corpus): both must be present.
    repo_url: Optional[str] = Field(None, max_length=512)
    repo_branch: Optional[str] = Field(None, max_length=256)
    # Inference source: both must be present.
    endpoint: Optional[str] = Field(None, max_length=512)
    model: Optional[str] = Field(None, max_length=256)


class InferenceValidateIn(BaseModel):
    endpoint: str = Field(..., min_length=1, max_length=512)
    model: str = Field(..., min_length=1, max_length=256)


# ------------------------- Helpers -------------------------


def _slugify(name: str) -> str:
    """Convert a name to a safe filesystem directory component."""
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^\w\s-]", "", name).strip()
    name = re.sub(r"[\s_-]+", "_", name)
    return name or "unnamed"


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


# ========================= RUNS =========================


@app.get("/api/runs")
async def list_runs(limit: int = Query(50, ge=1, le=200)):
    """List recent PipelineRuns, enriched with run_sessions metadata when available."""
    if not validations.ENABLED:
        return {"runs": [], "enabled": False}
    try:
        runs = validations.list_recent(limit=limit)
    except Exception as e:
        log.exception("list runs failed")
        raise HTTPException(500, f"list failed: {e}")
    # Bulk-fetch session rows by run_name; the table is small (one row per run)
    names = [r.get("name") for r in runs if r.get("name")]
    sessions_by_name: dict[str, dict] = {}
    if names:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT run_name, name, description, category, tags, "
                    "gpu_energy_joules, total_gen_tokens, total_prompt_tokens "
                    "FROM run_sessions WHERE run_name = ANY($1::text[])",
                    names,
                )
            for row in rows:
                sessions_by_name[row["run_name"]] = dict(row)
        except Exception as e:
            log.warning("list_runs session join failed: %s", e)
    for r in runs:
        s = sessions_by_name.get(r.get("name"))
        if s:
            r["session_name"] = s.get("name") or None
            r["category"] = s.get("category")
            r["gpu_energy_joules"] = s.get("gpu_energy_joules")
            r["total_gen_tokens"]  = s.get("total_gen_tokens")
            r["total_prompt_tokens"] = s.get("total_prompt_tokens")
    return {"runs": runs, "enabled": True}


def _resolve_run_params(payload: "RunTriggerIn") -> dict:
    """Fill missing pipeline params from current source ConfigMap state.

    Modal-supplied values take precedence; anything blank falls back to the
    spec/corpus/inference ConfigMaps. This makes Config-tab values the
    authoritative defaults for every run, instead of relying on Ansible-time
    pipeline defaults that drift from runtime state.
    """
    resolved = {
        "spec_repo_url": payload.spec_repo_url,
        "spec_repo_branch": payload.spec_repo_branch,
        "corpus_repo_url": payload.corpus_repo_url,
        "corpus_repo_branch": payload.corpus_repo_branch,
        "corpus_subpath": payload.corpus_subpath,
        "inference_endpoint": payload.inference_endpoint,
        "inference_model": payload.inference_model,
    }
    if not sources.is_available():
        return resolved
    try:
        spec_state = sources.get_source_state("spec")
        if not resolved["spec_repo_url"]:
            resolved["spec_repo_url"] = spec_state.get("repo_url")
        if not resolved["spec_repo_branch"]:
            resolved["spec_repo_branch"] = spec_state.get("repo_branch")
    except Exception as e:
        log.warning("could not read spec source: %s", e)
    try:
        corpus_state = sources.get_source_state("corpus")
        if not resolved["corpus_repo_url"]:
            resolved["corpus_repo_url"] = corpus_state.get("repo_url")
        if not resolved["corpus_repo_branch"]:
            resolved["corpus_repo_branch"] = corpus_state.get("repo_branch")
    except Exception as e:
        log.warning("could not read corpus source: %s", e)
    try:
        inf_state = sources.get_source_state("inference")
        if not resolved["inference_endpoint"]:
            resolved["inference_endpoint"] = inf_state.get("endpoint")
        if not resolved["inference_model"]:
            resolved["inference_model"] = inf_state.get("model")
    except Exception as e:
        log.info("inference source not available (likely not deployed yet): %s", e)
    # If the modal didn't pass a UC subpath, probe the cloned corpus tree.
    if not resolved["corpus_subpath"]:
        try:
            root = Path(CORPUS_DIR)
            for c in ("dav/use-cases", "use-cases"):
                if (root / c).is_dir():
                    resolved["corpus_subpath"] = c
                    break
        except Exception as e:
            log.warning("UC subpath probe failed: %s", e)
    return resolved


@app.post("/api/runs")
async def trigger_run(payload: RunTriggerIn, request: Request):
    """Trigger a new DAV pipeline run."""
    if not validations.ENABLED:
        raise HTTPException(403, "pipeline trigger disabled")
    if payload.mode not in VALID_MODES:
        raise HTTPException(400, f"invalid mode; must be one of {sorted(VALID_MODES)}")
    reviewer = get_user(request)
    if payload.category and payload.category not in RUN_CATEGORIES:
        raise HTTPException(400, f"invalid category; must be one of {RUN_CATEGORIES}")
    params = _resolve_run_params(payload)
    try:
        result = validations.trigger_run(
            triggered_by=reviewer,
            branch=payload.branch,
            commit_sha=payload.commit_sha,
            inference_endpoint=params["inference_endpoint"],
            inference_model=params["inference_model"],
            mode=payload.mode,
            sample_count=payload.sample_count,
            corpus_subpath=params["corpus_subpath"],
            corpus_repo_url=params["corpus_repo_url"],
            corpus_repo_branch=params["corpus_repo_branch"],
            spec_repo_url=params["spec_repo_url"],
            spec_repo_branch=params["spec_repo_branch"],
            halt_on_error=payload.halt_on_error,
        )
    except Exception as e:
        log.exception("run trigger failed")
        raise HTTPException(500, f"trigger failed: {e}")

    # Snapshot the vLLM token counters NOW so the run-detail drawer can
    # compute live "session" deltas that persist across page reloads.
    # Best-effort; if Prometheus is briefly unavailable, baseline stays NULL
    # and the drawer falls back to client-side delta computation.
    baseline_gen = baseline_prompt = None
    try:
        snap = await metrics.snapshot()
        if snap.get("available"):
            v = snap.get("vllm") or {}
            baseline_gen    = v.get("gen_tokens_total")
            baseline_prompt = v.get("prompt_tokens_total")
    except Exception as e:
        log.info("trigger: token-baseline snapshot failed (%s); session totals will start at zero", e)

    # Persist the run-session row (name + category + audit trail + baseline).
    # Failures here don't roll back the PipelineRun.
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO run_sessions
                   (run_name, name, description, category, tags, mode,
                    created_by, started_at,
                    baseline_gen_tokens, baseline_prompt_tokens)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, now(), $8, $9)""",
                result["name"], payload.name, payload.description,
                payload.category or "ad-hoc", payload.tags or [],
                payload.mode, reviewer,
                baseline_gen, baseline_prompt,
            )
    except Exception as e:
        log.warning("run_sessions insert failed for %s: %s", result.get("name"), e)

    return {"ok": True, "run": result, "resolved_params": params}


@app.get("/api/runs/status")
async def runs_status():
    """Capability check — is the pipeline trigger wired up?"""
    return {
        "enabled": validations.ENABLED,
        "available": validations.is_available(),
        "pipeline_name": validations.PIPELINE_NAME,
        "namespace": validations.NAMESPACE,
        "default_branch": validations.DEFAULT_BRANCH,
    }


@app.get("/api/runs/{name}/logs")
async def get_run_task_logs(
    name: str,
    task: str = Query(..., description="logical step name within the pipeline (e.g. 'run-corpus')"),
    tail: int = Query(200, ge=1, le=2000, description="number of trailing log lines"),
):
    """Return the tail of a specific TaskRun's pod logs.

    The run-detail UI uses this to surface why a Failed task failed.
    Falls back to logs from all containers if step-name container isn't found.
    """
    if not validations.ENABLED:
        raise HTTPException(403, "pipeline trigger disabled")
    try:
        result = validations.get_task_logs(run_name=name, step=task, tail=tail)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        log.exception("task logs fetch failed")
        raise HTTPException(500, f"logs failed: {e}")
    return result


TERMINAL_PHASES = {"Succeeded", "Failed", "Cancelled", "TimedOut"}


# Static routes must be declared before the catch-all /api/runs/{name}
# (FastAPI matches in source order — without this the "categories" path
# segment gets bound to the {name} parameter and returns 404).
@app.get("/api/runs/categories")
async def list_run_categories_v2():
    """Curated list of categories the New Run modal offers."""
    return {"categories": RUN_CATEGORIES}


@app.get("/api/runs/stats")
async def runs_stats():
    """Aggregate energy + token stats across all finalized runs.

    Used by the runs view header chip + the run-detail drawer's
    'context' line. Energy reported in kWh (joules / 3.6e6).
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT
                 COUNT(*)                                                AS total_runs,
                 COALESCE(SUM(gpu_energy_joules), 0) / 3600000.0          AS total_kwh,
                 COALESCE(SUM(gpu_energy_joules)
                          FILTER (WHERE completed_at > now() - interval '24 hours'), 0) / 3600000.0 AS last_24h_kwh,
                 COALESCE(SUM(gpu_energy_joules)
                          FILTER (WHERE completed_at > now() - interval '7 days'),    0) / 3600000.0 AS last_7d_kwh,
                 COALESCE(SUM(total_gen_tokens), 0)::BIGINT               AS total_gen_tokens,
                 COALESCE(SUM(total_prompt_tokens), 0)::BIGINT            AS total_prompt_tokens
               FROM run_sessions
               WHERE finalized_at IS NOT NULL"""
        )
    return {
        "total_runs":         int(row["total_runs"] or 0),
        "total_kwh":          float(row["total_kwh"] or 0.0),
        "last_24h_kwh":       float(row["last_24h_kwh"] or 0.0),
        "last_7d_kwh":        float(row["last_7d_kwh"] or 0.0),
        "total_gen_tokens":   int(row["total_gen_tokens"] or 0),
        "total_prompt_tokens": int(row["total_prompt_tokens"] or 0),
    }


@app.get("/api/runs/{name}/turns")
async def get_run_turns(
    name: str,
    file: Optional[str] = Query(None, description="specific turns file (e.g. <uuid>.seed-0.jsonl); when None, lists available files"),
    since: int = Query(0, ge=0, description="byte offset returned by previous call's next_offset"),
    max_records: int = Query(500, ge=1, le=2000),
):
    """List or tail the structured per-turn JSONL files for a PipelineRun.

    First call: omit `file` → returns {"files": [...]} sorted by mtime.
    Subsequent calls: pass `file` + `since` → returns delta records.
    """
    if not validations.ENABLED:
        raise HTTPException(403, "pipeline trigger disabled")
    # Resolve PipelineRun → workspace run_id via timestamp correlation
    try:
        detail = validations.get_run_detail(name)
    except KeyError:
        raise HTTPException(404, f"run {name!r} not found")
    started = detail.get("started_at") or detail.get("created_at")
    if not started or not _results.is_available():
        return {"files": [], "records": []}
    progress = _results.find_progress_near(started, tolerance_seconds=600)
    if not progress:
        return {"files": [], "records": [], "note": "no workspace run dir matches the PipelineRun start time"}
    run_id = progress.get("_run_dir")
    if not file:
        return {"run_id": run_id, "files": _results.list_turns_files(run_id)}
    res = _results.tail_turns(run_id, file, since_offset=since, max_records=max_records)
    res["run_id"] = run_id
    res["file"] = file
    return res


async def _maybe_finalize_session(detail: dict) -> Optional[dict]:
    """If the run is in a terminal phase and we haven't computed final stats
    yet, query Prometheus for energy/tokens and persist to run_sessions.

    Returns the (possibly-newly-finalized) session row, or None if no session
    row exists for this run.
    """
    name = detail.get("name"); phase = detail.get("phase")
    if not name:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM run_sessions WHERE run_name=$1", name)
    if not row:
        return None
    out = dict(row)
    for k in ("created_at", "started_at", "completed_at", "finalized_at"):
        if out.get(k):
            out[k] = out[k].isoformat()

    # Only attempt finalization once, and only for terminal phases
    if phase not in TERMINAL_PHASES or out.get("finalized_at") or not metrics.is_available():
        return out
    started = detail.get("started_at") or detail.get("created_at")
    completed = detail.get("completed_at")
    if not started or not completed:
        return out
    try:
        agg = await metrics.range_aggregates(started, completed)
    except Exception as e:
        log.warning("finalize: range_aggregates failed for %s: %s", name, e)
        return out
    if not agg.get("available"):
        return out
    # Walk the TaskRuns to compute UC totals (run-corpus has the counts in its
    # final log line, but cheap proxy: count failed taskruns vs total)
    uc_total = None  # not reliably available without parsing the run-corpus log
    uc_succeeded = None; uc_failed = None
    # We DO know whether the pipeline succeeded overall; UC-level stats need
    # workspace/results parse — defer; leave NULL for now.

    # asyncpg refuses str for TIMESTAMPTZ even with ::timestamptz cast; convert.
    from datetime import datetime as _dt
    try:
        started_dt   = _dt.fromisoformat(started.replace("Z", "+00:00"))
        completed_dt = _dt.fromisoformat(completed.replace("Z", "+00:00"))
    except Exception as e:
        log.warning("finalize: bad timestamps for %s: %s", name, e)
        return out
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE run_sessions SET
                    started_at=$2, completed_at=$3, phase=$4,
                    wall_time_seconds=$5,
                    gpu_energy_joules=$6, gpu_avg_power_watts=$7,
                    gpu_peak_power_watts=$8, gpu_avg_gfx_activity=$9,
                    total_prompt_tokens=$10, total_gen_tokens=$11,
                    finalized_at=now()
                   WHERE run_name=$1""",
                name, started_dt, completed_dt, phase,
                float(agg.get("window_seconds") or 0),
                agg.get("gpu_energy_joules"),
                agg.get("gpu_avg_power_watts"),
                agg.get("gpu_peak_power_watts"),
                agg.get("gpu_avg_gfx_activity"),
                agg.get("total_prompt_tokens"),
                agg.get("total_gen_tokens"),
            )
            row = await conn.fetchrow("SELECT * FROM run_sessions WHERE run_name=$1", name)
        out = dict(row)
        for k in ("created_at", "started_at", "completed_at", "finalized_at"):
            if out.get(k):
                out[k] = out[k].isoformat()
        log.info("finalized session %s: energy=%.0fJ tokens=p%s/g%s",
                 name, agg.get("gpu_energy_joules") or 0,
                 agg.get("total_prompt_tokens"), agg.get("total_gen_tokens"))
    except Exception as e:
        log.warning("finalize: DB update failed for %s: %s", name, e)
    return out


@app.get("/api/runs/{name}")
async def get_run_detail(name: str):
    """Return Tekton PipelineRun spec + per-TaskRun status + session metadata
    + per-UC progress (in-flight) + live session token deltas for the
    run-detail UI. Lazy-finalizes power/token stats on the first view after
    the run reaches a terminal phase."""
    if not validations.ENABLED:
        raise HTTPException(403, "pipeline trigger disabled")
    try:
        detail = validations.get_run_detail(name)
    except KeyError:
        raise HTTPException(404, f"run {name!r} not found")
    except Exception as e:
        log.exception("run detail fetch failed")
        raise HTTPException(500, f"detail failed: {e}")
    session = await _maybe_finalize_session(detail)
    if session is not None:
        detail["session"] = session

    # Per-UC progress: find the matching workspace run-dir's run-progress.yaml
    # by timestamp correlation. Only useful while the run is in flight.
    if detail.get("phase") not in TERMINAL_PHASES:
        started = detail.get("started_at") or detail.get("created_at")
        if started and _results.is_available():
            try:
                progress = _results.find_progress_near(started)
                if progress:
                    detail["progress"] = progress
            except Exception as e:
                log.info("progress lookup failed for %s: %s", name, e)

    # Live session aggregates (energy/power/tokens) for in-flight runs.
    # On terminal phase the persisted run_sessions.* values are authoritative;
    # while running, compute on-the-fly with range_aggregates(started, now).
    if session and detail.get("phase") not in TERMINAL_PHASES:
        started = detail.get("started_at") or detail.get("created_at")
        if started and metrics.is_available():
            try:
                from datetime import datetime as _dt2, timezone as _tz2
                now_iso = _dt2.now(_tz2.utc).isoformat()
                live = await metrics.range_aggregates(started, now_iso)
                if live.get("available"):
                    session["live_wall_time_seconds"]   = live.get("window_seconds")
                    session["live_gpu_energy_joules"]   = live.get("gpu_energy_joules")
                    session["live_gpu_avg_power_watts"] = live.get("gpu_avg_power_watts")
                    session["live_gpu_peak_power_watts"]= live.get("gpu_peak_power_watts")
                    # Tokens from increase() — independent of the trigger-time
                    # baseline persisted in run_sessions, so this and the
                    # baseline-delta tile may diverge slightly. The baseline
                    # method is more reliable for "session totals since I hit
                    # Trigger"; increase() is what an observability dashboard
                    # would report for the same window. We surface both.
                    session["live_total_prompt_tokens"] = live.get("total_prompt_tokens")
                    session["live_total_gen_tokens"]    = live.get("total_gen_tokens")
            except Exception as e:
                log.info("live aggregate computation failed for %s: %s", name, e)

    # Live session token deltas: persisted baseline (captured at trigger time)
    # minus current Prometheus counter. Survives browser reload — replaces
    # the client-side baseline approach.
    if session and session.get("baseline_gen_tokens") is not None:
        try:
            snap = await metrics.snapshot()
            if snap.get("available"):
                v = snap.get("vllm") or {}
                cur_gen    = v.get("gen_tokens_total")
                cur_prompt = v.get("prompt_tokens_total")
                bg = session.get("baseline_gen_tokens")
                bp = session.get("baseline_prompt_tokens")
                # Treat counter regress (vLLM restart) as a new baseline:
                # session counters start fresh, totals never go negative.
                gen_delta    = (cur_gen - bg)    if (cur_gen is not None and bg is not None and cur_gen >= bg) else None
                prompt_delta = (cur_prompt - bp) if (cur_prompt is not None and bp is not None and cur_prompt >= bp) else None
                detail["live_session_gen_tokens"]    = int(gen_delta)    if gen_delta is not None else None
                detail["live_session_prompt_tokens"] = int(prompt_delta) if prompt_delta is not None else None
        except Exception as e:
            log.info("live token delta failed for %s: %s", name, e)
    return detail


@app.get("/api/metrics/snapshot")
async def metrics_snapshot():
    """Live GPU + vLLM metric snapshot for the run-detail UI.

    Queries cluster Prometheus (thanos-querier) via the API pod's SA token.
    Returns per-GPU rows + vLLM aggregates. Polled by the UI every ~3s.
    """
    try:
        return await metrics.snapshot()
    except Exception as e:
        log.exception("metrics snapshot failed")
        raise HTTPException(500, f"snapshot failed: {e}")


@app.get("/api/metrics/timeseries")
async def metrics_timeseries(
    start: str = Query(..., description="ISO 8601 run start timestamp"),
    end: str = Query("", description="ISO 8601 run end timestamp; omit for in-flight runs (defaults to now)"),
):
    """Time-series GPU + vLLM data for sparkline rendering in the run-detail drawer.

    Returns ~60 data points per metric across the run window (step auto-chosen).
    GPU metrics return one series per GPU; vLLM metrics return a single aggregated series.
    """
    try:
        return await metrics.timeseries(start, end or None)
    except Exception as e:
        log.exception("metrics timeseries failed")
        raise HTTPException(500, f"timeseries failed: {e}")


# ========================= RESULTS =========================


@app.get("/api/results")
async def list_results():
    """List all run result directories found on the workspace PVC."""
    if not _results.is_available():
        return {"results": [], "available": False,
                "workspace_path": _results.WORKSPACE_PATH}
    try:
        runs = _results.list_runs()
        return {"results": runs, "available": True,
                "workspace_path": _results.WORKSPACE_PATH}
    except Exception as e:
        log.exception("list results failed")
        raise HTTPException(500, f"list failed: {e}")


# Static sub-paths must be declared before the /{run_id} catch-all.
@app.get("/api/results/compare")
async def compare_results(
    a: str = Query(..., description="first run_id (baseline)"),
    b: str = Query(..., description="second run_id (the newer run)"),
):
    """Compare two workspace runs side-by-side.

    Returns per-UC verdict diff, added/removed gap IDs, and summary-level
    deltas (wall time, pass/fail counts, verdict change count).
    """
    if not _results.is_available():
        raise HTTPException(503, "workspace PVC not mounted")
    try:
        return _results.compare_runs(a, b)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        log.exception("compare_runs failed")
        raise HTTPException(500, f"compare failed: {e}")


@app.get("/api/results/{run_id}")
async def get_result(run_id: str):
    """Return the run-summary.yaml content for a specific run, enriched with
    per-UC verdicts read from the individual analysis files."""
    if not _results.is_available():
        raise HTTPException(503, "workspace PVC not mounted")
    summary = _results.get_run_summary_enriched(run_id)
    if summary is None:
        raise HTTPException(404, f"run {run_id!r} not found")
    return summary


@app.get("/api/results/{run_id}/uc/{uc_uuid:path}")
async def get_result_uc(run_id: str, uc_uuid: str):
    """Return the analysis output for a specific UC within a run."""
    if not _results.is_available():
        raise HTTPException(503, "workspace PVC not mounted")
    analysis = _results.get_analysis(run_id, uc_uuid)
    if analysis is None:
        raise HTTPException(404, f"analysis for {uc_uuid!r} not found in run {run_id!r}")
    return analysis


# ========================= UC ASSIST =========================


class UCAssistIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    current_yaml: Optional[str] = Field(None, max_length=64000)
    context: Optional[str] = Field(None, max_length=2000)


@app.get("/api/uc-assist/status")
async def uc_assist_status():
    """Check whether the UC assist endpoint is configured."""
    return {
        "available": uc_assist.is_available(),
        "model": uc_assist.ASSIST_MODEL,
        "endpoint": uc_assist.ASSIST_ENDPOINT if uc_assist.is_available() else None,
    }


@app.post("/api/uc-assist")
async def uc_assist_chat(payload: UCAssistIn, request: Request):
    """NL-assisted UC authoring — ask the model to draft or refine a UC.

    Returns {"explanation": str, "yaml_suggestion": str|null} on success,
    or {"error": str} if the assist endpoint is misconfigured or unreachable.
    """
    get_user(request)  # auth check — require authenticated caller
    result = await uc_assist.chat(
        user_message=payload.message,
        current_yaml=payload.current_yaml,
        context=payload.context,
    )
    if "error" in result and not result.get("explanation"):
        raise HTTPException(503, result["error"])
    return result


# ========================= USE CASES =========================


def _parse_uc_yaml(yaml_content: str) -> dict:
    """Parse UC YAML and extract key fields. Raises ValueError on bad content."""
    try:
        data = _yaml.safe_load(yaml_content)
    except Exception as e:
        raise ValueError(f"invalid YAML: {e}")
    if not isinstance(data, dict):
        raise ValueError("UC YAML must be a mapping")
    return data


@app.get("/api/use-cases")
async def list_use_cases(
    source: Optional[str] = Query(None, description="'managed', 'corpus', or None for both"),
):
    """List use cases — from the managed DB, the corpus files, or both."""
    managed = []
    corpus_ucs = []

    if source in (None, "managed"):
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT uuid, title, tags, lifecycle_state, "
                "created_by, created_at, updated_by, updated_at "
                "FROM managed_use_cases ORDER BY updated_at DESC"
            )
        managed = [
            {
                **dict(r),
                "source": "managed",
                "created_at": r["created_at"].isoformat(),
                "updated_at": r["updated_at"].isoformat(),
            }
            for r in rows
        ]

    if source in (None, "corpus"):
        # Corpus UC files — already seeded into the files table; filter to .yaml files
        # that look like UCs (have a uuid field when parsed as YAML).
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT path, content, size_bytes, folder FROM files "
                "WHERE path LIKE '%.yaml' OR path LIKE '%.yml' ORDER BY path"
            )
        for r in rows:
            try:
                data = _yaml.safe_load(r["content"])
                if not isinstance(data, dict) or "uuid" not in data:
                    continue
                corpus_ucs.append({
                    "uuid":    data.get("uuid"),
                    "title":   data.get("scenario", {}).get("description", "")[:80]
                               if isinstance(data.get("scenario"), dict) else "",
                    "handle":  data.get("handle"),
                    "tags":    data.get("tags", []),
                    "path":    r["path"],
                    "source":  "corpus",
                })
            except Exception:
                continue

    return {"use_cases": managed + corpus_ucs}


@app.get("/api/use-cases/{uuid:path}")
async def get_use_case(uuid: str):
    """Return a single use case by uuid — managed DB first, then corpus files."""
    # Check managed DB
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM managed_use_cases WHERE uuid = $1", uuid
        )
        if row:
            d = dict(row)
            d["source"] = "managed"
            d["created_at"] = d["created_at"].isoformat()
            d["updated_at"] = d["updated_at"].isoformat()
            # Fetch set memberships
            set_rows = await conn.fetch(
                """SELECT s.id, s.name FROM use_case_sets s
                   JOIN use_case_set_members m ON m.set_id = s.id
                   WHERE m.uc_uuid = $1 ORDER BY s.name""",
                uuid,
            )
            d["sets"] = [{"id": r["id"], "name": r["name"]} for r in set_rows]
            return d

    # Fall back to corpus files
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT path, content FROM files WHERE path LIKE '%.yaml' OR path LIKE '%.yml'"
        )
    for r in rows:
        try:
            data = _yaml.safe_load(r["content"])
            if isinstance(data, dict) and data.get("uuid") == uuid:
                return {"uuid": uuid, "yaml_content": r["content"],
                        "path": r["path"], "source": "corpus", "parsed": data}
        except Exception:
            continue

    raise HTTPException(404, f"use case {uuid!r} not found")


@app.post("/api/use-cases")
async def create_use_case(payload: ManagedUCIn, request: Request):
    """Create a managed use case. UUID is taken from the YAML content."""
    user = get_user(request)
    try:
        data = _parse_uc_yaml(payload.yaml_content)
    except ValueError as e:
        raise HTTPException(400, str(e))

    uc_uuid = data.get("uuid")
    if not uc_uuid or not isinstance(uc_uuid, str):
        raise HTTPException(400, "UC YAML must have a non-empty 'uuid' field")

    title = ""
    if isinstance(data.get("scenario"), dict):
        title = (data["scenario"].get("description") or "")[:120]
    if not title:
        title = data.get("handle", uc_uuid)

    tags = payload.tags or data.get("tags", [])

    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT 1 FROM managed_use_cases WHERE uuid = $1", uc_uuid
        )
        if existing:
            raise HTTPException(409, f"use case {uc_uuid!r} already exists; use PUT to update")
        await conn.execute(
            """
            INSERT INTO managed_use_cases
              (uuid, title, yaml_content, created_by, updated_by, tags)
            VALUES ($1, $2, $3, $4, $4, $5)
            """,
            uc_uuid, title, payload.yaml_content, user, tags,
        )
    return {"ok": True, "uuid": uc_uuid, "title": title}


@app.put("/api/use-cases/{uuid:path}")
async def update_use_case(uuid: str, payload: ManagedUCIn, request: Request):
    """Update an existing managed use case."""
    user = get_user(request)
    try:
        data = _parse_uc_yaml(payload.yaml_content)
    except ValueError as e:
        raise HTTPException(400, str(e))

    yaml_uuid = data.get("uuid")
    if yaml_uuid and yaml_uuid != uuid:
        raise HTTPException(400, f"UUID in YAML ({yaml_uuid!r}) does not match URL ({uuid!r})")

    title = ""
    if isinstance(data.get("scenario"), dict):
        title = (data["scenario"].get("description") or "")[:120]
    if not title:
        title = data.get("handle", uuid)

    tags = payload.tags or data.get("tags", [])

    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE managed_use_cases
            SET yaml_content=$2, title=$3, updated_by=$4, updated_at=now(), tags=$5
            WHERE uuid=$1
            """,
            uuid, payload.yaml_content, title, user, tags,
        )
    if result == "UPDATE 0":
        raise HTTPException(404, f"use case {uuid!r} not found in managed DB")
    return {"ok": True, "uuid": uuid, "title": title}


@app.delete("/api/use-cases/{uuid:path}")
async def delete_use_case(uuid: str, request: Request):
    """Delete a managed use case."""
    user = get_user(request)
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM managed_use_cases WHERE uuid = $1", uuid
        )
    if result == "DELETE 0":
        raise HTTPException(404, f"use case {uuid!r} not found in managed DB")
    log.info("Use case %s deleted by %s", uuid, user)
    return {"ok": True, "uuid": uuid}


@app.post("/api/use-cases/{uuid}/transition")
async def transition_use_case(uuid: str, payload: LifecycleTransitionIn, request: Request):
    """Advance or retract a managed UC's lifecycle state."""
    user = get_user(request)
    if payload.to_state not in UC_STATES:
        raise HTTPException(400, f"invalid state; must be one of {sorted(UC_STATES)}")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT lifecycle_state FROM managed_use_cases WHERE uuid = $1", uuid
        )
        if not row:
            raise HTTPException(404, f"use case {uuid!r} not found in managed DB")
        from_state = row["lifecycle_state"]
        allowed = VALID_TRANSITIONS.get(from_state, set())
        if payload.to_state not in allowed:
            raise HTTPException(
                400,
                f"cannot transition from '{from_state}' to '{payload.to_state}'; "
                f"allowed: {sorted(allowed) or 'none'}",
            )
        async with conn.transaction():
            await conn.execute(
                "UPDATE managed_use_cases SET lifecycle_state=$2, updated_by=$3, updated_at=now() WHERE uuid=$1",
                uuid, payload.to_state, user,
            )
            await conn.execute(
                "INSERT INTO lifecycle_events(uc_uuid, from_state, to_state, actor, notes) "
                "VALUES ($1, $2, $3, $4, $5)",
                uuid, from_state, payload.to_state, user, payload.notes or "",
            )
    log.info("UC %s: %s → %s by %s", uuid, from_state, payload.to_state, user)
    return {"ok": True, "uuid": uuid, "from_state": from_state, "to_state": payload.to_state}


@app.get("/api/use-cases/{uuid}/lifecycle")
async def get_use_case_lifecycle(uuid: str):
    """Return the lifecycle event history for a managed UC."""
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM managed_use_cases WHERE uuid=$1", uuid)
        if not exists:
            raise HTTPException(404, f"use case {uuid!r} not found")
        rows = await conn.fetch(
            "SELECT from_state, to_state, actor, notes, created_at "
            "FROM lifecycle_events WHERE uc_uuid=$1 ORDER BY created_at DESC",
            uuid,
        )
    return {
        "uuid": uuid,
        "events": [
            {**dict(r), "created_at": r["created_at"].isoformat()}
            for r in rows
        ],
    }


# ========================= SETS =========================


def _set_row(r, member_count: int = 0) -> dict:
    return {
        "id": r["id"],
        "name": r["name"],
        "description": r["description"],
        "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat(),
        "updated_at": r["updated_at"].isoformat(),
        "member_count": member_count,
    }


@app.get("/api/sets")
async def list_sets():
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT s.*, COUNT(m.uc_uuid) AS member_count
               FROM use_case_sets s
               LEFT JOIN use_case_set_members m ON m.set_id = s.id
               GROUP BY s.id ORDER BY s.name"""
        )
    return {"sets": [_set_row(r, r["member_count"]) for r in rows]}


@app.post("/api/sets")
async def create_set(payload: SetIn, request: Request):
    user = get_user(request)
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                "INSERT INTO use_case_sets(name, description, created_by) "
                "VALUES ($1, $2, $3) RETURNING *",
                payload.name, payload.description, user,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, f"a set named {payload.name!r} already exists")
    return _set_row(row)


@app.get("/api/sets/{set_id}")
async def get_set(set_id: int):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT s.*, COUNT(m.uc_uuid) AS member_count FROM use_case_sets s "
            "LEFT JOIN use_case_set_members m ON m.set_id = s.id "
            "WHERE s.id=$1 GROUP BY s.id",
            set_id,
        )
        if not row:
            raise HTTPException(404, f"set {set_id} not found")
        members = await conn.fetch(
            "SELECT uc_uuid, uc_source, uc_handle, uc_path, added_by, added_at "
            "FROM use_case_set_members WHERE set_id=$1 ORDER BY added_at",
            set_id,
        )
    return {
        **_set_row(row, row["member_count"]),
        "members": [
            {**dict(m), "added_at": m["added_at"].isoformat()}
            for m in members
        ],
    }


@app.put("/api/sets/{set_id}")
async def update_set(set_id: int, payload: SetIn, request: Request):
    user = get_user(request)  # noqa: F841 — auth check
    async with pool.acquire() as conn:
        try:
            result = await conn.execute(
                "UPDATE use_case_sets SET name=$2, description=$3, updated_at=now() WHERE id=$1",
                set_id, payload.name, payload.description,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, f"a set named {payload.name!r} already exists")
    if result == "UPDATE 0":
        raise HTTPException(404, f"set {set_id} not found")
    return {"ok": True, "id": set_id}


@app.delete("/api/sets/{set_id}")
async def delete_set(set_id: int, request: Request):
    user = get_user(request)  # noqa: F841 — auth check
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM use_case_sets WHERE id=$1", set_id)
    if result == "DELETE 0":
        raise HTTPException(404, f"set {set_id} not found")
    return {"ok": True, "id": set_id}


@app.post("/api/sets/{set_id}/members")
async def add_set_member(set_id: int, payload: SetMemberIn, request: Request):
    user = get_user(request)
    if payload.uc_source not in ("managed", "corpus"):
        raise HTTPException(400, "uc_source must be 'managed' or 'corpus'")
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM use_case_sets WHERE id=$1", set_id)
        if not exists:
            raise HTTPException(404, f"set {set_id} not found")
        try:
            await conn.execute(
                "INSERT INTO use_case_set_members"
                "(set_id, uc_uuid, uc_source, uc_handle, uc_path, added_by) "
                "VALUES ($1,$2,$3,$4,$5,$6)",
                set_id, payload.uc_uuid, payload.uc_source,
                payload.uc_handle, payload.uc_path, user,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, f"UC {payload.uc_uuid!r} is already in this set")
    return {"ok": True, "set_id": set_id, "uc_uuid": payload.uc_uuid}


@app.delete("/api/sets/{set_id}/members/{uc_uuid:path}")
async def remove_set_member(set_id: int, uc_uuid: str, request: Request):
    user = get_user(request)  # noqa: F841
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM use_case_set_members WHERE set_id=$1 AND uc_uuid=$2",
            set_id, uc_uuid,
        )
    if result == "DELETE 0":
        raise HTTPException(404, "member not found in set")
    return {"ok": True}


@app.get("/api/sets/{set_id}/corpus-subpath")
async def set_corpus_subpath(set_id: int):
    """Return the common corpus path prefix for corpus UCs in this set.
    Used by the UI to pre-fill corpus_subpath when triggering a set run."""
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM use_case_sets WHERE id=$1", set_id)
        if not exists:
            raise HTTPException(404, f"set {set_id} not found")
        paths = await conn.fetch(
            "SELECT uc_path FROM use_case_set_members "
            "WHERE set_id=$1 AND uc_source='corpus' AND uc_path IS NOT NULL",
            set_id,
        )
        managed_count = await conn.fetchval(
            "SELECT COUNT(*) FROM use_case_set_members WHERE set_id=$1 AND uc_source='managed'",
            set_id,
        )
        corpus_count = len(paths)
    if not paths:
        return {"subpath": None, "corpus_count": 0, "managed_count": managed_count}
    # Compute common prefix of all corpus UC paths
    path_strs = [r["uc_path"] for r in paths]
    from os.path import commonpath, dirname
    try:
        common = commonpath(path_strs)
        # Use the directory if common is a file path
        from pathlib import Path as _Path
        cp = _Path(common)
        subpath = str(cp) if cp.is_dir() else str(cp.parent)
    except ValueError:
        subpath = ""
    return {"subpath": subpath or None, "corpus_count": corpus_count, "managed_count": managed_count}


@app.post("/api/sets/{set_id}/promote")
async def promote_set_members(set_id: int, payload: SetPromoteIn, request: Request):
    """Bulk-transition all managed UC members of a set from from_state → to_state."""
    user = get_user(request)
    if payload.from_state not in UC_STATES:
        raise HTTPException(400, f"invalid from_state; must be one of {sorted(UC_STATES)}")
    if payload.to_state not in UC_STATES:
        raise HTTPException(400, f"invalid to_state; must be one of {sorted(UC_STATES)}")
    allowed = VALID_TRANSITIONS.get(payload.from_state, set())
    if payload.to_state not in allowed:
        raise HTTPException(
            400,
            f"cannot transition from '{payload.from_state}' to '{payload.to_state}'; "
            f"allowed: {sorted(allowed) or 'none'}",
        )
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM use_case_sets WHERE id=$1", set_id)
        if not exists:
            raise HTTPException(404, f"set {set_id} not found")
        members = await conn.fetch(
            """SELECT uc.uuid FROM managed_use_cases uc
               JOIN use_case_set_members m ON m.uc_uuid = uc.uuid AND m.uc_source = 'managed'
               WHERE m.set_id = $1 AND uc.lifecycle_state = $2""",
            set_id, payload.from_state,
        )
        promoted = 0
        async with conn.transaction():
            for row in members:
                uid = row["uuid"]
                await conn.execute(
                    "UPDATE managed_use_cases SET lifecycle_state=$2, updated_by=$3, updated_at=now() WHERE uuid=$1",
                    uid, payload.to_state, user,
                )
                await conn.execute(
                    "INSERT INTO lifecycle_events(uc_uuid, from_state, to_state, actor, notes) "
                    "VALUES ($1, $2, $3, $4, $5)",
                    uid, payload.from_state, payload.to_state, user, payload.notes or "",
                )
                promoted += 1
    log.info("Set %s: promoted %d UCs %s → %s by %s", set_id, promoted, payload.from_state, payload.to_state, user)
    return {"ok": True, "set_id": set_id, "promoted": promoted,
            "from_state": payload.from_state, "to_state": payload.to_state}


# ========================= EXPORT / IMPORT =========================


@app.get("/api/export")
async def export_use_cases(
    format: str = Query("tar.gz", description="Archive format: tar.gz, zip, or tar"),
    state: Optional[str] = Query(None, description="Filter by lifecycle state"),
    set_id: Optional[int] = Query(None, description="Export members of this set only"),
):
    """Export managed use cases as an archive.

    Archive structure: {lifecycle_state}/{set_name_or__ungrouped}/{uc_uuid}.yaml
    """
    if format not in ("tar.gz", "zip", "tar"):
        raise HTTPException(400, "format must be one of: tar.gz, zip, tar")
    if state and state not in UC_STATES:
        raise HTTPException(400, f"invalid state; must be one of {sorted(UC_STATES)}")

    async with pool.acquire() as conn:
        if set_id is not None:
            exists = await conn.fetchval("SELECT 1 FROM use_case_sets WHERE id=$1", set_id)
            if not exists:
                raise HTTPException(404, f"set {set_id} not found")
            set_name_row = await conn.fetchrow("SELECT name FROM use_case_sets WHERE id=$1", set_id)
            export_set_name = set_name_row["name"] if set_name_row else str(set_id)

            if state:
                uc_rows = await conn.fetch(
                    """SELECT uc.uuid, uc.yaml_content, uc.lifecycle_state
                       FROM managed_use_cases uc
                       JOIN use_case_set_members m ON m.uc_uuid = uc.uuid AND m.uc_source = 'managed'
                       WHERE m.set_id = $1 AND uc.lifecycle_state = $2
                       ORDER BY uc.lifecycle_state, uc.uuid""",
                    set_id, state,
                )
            else:
                uc_rows = await conn.fetch(
                    """SELECT uc.uuid, uc.yaml_content, uc.lifecycle_state
                       FROM managed_use_cases uc
                       JOIN use_case_set_members m ON m.uc_uuid = uc.uuid AND m.uc_source = 'managed'
                       WHERE m.set_id = $1
                       ORDER BY uc.lifecycle_state, uc.uuid""",
                    set_id,
                )
        else:
            export_set_name = None
            if state:
                uc_rows = await conn.fetch(
                    "SELECT uuid, yaml_content, lifecycle_state FROM managed_use_cases "
                    "WHERE lifecycle_state = $1 ORDER BY lifecycle_state, uuid",
                    state,
                )
            else:
                uc_rows = await conn.fetch(
                    "SELECT uuid, yaml_content, lifecycle_state FROM managed_use_cases "
                    "ORDER BY lifecycle_state, uuid"
                )

        # For each UC, fetch its set memberships (only needed when not scoped to a single set)
        uc_sets: dict[str, list[str]] = {}
        for row in uc_rows:
            if export_set_name is not None:
                uc_sets[row["uuid"]] = [export_set_name]
            else:
                set_rows = await conn.fetch(
                    "SELECT s.name FROM use_case_sets s "
                    "JOIN use_case_set_members m ON m.set_id = s.id "
                    "WHERE m.uc_uuid = $1 ORDER BY s.name",
                    row["uuid"],
                )
                uc_sets[row["uuid"]] = [r["name"] for r in set_rows]

    buf = io.BytesIO()

    if format in ("tar.gz", "tar"):
        mode = "w:gz" if format == "tar.gz" else "w:"
        with tarfile.open(fileobj=buf, mode=mode) as tf:
            for row in uc_rows:
                uid = row["uuid"]
                lc = row["lifecycle_state"]
                dirs = uc_sets.get(uid) or ["_ungrouped"]
                content = row["yaml_content"].encode("utf-8")
                for sname in dirs:
                    path = f"{lc}/{_slugify(sname)}/{uid}.yaml"
                    info = tarfile.TarInfo(name=path)
                    info.size = len(content)
                    tf.addfile(info, io.BytesIO(content))
        ext = ".tar.gz" if format == "tar.gz" else ".tar"
        media_type = "application/gzip" if format == "tar.gz" else "application/x-tar"
    else:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            seen: set[str] = set()
            for row in uc_rows:
                uid = row["uuid"]
                lc = row["lifecycle_state"]
                dirs = uc_sets.get(uid) or ["_ungrouped"]
                for sname in dirs:
                    path = f"{lc}/{_slugify(sname)}/{uid}.yaml"
                    if path not in seen:
                        zf.writestr(path, row["yaml_content"])
                        seen.add(path)
        ext = ".zip"
        media_type = "application/zip"

    buf.seek(0)
    scope = f"-{_slugify(export_set_name)}" if export_set_name else (f"-{state}" if state else "")
    filename = f"dav-use-cases{scope}{ext}"
    return Response(
        content=buf.read(),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/import")
async def import_use_cases(request: Request, file: UploadFile = File(...)):
    """Import managed use cases from an archive (.tar.gz, .tar, .zip).

    Archive structure: {lifecycle_state}/{set_name}|_ungrouped/{uuid}.yaml
    Lifecycle state is taken from the top-level directory. If set_name is not
    '_ungrouped', the UC is added to a named set (created on demand).
    """
    user = get_user(request)
    data = await file.read()
    fname = file.filename or ""

    if fname.endswith(".zip"):
        fmt = "zip"
    elif fname.endswith(".tar.gz") or fname.endswith(".tgz"):
        fmt = "tar.gz"
    elif fname.endswith(".tar"):
        fmt = "tar"
    elif data[:2] == b"PK":
        fmt = "zip"
    else:
        fmt = "tar.gz"  # tarfile r:* auto-detects

    entries: list[tuple[str, str]] = []
    try:
        if fmt == "zip":
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for name in zf.namelist():
                    if name.endswith("/"):
                        continue
                    entries.append((name, zf.read(name).decode("utf-8")))
        else:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
                for member in tf.getmembers():
                    if not member.isfile():
                        continue
                    fobj = tf.extractfile(member)
                    if fobj is None:
                        continue
                    entries.append((member.name, fobj.read().decode("utf-8")))
    except Exception as e:
        raise HTTPException(400, f"could not read archive: {e}")

    created = 0
    updated = 0
    transitioned = 0
    skipped = 0
    errors: list[str] = []

    async with pool.acquire() as conn:
        for path, content in entries:
            if not (path.endswith(".yaml") or path.endswith(".yml")):
                continue
            parts = path.strip("/").split("/")
            if len(parts) < 3:
                errors.append(f"{path}: expected {{state}}/{{set}}/{{uuid}}.yaml structure")
                continue

            target_state = parts[0]
            set_name = parts[1]

            if target_state not in UC_STATES:
                errors.append(f"{path}: unknown lifecycle state {target_state!r}")
                continue

            try:
                uc_data = _parse_uc_yaml(content)
            except ValueError as e:
                errors.append(f"{path}: {e}")
                continue

            uc_uuid = uc_data.get("uuid")
            if not uc_uuid or not isinstance(uc_uuid, str):
                errors.append(f"{path}: no uuid field in YAML")
                continue

            title = ""
            if isinstance(uc_data.get("scenario"), dict):
                title = (uc_data["scenario"].get("description") or "")[:120]
            if not title:
                title = uc_data.get("handle", uc_uuid)
            tags = uc_data.get("tags", [])

            try:
                async with conn.transaction():
                    existing = await conn.fetchrow(
                        "SELECT lifecycle_state FROM managed_use_cases WHERE uuid=$1", uc_uuid
                    )
                    if existing is None:
                        await conn.execute(
                            """INSERT INTO managed_use_cases
                               (uuid, title, yaml_content, lifecycle_state, created_by, updated_by, tags)
                               VALUES ($1, $2, $3, $4, $5, $5, $6)""",
                            uc_uuid, title, content, target_state, user, tags,
                        )
                        await conn.execute(
                            "INSERT INTO lifecycle_events(uc_uuid, from_state, to_state, actor, notes) "
                            "VALUES ($1, NULL, $2, $3, 'imported')",
                            uc_uuid, target_state, user,
                        )
                        created += 1
                    else:
                        await conn.execute(
                            """UPDATE managed_use_cases SET yaml_content=$2, title=$3,
                               updated_by=$4, updated_at=now(), tags=$5 WHERE uuid=$1""",
                            uc_uuid, content, title, user, tags,
                        )
                        from_state = existing["lifecycle_state"]
                        if from_state != target_state:
                            allowed = VALID_TRANSITIONS.get(from_state, set())
                            if target_state not in allowed:
                                errors.append(
                                    f"{path}: {uc_uuid!r} cannot transition "
                                    f"from '{from_state}' to '{target_state}' — content updated, state unchanged"
                                )
                                skipped += 1
                            else:
                                await conn.execute(
                                    """UPDATE managed_use_cases SET lifecycle_state=$2,
                                       updated_by=$3, updated_at=now() WHERE uuid=$1""",
                                    uc_uuid, target_state, user,
                                )
                                await conn.execute(
                                    "INSERT INTO lifecycle_events(uc_uuid, from_state, to_state, actor, notes) "
                                    "VALUES ($1, $2, $3, $4, 'imported')",
                                    uc_uuid, from_state, target_state, user,
                                )
                                transitioned += 1
                        updated += 1

                    if set_name != "_ungrouped":
                        set_row = await conn.fetchrow(
                            "SELECT id FROM use_case_sets WHERE lower(name)=lower($1)", set_name
                        )
                        if set_row is None:
                            set_row = await conn.fetchrow(
                                "INSERT INTO use_case_sets(name, description, created_by) "
                                "VALUES ($1, '', $2) RETURNING id",
                                set_name, user,
                            )
                        sid = set_row["id"]
                        already = await conn.fetchval(
                            "SELECT 1 FROM use_case_set_members WHERE set_id=$1 AND uc_uuid=$2",
                            sid, uc_uuid,
                        )
                        if not already:
                            await conn.execute(
                                "INSERT INTO use_case_set_members(set_id, uc_uuid, uc_source, added_by) "
                                "VALUES ($1, $2, 'managed', $3)",
                                sid, uc_uuid, user,
                            )
            except Exception as e:
                errors.append(f"{path}: unexpected error: {e}")
                log.exception("import error for %s", path)

    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "transitioned": transitioned,
        "skipped": skipped,
        "errors": errors,
    }


# ========================= CORPUS FILES (legacy) =========================


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


# ========================= REVIEWS (legacy) =========================


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


# ========================= HISTORY (legacy) =========================


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


# ========================= DASHBOARD (legacy) =========================


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
            FROM review_events ORDER BY created_at DESC LIMIT 10
            """
        )
        reviewers = await conn.fetch(
            """
            SELECT reviewer, COUNT(*) AS events, MAX(created_at) AS last_active
            FROM review_events GROUP BY reviewer ORDER BY last_active DESC LIMIT 20
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


# ========================= ANALYSIS INGESTION =========================


async def _ingest_run_analyses(run_id: str, conn: "asyncpg.Connection") -> dict:
    """Ingest a workspace run's analysis results into Postgres.

    Reads run-summary.yaml (enriched with verdicts) + individual analysis files.
    Idempotent — existing rows for this run_id are deleted and re-inserted so
    re-ingestion after a re-run is safe. Returns a summary dict.
    """
    if not _results.is_available():
        raise HTTPException(503, "workspace PVC not mounted")
    summary = _results.get_run_summary_enriched(run_id)
    if not summary:
        raise HTTPException(404, f"run {run_id!r} not found on workspace PVC")

    from datetime import datetime as _dt

    def _parse_ts(s):
        if not s:
            return None
        try:
            return _dt.fromisoformat(str(s).replace("Z", "+00:00"))
        except Exception:
            return None

    async with conn.transaction():
        # Clear existing ingestion for this run so re-ingestion is safe
        await conn.execute("DELETE FROM analysis_runs WHERE run_id=$1", run_id)

        await conn.execute(
            """INSERT INTO analysis_runs
               (run_id, mode, started_at, finished_at, total_ucs,
                successful, failed, total_samples)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
            run_id,
            summary.get("mode"),
            _parse_ts(summary.get("started_at")),
            _parse_ts(summary.get("finished_at")),
            summary.get("total_ucs", 0),
            summary.get("successful", 0),
            summary.get("failed", 0),
            summary.get("total_samples", 0),
        )

        ingested_ucs = 0
        ingested_gaps = 0
        for uc in (summary.get("ucs") or []):
            uc_uuid = uc.get("uc_uuid")
            if not uc_uuid:
                continue
            # Load full analysis for this UC
            analysis = _results.get_analysis(run_id, uc_uuid)
            meta = {}
            overall = None
            analyzed_at = None
            model = None
            endpoint_url = None
            engine_version = None
            gaps = []
            if analysis and analysis.get("_source") == "single":
                a_meta = analysis.get("analysis_metadata") or {}
                summary_block = analysis.get("summary") or {}
                meta = a_meta
                overall = summary_block.get("overall_assessment") or analysis.get("overall_assessment")
                analyzed_at = _parse_ts(a_meta.get("analyzed_at"))
                model = a_meta.get("model")
                endpoint_url = a_meta.get("endpoint_url")
                engine_version = a_meta.get("engine_version")
                gaps = analysis.get("gaps_identified") or []
            elif analysis and analysis.get("_source") == "explore":
                # Use first sample's metadata
                first = (analysis.get("samples") or [{}])[0] if analysis.get("samples") else {}
                a_meta = first.get("analysis_metadata") or {}
                model = a_meta.get("model")
                endpoint_url = a_meta.get("endpoint_url")
                engine_version = a_meta.get("engine_version")
                # Collect gaps from all samples (deduplicated by gap_id)
                seen_gap_ids = set()
                for sample in (analysis.get("samples") or []):
                    for g in (sample.get("gaps_identified") or []):
                        gid = g.get("gap_id")
                        if gid and gid not in seen_gap_ids:
                            gaps.append(g)
                            seen_gap_ids.add(gid)

            row = await conn.fetchrow(
                """INSERT INTO uc_analyses
                   (run_id, uc_uuid, uc_handle, status, verdict, overall_assessment,
                    wall_time_seconds, sample_count, engine_version, model,
                    endpoint_url, analyzed_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                   RETURNING id""",
                run_id, uc_uuid,
                uc.get("uc_handle"),
                uc.get("status"),
                uc.get("verdict"),
                overall,
                uc.get("wall_time_seconds"),
                uc.get("sample_count"),
                engine_version, model, endpoint_url, analyzed_at,
            )
            analysis_id = row["id"]
            ingested_ucs += 1

            for gap in gaps:
                if not isinstance(gap, dict):
                    continue
                sev = gap.get("severity")
                if isinstance(sev, dict):
                    sev = sev.get("label")
                await conn.execute(
                    """INSERT INTO uc_gaps
                       (analysis_id, run_id, uc_uuid, gap_id, title, description, severity)
                       VALUES ($1,$2,$3,$4,$5,$6,$7)""",
                    analysis_id, run_id, uc_uuid,
                    gap.get("gap_id"),
                    gap.get("title"),
                    gap.get("description"),
                    sev,
                )
                ingested_gaps += 1

    return {
        "run_id": run_id,
        "ingested_ucs": ingested_ucs,
        "ingested_gaps": ingested_gaps,
    }


@app.post("/api/analysis/ingest/{run_id:path}")
async def ingest_analysis(run_id: str, request: Request):
    """Ingest a workspace run's analysis results into Postgres.

    Idempotent — safe to re-run after additional UCs complete or to refresh
    data after a re-run. Returns counts of ingested UC analyses and gaps.
    """
    get_user(request)
    try:
        async with pool.acquire() as conn:
            result = await _ingest_run_analyses(run_id, conn)
        return {"ok": True, **result}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("analysis ingest failed for %s", run_id)
        raise HTTPException(500, f"ingest failed: {e}")


@app.get("/api/analysis/runs")
async def list_ingested_runs(limit: int = Query(50, ge=1, le=500)):
    """List all runs that have been ingested into Postgres, newest first."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT run_id, mode, started_at, finished_at, total_ucs,
                      successful, failed, total_samples, ingested_at
               FROM analysis_runs ORDER BY started_at DESC NULLS LAST LIMIT $1""",
            limit,
        )
    return {
        "runs": [
            {**dict(r),
             "started_at":  r["started_at"].isoformat()  if r["started_at"]  else None,
             "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
             "ingested_at": r["ingested_at"].isoformat() if r["ingested_at"] else None,
            }
            for r in rows
        ]
    }


@app.get("/api/analysis/gaps")
async def query_gaps(
    uc_uuid: Optional[str] = Query(None, description="filter by UC uuid"),
    gap_id: Optional[str] = Query(None, description="filter by gap ID"),
    run_id: Optional[str] = Query(None, description="filter by run ID"),
    limit: int = Query(200, ge=1, le=2000),
):
    """Query ingested gaps across runs. Useful for cross-run gap trend analysis."""
    clauses = []
    args: list = []

    def _add(clause: str, val):
        args.append(val)
        clauses.append(clause.replace("?", f"${len(args)}"))

    if uc_uuid:
        _add("g.uc_uuid = ?", uc_uuid)
    if gap_id:
        _add("g.gap_id = ?", gap_id)
    if run_id:
        _add("g.run_id = ?", run_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT g.id, g.run_id, g.uc_uuid, g.gap_id, g.title,
                       g.description, g.severity, g.ingested_at,
                       ua.verdict, ua.uc_handle,
                       ar.started_at AS run_started_at
                FROM uc_gaps g
                JOIN uc_analyses ua ON ua.id = g.analysis_id
                JOIN analysis_runs ar ON ar.run_id = g.run_id
                {where}
                ORDER BY g.ingested_at DESC
                LIMIT ${len(args)+1}""",
            *args, limit,
        )
    return {
        "gaps": [
            {**dict(r),
             "ingested_at":    r["ingested_at"].isoformat()    if r["ingested_at"]    else None,
             "run_started_at": r["run_started_at"].isoformat() if r["run_started_at"] else None,
            }
            for r in rows
        ]
    }


# ========================= SOURCING =========================


@app.get("/api/sources")
async def sources_state():
    try:
        if not sources.is_available():
            raise HTTPException(503, "sources not available (ConfigMap or RBAC missing)")
        return {"sources": sources.get_all_sources_state()}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("sources state read failed")
        raise HTTPException(500, f"read failed: {e}")


@app.get("/api/sources/corpus/uc-subpath")
async def detect_uc_subpath():
    """Probe the cloned corpus tree for a UC directory.

    DAV-consumer convention: `dav/use-cases/` at the corpus root.
    Legacy convention: `use-cases/` at the corpus root.
    Falls back to None if neither exists.

    Returns the relative path the pipeline's corpus-uc-subpath param should use.
    """
    root = Path(CORPUS_DIR)
    candidates = ["dav/use-cases", "use-cases"]
    detected = None
    available = []
    for c in candidates:
        if (root / c).is_dir():
            available.append(c)
            if detected is None:
                detected = c
    return {
        "corpus_dir": str(root),
        "corpus_dir_exists": root.exists(),
        "detected": detected,
        "candidates_found": available,
        "fallback": "use-cases",
    }


@app.get("/api/sources/branches")
async def sources_branches(repo_url: str = Query(..., min_length=1)):
    try:
        branches = sources.list_branches(repo_url)
        return {"repo_url": repo_url, "branches": branches}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.exception("branch listing failed")
        raise HTTPException(500, f"listing failed: {e}")


@app.get("/api/sources/inference/models")
async def list_inference_models_endpoint(endpoint: str = Query(..., min_length=1, max_length=512)):
    """List the model IDs published by an OpenAI-compatible inference endpoint.

    Pure read — does NOT persist anything. Used by the Config UI to populate
    the model dropdown after the user enters an endpoint.
    """
    try:
        return await sources.list_inference_models(endpoint)
    except Exception as e:
        log.exception("inference list-models failed")
        raise HTTPException(500, f"list models failed: {e}")


@app.post("/api/sources/inference/validate")
async def validate_inference_endpoint(payload: InferenceValidateIn):
    """Probe an OpenAI-compatible endpoint for reachability + model presence.

    Pure check — does NOT persist anything. Used by the Config UI before Apply.
    """
    try:
        return await sources.validate_inference(payload.endpoint, payload.model)
    except Exception as e:
        log.exception("inference validation failed")
        raise HTTPException(500, f"validate failed: {e}")


@app.post("/api/sources/{kind}")
async def sources_apply(kind: str, payload: SourceApplyIn, request: Request):
    if kind not in sources.SOURCES:
        raise HTTPException(400, f"unknown source kind: {kind}")
    reviewer = get_user(request)
    try:
        new_state = sources.apply_source(
            kind=kind,
            applied_by=reviewer,
            repo_url=payload.repo_url,
            repo_branch=payload.repo_branch,
            endpoint=payload.endpoint,
            model=payload.model,
        )
        return {"ok": True, "state": new_state}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.exception("sources apply failed")
        raise HTTPException(500, f"apply failed: {e}")


@app.get("/api/sources/{kind}")
async def sources_kind_state(kind: str):
    if kind not in sources.SOURCES:
        raise HTTPException(400, f"unknown source kind: {kind}")
    try:
        return {"state": sources.get_source_state(kind)}
    except Exception as e:
        log.exception("source kind state read failed")
        raise HTTPException(500, f"read failed: {e}")


# ========================= LEGACY SELF-TEST (kept for backward compat) =========================


@app.get("/api/self-test/status")
async def self_test_status():
    return {
        "enabled": validations.ENABLED,
        "available": validations.is_available(),
        "pipeline_name": validations.PIPELINE_NAME,
        "namespace": validations.NAMESPACE,
        "default_branch": validations.DEFAULT_BRANCH,
    }


@app.post("/api/self-test/run")
async def self_test_run(payload: RunTriggerIn, request: Request):
    if not validations.ENABLED:
        raise HTTPException(403, "trigger disabled")
    reviewer = get_user(request)
    try:
        result = validations.trigger_run(
            triggered_by=reviewer,
            branch=payload.branch,
            commit_sha=payload.commit_sha,
            inference_endpoint=payload.inference_endpoint,
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
