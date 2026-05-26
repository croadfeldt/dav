"""DAV Console API.

Thin FastAPI over Postgres + Kubernetes + workspace PVC.
Auth is terminated upstream (oauth-proxy sidecar) which injects
X-Forwarded-User / X-Forwarded-Email headers.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import tarfile
import time
import unicodedata
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import asyncpg
import httpx
import yaml as _yaml
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .corpus_loader import walk_corpus, parse_patterns
from . import validations
from . import sources
from . import metrics
from . import results as _results
from . import uc_assist
from . import corpus_push

log = logging.getLogger("dav-review-api")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())

DB_DSN = os.environ["DATABASE_URL"]
CORPUS_MODE = os.environ.get("CORPUS_MODE", "directory").lower()
CORPUS_DIR = os.environ.get("CORPUS_DIR", "/data/repo")
CORPUS_PATH = os.environ.get("CORPUS_PATH", "/etc/dav-review/corpus.json")
CORPUS_INCLUDE = parse_patterns(os.environ.get("CORPUS_INCLUDE"))
CORPUS_EXCLUDE = parse_patterns(os.environ.get("CORPUS_EXCLUDE"))
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
MIGRATE_002_PATH = Path(__file__).parent / "migrate_002_model_configs.sql"
MIGRATE_003_PATH = Path(__file__).parent / "migrate_003_model_defaults.sql"
MIGRATE_004_PATH = Path(__file__).parent / "migrate_004_default_set.sql"
MIGRATE_005_PATH = Path(__file__).parent / "migrate_005_corpus_push.sql"
MIGRATE_006_PATH = Path(__file__).parent / "migrate_006_run_lineage.sql"
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
        log.info("Applying migration 002 (model_configs consolidation)...")
        await conn.execute(MIGRATE_002_PATH.read_text())
        log.info("Applying migration 003 (model_defaults)...")
        await conn.execute(MIGRATE_003_PATH.read_text())
        log.info("Applying migration 004 (default set marker)...")
        await conn.execute(MIGRATE_004_PATH.read_text())
        log.info("Applying migration 005 (corpus push state)...")
        await conn.execute(MIGRATE_005_PATH.read_text())
        log.info("Applying migration 006 (run lineage + state)...")
        await conn.execute(MIGRATE_006_PATH.read_text())
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


app = FastAPI(title="DAV Console API", version="0.9.5", lifespan=lifespan)

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
    # Optional UC selection — when set, engine processes ONLY these UCs from
    # within corpus_subpath (siblings are skipped). Used by Sets runs and
    # single-UC test eval. Empty/None = whole subpath (legacy).
    uc_handles: Optional[list[str]] = None
    uc_uuids: Optional[list[str]] = None
    # Managed UCs to materialize from the console API at run start (engine
    # fetches each UUID via GET /api/use-cases/<uuid>, writes the YAML to a
    # temp dir, processes alongside corpus UCs). Lets reviewers test
    # unpushed UCs without touching the corpus repo. Pairs with the existing
    # uc_handles / uc_uuids filter — managed UCs always run when listed here.
    managed_uc_uuids: Optional[list[str]] = None
    # R2 — lineage: which Set (if any) the run was triggered for, and how
    # the user selected the UCs. Stored on run_sessions for provenance.
    set_id:         Optional[int] = None
    set_name:       Optional[str] = None
    selection_mode: Optional[str] = None  # 'set' | 'selection' | 'individual' | 'corpus'
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
    # When to_state == "approved", the API requires at least one passing run
    # attached to the UC (status='success' AND verdict IN ('supported',
    # 'partially_supported')). Set `override=True` plus a non-empty `notes`
    # reason to approve anyway (e.g. trivial UC that doesn't merit a test run).
    override: bool = False


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
            uc_handles=payload.uc_handles,
            uc_uuids=payload.uc_uuids,
            managed_uc_uuids=payload.managed_uc_uuids,
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
    # R2: snapshot the lifecycle state of every referenced managed UC at
    # trigger time so the result can later show "was this approved when
    # tested" even if the UC moves states or is deleted afterward.
    uc_state_snapshot = {}
    referenced_managed = set(payload.managed_uc_uuids or [])
    # Set-runs may include managed members via uc_uuids too, but managed
    # source UCs that aren't pushed are exclusively in managed_uc_uuids.
    # Also snapshot any uc_uuids that happen to be managed in the DB.
    if payload.uc_uuids:
        referenced_managed.update(payload.uc_uuids)
    if referenced_managed:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT uuid, lifecycle_state FROM managed_use_cases "
                    "WHERE uuid = ANY($1::text[])",
                    list(referenced_managed),
                )
            for r in rows:
                uc_state_snapshot[r["uuid"]] = r["lifecycle_state"] or "draft"
        except Exception as e:
            log.warning("uc-state snapshot failed: %s", e)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO run_sessions
                   (run_name, name, description, category, tags, mode,
                    created_by, started_at,
                    baseline_gen_tokens, baseline_prompt_tokens,
                    set_id, set_name, selection_mode, uc_state_snapshot)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, now(), $8, $9,
                           $10, $11, $12, $13::jsonb)""",
                result["name"], payload.name, payload.description,
                payload.category or "ad-hoc", payload.tags or [],
                payload.mode, reviewer,
                baseline_gen, baseline_prompt,
                payload.set_id, payload.set_name, payload.selection_mode,
                json.dumps(uc_state_snapshot) if uc_state_snapshot else None,
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
    """List all run result directories found on the workspace PVC.

    Each result is enriched with the human-readable `session_name` /
    `session_description` / `session_category` from `run_sessions` when
    the workspace run_id has been correlated to a Tekton PipelineRun
    (via `analysis_runs.run_name` ↔ `run_sessions.run_name`).
    """
    if not _results.is_available():
        return {"results": [], "available": False,
                "workspace_path": _results.WORKSPACE_PATH}
    try:
        runs = _results.list_runs()
        if runs and pool is not None:
            run_ids = [r["run_id"] for r in runs]
            async with pool.acquire() as conn:
                meta_rows = await conn.fetch(
                    """SELECT ar.run_id, ar.run_name,
                              rs.name AS session_name,
                              rs.description AS session_description,
                              rs.category AS session_category
                       FROM analysis_runs ar
                       LEFT JOIN run_sessions rs ON rs.run_name = ar.run_name
                       WHERE ar.run_id = ANY($1::text[])""",
                    run_ids,
                )
            meta_by_id = {m["run_id"]: m for m in meta_rows}
            for r in runs:
                m = meta_by_id.get(r["run_id"])
                if m:
                    r["run_name"]            = m["run_name"]
                    r["session_name"]        = m["session_name"] or None
                    r["session_description"] = m["session_description"] or None
                    r["session_category"]    = m["session_category"] or None
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
    per-UC verdicts from the analysis files AND per-UC lineage/state from
    the DB (R2: lifecycle_state_at_run, source_kind, session-level set
    context)."""
    if not _results.is_available():
        raise HTTPException(503, "workspace PVC not mounted")
    summary = _results.get_run_summary_enriched(run_id)
    if summary is None:
        raise HTTPException(404, f"run {run_id!r} not found")
    # R2: enrich with DB-stored lineage + state
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                meta = await conn.fetchrow(
                    """SELECT ar.run_name,
                              rs.set_id, rs.set_name, rs.selection_mode,
                              rs.name AS session_name
                       FROM analysis_runs ar
                       LEFT JOIN run_sessions rs ON rs.run_name = ar.run_name
                       WHERE ar.run_id = $1""",
                    run_id,
                )
                rows = await conn.fetch(
                    """SELECT uc_uuid, lifecycle_state_at_run, source_kind
                       FROM uc_analyses WHERE run_id = $1""",
                    run_id,
                )
            if meta:
                summary["session_name"]    = meta["session_name"] or None
                summary["set_id"]          = meta["set_id"]
                summary["set_name"]        = meta["set_name"] or None
                summary["selection_mode"]  = meta["selection_mode"] or None
            state_by_uuid = {r["uc_uuid"]: (r["lifecycle_state_at_run"], r["source_kind"]) for r in rows}
            for uc in (summary.get("ucs") or []):
                s = state_by_uuid.get(uc.get("uc_uuid"))
                if s:
                    uc["lifecycle_state_at_run"] = s[0]
                    uc["source_kind"] = s[1]
        except Exception as e:
            log.warning("get_result: lineage enrichment failed for %s: %s", run_id, e)
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
    model_config_id: Optional[int] = None
    endpoint_url: Optional[str] = None
    model_id: Optional[str] = None


class MCPServerIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    sse_url: str = Field(..., min_length=1, max_length=512)
    description: str = Field("", max_length=512)
    enabled: bool = True
    use_uc_assist: bool = False


class ModelConfigIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    provider: str = Field(..., pattern="^(openai|anthropic)$")
    endpoint_url: str = Field(..., min_length=1, max_length=512)
    model_id: str = Field(..., min_length=1, max_length=256)
    api_key: str = Field("", max_length=512)
    enabled: bool = True
    is_local: bool = False
    use_arch_review: bool = True
    use_uc_assist: bool = False


class ArchReviewIn(BaseModel):
    scope: str = Field(..., pattern="^(uc|run)$")
    model_config_id: Optional[int] = None
    endpoint_url: Optional[str] = None
    model_id: Optional[str] = None
    run_id: Optional[str] = None
    uc_uuid: Optional[str] = None

class EnhancementIn(BaseModel):
    scope: str = Field(..., pattern="^(uc|run)$")
    model_config_id: Optional[int] = None
    endpoint_url: Optional[str] = None
    model_id: Optional[str] = None
    run_id: Optional[str] = None
    uc_uuid: Optional[str] = None

class CodeRepoIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    provider: str = Field(..., pattern="^(github|gitlab)$")
    repo_url: str = Field(..., min_length=1, max_length=512)
    default_branch: str = Field("main", max_length=256)
    token: str = Field("", max_length=512)
    enabled: bool = True

class PrCreateIn(BaseModel):
    repo_config_id: int
    run_id: str
    uc_uuid: Optional[str] = None
    scope: str = Field("uc", pattern="^(uc|run)$")
    branch: str = Field(..., min_length=1, max_length=256)
    title: str = Field(..., min_length=1, max_length=512)
    base_branch: Optional[str] = None
    file_path: str = Field(..., min_length=1, max_length=512)
    enhancement_text: str = ""
    # R3 — approval gate: when any source UC is non-approved (or unknown
    # state — corpus UCs without a snapshot fall into a separate bucket),
    # the API returns 409 with the list. Setting override=true + a non-empty
    # override_reason allows the PR to be created anyway, and the reason is
    # noted in the PR body.
    override:        bool = False
    override_reason: Optional[str] = None


@app.get("/api/uc-assist/models")
async def list_uc_assist_models():
    """List all enabled model configs (api_key masked). Alias for /api/models filtered to enabled."""
    if pool is None:
        raise HTTPException(503, "pool not initialized")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, provider, endpoint_url, model_id,
                      CASE WHEN api_key != '' THEN '••••••••' ELSE '' END AS api_key,
                      enabled, is_local, use_arch_review, use_uc_assist,
                      created_by, created_at, updated_at
               FROM model_configs
               WHERE enabled
               ORDER BY name"""
        )
    return [dict(r) for r in rows]


@app.post("/api/uc-assist")
async def uc_assist_chat(payload: UCAssistIn, request: Request):
    """NL-assisted UC authoring — ask the model to draft or refine a UC.

    model_config_id selects which model_configs row to use (must have
    use_uc_assist=true).  Falls back to env-var config when omitted and
    no DB row is available.

    Returns {"explanation": str, "yaml_suggestion": str|null} on success,
    or {"error": str} if the assist endpoint is misconfigured or unreachable.
    """
    get_user(request)
    cfg: Optional[dict] = None
    if payload.model_config_id is not None:
        if pool is None:
            raise HTTPException(503, "pool not initialized")
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM model_configs WHERE id=$1 AND enabled",
                payload.model_config_id,
            )
        if not row:
            raise HTTPException(404, "Model config not found or disabled")
        cfg = dict(row)
    elif payload.endpoint_url and payload.model_id:
        if pool is not None:
            async with pool.acquire() as conn:
                base = await conn.fetchrow(
                    "SELECT provider, api_key FROM model_configs WHERE endpoint_url=$1 AND enabled ORDER BY id LIMIT 1",
                    payload.endpoint_url,
                )
        else:
            base = None
        cfg = {
            "provider":     base["provider"] if base else "openai",
            "endpoint_url": payload.endpoint_url,
            "model_id":     payload.model_id,
            "api_key":      base["api_key"] if base else "",
        }
    result = await uc_assist.chat(
        user_message=payload.message,
        current_yaml=payload.current_yaml,
        context=payload.context,
        cfg=cfg,
        pool=pool,
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


# Engine-side validation rules for managed UCs, mirrored here so authors see
# errors at save time instead of at run time. Hardcoded to the DCM consumer
# profile for now (single-consumer install). When multi-consumer ships, this
# should fetch from a per-consumer profile API.
_DCM_LIFECYCLE_PHASES = {
    "new_request", "modification", "decommission",
    "drift_detection", "brownfield_ingestion",
    "rehydration_faithful", "rehydration_provider_portable",
    "rehydration_historical_exact", "rehydration_historical_portable",
    "expiry_enforcement",
}
_DCM_RESOURCE_COMPLEXITIES = {
    "single_no_deps", "hard_dependencies", "composite_service",
    "conditional_soft_deps", "process_resource", "cross_dependency_payload",
}
_DCM_POLICY_COMPLEXITIES = {
    "system_defaults_only", "single_gatekeeper", "multi_policy_chain",
    "conflicting_policies", "orchestration_flow_static",
    "dynamic_conditional_flow", "cross_domain_constraint",
    "human_escalation_required", "governance_matrix_enforcement",
    "recovery_policy",
}
_DCM_PROVIDER_LANDSCAPES = {
    "single_eligible", "multiple_eligible", "none_eligible",
    "peer_dcm_required", "process_provider", "mixed",
}
_DCM_GOVERNANCE_CONTEXTS = {
    "no_governance", "standard_governance", "audit_heavy",
    "compliance_gated", "sovereignty_enforced",
}
_DCM_FAILURE_MODES = {
    "happy_path", "provider_failure", "policy_violation",
    "peer_dcm_disconnect", "data_inconsistency", "rollback_required",
    "partial_fulfillment", "timeout", "resource_exhaustion",
}
_DCM_PROFILES = {"minimal", "dev", "standard", "prod", "fsi", "sovereign"}
_VALID_GEN_MODES = {"regression", "pr-targeted", "authoring"}
_VALID_GEN_SOURCES = {"corpus", "llm-unguided", "llm-guided", "human-authored"}


def _validate_uc_yaml(parsed: dict) -> list[str]:
    """Return a list of human-readable validation errors for a parsed UC YAML.

    Mirrors the engine's UseCase.validate() against the DCM consumer profile
    so authors see issues at save time, not at run time. Empty list = valid.
    """
    errors: list[str] = []
    # uuid
    uid = parsed.get("uuid")
    if not isinstance(uid, str) or not uid.strip():
        errors.append("uuid is required and must be a non-empty string")
    elif not uid.startswith("uc-"):
        errors.append(f"uuid '{uid}' must start with 'uc-'")
    # generated_by
    gb = parsed.get("generated_by") or {}
    if not isinstance(gb, dict):
        errors.append("generated_by must be a mapping")
    else:
        m = gb.get("mode")
        if m not in _VALID_GEN_MODES:
            errors.append(f"generated_by.mode '{m}' not in {sorted(_VALID_GEN_MODES)}")
        s = gb.get("source")
        if s not in _VALID_GEN_SOURCES:
            errors.append(f"generated_by.source '{s}' not in {sorted(_VALID_GEN_SOURCES)}")
    # scenario
    sc = parsed.get("scenario") or {}
    if not isinstance(sc, dict):
        errors.append("scenario must be a mapping")
        return errors  # cascading checks below need a mapping
    for key in ("description", "intent"):
        v = sc.get(key)
        if not isinstance(v, str) or not v.strip():
            errors.append(f"scenario.{key} must not be empty")
    crit = sc.get("success_criteria")
    if not isinstance(crit, list) or not crit:
        errors.append("scenario.success_criteria must have at least one item")
    # actor
    actor = sc.get("actor") or {}
    if not isinstance(actor, dict):
        errors.append("scenario.actor must be a mapping")
    else:
        if not (actor.get("persona") or "").strip():
            errors.append("actor.persona must not be empty")
        if actor.get("profile") not in _DCM_PROFILES:
            errors.append(f"actor.profile '{actor.get('profile')}' not in {sorted(_DCM_PROFILES)}")
    # scenario.profile
    if sc.get("profile") not in _DCM_PROFILES:
        errors.append(f"scenario.profile '{sc.get('profile')}' not in {sorted(_DCM_PROFILES)}")
    # dimensions
    dims = sc.get("dimensions") or {}
    if not isinstance(dims, dict):
        errors.append("scenario.dimensions must be a mapping")
    else:
        for name, allowed in (
            ("lifecycle_phase",      _DCM_LIFECYCLE_PHASES),
            ("resource_complexity",  _DCM_RESOURCE_COMPLEXITIES),
            ("policy_complexity",    _DCM_POLICY_COMPLEXITIES),
            ("provider_landscape",   _DCM_PROVIDER_LANDSCAPES),
            ("governance_context",   _DCM_GOVERNANCE_CONTEXTS),
            ("failure_mode",         _DCM_FAILURE_MODES),
        ):
            v = dims.get(name)
            if v not in allowed:
                errors.append(f"dimensions.{name} '{v}' not in {sorted(allowed)}")
    return errors


def _derive_uc_title(parsed: dict, fallback_id: str) -> str:
    """Derive a human-readable title for a managed UC.

    Priority: top-level `title:` field > scenario.description > handle > uuid.
    Always truncated to 120 chars (matches the column constraint pragma).
    """
    raw = parsed.get("title")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:120]
    scenario = parsed.get("scenario")
    if isinstance(scenario, dict):
        desc = scenario.get("description")
        if isinstance(desc, str) and desc.strip():
            return desc.strip()[:120]
    handle = parsed.get("handle")
    if isinstance(handle, str) and handle.strip():
        return handle.strip()[:120]
    return fallback_id


@app.get("/api/use-cases")
async def list_use_cases(
    source: Optional[str] = Query(None, description="'managed', 'corpus', or None for both"),
):
    """List use cases — from the managed DB, the corpus files, or both.

    Each row carries `set_ids: [int]` so the merged UC/Sets UI can
    filter the list by set membership without an N+1 query per UC.
    """
    managed = []
    corpus_ucs = []

    # Pre-build uuid → [set_ids] map once for all UCs (managed + corpus).
    async with pool.acquire() as conn:
        member_rows = await conn.fetch(
            "SELECT uc_uuid, set_id FROM use_case_set_members"
        )
    set_ids_by_uuid: dict[str, list[int]] = {}
    for r in member_rows:
        set_ids_by_uuid.setdefault(r["uc_uuid"], []).append(int(r["set_id"]))

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
                "set_ids": set_ids_by_uuid.get(r["uuid"], []),
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
                uc_uuid = data.get("uuid")
                corpus_ucs.append({
                    "uuid":    uc_uuid,
                    "title":   data.get("scenario", {}).get("description", "")[:80]
                               if isinstance(data.get("scenario"), dict) else "",
                    "handle":  data.get("handle"),
                    "tags":    data.get("tags", []),
                    "path":    r["path"],
                    "source":  "corpus",
                    "set_ids": set_ids_by_uuid.get(uc_uuid, []),
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
            # Parse the YAML so the UI can render scenario/dimensions/intent
            # (renderUCDetail expects `parsed` like the corpus path returns)
            try:
                d["parsed"] = _yaml.safe_load(d.get("yaml_content") or "") or {}
                if not isinstance(d["parsed"], dict):
                    d["parsed"] = {}
            except Exception:
                d["parsed"] = {}
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


@app.post("/api/use-cases/validate")
async def validate_use_case(payload: ManagedUCIn):
    """Lint a UC YAML against engine validation rules without saving.

    Used by the UC editor's Validate button. Returns {ok, errors[]}.
    """
    try:
        data = _parse_uc_yaml(payload.yaml_content)
    except ValueError as e:
        return {"ok": False, "errors": [str(e)]}
    errors = _validate_uc_yaml(data)
    return {"ok": not errors, "errors": errors}


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

    # Pre-flight engine validation — catch bad enum values / missing uc-
    # prefix / etc. now instead of at run time. Returns 400 with a list.
    val_errors = _validate_uc_yaml(data)
    if val_errors:
        raise HTTPException(400, {
            "detail": "uc_validation_failed",
            "message": "UC YAML failed engine validation; fix and resubmit.",
            "errors": val_errors,
        })

    title = _derive_uc_title(data, uc_uuid)
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

    val_errors = _validate_uc_yaml(data)
    if val_errors:
        raise HTTPException(400, {
            "detail": "uc_validation_failed",
            "message": "UC YAML failed engine validation; fix and resubmit.",
            "errors": val_errors,
        })

    title = _derive_uc_title(data, uuid)
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


_PASSING_VERDICTS = ("supported", "partially_supported")


@app.post("/api/use-cases/{uuid}/transition")
async def transition_use_case(uuid: str, payload: LifecycleTransitionIn, request: Request):
    """Advance or retract a managed UC's lifecycle state.

    Approval gate: transitioning to `approved` requires at least one
    passing run on file (uc_analyses.status='success' AND verdict IN
    'supported' / 'partially_supported'). Soft override available via
    `override=True` + a non-empty `notes` reason — the override and
    reason are recorded in the lifecycle event.
    """
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

        # Approval gate
        if payload.to_state == "approved":
            passing_count = await conn.fetchval(
                "SELECT COUNT(*) FROM uc_analyses "
                "WHERE uc_uuid=$1 AND status='success' AND verdict = ANY($2::text[])",
                uuid, list(_PASSING_VERDICTS),
            )
            if not passing_count:
                if not payload.override:
                    raise HTTPException(
                        409,
                        "Cannot approve: no passing run on file. Run a test "
                        "evaluation first, OR set override=true with a notes "
                        "reason to approve anyway (e.g. trivial UC).",
                    )
                if not (payload.notes or "").strip():
                    raise HTTPException(
                        400,
                        "Override requires a non-empty notes reason explaining "
                        "why this UC is being approved without a passing run.",
                    )

        notes = payload.notes or ""
        if payload.override and payload.to_state == "approved":
            # Tag override in the notes so it's discoverable in lifecycle history
            notes = f"[OVERRIDE: no passing run] {notes}"
        async with conn.transaction():
            await conn.execute(
                "UPDATE managed_use_cases SET lifecycle_state=$2, updated_by=$3, updated_at=now() WHERE uuid=$1",
                uuid, payload.to_state, user,
            )
            await conn.execute(
                "INSERT INTO lifecycle_events(uc_uuid, from_state, to_state, actor, notes) "
                "VALUES ($1, $2, $3, $4, $5)",
                uuid, from_state, payload.to_state, user, notes,
            )
    log.info("UC %s: %s → %s by %s%s", uuid, from_state, payload.to_state, user,
             " (override)" if payload.override else "")
    return {"ok": True, "uuid": uuid, "from_state": from_state, "to_state": payload.to_state}


@app.get("/api/use-cases/{uuid}/runs")
async def get_use_case_runs(uuid: str, limit: int = 20):
    """Recent runs that processed this UC, newest first, with per-run verdict.

    Supports both managed and corpus UCs — the uuid is the join key on
    uc_analyses. Used by the UC detail pane's "Test history" section.
    """
    if limit < 1 or limit > 200:
        limit = 20
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT ua.run_id, ua.uc_handle, ua.status, ua.verdict,
                      ua.wall_time_seconds, ua.sample_count, ua.model,
                      ua.analyzed_at, ua.ingested_at,
                      (SELECT COUNT(*) FROM uc_gaps g WHERE g.analysis_id = ua.id) AS gap_count
               FROM uc_analyses ua
               WHERE ua.uc_uuid = $1
               ORDER BY COALESCE(ua.analyzed_at, ua.ingested_at) DESC
               LIMIT $2""",
            uuid, limit,
        )
    return {
        "uuid": uuid,
        "runs": [
            {
                "run_id": r["run_id"],
                "uc_handle": r["uc_handle"],
                "status": r["status"],
                "verdict": r["verdict"],
                "wall_time_seconds": r["wall_time_seconds"],
                "sample_count": r["sample_count"],
                "model": r["model"],
                "analyzed_at": r["analyzed_at"].isoformat() if r["analyzed_at"] else None,
                "ingested_at": r["ingested_at"].isoformat(),
                "gap_count": int(r["gap_count"] or 0),
            }
            for r in rows
        ],
    }


class PushToCorpusIn(BaseModel):
    target_path:    Optional[str] = None   # default: <corpus_subpath>/<handle>.yaml
    branch_name:    Optional[str] = None   # default: dav-push/<uc-uuid>
    base_branch:    Optional[str] = None   # default: configured corpus branch (or 'main')
    commit_message: Optional[str] = None   # default: derived from title + action
    pr_title:       Optional[str] = None
    pr_body:        Optional[str] = None
    # Push is gated on lifecycle_state == 'approved'. Set override=True to
    # force-push an unapproved UC (recorded in the PR body for transparency).
    override:       bool = False


@app.get("/api/corpus-push/status")
async def corpus_push_status():
    """Tell the UI whether push-to-corpus is configured + which host it targets.

    The UI uses this to enable/disable the Push button and surface the
    right message when something's missing (no token, unsupported host,
    no corpus URL set yet).
    """
    corpus_url = ""
    try:
        corpus = sources.get_source_state("corpus")
        corpus_url = (corpus or {}).get("repo_url", "") or ""
    except Exception:
        pass
    host = "github" if corpus_push.is_github(corpus_url) else \
           ("none" if not corpus_url else "unsupported")
    return {
        "configured":  corpus_push.is_configured(),
        "corpus_url":  corpus_url,
        "host":        host,
        "env_var":     corpus_push.GITHUB_TOKEN_ENV,
    }


@app.post("/api/use-cases/{uuid}/push-to-corpus")
async def push_use_case_to_corpus(uuid: str, payload: PushToCorpusIn, request: Request):
    """Open or refresh a PR that adds/updates this UC's YAML in the corpus repo.

    Reads the corpus repo URL + branch from the configured sources. Uses
    the GitHub Contents/Refs/Pulls API server-side; no shell-out to git.
    Updates managed_use_cases.corpus_* state so the UI can render the
    PR link and re-push action.
    """
    user = get_user(request)
    # Pull the UC
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM managed_use_cases WHERE uuid = $1", uuid
        )
    if not row:
        raise HTTPException(404, f"use case {uuid!r} not found in managed DB")
    uc = dict(row)

    # Lifecycle gate: only `approved` UCs may be pushed (soft override available).
    lc_state = uc.get("lifecycle_state") or "draft"
    if lc_state != "approved" and not payload.override:
        raise HTTPException(
            409,
            f"UC is in '{lc_state}'. Push requires lifecycle 'approved' "
            f"(move it through ready → in_review → approved), OR set "
            f"override=true to push anyway (will be noted in the PR body).",
        )

    # Resolve corpus repo config from the consumer's source ConfigMap
    try:
        corpus = sources.get_source_state("corpus")
    except Exception as e:
        raise HTTPException(500, f"Could not read corpus source state: {e}")
    corpus_url = (corpus or {}).get("repo_url", "")
    if not corpus_url:
        raise HTTPException(400, "No corpus repo URL configured (Config → Sources)")
    if not corpus_push.is_github(corpus_url):
        raise HTTPException(400, f"Unsupported corpus host (only GitHub today): {corpus_url}")
    if not corpus_push.is_configured():
        raise HTTPException(
            400,
            f"Push token not set — add {corpus_push.GITHUB_TOKEN_ENV} to the consumer Secret",
        )
    base_branch = payload.base_branch or corpus.get("repo_branch") or "main"
    owner, repo = corpus_push.parse_github_url(corpus_url)

    # Compute file path: <corpus_subpath>/<handle>.yaml; fall back to <uuid>.yaml.
    # Subpath is detected from the on-disk corpus clone (matches what the engine reads).
    try:
        parsed = _parse_uc_yaml(uc.get("yaml_content") or "")
    except ValueError as e:
        raise HTTPException(400, f"UC YAML invalid: {e}")
    handle = (parsed.get("handle") or "").strip().strip("/")
    subpath = ""
    for c in ("dav/use-cases", "use-cases"):
        if (Path(CORPUS_DIR) / c).is_dir():
            subpath = c
            break
    if not subpath:
        subpath = "dav/use-cases"   # DAV convention fallback
    if payload.target_path:
        file_path = payload.target_path.strip("/")
    elif handle:
        file_path = f"{subpath + '/' if subpath else ''}{handle}.yaml"
    else:
        file_path = f"{subpath + '/' if subpath else ''}{uuid}.yaml"

    # Branch + commit defaults
    branch_name = payload.branch_name or uc.get("corpus_branch") \
                  or f"dav-push/{uuid[:32]}"
    title = _derive_uc_title(parsed, uuid)
    action_verb = "Update" if uc.get("corpus_pr_url") else "Add"
    commit_message = payload.commit_message or f"{action_verb} UC: {title}"
    pr_title = payload.pr_title or commit_message
    override_note = ""
    if lc_state != "approved" and payload.override:
        override_note = (
            f"\n\n> ⚠ **Override:** UC is in `{lc_state}` state, not `approved`. "
            f"Pushed via the override path — reviewer should confirm intent."
        )
    pr_body = payload.pr_body or (
        f"Pushed from the DAV review console by `{user}`.\n\n"
        f"- UUID: `{uuid}`\n"
        f"- Handle: `{handle or '—'}`\n"
        f"- Path: `{file_path}`\n"
        f"- Lifecycle state at push: `{lc_state}`\n\n"
        f"This PR was opened or refreshed via the **Push to corpus** action."
        f"{override_note}"
    )

    existing_pr_number = None
    if uc.get("corpus_pr_url"):
        m = re.search(r"/pull/(\d+)", uc["corpus_pr_url"])
        if m:
            existing_pr_number = int(m.group(1))

    try:
        result = await corpus_push.push_uc_to_github(
            owner=owner,
            repo=repo,
            base_branch=base_branch,
            file_path=file_path,
            file_content=uc.get("yaml_content") or "",
            branch_name=branch_name,
            commit_message=commit_message,
            pr_title=pr_title,
            pr_body=pr_body,
            author_name=user or "dav-review-console",
            author_email=f"{user or 'dav-review-console'}@dav.local",
            existing_pr_number=existing_pr_number,
        )
    except RuntimeError as e:
        log.warning("push_to_corpus uuid=%s: %s", uuid, e)
        raise HTTPException(502, f"GitHub push failed: {e}")

    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE managed_use_cases
                  SET corpus_pr_url      = $2,
                      corpus_pr_state    = 'open',
                      corpus_commit_sha  = $3,
                      corpus_synced_at   = now(),
                      corpus_synced_by   = $4,
                      corpus_synced_path = $5,
                      corpus_branch      = $6
                WHERE uuid = $1""",
            uuid, result["pr_url"], result["commit_sha"], user,
            result["path"], result["branch"],
        )
    log.info("UC %s pushed to %s/%s on %s (PR #%s, %s)",
             uuid, owner, repo, result["branch"], result.get("pr_number"), result["action"])
    return {
        "ok":         True,
        "uuid":       uuid,
        "pr_url":     result["pr_url"],
        "pr_number":  result.get("pr_number"),
        "branch":     result["branch"],
        "commit_sha": result["commit_sha"],
        "path":       result["path"],
        "action":     result["action"],   # "created" | "updated"
    }


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
        "is_default": bool(r["is_default"]) if "is_default" in r else False,
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


@app.put("/api/sets/{set_id}/default")
async def set_default_set(set_id: int, request: Request):
    """Mark this Set as the project default. Clears the previous default.

    Used by the New Run modal to pre-populate UC selection and (later) by
    out-of-band run scheduling that needs an implicit UC set.
    """
    get_user(request)  # auth check
    async with pool.acquire() as conn:
        async with conn.transaction():
            exists = await conn.fetchval(
                "SELECT 1 FROM use_case_sets WHERE id=$1", set_id
            )
            if not exists:
                raise HTTPException(404, f"set {set_id} not found")
            await conn.execute(
                "UPDATE use_case_sets SET is_default=FALSE WHERE is_default AND id<>$1",
                set_id,
            )
            await conn.execute(
                "UPDATE use_case_sets SET is_default=TRUE, updated_at=now() WHERE id=$1",
                set_id,
            )
    return {"ok": True, "id": set_id, "is_default": True}


@app.delete("/api/sets/{set_id}/default")
async def clear_default_set(set_id: int, request: Request):
    """Unmark this Set as the project default, leaving no default."""
    get_user(request)
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE use_case_sets SET is_default=FALSE, updated_at=now() WHERE id=$1",
            set_id,
        )
    if result == "UPDATE 0":
        raise HTTPException(404, f"set {set_id} not found")
    return {"ok": True, "id": set_id, "is_default": False}


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
        # Serialize concurrent ingest requests for the same run (auto-ingest on
        # selection can race with a manual Re-ingest click). Lock key is a
        # stable 63-bit hash of the run_id so it never collides with schema.sql.
        import hashlib as _hashlib
        _lock_key = int(_hashlib.md5(f"ingest:{run_id}".encode()).hexdigest()[:15], 16)
        await conn.execute(f"SELECT pg_advisory_xact_lock({_lock_key})")
        # Clear existing ingestion for this run so re-ingestion is safe
        await conn.execute("DELETE FROM analysis_runs WHERE run_id=$1", run_id)

        # R2 — correlate this workspace run_id to its run_sessions row via
        # timestamp (workspace run_ids carry a timestamp prefix that's set when
        # the engine spawns; the run_sessions row was created at trigger time,
        # typically a few seconds earlier). Pull the uc_state_snapshot for the
        # per-UC lifecycle_state_at_run column.
        run_session = None
        run_started_ts = _parse_ts(summary.get("started_at"))
        if run_started_ts:
            run_session = await conn.fetchrow(
                """SELECT run_name, uc_state_snapshot, set_name, selection_mode
                   FROM run_sessions
                   WHERE started_at BETWEEN $1::timestamptz - interval '15 minutes'
                                        AND $1::timestamptz + interval '15 minutes'
                   ORDER BY ABS(EXTRACT(EPOCH FROM (started_at - $1::timestamptz))) ASC
                   LIMIT 1""",
                run_started_ts,
            )
        run_name_for_analysis = run_session["run_name"] if run_session else None
        uc_state_snapshot = {}
        if run_session and run_session["uc_state_snapshot"]:
            raw = run_session["uc_state_snapshot"]
            # asyncpg returns JSONB as str by default — parse defensively
            if isinstance(raw, str):
                try: uc_state_snapshot = json.loads(raw)
                except Exception: pass
            elif isinstance(raw, dict):
                uc_state_snapshot = raw

        await conn.execute(
            """INSERT INTO analysis_runs
               (run_id, run_name, mode, started_at, finished_at, total_ucs,
                successful, failed, total_samples)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
            run_id,
            run_name_for_analysis,
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

            # R2: state-at-run from the snapshot; if not in the snapshot,
            # the UC was corpus-source (no managed lifecycle).
            state_at_run = uc_state_snapshot.get(uc_uuid)
            source_kind = "managed" if state_at_run else "corpus"
            row = await conn.fetchrow(
                """INSERT INTO uc_analyses
                   (run_id, uc_uuid, uc_handle, status, verdict, overall_assessment,
                    wall_time_seconds, sample_count, engine_version, model,
                    endpoint_url, analyzed_at,
                    lifecycle_state_at_run, source_kind)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                   RETURNING id""",
                run_id, uc_uuid,
                uc.get("uc_handle"),
                uc.get("status"),
                uc.get("verdict"),
                overall,
                uc.get("wall_time_seconds"),
                uc.get("sample_count"),
                engine_version, model, endpoint_url, analyzed_at,
                state_at_run, source_kind,
            )
            analysis_id = row["id"]
            ingested_ucs += 1

            for gap_idx, gap in enumerate(gaps, 1):
                if not isinstance(gap, dict):
                    continue
                sev = gap.get("severity")
                if isinstance(sev, dict):
                    sev = sev.get("label")
                # Engine schema has no gap_id/title fields; auto-generate them.
                gap_id = gap.get("gap_id") or f"GAP-{gap_idx:03d}"
                desc = gap.get("description") or ""
                title = gap.get("title") or (desc[:80] + ("…" if len(desc) > 80 else ""))
                await conn.execute(
                    """INSERT INTO uc_gaps
                       (analysis_id, run_id, uc_uuid, gap_id, title,
                        description, severity, recommendation, rationale)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                    analysis_id, run_id, uc_uuid,
                    gap_id, title, desc, sev,
                    gap.get("recommendation"),
                    gap.get("rationale"),
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


# ========================= MCP SERVERS =========================


@app.get("/api/mcp-servers")
async def list_mcp_servers():
    if pool is None:
        raise HTTPException(503, "pool not initialized")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM mcp_server_configs ORDER BY name"
        )
    return [dict(r) for r in rows]


@app.post("/api/mcp-servers", status_code=201)
async def create_mcp_server(payload: MCPServerIn, request: Request):
    user = get_user(request)
    if pool is None:
        raise HTTPException(503, "pool not initialized")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO mcp_server_configs
                 (name, sse_url, description, enabled, use_uc_assist, created_by)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING *""",
            payload.name, payload.sse_url.rstrip("/"),
            payload.description, payload.enabled, payload.use_uc_assist, user,
        )
    return dict(row)


@app.put("/api/mcp-servers/{mid}")
async def update_mcp_server(mid: int, payload: MCPServerIn, request: Request):
    get_user(request)
    if pool is None:
        raise HTTPException(503, "pool not initialized")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE mcp_server_configs
               SET name=$1, sse_url=$2, description=$3, enabled=$4,
                   use_uc_assist=$5, updated_at=now()
               WHERE id=$6 RETURNING *""",
            payload.name, payload.sse_url.rstrip("/"),
            payload.description, payload.enabled, payload.use_uc_assist, mid,
        )
    if not row:
        raise HTTPException(404, "MCP server not found")
    return dict(row)


@app.delete("/api/mcp-servers/{mid}", status_code=204)
async def delete_mcp_server(mid: int, request: Request):
    get_user(request)
    if pool is None:
        raise HTTPException(503, "pool not initialized")
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM mcp_server_configs WHERE id=$1", mid)


@app.get("/api/mcp-servers/health")
async def mcp_servers_health():
    """Poll /health on each registered MCP server; returns per-server status."""
    if pool is None:
        raise HTTPException(503, "pool not initialized")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, sse_url, enabled FROM mcp_server_configs ORDER BY name"
        )

    async def check(row):
        if not row["enabled"]:
            return {"id": row["id"], "name": row["name"], "enabled": False, "healthy": False}
        base = row["sse_url"].rsplit("/sse", 1)[0]
        health_url = f"{base}/health"
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5.0, verify=False) as cx:
                resp = await cx.get(health_url)
            latency = int((time.monotonic() - t0) * 1000)
            healthy = resp.status_code == 200
            return {"id": row["id"], "name": row["name"], "enabled": True,
                    "healthy": healthy, "latency_ms": latency,
                    "status_code": resp.status_code}
        except Exception as e:
            return {"id": row["id"], "name": row["name"], "enabled": True,
                    "healthy": False, "error": str(e)}

    results = await asyncio.gather(*[check(r) for r in rows])
    return list(results)


# ========================= REVIEW MODELS =========================


@app.get("/api/models")
async def list_review_models():
    """List all configured model endpoints; api_key is masked."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, provider, endpoint_url, model_id,
                      CASE WHEN api_key != '' THEN '••••••••' ELSE '' END AS api_key,
                      enabled, is_local, use_arch_review, use_uc_assist,
                      created_by, created_at, updated_at
               FROM model_configs ORDER BY created_at"""
        )
    return [dict(r) for r in rows]


@app.post("/api/models", status_code=201)
async def create_review_model(payload: ModelConfigIn, request: Request):
    user = get_user(request)
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """INSERT INTO model_configs
                     (name, provider, endpoint_url, model_id, api_key, enabled,
                      is_local, use_arch_review, use_uc_assist, created_by)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                   RETURNING id, name, provider, endpoint_url, model_id, enabled,
                             is_local, use_arch_review, use_uc_assist, created_at""",
                payload.name, payload.provider, payload.endpoint_url,
                payload.model_id, payload.api_key, payload.enabled,
                payload.is_local, payload.use_arch_review, payload.use_uc_assist, user,
            )
        except Exception as e:
            if "unique" in str(e).lower():
                raise HTTPException(409, f"A model named '{payload.name}' already exists")
            raise HTTPException(500, str(e))
    return dict(row)


@app.put("/api/models/{mid}")
async def update_review_model(mid: int, payload: ModelConfigIn, request: Request):
    get_user(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE model_configs
               SET name=$1, provider=$2, endpoint_url=$3, model_id=$4,
                   api_key = CASE WHEN $5 != '' THEN $5 ELSE api_key END,
                   enabled=$6, is_local=$7, use_arch_review=$8, use_uc_assist=$9,
                   updated_at=now()
               WHERE id=$10 RETURNING id""",
            payload.name, payload.provider, payload.endpoint_url,
            payload.model_id, payload.api_key, payload.enabled,
            payload.is_local, payload.use_arch_review, payload.use_uc_assist, mid,
        )
    if not row:
        raise HTTPException(404, "Model config not found")
    return {"ok": True}


@app.delete("/api/models/{mid}")
async def delete_review_model(mid: int, request: Request):
    get_user(request)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM model_configs WHERE id=$1", mid)
    return {"ok": True}


# ========================= MODEL DEFAULTS =========================

_VALID_DEFAULT_KEYS = {"evaluation"}


class ModelDefaultIn(BaseModel):
    model_config_id: Optional[int] = None


@app.get("/api/model-defaults")
async def get_model_defaults():
    """Return project-scoped model defaults keyed by pipeline type."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT key, model_config_id FROM model_defaults")
    return {r["key"]: r["model_config_id"] for r in rows}


@app.put("/api/model-defaults/{key}")
async def set_model_default(key: str, payload: ModelDefaultIn, request: Request):
    """Set or clear a project-scoped model default."""
    if key not in _VALID_DEFAULT_KEYS:
        raise HTTPException(400, f"unknown default key: {key!r} — valid: {sorted(_VALID_DEFAULT_KEYS)}")
    user = get_user(request)
    if payload.model_config_id is not None:
        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT 1 FROM model_configs WHERE id=$1 AND enabled", payload.model_config_id
            )
        if not exists:
            raise HTTPException(404, "model config not found or disabled")
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO model_defaults (key, model_config_id, updated_by, updated_at)
               VALUES ($1, $2, $3, NOW())
               ON CONFLICT (key) DO UPDATE
               SET model_config_id = EXCLUDED.model_config_id,
                   updated_by      = EXCLUDED.updated_by,
                   updated_at      = NOW()""",
            key, payload.model_config_id, user,
        )
    return {"ok": True}


# ========================= ARCHITECTURAL REVIEW =========================


@app.post("/api/arch-review")
async def arch_review(payload: ArchReviewIn, request: Request):
    """Stream an architectural review from a configured model.

    Scope 'uc': reviews gaps for a single use case.
    Scope 'run': cross-cutting review across all UCs in a run.

    Returns text/event-stream with data: {"text": "..."} chunks,
    a final data: [DONE], or data: {"error": "..."} on failure.
    """
    get_user(request)
    from . import arch_review as _ar

    async with pool.acquire() as conn:
        if payload.model_config_id is not None:
            model_row = await conn.fetchrow(
                "SELECT * FROM model_configs WHERE id=$1 AND enabled",
                payload.model_config_id,
            )
            if not model_row:
                raise HTTPException(404, "Model config not found or disabled")
            model_row = dict(model_row)
        elif payload.endpoint_url and payload.model_id:
            # Custom endpoint+model: inherit provider/api_key from a registered
            # row at the same endpoint, falling back to openai/no-key.
            base = await conn.fetchrow(
                "SELECT provider, api_key FROM model_configs WHERE endpoint_url=$1 AND enabled ORDER BY id LIMIT 1",
                payload.endpoint_url,
            )
            model_row = {
                "provider":     base["provider"] if base else "openai",
                "endpoint_url": payload.endpoint_url,
                "model_id":     payload.model_id,
                "api_key":      base["api_key"]  if base else "",
            }
        else:
            raise HTTPException(400, "Provide model_config_id or endpoint_url+model_id")

        if payload.scope == "uc":
            if not payload.run_id or not payload.uc_uuid:
                raise HTTPException(400, "run_id and uc_uuid required for UC scope")
            analysis = await conn.fetchrow(
                "SELECT * FROM uc_analyses WHERE run_id=$1 AND uc_uuid=$2",
                payload.run_id, payload.uc_uuid,
            )
            if not analysis:
                raise HTTPException(404, "Analysis not found for this run+UC combination")
            gaps = await conn.fetch(
                "SELECT * FROM uc_gaps WHERE analysis_id=$1 ORDER BY id",
                analysis["id"],
            )
            uc = await conn.fetchrow(
                "SELECT uuid, yaml_content FROM managed_use_cases WHERE uuid=$1",
                payload.uc_uuid,
            )
            user_prompt = _ar._build_uc_prompt(
                dict(uc) if uc else {"uuid": payload.uc_uuid},
                dict(analysis),
                [dict(g) for g in gaps],
            )
            system_prompt = _ar._UC_SYSTEM

        else:  # run
            if not payload.run_id:
                raise HTTPException(400, "run_id required for run scope")
            uc_rows = await conn.fetch(
                "SELECT * FROM uc_analyses WHERE run_id=$1 ORDER BY uc_handle NULLS LAST",
                payload.run_id,
            )
            uc_analyses: list[dict] = []
            for ua in uc_rows:
                gaps = await conn.fetch(
                    "SELECT * FROM uc_gaps WHERE analysis_id=$1", ua["id"]
                )
                uc_analyses.append({**dict(ua), "gaps": [dict(g) for g in gaps]})
            user_prompt = _ar._build_run_prompt(payload.run_id, uc_analyses)
            system_prompt = _ar._RUN_SYSTEM

    model = dict(model_row)

    async def _gen():
        try:
            async for chunk in _ar.stream_review(
                provider=model["provider"],
                endpoint_url=model["endpoint_url"],
                model_id=model["model_id"],
                api_key=model["api_key"],
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            ):
                yield f"data: {json.dumps({'text': chunk})}\n\n"
        except Exception as exc:
            log.exception("Arch review stream error")
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


@app.get("/api/arch-review/prompt")
async def get_arch_review_prompt(
    scope: str = Query(..., pattern="^(uc|run)$"),
    run_id: str = Query(...),
    uc_uuid: Optional[str] = Query(None),
):
    """Return system + user prompts for an arch review without calling any model.

    Intended for copy-to-clipboard so users can paste into Claude Code or chat.
    """
    from . import arch_review as _ar

    async with pool.acquire() as conn:
        if scope == "uc":
            if not uc_uuid:
                raise HTTPException(400, "uc_uuid required for UC scope")
            analysis = await conn.fetchrow(
                "SELECT * FROM uc_analyses WHERE run_id=$1 AND uc_uuid=$2",
                run_id, uc_uuid,
            )
            if not analysis:
                raise HTTPException(404, "Analysis not found — select the run in Results to trigger ingest")
            gaps = await conn.fetch(
                "SELECT * FROM uc_gaps WHERE analysis_id=$1 ORDER BY id",
                analysis["id"],
            )
            uc = await conn.fetchrow(
                "SELECT uuid, yaml_content FROM managed_use_cases WHERE uuid=$1",
                uc_uuid,
            )
            user_prompt = _ar._build_uc_prompt(
                dict(uc) if uc else {"uuid": uc_uuid},
                dict(analysis),
                [dict(g) for g in gaps],
            )
            system_prompt = _ar._UC_SYSTEM
        else:
            uc_rows = await conn.fetch(
                "SELECT * FROM uc_analyses WHERE run_id=$1 ORDER BY uc_handle NULLS LAST",
                run_id,
            )
            uc_analyses: list[dict] = []
            for ua in uc_rows:
                gaps = await conn.fetch(
                    "SELECT * FROM uc_gaps WHERE analysis_id=$1", ua["id"]
                )
                uc_analyses.append({**dict(ua), "gaps": [dict(g) for g in gaps]})
            user_prompt = _ar._build_run_prompt(run_id, uc_analyses)
            system_prompt = _ar._RUN_SYSTEM

    return {"system_prompt": system_prompt, "user_prompt": user_prompt}


# ── Enhancement planning ──────────────────────────────────────────────────────

async def _enhancement_prompts(scope: str, run_id: str, uc_uuid: Optional[str], conn):
    """Shared DB logic for both the streaming and prompt-export endpoints."""
    from . import arch_review as _ar
    if scope == "uc":
        if not uc_uuid:
            raise HTTPException(400, "uc_uuid required for UC scope")
        analysis = await conn.fetchrow(
            "SELECT * FROM uc_analyses WHERE run_id=$1 AND uc_uuid=$2", run_id, uc_uuid
        )
        if not analysis:
            raise HTTPException(404, "Analysis not found — select the run in Results to trigger ingest")
        gaps = await conn.fetch("SELECT * FROM uc_gaps WHERE analysis_id=$1 ORDER BY id", analysis["id"])
        uc = await conn.fetchrow(
            "SELECT uuid, yaml_content FROM managed_use_cases WHERE uuid=$1", uc_uuid
        )
        return (
            _ar._build_enhancement_prompt(
                dict(uc) if uc else {"uuid": uc_uuid}, dict(analysis), [dict(g) for g in gaps]
            ),
            _ar._ENHANCEMENT_UC_SYSTEM,
        )
    else:
        uc_rows = await conn.fetch(
            "SELECT * FROM uc_analyses WHERE run_id=$1 ORDER BY uc_handle NULLS LAST", run_id
        )
        uc_analyses: list[dict] = []
        for ua in uc_rows:
            gaps = await conn.fetch("SELECT * FROM uc_gaps WHERE analysis_id=$1", ua["id"])
            uc_analyses.append({**dict(ua), "gaps": [dict(g) for g in gaps]})
        return _ar._build_enhancement_run_prompt(run_id, uc_analyses), _ar._ENHANCEMENT_RUN_SYSTEM


@app.post("/api/enhancements")
async def enhancements(payload: EnhancementIn, request: Request):
    """Stream enhancement specifications from a configured model.

    Same SSE protocol as /api/arch-review: data: {"text": "..."} chunks,
    data: [DONE] on completion, data: {"error": "..."} on failure.
    """
    from . import arch_review as _ar

    async with pool.acquire() as conn:
        if payload.model_config_id is not None:
            model_row = await conn.fetchrow(
                "SELECT * FROM model_configs WHERE id=$1 AND enabled",
                payload.model_config_id,
            )
            if not model_row:
                raise HTTPException(404, "Model config not found or disabled")
            model_row = dict(model_row)
        elif payload.endpoint_url and payload.model_id:
            base = await conn.fetchrow(
                "SELECT provider, api_key FROM model_configs WHERE endpoint_url=$1 AND enabled ORDER BY id LIMIT 1",
                payload.endpoint_url,
            )
            model_row = {
                "provider":     base["provider"] if base else "openai",
                "endpoint_url": payload.endpoint_url,
                "model_id":     payload.model_id,
                "api_key":      base["api_key"]  if base else "",
            }
        else:
            raise HTTPException(400, "Provide model_config_id or endpoint_url+model_id")
        if not payload.run_id:
            raise HTTPException(400, "run_id required")
        user_prompt, system_prompt = await _enhancement_prompts(
            payload.scope, payload.run_id, payload.uc_uuid, conn
        )

    model = model_row

    async def _gen():
        try:
            async for chunk in _ar.stream_review(
                provider=model["provider"],
                endpoint_url=model["endpoint_url"],
                model_id=model["model_id"],
                api_key=model["api_key"],
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            ):
                yield f"data: {json.dumps({'text': chunk})}\n\n"
        except Exception as exc:
            log.exception("enhancement stream error")
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


@app.get("/api/enhancements/prompt")
async def get_enhancement_prompt(
    scope: str = Query(..., pattern="^(uc|run)$"),
    run_id: str = Query(...),
    uc_uuid: Optional[str] = Query(None),
):
    """Return system + user prompts for enhancement planning without calling any model."""
    async with pool.acquire() as conn:
        user_prompt, system_prompt = await _enhancement_prompts(scope, run_id, uc_uuid, conn)
    return {"system_prompt": system_prompt, "user_prompt": user_prompt}


# ========================= CODE REPOSITORIES =========================


@app.get("/api/code-repos")
async def list_code_repos():
    """List configured code repos; token is masked."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, provider, repo_url, default_branch,
                      CASE WHEN token != '' THEN '••••••••' ELSE '' END AS token,
                      enabled, created_by, created_at, updated_at
               FROM code_repo_configs ORDER BY created_at"""
        )
    return [dict(r) for r in rows]


@app.post("/api/code-repos", status_code=201)
async def create_code_repo(payload: CodeRepoIn, request: Request):
    user = get_user(request)
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """INSERT INTO code_repo_configs
                     (name, provider, repo_url, default_branch, token, enabled, created_by)
                   VALUES ($1,$2,$3,$4,$5,$6,$7)
                   RETURNING id, name, provider, repo_url, default_branch,
                             CASE WHEN token != '' THEN '••••••••' ELSE '' END AS token,
                             enabled, created_by, created_at""",
                payload.name, payload.provider, payload.repo_url,
                payload.default_branch, payload.token, payload.enabled, user,
            )
        except Exception as e:
            if "unique" in str(e).lower():
                raise HTTPException(409, f"A repo named '{payload.name}' already exists")
            raise HTTPException(500, str(e))
    return dict(row)


@app.put("/api/code-repos/{rid}")
async def update_code_repo(rid: int, payload: CodeRepoIn, request: Request):
    get_user(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE code_repo_configs
               SET name=$1, provider=$2, repo_url=$3, default_branch=$4,
                   token = CASE WHEN $5 != '' THEN $5 ELSE token END,
                   enabled=$6, updated_at=now()
               WHERE id=$7 RETURNING id""",
            payload.name, payload.provider, payload.repo_url,
            payload.default_branch, payload.token, payload.enabled, rid,
        )
    if not row:
        raise HTTPException(404, "Code repo not found")
    return {"ok": True}


@app.delete("/api/code-repos/{rid}", status_code=204)
async def delete_code_repo(rid: int, request: Request):
    get_user(request)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM code_repo_configs WHERE id=$1", rid)
    return Response(status_code=204)


# ========================= PR / MR CREATION =========================


def _slugify_handle(handle: str) -> str:
    h = unicodedata.normalize("NFKD", handle)
    h = h.encode("ascii", "ignore").decode("ascii")
    h = re.sub(r"[^\w-]", "-", h).strip("-")
    h = re.sub(r"-+", "-", h)
    return h.lower() or "unknown"


async def _pr_gap_context(scope: str, run_id: str, uc_uuid: Optional[str], conn) -> dict:
    """Build PR metadata (title, branch, file_path, gap_context) from gap DB rows."""
    import base64 as _b64

    if scope == "uc":
        if not uc_uuid:
            raise HTTPException(400, "uc_uuid required for uc scope")
        ana = await conn.fetchrow(
            "SELECT uc_handle, verdict, overall_assessment FROM uc_analyses WHERE run_id=$1 AND uc_uuid=$2",
            run_id, uc_uuid,
        )
        if not ana:
            raise HTTPException(404, "Analysis not found for this run+UC")
        handle = ana["uc_handle"] or uc_uuid
        verdict = ana["verdict"] or "unknown"
        gaps = await conn.fetch(
            "SELECT gap_id, title, description, severity FROM uc_gaps WHERE run_id=$1 AND uc_uuid=$2 ORDER BY id",
            run_id, uc_uuid,
        )
        slug = _slugify_handle(handle)
        title = f"gap({handle}): address {len(gaps)} coverage gap(s)"
        branch = f"gap/{slug}"
        file_path = f"enhancements/{slug}.md"

        lines = [
            "## Context\n",
            f"**Run:** `{run_id}`  ",
            f"**Use case:** `{handle}`  ",
            f"**Verdict:** {verdict}  ",
            "",
            "## Gaps addressed\n",
        ]
        for g in gaps:
            sev = g["severity"] or ""
            try:
                import json as _j
                sev_obj = _j.loads(sev) if sev.startswith("{") else {}
                sev_label = sev_obj.get("label") or sev_obj.get("band") or sev
            except Exception:
                sev_label = sev
            lines.append(f"- **[{g['gap_id'] or '?'}]** {g['title'] or ''} *(severity: {sev_label})*")
            if g["description"]:
                lines.append(f"  > {g['description'][:300]}")
    else:
        # Run scope
        uc_rows = await conn.fetch(
            "SELECT DISTINCT uc_uuid, uc_handle, verdict FROM uc_analyses WHERE run_id=$1 ORDER BY uc_handle",
            run_id,
        )
        gap_rows = await conn.fetch(
            "SELECT uc_uuid, gap_id, title, description, severity FROM uc_gaps WHERE run_id=$1 ORDER BY uc_uuid, id",
            run_id,
        )
        title = f"gap(run/{run_id}): cross-cutting enhancements"
        branch = f"gap/run-{_slugify_handle(run_id)}"
        file_path = f"enhancements/run-{_slugify_handle(run_id)}.md"
        handle = run_id
        verdict = f"{len(uc_rows)} UCs"

        lines = [
            "## Context\n",
            f"**Run:** `{run_id}`  ",
            f"**Use cases:** {len(uc_rows)}  ",
            f"**Total gaps:** {len(gap_rows)}  ",
            "",
            "## Gaps addressed\n",
        ]
        by_uc = {}
        for g in gap_rows:
            by_uc.setdefault(g["uc_uuid"], []).append(g)
        for u in uc_rows:
            uc_gaps = by_uc.get(u["uc_uuid"], [])
            if not uc_gaps:
                continue
            lines.append(f"\n### {u['uc_handle'] or u['uc_uuid']} — {u['verdict'] or 'unknown'}\n")
            for g in uc_gaps:
                lines.append(f"- **[{g['gap_id'] or '?'}]** {g['title'] or ''}")

    gap_context = "\n".join(lines)
    return {"title": title, "branch": branch, "file_path": file_path, "gap_context": gap_context}


@app.get("/api/pr/preview")
async def pr_preview(
    scope: str = Query(..., pattern="^(uc|run)$"),
    run_id: str = Query(...),
    uc_uuid: Optional[str] = Query(None),
):
    """Return PR metadata (title, branch, file_path, gap_context) without touching any remote."""
    async with pool.acquire() as conn:
        return await _pr_gap_context(scope, run_id, uc_uuid, conn)


def _parse_repo_url(provider: str, repo_url: str) -> tuple[str, str]:
    """Return (api_base, repo_path) from a GitHub/GitLab repo URL.

    GitHub: https://github.com/owner/repo → ('https://api.github.com', 'owner/repo')
    GitLab: https://gitlab.com/group/repo → ('https://gitlab.com', 'group/repo')
            or self-hosted: https://gitlab.example.com/group/repo
    """
    from urllib.parse import urlparse
    parsed = urlparse(repo_url.rstrip("/").removesuffix(".git"))
    path = parsed.path.lstrip("/")
    if provider == "github":
        return "https://api.github.com", path
    else:
        # GitLab: API lives at same host
        return f"{parsed.scheme}://{parsed.netloc}", path


async def _github_create_pr(
    token: str, repo_url: str, base_branch: str,
    branch: str, title: str, body: str, file_path: str, file_content: str,
    timeout: float = 30.0,
) -> str:
    api_base, repo_path = _parse_repo_url("github", repo_url)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    import base64 as _b64
    async with httpx.AsyncClient(timeout=timeout) as cx:
        # 1. Get base branch SHA
        r = await cx.get(f"{api_base}/repos/{repo_path}/git/ref/heads/{base_branch}", headers=headers)
        if r.status_code != 200:
            raise HTTPException(502, f"GitHub: could not read base branch '{base_branch}': {r.text[:200]}")
        sha = r.json()["object"]["sha"]

        # 2. Create branch
        r = await cx.post(f"{api_base}/repos/{repo_path}/git/refs", headers=headers, json={
            "ref": f"refs/heads/{branch}", "sha": sha,
        })
        if r.status_code not in (201, 422):  # 422 = branch already exists
            raise HTTPException(502, f"GitHub: branch creation failed: {r.text[:200]}")

        # 3. Create/update file
        encoded = _b64.b64encode(file_content.encode()).decode()
        r = await cx.put(
            f"{api_base}/repos/{repo_path}/contents/{file_path}", headers=headers,
            json={"message": title, "content": encoded, "branch": branch},
        )
        if r.status_code not in (200, 201):
            raise HTTPException(502, f"GitHub: file creation failed: {r.text[:200]}")

        # 4. Create PR
        r = await cx.post(f"{api_base}/repos/{repo_path}/pulls", headers=headers, json={
            "title": title, "body": body, "head": branch, "base": base_branch,
        })
        if r.status_code not in (200, 201):
            raise HTTPException(502, f"GitHub: PR creation failed: {r.text[:200]}")
        return r.json()["html_url"]


async def _gitlab_create_mr(
    token: str, repo_url: str, base_branch: str,
    branch: str, title: str, body: str, file_path: str, file_content: str,
    timeout: float = 30.0,
) -> str:
    from urllib.parse import quote
    import base64 as _b64
    api_base, repo_path = _parse_repo_url("gitlab", repo_url)
    encoded_path = quote(repo_path, safe="")
    headers = {"PRIVATE-TOKEN": token, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=timeout) as cx:
        # 1. Create branch
        r = await cx.post(
            f"{api_base}/api/v4/projects/{encoded_path}/repository/branches",
            headers=headers,
            params={"branch": branch, "ref": base_branch},
        )
        if r.status_code not in (200, 201):
            raise HTTPException(502, f"GitLab: branch creation failed: {r.text[:200]}")

        # 2. Create file
        encoded_file = quote(file_path, safe="")
        r = await cx.post(
            f"{api_base}/api/v4/projects/{encoded_path}/repository/files/{encoded_file}",
            headers=headers,
            json={
                "branch": branch,
                "content": file_content,
                "commit_message": title,
                "encoding": "text",
            },
        )
        if r.status_code not in (200, 201):
            raise HTTPException(502, f"GitLab: file creation failed: {r.text[:200]}")

        # 3. Create MR
        r = await cx.post(
            f"{api_base}/api/v4/projects/{encoded_path}/merge_requests",
            headers=headers,
            json={
                "source_branch": branch,
                "target_branch": base_branch,
                "title": title,
                "description": body,
                "remove_source_branch": True,
            },
        )
        if r.status_code not in (200, 201):
            raise HTTPException(502, f"GitLab: MR creation failed: {r.text[:200]}")
        return r.json()["web_url"]


@app.post("/api/pr/create")
async def pr_create(payload: PrCreateIn, request: Request):
    """Push a branch with the enhancement spec and open a PR/MR.

    R3 approval gate: if any source UC's `lifecycle_state_at_run` is
    non-approved, the request is rejected with 409 unless
    `override=true` + a non-empty `override_reason` is supplied. The
    override + reason are then noted in the PR body.
    """
    user = get_user(request)
    async with pool.acquire() as conn:
        repo_row = await conn.fetchrow(
            "SELECT provider, repo_url, default_branch, token, enabled FROM code_repo_configs WHERE id=$1",
            payload.repo_config_id,
        )
        if not repo_row:
            raise HTTPException(404, "Code repo config not found")
        if not repo_row["enabled"]:
            raise HTTPException(400, "Code repo is disabled")
        if not repo_row["token"]:
            raise HTTPException(400, "No token configured for this repo")

        # R3 — collect non-approved source UCs (defense in depth alongside
        # the client-side warning). Corpus-sourced UCs (no lifecycle state)
        # are not gated.
        if payload.scope == "uc" and payload.uc_uuid:
            gate_rows = await conn.fetch(
                """SELECT uc_uuid, uc_handle, lifecycle_state_at_run, source_kind
                   FROM uc_analyses
                   WHERE run_id=$1 AND uc_uuid=$2""",
                payload.run_id, payload.uc_uuid,
            )
        else:
            gate_rows = await conn.fetch(
                """SELECT uc_uuid, uc_handle, lifecycle_state_at_run, source_kind
                   FROM uc_analyses
                   WHERE run_id=$1""",
                payload.run_id,
            )
        non_approved = [
            {
                "uc_uuid":  r["uc_uuid"],
                "uc_handle": r["uc_handle"],
                "state":    r["lifecycle_state_at_run"],
                "source":   r["source_kind"],
            }
            for r in gate_rows
            if (r["source_kind"] == "managed"
                and (r["lifecycle_state_at_run"] or "draft") != "approved")
        ]
        if non_approved and not payload.override:
            raise HTTPException(409, {
                "detail": "approval_gate",
                "message": f"{len(non_approved)} source UC(s) not in 'approved' state — refusing to create PR.",
                "non_approved": non_approved,
                "hint": "Approve the UCs first, or pass override=true with a non-empty override_reason.",
            })
        if payload.override and non_approved and not (payload.override_reason or "").strip():
            raise HTTPException(400, "override_reason is required when override=true and source UCs are not approved")

        ctx = await _pr_gap_context(payload.scope, payload.run_id, payload.uc_uuid, conn)

    gap_context = ctx["gap_context"]
    enh = payload.enhancement_text.strip()
    pr_body = gap_context
    if enh:
        pr_body += "\n\n## Enhancement specification\n\n" + enh
    # R3 — annotate override in the PR body for the corpus reviewer to see
    if payload.override and non_approved:
        lines = [
            "",
            "> ⚠ **Approval gate overridden** — this PR was generated from results that include "
            f"{len(non_approved)} UC(s) NOT in the `approved` state at run time.",
            f"> Override invoked by `{user}` with the reason:",
            f"> > {payload.override_reason.strip()}",
            ">",
            "> UCs at the time of the run:",
        ]
        for nr in non_approved:
            lines.append(
                f"> - `{nr['uc_handle'] or nr['uc_uuid']}` — `{nr['state'] or 'draft'}` (source: {nr['source']})"
            )
        pr_body += "\n" + "\n".join(lines)
    pr_body += "\n\n---\n*Generated by DAV Console*"

    file_content = f"# {payload.title}\n\n{pr_body}"
    base_branch = payload.base_branch or repo_row["default_branch"]
    provider = repo_row["provider"]

    try:
        if provider == "github":
            pr_url = await _github_create_pr(
                token=repo_row["token"],
                repo_url=repo_row["repo_url"],
                base_branch=base_branch,
                branch=payload.branch,
                title=payload.title,
                body=pr_body,
                file_path=payload.file_path,
                file_content=file_content,
            )
        else:
            pr_url = await _gitlab_create_mr(
                token=repo_row["token"],
                repo_url=repo_row["repo_url"],
                base_branch=base_branch,
                branch=payload.branch,
                title=payload.title,
                body=pr_body,
                file_path=payload.file_path,
                file_content=file_content,
            )
    except HTTPException:
        raise
    except Exception as e:
        log.exception("pr_create failed")
        raise HTTPException(502, f"Remote API error: {e}")

    return {"pr_url": pr_url}
