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
import secrets
import tarfile
import time
from datetime import datetime, timedelta, timezone
import unicodedata
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Union

import asyncpg
import httpx
import yaml as _yaml
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .corpus_loader import walk_corpus, parse_patterns
from .uc_priority import (
    PRIORITY_DEFAULTS as _PRIORITY_DEFAULTS,
    normalize_priority as _normalize_uc_priority,
    derive_priority as _derive_uc_priority,
)
from . import capability_density as _capability_density
from . import capability_graph as _capability_graph
from . import capability_catalog as _capability_catalog
from . import assessment_ingest as _assessment_ingest
from . import api_tokens
from . import maturity_seed as _maturity_seed
from . import db_bootstrap as _db_bootstrap
from . import maturity_scoring as _maturity_scoring
from . import prompts_registry as _prompts_registry
from . import analysis_compare as _analysis_compare
from . import uc_readiness as _uc_readiness
from .uc_list import collapse_duplicates as _collapse_uc_duplicates
from . import validations
from . import sources
from . import metrics
from . import rbac
from . import audit
from . import results as _results
from . import uc_assist
from . import corpus_push
from . import enhancement_apply as _enh_apply
from . import repos as _repos
from .repos import _parse_jsonb
from . import projector as _projector
from . import pr_comments as _pr_comments
from . import credentials as _credentials
from . import failure_taxonomy as _ft
from . import ldap_auth
from . import local_auth
from . import crypto
from . import diagnose as _diagnose
from . import experiment_eval as _expeval
from . import run_selection as _run_selection

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
MIGRATE_007_PATH = Path(__file__).parent / "migrate_007_managed_repos.sql"
MIGRATE_008_PATH = Path(__file__).parent / "migrate_008_pr_comments.sql"
MIGRATE_009_PATH = Path(__file__).parent / "migrate_009_repo_credentials.sql"
MIGRATE_010_PATH = Path(__file__).parent / "migrate_010_shared_credentials.sql"
MIGRATE_011_PATH = Path(__file__).parent / "migrate_011_consolidate_code_repos.sql"
MIGRATE_012_PATH = Path(__file__).parent / "migrate_012_namespace_first_class.sql"
MIGRATE_013_PATH = Path(__file__).parent / "migrate_013_infrastructure_confidence.sql"
MIGRATE_014_PATH = Path(__file__).parent / "migrate_014_model_capabilities.sql"
MIGRATE_015_PATH = Path(__file__).parent / "migrate_015_improvement_proposals.sql"
MIGRATE_016_PATH = Path(__file__).parent / "migrate_016_experiments.sql"
MIGRATE_017_PATH = Path(__file__).parent / "migrate_017_capability_catalog.sql"
MIGRATE_018_PATH = Path(__file__).parent / "migrate_018_audit_log.sql"
MIGRATE_019_PATH = Path(__file__).parent / "migrate_019_assessments.sql"
MIGRATE_020_PATH = Path(__file__).parent / "migrate_020_reconcile_catalog.sql"
MIGRATE_021_PATH = Path(__file__).parent / "migrate_021_maturity_wall.sql"
MIGRATE_022_PATH = Path(__file__).parent / "migrate_022_api_tokens.sql"
MIGRATE_023_PATH = Path(__file__).parent / "migrate_023_recording_jobs.sql"
MIGRATE_024_PATH = Path(__file__).parent / "migrate_024_branch_tracking.sql"
MIGRATE_025_PATH = Path(__file__).parent / "migrate_025_agent_accounts.sql"
MIGRATE_026_PATH = Path(__file__).parent / "migrate_026_repos_project_unique.sql"
ANON_REVIEWER = os.environ.get("ANONYMOUS_REVIEWER", "anonymous")
ALLOW_ANON_WRITES = os.environ.get("ALLOW_ANON_WRITES", "false").lower() == "true"
# Secured dav-docs-mcp self-registration (its LoadBalancer SSE URL + bearer token).
DAV_DOCS_MCP_URL = os.environ.get("DAV_DOCS_MCP_URL", "").strip()
DAV_DOCS_MCP_TOKEN = os.environ.get("DAV_DOCS_MCP_TOKEN", "").strip()
# --- Internal service-to-service auth (engine → this API) ---------------------
# The engine fetches managed UCs from this API's gated /api/use-cases endpoint.
# Rather than a shared static secret, it presents its Kubernetes ServiceAccount
# *projected token* (audience-scoped, ~1h TTL, auto-rotated by the kubelet) as a
# Bearer token. We validate it via the TokenReview API and accept ONLY our
# pipeline ServiceAccount scoped to our audience — short-lived, identity-bound,
# nothing static to leak. A valid token resolves to identity "system:engine" and
# bypasses the approval gate + project privilege checks for that request only,
# WITHOUT widening the externally-reachable surface.
INTERNAL_IDENTITY = "system:engine"
DAV_API_AUDIENCE = os.environ.get("DAV_API_AUDIENCE", "dav-api").strip()
_TRUSTED_SERVICE_ACCOUNTS = set(
    s.strip() for s in os.environ.get(
        "DAV_TRUSTED_SERVICE_ACCOUNTS",
        f"system:serviceaccount:{os.environ.get('DAV_NAMESPACE', 'dav')}:pipeline",
    ).split(",") if s.strip()
)
# Short-lived positive/negative cache keyed by token digest, so a multi-UC fetch
# (~23 calls) doesn't issue 23 TokenReviews.
_svc_token_cache: dict = {}


async def _validate_service_token(request) -> bool:
    """Validate the request's Bearer SA token via TokenReview (cached briefly by
    digest). Network call → offloaded to a thread. Returns True iff it's our
    trusted, audience-scoped pipeline ServiceAccount."""
    authz = request.headers.get("Authorization", "")
    if not authz.startswith("Bearer "):
        return False
    token = authz[7:].strip()
    if token.count(".") != 2:   # must look like a JWT — skip cheap bogus values
        return False
    import hashlib
    import time as _t
    key = hashlib.sha256(token.encode()).hexdigest()
    now = _t.monotonic()
    hit = _svc_token_cache.get(key)
    if hit and hit[1] > now:
        return hit[0]
    try:
        ok = await asyncio.to_thread(
            validations.review_service_token,
            token, DAV_API_AUDIENCE, _TRUSTED_SERVICE_ACCOUNTS,
        )
    except Exception as e:
        log.warning("service-token validation error: %s", e)
        ok = None
    # ok is None for a TRANSIENT TokenReview failure — do NOT cache it, so the
    # next request (e.g. the engine's next managed-UC fetch with the same token)
    # re-validates immediately instead of being locked out. Only cache definite
    # outcomes: positive 60s; negative a SHORT 2s (just enough to dampen a flood
    # of distinct bogus tokens without penalising a valid one that blipped).
    if ok is None:
        return False
    # Cap the cache so a flood of distinct bogus tokens can't grow it unbounded.
    if len(_svc_token_cache) > 512:
        _svc_token_cache.clear()
    _svc_token_cache[key] = (ok, now + (60.0 if ok else 2.0))
    return ok


def _service_token_ok(request) -> bool:
    """Sync read of the per-request flag set by the approval gate after a
    successful TokenReview. The endpoints/guards call this; the (async)
    validation itself happens once, up front, in _approval_gate."""
    return bool(getattr(request.state, "_svc_ok", False))

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
                    "SELECT run_name, started_at, "
                    "  (trigger_payload->>'effective_timeout_seconds')::int AS eff_timeout, "
                    "  coalesce((trigger_payload->>'console_timeout_fired')::bool, false) AS timeout_fired "
                    "FROM run_sessions "
                    "WHERE finalized_at IS NULL "
                    "AND created_at < now() - interval '2 minutes' "
                    "ORDER BY created_at DESC LIMIT 20"
                )
            for r in rows:
                try:
                    detail = await asyncio.to_thread(validations.get_run_detail, r["run_name"])
                    if detail.get("phase") in TERMINAL_PHASES:
                        await _maybe_finalize_session(detail)
                    elif (r["eff_timeout"] and r["started_at"] and not r["timeout_fired"]
                          and (datetime.now(timezone.utc) - r["started_at"]).total_seconds() > r["eff_timeout"]):
                        # Console-enforced "time allowed": the Tekton spec timeout is the
                        # immutable 24h failsafe; the effective (user/ETA) timeout is
                        # enforced here by cancelling — a status update Tekton allows.
                        log.warning("run %s exceeded console time-allowed (%ss) — cancelling",
                                    r["run_name"], r["eff_timeout"])
                        await asyncio.to_thread(validations.cancel_run, r["run_name"])
                        async with pool.acquire() as conn:
                            await conn.execute(
                                "UPDATE run_sessions SET trigger_payload = jsonb_set("
                                "  coalesce(trigger_payload,'{}'::jsonb),"
                                "  '{console_timeout_fired}', 'true'::jsonb)"
                                " WHERE run_name=$1", r["run_name"])
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


async def _analysis_ingest_loop():
    """Background task: scan the workspace `results/` directory for
    run-summary.yaml files whose run_id isn't in `analysis_runs` yet,
    and ingest them. Runs every 5 minutes + at startup. Idempotent —
    `_ingest_run_analyses()` upserts and is safe to re-call.

    Without this, `/api/use-cases/{uuid}/runs`, the UC detail panel's
    "Test history" section, and run-comparison views silently return
    empty for any run not manually ingested via `POST /api/analysis/ingest/{run_id}`.
    The `/api/results` endpoint always worked because it reads the PVC
    directly; the manual-ingest gap was an artifact (engine writes to
    disk; nobody wired the post-pipeline auto-ingest before now).
    """
    import asyncio
    SLEEP_SECONDS = 300   # 5 minutes
    INITIAL_DELAY = 10    # let the pool + migrations settle first
    await asyncio.sleep(INITIAL_DELAY)
    while True:
        try:
            on_disk = []
            try:
                on_disk = [r["run_id"] for r in _results.list_runs() if r.get("run_id")]
            except Exception as e:
                log.info("ingest loop: list_runs failed (%s); workspace not mounted yet?", e)
            if on_disk:
                async with pool.acquire() as conn:
                    ingested_rows = await conn.fetch(
                        "SELECT run_id FROM analysis_runs WHERE run_id = ANY($1)",
                        on_disk,
                    )
                ingested = {r["run_id"] for r in ingested_rows}
                pending = [rid for rid in on_disk if rid not in ingested]
                if pending:
                    log.info("ingest loop: %d run(s) on disk not yet in DB; ingesting", len(pending))
                for rid in pending:
                    try:
                        async with pool.acquire() as conn:
                            result = await _ingest_run_analyses(rid, conn)
                        log.info(
                            "ingest loop: %s — ucs=%s gaps=%s",
                            rid, result.get("ucs_ingested"), result.get("gaps_ingested"),
                        )
                    except Exception as e:
                        log.warning("ingest loop: %s failed (%s); will retry next pass", rid, e)
            await asyncio.sleep(SLEEP_SECONDS)
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.warning("ingest loop hiccup: %s", e)
            await asyncio.sleep(SLEEP_SECONDS)


async def _backfill_uc_projections(conn: "asyncpg.Connection") -> int:
    """Self-healing: fill priority + readiness projections for managed UCs saved
    before those columns existed (DCM features #1/#4). Keyed on readiness_score
    IS NULL — readiness is always computable, so NULL unambiguously means "never
    projected" (priority legitimately stays NULL for unranked UCs, so it can't be
    the signal). Idempotent: a no-op once every row is projected, since save and
    import now stamp these columns. Unparseable legacy rows are left NULL (they
    re-scan harmlessly next boot) rather than stamped with misleading scores."""
    rows = await conn.fetch(
        "SELECT uuid, yaml_content FROM managed_use_cases WHERE readiness_score IS NULL"
    )
    if not rows:
        return 0
    n = 0
    for r in rows:
        try:
            data = _parse_uc_yaml(r["yaml_content"] or "")
        except ValueError:
            log.warning("backfill: skipping unparseable UC %s", r["uuid"])
            continue
        priority, priority_score = _derive_uc_priority(data)
        readiness = _uc_readiness.score_use_case(data)["score"]
        await conn.execute(
            "UPDATE managed_use_cases SET priority=$2, priority_score=$3, readiness_score=$4 WHERE uuid=$1",
            r["uuid"], priority, priority_score, readiness,
        )
        n += 1
    if n:
        log.info("Backfilled priority/readiness projections for %d managed UC(s)", n)
    return n


# Tenancy Phase 2: runtime connections resolve tables via this search_path. Default 'public' = the
# pre-tenancy single-schema layout (no behavior change). After the schema-per-tenant migration, set
# DAV_RUNTIME_SEARCH_PATH='tenant_flightpath, public' so client tables resolve to the tenant schema and
# control/platform tables fall through to public. Validated by dry-run: boot MUST run DDL in `public`
# (CREATE TABLE IF NOT EXISTS only checks the FIRST search_path schema, so a tenant-first path would
# shadow control tables) — the boot conn below forces search_path=public for migrations/schema.
import re as _re_sp
_DEFAULT_SEARCH_PATH = (os.environ.get("DAV_RUNTIME_SEARCH_PATH", "public") or "public").strip()
if not _re_sp.match(r'^[A-Za-z0-9_,\s]+$', _DEFAULT_SEARCH_PATH):
    log.warning("DAV_RUNTIME_SEARCH_PATH %r is invalid; falling back to 'public'", _DEFAULT_SEARCH_PATH)
    _DEFAULT_SEARCH_PATH = "public"


async def _pool_setup(conn):
    """Runs on every pool.acquire(): pin the runtime search_path so client/control tables resolve
    to the right schema. The boot overrides to 'public' for DDL (see lifespan)."""
    await conn.execute(f"SET search_path = {_DEFAULT_SEARCH_PATH}")


async def _control_seeds(conn) -> None:
    """Control-plane seeds — run once in `public` (search_path set by _db_bootstrap)."""
    await _seed_docs_mcp(conn)
    try:
        await _capability_catalog.seed_dcm_taxonomy(conn)
    except Exception:
        log.exception("DCM taxonomy seed failed (non-fatal)")
    try:
        await _maturity_seed.seed_default_framework(conn)
    except Exception:
        log.exception("default maturity framework seed failed (non-fatal)")
    await _migrate_code_repo_configs(conn)


async def _client_seeds(conn, schema: str) -> None:
    """Per-tenant client seeds — run per tenant schema (search_path=<schema>,public, set by _db_bootstrap)."""
    await _seed_corpus(conn)
    await _seed_managed_repos(conn)
    await _backfill_uc_projections(conn)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    log.info("Connecting to Postgres...")
    pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=8, command_timeout=30, setup=_pool_setup)
    async with pool.acquire() as conn:
        # Tenancy Phase 2: schema-aware bootstrap. Control DDL/seeds -> public; client DDL/seeds ->
        # each tenant schema (search_path managed by _db_bootstrap). The legacy flat MIGRATE_0xx +
        # SCHEMA_PATH list is folded into the generated base schemas (schema_control/client.sql);
        # existing schemas are adopted (not re-run), empty ones get the base. See
        # docs/tenancy-phase2-tenant-aware-runner.md.
        await _db_bootstrap.bootstrap(conn, control_seeds=_control_seeds, client_seeds=_client_seeds)
    await _load_ldap_cfg()
    await _load_smtp_cfg()
    await _seed_default_admin()
    # Enforce the break-glass invariant: the config default admin is enabled iff
    # no other enabled platform admin exists (reconciled here + after role changes).
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                await _reconcile_admin(conn)
        except Exception:
            log.exception("default-admin reconcile failed on boot")
    # ALWAYS load the internal approved set on boot. _seed_default_admin only
    # reloads it on the boot that first inserts; on later boots it early-returns,
    # and with no LDAP there's no sync — leaving _approved_lower empty and
    # locking out every internal user (incl. the break-glass admin) once
    # DAV_REQUIRE_AUTH disables the fail-open.
    await _reload_approved()
    await _load_aliases()   # #39 identity unification
    await api_tokens.load_cache(pool)   # agent/pipeline PATs (revocable bearer tokens)
    if _ldap_is_configured():
        log.info("LDAP configured (enforce=%s) — running initial user sync...", _ldap_enforcing())
        await _sync_ldap_users()
    else:
        log.info("Internal-users mode (no LDAP): %d approved identit%s loaded.",
                 len(_approved_lower), "y" if len(_approved_lower) == 1 else "ies")
    log.info("Ready.")
    import asyncio
    finalizer_task = asyncio.create_task(_finalizer_loop())
    pr_comments_task = asyncio.create_task(_pr_comments.poller_loop(pool))
    ingest_task = asyncio.create_task(_analysis_ingest_loop())
    corpus_sync_task = asyncio.create_task(_corpus_sync_loop())
    ldap_task = asyncio.create_task(_ldap_sync_loop()) if _ldap_is_configured() else None
    yield
    finalizer_task.cancel()
    pr_comments_task.cancel()
    ingest_task.cancel()
    corpus_sync_task.cancel()
    if ldap_task:
        ldap_task.cancel()
    for t in (finalizer_task, pr_comments_task, ingest_task, corpus_sync_task, ldap_task):
        if t is None:
            continue
        try:
            await t
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


async def _seed_docs_mcp(conn: asyncpg.Connection) -> None:
    """Self-register the secured dav-docs-mcp server at PLATFORM scope (project_id NULL, so
    the scope resolver surfaces it in every project — #107 Phase 3) from env: its
    LoadBalancer SSE URL (DAV_DOCS_MCP_URL) + Fernet-encrypted bearer token
    (DAV_DOCS_MCP_TOKEN). Idempotent: updates the existing dav-docs-mcp row (migrating it to
    platform) or inserts one. No-op when the env is unset (dev / before the secured LB)."""
    if not DAV_DOCS_MCP_URL:
        return
    token_enc = crypto.encrypt(DAV_DOCS_MCP_TOKEN) if DAV_DOCS_MCP_TOKEN else ""
    desc = "DCM architecture spec — served via MCP (secured)"
    # Explicit upsert by name (the scope-aware unique index is on COALESCE expressions, so a
    # plain ON CONFLICT target no longer matches). Any existing row is migrated to platform.
    existing = await conn.fetchval(
        "SELECT id FROM mcp_server_configs WHERE lower(name)='dav-docs-mcp' ORDER BY id LIMIT 1")
    if existing is not None:
        await conn.execute(
            "UPDATE mcp_server_configs SET sse_url=$1, auth_token_encrypted=$2, description=$3, "
            "project_id=NULL, use_category=NULL, updated_at=now() WHERE id=$4",
            DAV_DOCS_MCP_URL, token_enc, desc, existing)
    else:
        await conn.execute(
            "INSERT INTO mcp_server_configs (name, description, sse_url, enabled, use_uc_assist, "
            " auth_token_encrypted, created_by, project_id, use_category) "
            "VALUES ('dav-docs-mcp', $1, $2, true, false, $3, 'system', NULL, NULL)",
            desc, DAV_DOCS_MCP_URL, token_enc)
    log.info("dav-docs-mcp self-registered (platform scope, token %s)",
             "set" if token_enc else "none")


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


# ── Corpus-files cache reconciliation (multi-source) ─────────────────────────
# The `files` table backs All-set membership, the catalog, and /api/corpus. In
# multi-source mode the legacy boot pre-seed is disabled (the engine clones the
# corpus at run time), which left this cache a stale orphan. sync_corpus_files()
# reconciles it from the SAME registered corpus repos the engine uses, with
# mark-and-sweep so removed/renamed UCs are pruned. Paths mirror the engine's
# staging exactly: <namespace>/<path under the corpus role's root_path>.
_last_corpus_sync: dict = {"at": None, "result": None}


async def _clone_corpus_repo(repo_url: str, branch: str, pat: str,
                             root_path: str, namespace: str) -> tuple[list, Optional[str]]:
    """Shallow-clone a corpus repo and return (entries, error). Each entry is
    {path: '<namespace>/<rel>', content}, <rel> relative to the corpus role's
    root_path — mirroring the engine's <namespace>/ staging. A branch starting
    with '-' is rejected (git arg-injection); git stderr is never echoed (PAT)."""
    if not repo_url or not branch:
        return [], "missing repo_url/branch"
    if branch.startswith("-"):
        return [], f"invalid branch {branch!r}"

    def _run():
        import subprocess
        import tempfile
        import shutil
        tmp = tempfile.mkdtemp(prefix="dav-corpus-sync-")
        try:
            u = repo_url
            if pat and repo_url.startswith("https://"):
                u = repo_url.replace("https://", f"https://x-access-token:{pat}@", 1)
            r = subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", branch, "--", u, tmp],
                capture_output=True, text=True, timeout=180,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
            if r.returncode != 0:
                return None, f"clone failed for {repo_url} (branch {branch!r})"
            base = Path(tmp)
            if root_path:
                base = base / root_path
            if not base.exists():
                return [], None   # root_path absent in this repo → no corpus files
            out = []
            for e in walk_corpus(base, CORPUS_INCLUDE, CORPUS_EXCLUDE):
                out.append({"path": f"{namespace}/{e['path']}", "content": e["content"]})
            return out, None
        except Exception as ex:
            return None, f"clone error for {repo_url}: {type(ex).__name__}"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    entries, err = await asyncio.to_thread(_run)
    return (entries or []), err


async def sync_corpus_files(conn: asyncpg.Connection, reason: str = "manual") -> dict:
    """Reconcile the `files` cache from the registered corpus repos (roles @>
    {corpus}). Upserts all current files then sweeps rows not seen this sync.
    GUARD: the sweep runs ONLY when every configured repo cloned successfully
    and ≥1 file was loaded — so a transient clone failure never wipes the
    cache. Logs added/pruned counts (no silent truncation)."""
    rows = await conn.fetch(
        "SELECT namespace, repo_url, repo_branch, root_path, metadata, "
        "       github_pat_encrypted "
        "FROM managed_repos WHERE 'corpus' = ANY(roles)")
    if not rows:
        return {"ok": False, "reason": "no corpus repos registered", "repos": []}
    sync_start = await conn.fetchval("SELECT now()")
    seen_total, all_ok, per_repo = 0, True, []
    for r in rows:
        ns = (r["namespace"] or "").strip("/") or "corpus"
        repo = {"root_path": r["root_path"], "metadata": _repos._parse_jsonb(r["metadata"])}
        root = _repos.resolve_root_path(repo, "corpus")
        pat = ""
        try:
            if r["github_pat_encrypted"]:
                pat = crypto.decrypt(r["github_pat_encrypted"]) or ""
        except Exception:
            pat = ""
        entries, err = await _clone_corpus_repo(
            r["repo_url"], r["repo_branch"] or "main", pat, root, ns)
        if err:
            all_ok = False
            per_repo.append({"namespace": ns, "error": err})
            log.warning("corpus sync: %s clone failed: %s", ns, err)
            continue
        for e in entries:
            await _upsert_file(conn, e["path"], e["content"])
        seen_total += len(entries)
        per_repo.append({"namespace": ns, "files": len(entries)})
    pruned, swept = 0, False
    if seen_total > 0 and all_ok:
        res = await conn.execute("DELETE FROM files WHERE last_seen_at < $1", sync_start)
        try:
            pruned = int(res.split()[-1])
        except (ValueError, IndexError):
            pruned = 0
        swept = True
    elif not all_ok:
        log.warning("corpus sync (%s): a repo clone failed — skipping sweep "
                    "(upsert-only) to avoid wiping the cache", reason)
    log.info("corpus sync (%s): %d files / %d repos, pruned %d (swept=%s)",
             reason, seen_total, len(rows), pruned, swept)
    result = {"ok": True, "reason": reason, "files_seen": seen_total,
              "pruned": pruned, "swept": swept, "repos": per_repo}
    _last_corpus_sync["at"] = time.time()
    _last_corpus_sync["result"] = result
    return result


async def _corpus_sync_loop():
    """Boot (first pass after a short settle) + hourly safety-net reconcile of
    the corpus-files cache. Webhook + manual endpoint cover event-driven
    refreshes; this is the backstop for missed webhooks / out-of-band edits."""
    await asyncio.sleep(20)   # let the pool + migrations settle
    while True:
        try:
            async with pool.acquire() as conn:
                await sync_corpus_files(conn, reason="periodic")
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.warning("corpus sync loop hiccup: %s", e)
        await asyncio.sleep(3600)


async def _ensure_corpus_fresh(conn: asyncpg.Connection, max_age: int = 600) -> None:
    """Pre-run validation: reconcile the corpus cache if it's stale (or never
    synced this process) before building a run's selection, so the UC count +
    membership reflect the current corpus. Best-effort — never blocks the run."""
    at = _last_corpus_sync["at"]
    if at is not None and (time.time() - at) < max_age:
        return
    try:
        await sync_corpus_files(conn, reason="pre-run")
    except Exception as e:
        log.warning("pre-run corpus freshen failed: %s", e)


async def _seed_managed_repos(conn: asyncpg.Connection) -> None:
    """First-run seed of the managed_repos registry from the existing
    dav-source-spec / dav-source-corpus ConfigMaps.

    No-op if the registry already has rows (we don't overwrite operator-
    managed state on subsequent startups). See ADR-003 for the migration
    contract.
    """
    # Pre-check: skip the ConfigMap reads if the table already has rows.
    count = await conn.fetchval("SELECT COUNT(*) FROM managed_repos")
    if count > 0:
        return
    # Pull the current ConfigMap contents via the sources module (which
    # already knows how to talk to k8s with our SA permissions). If k8s
    # isn't reachable, log and skip — the operator can seed manually via
    # the Repos UI / API.
    try:
        spec_state = sources.get_source_state("spec")
        corpus_state = sources.get_source_state("corpus")
    except Exception as e:
        log.info(
            "managed_repos seed: ConfigMap read failed (%s); leaving registry "
            "empty — operator will populate via Repos UI", e,
        )
        return

    spec_data = spec_state.get("data") or {}
    sources_yaml = spec_state.get("sources")
    # `spec_state['sources']` is the parsed list (if multi-source); but
    # repos.seed_from_existing_configmaps expects the YAML text so it can
    # do its own parsing/validation. Re-stringify if needed.
    if isinstance(sources_yaml, list):
        import yaml as _yaml_local
        sources_yaml = _yaml_local.safe_dump(sources_yaml)

    inserted = await _repos.seed_from_existing_configmaps(
        conn,
        spec_sources_yaml=sources_yaml if isinstance(sources_yaml, str) else None,
        spec_legacy_url=spec_data.get("repo_url"),
        spec_legacy_branch=spec_data.get("repo_branch"),
        corpus_url=(corpus_state.get("data") or {}).get("repo_url"),
        corpus_branch=(corpus_state.get("data") or {}).get("repo_branch"),
    )
    if inserted:
        log.info("managed_repos seeded %d row(s) from existing ConfigMaps", inserted)
        # Project the seeded registry back to the ConfigMap so the two are
        # consistent from t0. If the source ConfigMap was in legacy mode,
        # this converts it to multi-source mode (and rolls the MCP). If it
        # was already multi-source with the same data, projection is a
        # no-op (idempotent — projector compares and skips).
        try:
            spec_result = await _projector.project_spec_sources(
                conn, applied_by="system:seed",
            )
            log.info("managed_repos seed: spec projection %s", spec_result.get("status"))
        except Exception as e:
            log.warning(
                "managed_repos seed: spec projection failed (%s); registry is "
                "populated but ConfigMap may differ until next CRUD or "
                "manual POST /api/repos/project", e,
            )
        try:
            corpus_result = await _projector.project_corpus_sources(
                conn, applied_by="system:seed",
            )
            log.info("managed_repos seed: corpus projection %s", corpus_result.get("status"))
        except Exception as e:
            log.warning(
                "managed_repos seed: corpus projection failed (%s); "
                "dav-source-corpus ConfigMap may differ until next CRUD "
                "or manual POST /api/repos/project?role=corpus", e,
            )
    else:
        log.info(
            "managed_repos seed: nothing to seed (no spec or corpus ConfigMaps "
            "had usable data)"
        )


async def _migrate_code_repo_configs(conn: asyncpg.Connection) -> None:
    """ADR-006 — fold each code_repo_configs row into managed_repos with
    the 'enhancement-target' role. Match by repo_url; create otherwise.
    Migrate the plaintext token to Fernet-encrypted github_pat_encrypted
    only when the target row has no PAT yet (don't clobber existing
    ADR-004/005 credentials). Idempotent: a row that already has the
    enhancement-target role is skipped.
    """
    from . import crypto as _crypto
    try:
        # Skip migration if the legacy table doesn't exist (fresh install)
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'code_repo_configs')"
        )
        if not exists:
            return
        rows = await conn.fetch(
            "SELECT id, name, provider, repo_url, default_branch, token, enabled "
            "FROM code_repo_configs"
        )
    except Exception as e:
        log.warning("code_repo_configs migration: query failed (%s); skipping", e)
        return

    if not rows:
        log.info("code_repo_configs migration: nothing to migrate")
        return

    fernet_ok = _crypto.is_available()
    if not fernet_ok:
        log.warning(
            "code_repo_configs migration: Fernet key unavailable; managed_repos "
            "rows will be created/updated WITHOUT tokens. Operator must re-enter "
            "credentials via the Repos UI."
        )

    migrated_new = 0
    merged_existing = 0
    skipped = 0
    for r in rows:
        repo_url = r["repo_url"]
        name = r["name"] or repo_url
        provider = r["provider"]
        existing = await conn.fetchrow(
            "SELECT uuid, namespace, roles, github_pat_encrypted, "
            "       github_pat_credential_id, metadata "
            "FROM managed_repos WHERE repo_url = $1 LIMIT 1",
            repo_url,
        )
        token_enc = None
        if fernet_ok and r["token"]:
            try:
                token_enc = _crypto.encrypt(r["token"])
            except Exception as e:
                log.warning(
                    "code_repo_configs migration: cannot encrypt token for %s (%s)",
                    name, e,
                )
        if existing:
            existing_roles = list(existing["roles"] or [])
            if "enhancement-target" in existing_roles:
                skipped += 1
                continue
            new_roles = existing_roles + ["enhancement-target"]
            # Merge metadata: keep existing keys, set provider only if absent
            existing_meta = existing["metadata"]
            if isinstance(existing_meta, str):
                try:
                    import json as _json
                    existing_meta = _json.loads(existing_meta)
                except Exception:
                    existing_meta = {}
            elif existing_meta is None:
                existing_meta = {}
            if "provider" not in existing_meta and provider:
                existing_meta["provider"] = provider
            import json as _json
            # Only migrate the token if the target has neither inline nor FK
            should_set_token = (
                token_enc is not None
                and not existing["github_pat_encrypted"]
                and existing["github_pat_credential_id"] is None
            )
            if should_set_token:
                await conn.execute(
                    "UPDATE managed_repos SET roles = $1, metadata = $2::jsonb, "
                    "github_pat_encrypted = $3, updated_by = $4 WHERE uuid = $5",
                    new_roles, _json.dumps(existing_meta), token_enc,
                    "system:migration-011", existing["uuid"],
                )
            else:
                await conn.execute(
                    "UPDATE managed_repos SET roles = $1, metadata = $2::jsonb, "
                    "updated_by = $3 WHERE uuid = $4",
                    new_roles, _json.dumps(existing_meta),
                    "system:migration-011", existing["uuid"],
                )
            merged_existing += 1
            log.info(
                "code_repo_configs migration: merged 'enhancement-target' role "
                "into managed_repos %s (token: %s)",
                existing["namespace"], "migrated" if should_set_token else "kept existing",
            )
        else:
            # Create a new managed_repos row. Derive a namespace from name.
            ns = re.sub(r"[^a-z0-9-]+", "-", (name or "").lower()).strip("-")[:63] or f"code-repo-{r['id']}"
            try:
                from . import repos as _repos_mod
                import json as _json
                await _repos_mod.create_repo(
                    conn,
                    namespace=ns,
                    repo_url=repo_url,
                    repo_branch=r["default_branch"] or "main",
                    display_name=name,
                    roles=["enhancement-target"],
                    metadata={"provider": provider, "source": "migrated_from_code_repo_configs"},
                    github_pat=r["token"] if (fernet_ok and r["token"]) else None,
                    created_by="system:migration-011",
                )
                migrated_new += 1
                log.info(
                    "code_repo_configs migration: created managed_repos row %s for %s",
                    ns, repo_url,
                )
            except Exception as e:
                log.warning(
                    "code_repo_configs migration: failed to create row for %s (%s)",
                    name, e,
                )

    log.info(
        "code_repo_configs migration: %d created, %d merged, %d already-migrated",
        migrated_new, merged_existing, skipped,
    )


app = FastAPI(title="DAV Console API", version="0.10.0", lifespan=lifespan)

_cors = os.environ.get("CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors.split(",")] if _cors else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Approved-user gate. Rejects only when LDAP is configured AND enforcement is on
# AND a sync has succeeded AND the caller isn't approved — so it is a no-op until
# you deliberately enable it (DAV_LDAP_ENFORCE=true), preventing accidental
# lockout. Health/readiness probes and OPTIONS preflight always pass.
_GATE_EXEMPT_PREFIXES = ("/readyz", "/healthz", "/livez", "/metrics", "/docs", "/openapi",
                         "/api/auth/", "/api/invites/", "/api/me")

# ── Presence tracking (platform-admin status bar) ────────────────────────────
# In-memory last-seen per identity, updated in the gate on every authenticated
# request. "online" = a tab seen recently (any request, incl. background polls);
# "active" = a recent NON-poll request (a real action). Single API replica, so a
# plain dict is fine; it resets on pod restart (acceptable for a live gauge).
# #39 identity unification: in-memory alias → canonical reviewer map (single API replica, so a
# plain dict is fine; reloaded at boot + after any alias change). get_user resolves through it.
_ALIAS_MAP: dict = {}   # alias(lower) -> canonical reviewer


async def _load_aliases() -> None:
    global _ALIAS_MAP
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT lower(alias) AS alias, reviewer FROM account_identities")
        _ALIAS_MAP = {r["alias"]: r["reviewer"] for r in rows}
    except Exception as e:
        log.warning("alias map load failed: %s", e)


def _canonical_identity(raw: str) -> str:
    """Resolve any incoming identity to its canonical account (#39). Unknown → unchanged."""
    if not raw:
        return raw
    return _ALIAS_MAP.get(raw.lower(), raw)


_presence_seen: dict = {}     # user(lower) -> last request epoch
_presence_active: dict = {}   # user(lower) -> last non-poll request epoch
_PRESENCE_ONLINE_WINDOW = 120   # seconds — a 60s client poll keeps a tab "online"
_PRESENCE_ACTIVE_WINDOW = 300   # seconds — "actively using" within 5 min
# GET requests to these paths are background polling, not real activity — they
# keep a user "online" but do not mark them "active".
_PRESENCE_POLL_PATHS = frozenset({
    "/api/presence", "/api/me", "/api/runs/status", "/api/runs", "/healthz",
    "/readyz", "/api/mcp-servers/health", "/api/inbox/unread-count",
})


def _record_presence(user: str, request: Request) -> None:
    # Exclude non-human / service identities (e.g. system:engine) — presence reflects people.
    if not user or user.lower().startswith("system:"):
        return
    now = time.time()
    ul = user.lower()
    _presence_seen[ul] = now
    if request.method != "GET" or request.url.path not in _PRESENCE_POLL_PATHS:
        _presence_active[ul] = now


def _presence_counts() -> dict:
    now = time.time()
    # Prune anything older than the longest window so the dicts stay bounded.
    cutoff = now - max(_PRESENCE_ONLINE_WINDOW, _PRESENCE_ACTIVE_WINDOW)
    for d in (_presence_seen, _presence_active):
        for k in [k for k, t in d.items() if t < cutoff]:
            d.pop(k, None)
    online = sum(1 for t in _presence_seen.values() if now - t <= _PRESENCE_ONLINE_WINDOW)
    active = sum(1 for t in _presence_active.values() if now - t <= _PRESENCE_ACTIVE_WINDOW)
    return {"online": online, "active": active}


@app.middleware("http")
async def _approval_gate(request: Request, call_next):
    if (request.method == "OPTIONS"
            or not ((_ldap_is_configured() and _ldap_enforcing()) or _REQUIRE_AUTH)
            or request.url.path.startswith(_GATE_EXEMPT_PREFIXES)):
        return await call_next(request)
    # Trusted in-cluster service (the engine) authenticated by its SA projected
    # token via TokenReview. Validate once here, up front, and flag the request
    # so the downstream guards (get_user / require_priv) honor it without each
    # re-issuing a TokenReview.
    if await _validate_service_token(request):
        request.state._svc_ok = True
        return await call_next(request)
    try:
        user = get_user(request)
    except HTTPException:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "authentication required"}, status_code=401)
    if not _is_approved(user):
        from fastapi.responses import JSONResponse
        # An internal (session-cookie) identity that isn't approved means the
        # account was DELETED or DISABLED — reject immediately and NEVER re-create
        # it. This is what cuts a deleted/disabled user's existing sessions off on
        # their very next request (the gate re-validates the account every time).
        if local_auth.read_session(request.cookies.get(local_auth.SESSION_COOKIE, "")):
            return JSONResponse(
                {"detail": "Your account no longer exists or has been disabled."},
                status_code=401,
            )
        # Source-agnostic JIT provisioning is ONLY for proxy-authenticated
        # identities (OCP/LDAP first-login) — map them in as an enabled, role-less
        # account for an admin to assign roles. A disabled one stays out.
        enabled = await _ensure_account(user)
        if not enabled:
            return JSONResponse(
                {"detail": "Your account is disabled. Ask a platform admin to enable it."},
                status_code=403,
            )
    _record_presence(user, request)
    return await call_next(request)


# ── Audit (F3): record mutating actions + auth events ────────────────────────
# Registered AFTER the gate → outermost middleware, so it sees the final response
# (incl. the gate's 401/403s). Recording is fire-and-forget; auditing never adds
# request latency or breaks a request. Reads + /api/auth are not auto-captured
# (auth events are recorded explicitly in the login/logout handlers).
_audit_seen_expired: set = set()   # dedupe one 'auth.timeout' per expired token
_audit_tasks: set = set()          # hold refs so fire-and-forget tasks aren't GC'd


def _fire(coro) -> None:
    t = asyncio.create_task(coro)
    _audit_tasks.add(t)
    t.add_done_callback(_audit_tasks.discard)


def _client_ip(request: Request) -> Optional[str]:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


def _actor_source(request: Request) -> str:
    if getattr(request.state, "_svc_ok", False) or _service_token_ok(request):
        return "service"
    if local_auth.read_session(request.cookies.get(local_auth.SESSION_COOKIE, "")):
        return "session"
    return "proxy"


def _maybe_audit_timeout(request: Request) -> None:
    tok = request.cookies.get(local_auth.SESSION_COOKIE, "")
    if not tok:
        return
    status, email = local_auth.session_status(tok)
    if status != "expired":
        return
    sig = tok.rsplit(".", 1)[-1]
    if sig in _audit_seen_expired:
        return
    if len(_audit_seen_expired) > 5000:
        _audit_seen_expired.clear()
    _audit_seen_expired.add(sig)
    _fire(audit.record(
        pool, action="auth.timeout", actor=email, actor_source="session",
        path=request.url.path, ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"), summary="session expired"))


@app.middleware("http")
async def _audit_mw(request: Request, call_next):
    response = await call_next(request)
    try:
        if request.method == "OPTIONS":
            return response
        if response.status_code in (401, 403):
            _maybe_audit_timeout(request)
        if audit.should_audit(request.method, request.url.path):
            try:
                actor = get_user(request)
            except Exception:
                actor = None
            _fire(audit.record(
                pool, action=audit.action_label(request.method, request.url.path),
                actor=actor, actor_source=_actor_source(request),
                method=request.method, path=request.url.path,
                outcome=audit.outcome_for(response.status_code),
                status_code=response.status_code, ip=_client_ip(request),
                user_agent=request.headers.get("user-agent")))
    except Exception:
        log.exception("audit middleware failed (non-fatal)")
    return response


def get_user(request: Request) -> str:
    # Trusted in-cluster service (the engine fetching managed UCs) — a valid
    # internal token resolves to the system identity, no cookie/header needed.
    if _service_token_ok(request):
        return INTERNAL_IDENTITY
    # Agent / pipeline Personal Access Token (Authorization: Bearer dav_pat_...)
    # → the RBAC account it acts as. Checked before cookie/header so an agent can
    # authenticate with no session, then normal RBAC applies.
    pat_email = api_tokens.resolve(request.headers.get("Authorization", ""))
    if pat_email:
        return _canonical_identity(pat_email)
    # App-native session (internal users) first, then the oauth-proxy headers
    # (OCP/FreeIPA users), then anon. Identity is the email/username string.
    sess = local_auth.read_session(request.cookies.get(local_auth.SESSION_COOKIE, ""))
    if sess:
        return _canonical_identity(sess)   # #39: resolve aliases → canonical account
    # #171 hardening: X-Forwarded / X-Auth-Request identity is trusted ONLY in the
    # oauth-gated /api/auth/sso bootstrap (which validates them via oauth-proxy and
    # mints the app session cookie) — NOT in this general dependency. nginx already
    # scrubs these headers on /api/, so trusting them here bought nothing but a
    # latent admin-impersonation bypass should that single scrub ever regress (or
    # should the API ever be reached off the nginx path). Steady-state auth for
    # OCP/FreeIPA users is the app session cookie established via /sso; agents use a
    # PAT (handled above). This makes the nginx scrub defense-in-depth, not the sole
    # control.
    if ALLOW_ANON_WRITES:
        return ANON_REVIEWER
    raise HTTPException(status_code=401, detail="reviewer identity not provided")


# ------------------------- Multi-user / LDAP approval -------------------------
# Approved identities (usernames + emails, lowercased) cached in-memory for a
# fast per-request gate. Synced from the LDAP approval group into the `users`
# table + this set. Survives LDAP downtime (keeps last-known-good).
_approved_lower: set = set()
_ldap_state = {"synced_ok": False, "last_sync": None, "last_error": None, "count": 0}
_ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2, "platform-admin": 3}
# `uc-admin` is an orthogonal capability (the right to share/fork use cases
# across projects and manage UC source bindings), NOT a rung on the rank ladder
# above — sharing UCs isn't "more powerful than admin", it's a different axis.
# It can be granted globally (users.role) or per-project (project_members.role)
# and is checked via _can_manage_uc_sources(), never via _ROLE_RANK comparisons.
_ASSIGNABLE_GLOBAL_ROLES = set(_ROLE_RANK) | {"uc-admin"}
_ASSIGNABLE_PROJECT_ROLES = {"viewer", "editor", "admin", "uc-admin"}

# Runtime config (in-app settings → env fallback), refreshed at boot + on change.
_ldap_cfg: dict = ldap_auth.env_config()
_smtp_cfg: dict = {}


async def _load_setting(key: str) -> dict:
    if pool is None:
        return {}
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchval("SELECT value FROM app_settings WHERE key=$1", key)
        if row is None:
            return {}
        return json.loads(row) if isinstance(row, str) else dict(row)
    except Exception as e:
        log.warning("load setting %s failed: %s", key, e)
        return {}


async def _load_ldap_cfg() -> None:
    """Merge the in-app LDAP setting over the env defaults (DB wins; bind
    password is decrypted)."""
    global _ldap_cfg
    cfg = ldap_auth.env_config()
    data = await _load_setting("ldap")
    if data:
        for k in ("url", "bind_dn", "user_base", "group_dn", "user_attr",
                  "mail_attr", "name_attr", "member_attr"):
            if data.get(k):
                cfg[k] = data[k]
        if data.get("start_tls") is not None:
            cfg["start_tls"] = bool(data["start_tls"])
        if data.get("enforce") is not None:
            cfg["enforce"] = bool(data["enforce"])
        if data.get("bind_password_enc"):
            try:
                cfg["bind_password"] = crypto.decrypt(data["bind_password_enc"]) or cfg.get("bind_password", "")
            except Exception:
                pass
    _ldap_cfg = cfg


async def _load_smtp_cfg() -> None:
    global _smtp_cfg
    cfg = {"host": DAV_SMTP_HOST, "port": DAV_SMTP_PORT, "user": DAV_SMTP_USER,
           "password": DAV_SMTP_PASSWORD, "from": DAV_SMTP_FROM, "tls": DAV_SMTP_TLS,
           "verify_cert": DAV_SMTP_VERIFY, "base_url": DAV_BASE_URL}
    data = await _load_setting("smtp")
    if data:
        for k in ("host", "user", "from", "base_url"):
            if data.get(k):
                cfg[k] = data[k]
        if data.get("port"):
            cfg["port"] = int(data["port"])
        if data.get("tls") is not None:
            cfg["tls"] = bool(data["tls"])
        if data.get("verify_cert") is not None:
            cfg["verify_cert"] = bool(data["verify_cert"])
        if data.get("password_enc"):
            try:
                cfg["password"] = crypto.decrypt(data["password_enc"]) or cfg["password"]
            except Exception:
                pass
    _smtp_cfg = cfg


def _smtp_message(frm: str, to: str, subject: str, body: str):
    """Build an RFC 5322-complete EmailMessage. Date + Message-ID are mandatory
    headers — omitting them makes content filters (amavis bad_header) quarantine
    the mail as malformed (the cause of DAV's BouncedOutbound/BAD-HEADER)."""
    from email.message import EmailMessage
    from email.utils import formatdate, make_msgid
    frm = frm or "dav@localhost"
    dom = frm.rsplit("@", 1)[-1].strip(" >") if "@" in frm else "dav.local"
    msg = EmailMessage()
    msg["From"] = frm
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=dom or "dav.local")
    msg.set_content(body)
    return msg


def _ldap_is_configured() -> bool:
    return ldap_auth.is_configured(_ldap_cfg)


def _ldap_enforcing() -> bool:
    return bool(_ldap_cfg.get("enforce"))


async def _seed_default_admin() -> None:
    """Ensure the dedicated break-glass platform-admin account ALWAYS exists, so
    the deployment can never orphan itself. Created with a default password (must
    change on first login) and the Platform Admin role. When other platform
    admins already exist it's created DEACTIVATED (present in the accounts list
    but can't log in); reconcile_default_admin re-activates it only if every
    platform admin vanishes. Skipped if app sessions aren't configured."""
    if pool is None or not local_auth.sessions_enabled():
        return
    email = os.environ.get("DAV_DEFAULT_ADMIN_EMAIL", "admin@dav.local").strip().lower()
    pw = os.environ.get("DAV_DEFAULT_ADMIN_PASSWORD", "changeme")
    async with pool.acquire() as conn:
        if await conn.fetchval("SELECT 1 FROM users WHERE lower(reviewer)=$1", email):
            return  # already present — its enabled state is governed by reconcile
        # Created enabled ONLY when it's the sole admin (true bootstrap); when a
        # real platform admin already exists, seed it deactivated.
        enabled = (await rbac.enabled_platform_admin_count(conn)) == 0
        await conn.execute(
            """INSERT INTO users (reviewer, email, display_name, role, approved, source,
                                  password_hash, must_change_password, enabled)
               VALUES ($1,$1,'Default Admin','platform-admin',true,'internal',$2,true,$3)
               ON CONFLICT (reviewer) DO NOTHING""",
            email, local_auth.hash_password(pw), enabled)
        rid = await conn.fetchval("SELECT id FROM rbac_roles WHERE key='platform-admin'")
        if rid:
            await rbac.assign_role(conn, email, rid, None, "seed")
    await _reload_approved()
    log.info("Ensured break-glass default admin %r (enabled=%s).", email, enabled)


async def _sync_ldap_users() -> None:
    """Pull the LDAP approval group into the `users` table + in-memory set.
    Best-effort: on LDAP error, keep the previous approvals (no lockout)."""
    if not _ldap_is_configured() or pool is None:
        return
    try:
        approved = await asyncio.to_thread(ldap_auth.fetch_approved_users, _ldap_cfg)
    except Exception as e:
        _ldap_state["last_error"] = str(e)
        log.warning("LDAP sync failed (keeping last-known approvals): %s", e)
        return
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("UPDATE users SET approved=false WHERE source='ldap'")
            for u in approved:
                reviewer = (u.get("username") or u.get("email") or "").strip().lower()
                if not reviewer:
                    continue
                await conn.execute(
                    """INSERT INTO users (reviewer, email, display_name, approved, source)
                       VALUES ($1,$2,$3,true,'ldap')
                       ON CONFLICT (reviewer) DO UPDATE
                         SET email=EXCLUDED.email, display_name=EXCLUDED.display_name,
                             approved=true,
                             source=CASE WHEN users.source='bootstrap' THEN 'bootstrap' ELSE 'ldap' END""",
                    reviewer, u.get("email", ""), u.get("display_name", ""),
                )
            for adm in ldap_auth.BOOTSTRAP_ADMINS:
                await conn.execute(
                    """INSERT INTO users (reviewer, email, role, approved, source)
                       VALUES ($1,$1,'platform-admin',true,'bootstrap')
                       ON CONFLICT (reviewer) DO UPDATE
                         SET role='platform-admin', approved=true, source='bootstrap'""",
                    adm,
                )
        rows = await conn.fetch("SELECT lower(reviewer) r, lower(email) e FROM users WHERE approved")
    global _approved_lower
    _approved_lower = {x["r"] for x in rows} | {x["e"] for x in rows if x["e"]}
    _ldap_state.update(synced_ok=True, last_error=None, count=len(approved))
    log.info("LDAP sync: %d group members, %d approved identities", len(approved), len(_approved_lower))


async def _ldap_sync_loop() -> None:
    """Refresh approvals from LDAP every 10 minutes."""
    while True:
        try:
            await _sync_ldap_users()
        except Exception:
            log.exception("ldap sync loop error")
        await asyncio.sleep(600)


_REQUIRE_AUTH = os.environ.get("DAV_REQUIRE_AUTH", "false").lower() == "true"
# Break-glass: the configured default platform-admin is ALWAYS approved, so a
# stale/empty approved cache can never lock the operator out of their own
# deployment (the whole point of a seeded admin).
_DEFAULT_ADMIN_EMAIL = os.environ.get("DAV_DEFAULT_ADMIN_EMAIL", "admin@dav.local").strip().lower()


def _is_approved(user: str) -> bool:
    """True if `user` is an approved identity. Bootstrap admins are always
    approved (break-glass — survives an LDAP/identity mismatch). Before LDAP has
    ever synced AND when not requiring auth, fail OPEN to avoid lockout; once
    DAV_REQUIRE_AUTH is on (relaxed-proxy mode) approval is strict so a skip-auth
    `/api/*` isn't open to unapproved callers."""
    u = (user or "").strip().lower()
    if u in ldap_auth.BOOTSTRAP_ADMINS:
        return True
    if not _ldap_state["synced_ok"] and not _REQUIRE_AUTH:
        return True
    # 'approved' == an enabled account. The default admin's enablement is governed
    # by reconcile_default_admin (enabled iff it's the sole platform admin), so we
    # do NOT unconditionally allow it here — that would defeat the disable rule.
    return u in _approved_lower


async def _privs(user: str, project_id: Optional[int] = None) -> set:
    """Privilege keys `user` holds in the given project context (RBAC resolver)."""
    if pool is None or not user:
        return set()
    async with pool.acquire() as conn:
        return await rbac.privileges_for(conn, user, project_id)


async def _has_priv(user: str, privilege: str, project_id: Optional[int] = None) -> bool:
    return privilege in await _privs(user, project_id)


async def _user_role(user: str) -> str:
    """Legacy representative role string for back-compat (/api/me `role`)."""
    if pool is None:
        return "platform-admin" if (user or "").lower() in ldap_auth.BOOTSTRAP_ADMINS else "viewer"
    async with pool.acquire() as conn:
        return await rbac.representative_role(conn, user)


def _multiuser() -> bool:
    """Multi-user (role-gated) mode: LDAP configured, or auth is required (a
    relaxed proxy with internal users). Otherwise it's a single operator and
    role checks are a no-op."""
    return _ldap_is_configured() or _REQUIRE_AUTH


async def _is_project_admin(user: str) -> bool:
    """Can administer access somewhere: platform.admin, or project.members on any
    project (used to gate the Users & Access UI). Single-user mode is always true."""
    if not _multiuser():
        return True
    if pool is None:
        return False
    async with pool.acquire() as conn:
        if rbac.P_PLATFORM_ADMIN in await rbac.privileges_for(conn, user):
            return True
        return bool(await conn.fetchval(
            """SELECT 1 FROM rbac_account_roles ar
               JOIN rbac_role_privileges rp ON rp.role_id = ar.role_id
               WHERE lower(ar.reviewer)=lower($1) AND rp.privilege_key=$2 LIMIT 1""",
            user, rbac.P_PROJECT_MEMBERS))


async def _can_manage_uc_sources(user: str, project_id: Optional[int] = None) -> bool:
    """UC-store management folds into project.settings (Project Admin + Platform
    Admin manage UC stores). Single-user mode is always true."""
    if not _multiuser():
        return True
    if pool is None:
        return False
    async with pool.acquire() as conn:
        if rbac.P_PLATFORM_ADMIN in await rbac.privileges_for(conn, user):
            return True
        if project_id is not None:
            return rbac.P_PROJECT_SETTINGS in await rbac.privileges_for(conn, user, project_id)
        return bool(await conn.fetchval(
            """SELECT 1 FROM rbac_account_roles ar
               JOIN rbac_role_privileges rp ON rp.role_id = ar.role_id
               WHERE lower(ar.reviewer)=lower($1) AND rp.privilege_key=$2 LIMIT 1""",
            user, rbac.P_PROJECT_SETTINGS))


async def require_uc_admin(request: Request, project_id: Optional[int] = None) -> str:
    user = get_user(request)
    if not _multiuser():
        return user
    if not await _can_manage_uc_sources(user, project_id):
        raise HTTPException(403, "requires use-case admin")
    return user


async def require_role(request: Request, minimum: str) -> str:
    """Legacy guard mapped onto privileges: 'admin' and 'platform-admin' both
    require platform.admin; lower minimums just require an enabled account."""
    user = get_user(request)
    if _service_token_ok(request):
        return user
    if not _multiuser():
        return user
    if minimum in ("admin", "platform-admin"):
        if not await _has_priv(user, rbac.P_PLATFORM_ADMIN):
            raise HTTPException(403, f"requires {minimum} role")
    return user


async def _project_sealed(conn, project_id) -> bool:
    """A sealed (is_exclusive) project requires an explicit grant for EVERYONE, incl. platform
    admins (#130 seal). Only gates project-scoped privilege checks — platform operations
    (project_id=None) are never sealed, so break-glass (granting via the platform-scoped Config /
    grant matrix) always works. Default-false → no effect on existing projects."""
    if not project_id:
        return False
    return bool(await conn.fetchval("SELECT is_exclusive FROM projects WHERE id=$1", project_id))


async def _customer_sealed(conn, customer_id) -> bool:
    if not customer_id:
        return False
    return bool(await conn.fetchval("SELECT is_exclusive FROM customers WHERE id=$1", customer_id))


async def require_priv(request: Request, privilege: str, project_id: Optional[int] = None) -> str:
    """Auth dependency: require a specific privilege (optionally project-scoped).
    platform.admin is a superuser EXCEPT on a sealed project (explicit grant required)."""
    user = get_user(request)
    if _service_token_ok(request):
        return user
    if not _multiuser():
        return user
    privs = await _privs(user, project_id)
    if privilege in privs:
        return user
    if rbac.P_PLATFORM_ADMIN in privs:
        if not project_id or pool is None:
            return user
        async with pool.acquire() as _c:
            if not await _project_sealed(_c, project_id):
                return user
    raise HTTPException(403, f"requires privilege {privilege}")


async def _require_priv_conn(conn, request: Request, privilege: str,
                             project_id: Optional[int] = None) -> str:
    """Like require_priv but uses an already-held connection (avoids a nested
    pool.acquire when the handler also needs _active_project_id). platform.admin
    is a superuser."""
    user = get_user(request)
    if _service_token_ok(request):
        return user
    if not _multiuser():
        return user
    privs = await rbac.privileges_for(conn, user, project_id)
    if privilege in privs:
        return user
    # platform.admin is a superuser EXCEPT on a sealed project (explicit grant required, #130).
    if rbac.P_PLATFORM_ADMIN in privs and not await _project_sealed(conn, project_id):
        return user
    raise HTTPException(403, f"requires privilege {privilege}")


async def _gate_resource(conn, request: Request, table: str, id_col: str, id_val,
                         privilege: str, not_found: str = "not found") -> int:
    """Resolve a resource row's owning project_id, enforce `privilege` in that
    project, and return the project_id. Raises 404 if the row is absent (also the
    cross-project case — the active user can't even see it). `table`/`id_col` are
    code-literal constants, never user input."""
    owner = await conn.fetchval(f"SELECT project_id FROM {table} WHERE {id_col}=$1", id_val)
    if owner is None:
        raise HTTPException(404, not_found)
    await _require_priv_conn(conn, request, privilege, owner)
    return owner


async def require_project_admin(request: Request, project_id: Optional[int]) -> str:
    """Manage a project: platform.admin (can manage any project — to add self),
    or project.members on that project."""
    user = get_user(request)
    if not _multiuser():
        return user
    if await _has_priv(user, rbac.P_PLATFORM_ADMIN):
        return user
    if project_id is not None and await _has_priv(user, rbac.P_PROJECT_MEMBERS, project_id):
        return user
    raise HTTPException(403, "requires project admin")


async def require_tenant_admin(request: Request, tenant_id: Optional[int]) -> str:
    """Administer a tenant: platform.admin (any tenant), or tenant.admin on THIS tenant.
    Tenancy Phase 1b — delegates tenant/group/role management to a tenant's own admin."""
    user = get_user(request)
    if _service_token_ok(request) or not _multiuser():
        return user
    if await _has_priv(user, rbac.P_PLATFORM_ADMIN):
        return user
    if tenant_id is not None:
        async with pool.acquire() as conn:
            privs = await rbac.privileges_for(conn, user, tenant_id=tenant_id)
        if rbac.P_TENANT_ADMIN in privs:
            return user
    raise HTTPException(403, "requires tenant admin")


async def _require_group_admin(request: Request, group_id: int) -> str:
    """Gate a mutation on an existing group by its scope: a tenant group → tenant-admin of its
    tenant (or platform admin); any other scope → platform admin (for now)."""
    async with pool.acquire() as conn:
        g = await conn.fetchrow("SELECT scope, tenant_id FROM rbac_groups WHERE id=$1", group_id)
    if not g:
        raise HTTPException(404, "group not found")
    if g["scope"] == "tenant":
        return await require_tenant_admin(request, g["tenant_id"])
    return await require_role(request, "admin")


async def _require_customer_priv_conn(conn, request: Request, privilege: str,
                                      customer_id: Optional[int] = None) -> str:
    """Customer-axis guard: require a customer-scoped privilege on `customer_id`.
    platform.admin is a superuser EXCEPT on a sealed (is_exclusive) customer, which requires an
    explicit grant for everyone (#130 seal). Mirrors _require_priv_conn on the project axis."""
    user = get_user(request)
    if _service_token_ok(request) or not _multiuser():
        return user
    privs = await rbac.privileges_for(conn, user, None, customer_id)
    if privilege in privs:
        return user
    # platform.admin is a superuser EXCEPT on a sealed customer (explicit grant required, #130).
    if rbac.P_PLATFORM_ADMIN in privs and not await _customer_sealed(conn, customer_id):
        return user
    raise HTTPException(403, f"requires privilege {privilege}")


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
    # How many of a UC's ensemble samples run in parallel (pure throughput —
    # samples are independent). None = auto (min(sample_count, cap)).
    sample_concurrency: Optional[int] = None
    # How many UCs run in parallel (independent agent loops — pure wall-clock win).
    uc_concurrency: Optional[int] = None
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
    set_id:         Optional[Union[int, str]] = None  # int id or '__all__' sentinel
    set_name:       Optional[str] = None
    selection_mode: Optional[str] = None  # 'set' | 'selection' | 'individual' | 'corpus'
    # Optional "time allowed" (failsafe pipeline timeout) in seconds. Blank =
    # auto (ETA = uc_count × data-driven per-UC estimate, + failsafe buffer).
    time_allowed_seconds: Optional[int] = None
    # User-facing session metadata (persisted to run_sessions)
    name: str = ""
    description: str = ""
    # ADR-007 / M11b: per-run corpus namespace filter. Empty/None = all
    # role=corpus repos in the registry are included. Operator selects a
    # subset from the New Run modal for ad-hoc / debug runs.
    corpus_namespaces: Optional[list[str]] = None
    # Per-run spec source filter. Soft enforcement — flows through to the
    # engine which injects a focus hint into the LLM system prompt. The
    # MCP itself still serves every registered spec namespace; this only
    # tells the LLM which ones to prefer for grounding.
    spec_namespaces: Optional[list[str]] = None
    category: str = "ad-hoc"
    tags: list[str] = []
    # Per-run override for engine two-pass / single-pass stage-2. "1" or
    # None = two-pass (engine default); "0" = legacy single-pass. Surfaced
    # so operators can A/B without an engine rebuild — added 2026-05-29 for
    # the Qwen3.6-27B MTP investigation.
    stage2_two_pass: Optional[str] = None
    # Per-run stage-2 output budget override (Tekton max-tokens param; engine
    # default = dav_stage2_max_tokens). validations.trigger_run already
    # forwards it — this exposes it on the public trigger, bounded to the
    # engine's sane window.
    max_tokens: Optional[int] = Field(None, ge=256, le=32768)
    # Per-run grounding-nudge toggle (forwarded as Tekton grounding-nudge
    # "true"/"false"; None = engine default).
    grounding_nudge: Optional[bool] = None
    # Per-request inference HTTP timeout, forwarded as Tekton param
    # request-timeout-seconds (task-side param lands with the engine PR).
    request_timeout_seconds: Optional[int] = Field(None, ge=1)
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


class UserSettingsIn(BaseModel):
    settings: dict = Field(default_factory=dict)


@app.get("/api/me/settings")
async def get_my_settings(request: Request):
    """#129: the caller's persisted UI prefs (theme/mode/persona/view-mode/nav). Server-side
    so they follow the user across devices; localStorage is the fast local cache."""
    try:
        user = get_user(request)
    except HTTPException:
        return {"settings": {}}
    if pool is None:
        return {"settings": {}}
    async with pool.acquire() as conn:
        row = await conn.fetchval("SELECT settings FROM user_settings WHERE reviewer=$1", user.lower())
    s = row if isinstance(row, dict) else (json.loads(row) if row else {})
    return {"settings": s or {}}


@app.put("/api/me/settings")
async def put_my_settings(payload: UserSettingsIn, request: Request):
    """#129: merge-upsert the caller's UI prefs (partial updates merge, so one changed key
    doesn't clobber the rest)."""
    user = get_user(request)
    if pool is None:
        raise HTTPException(503, "pool not initialized")
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO user_settings (reviewer, settings, updated_at)
               VALUES ($1, $2::jsonb, now())
               ON CONFLICT (reviewer) DO UPDATE
               SET settings = user_settings.settings || EXCLUDED.settings, updated_at = now()""",
            user.lower(), json.dumps(payload.settings or {}))
    return {"ok": True}


@app.get("/api/me")
async def me(request: Request):
    try:
        user = get_user(request)
    except HTTPException:
        return {"reviewer": None, "authenticated": False}
    # True single-user mode (no LDAP AND no auth enforcement): sole operator is
    # platform admin. Otherwise authorization comes from RBAC.
    single_user = not _multiuser()
    must_change = False
    privileges: list = ["platform.admin"] if single_user else []
    roles_out: list = []
    role = "platform-admin"
    default_project_id = None
    active_project_id = None
    if not single_user and pool is not None:
        try:
            async with pool.acquire() as conn:
                # Privileges are resolved for the ACTIVE project so the UI sees the
                # caller's project-scoped privileges (models/runs/usecases/...) for
                # whatever project the top-bar switcher currently points at.
                active_project_id = await _active_project_id(request, conn)
                privileges = sorted(await rbac.privileges_for(conn, user, active_project_id))
                role = await rbac.representative_role(conn, user)
                for r in await rbac.roles_for(conn, user):
                    roles_out.append({"key": r["key"], "name": r["name"], "scope": r["scope"],
                                      "project_id": r["project_id"], "project_name": r.get("project_name")})
                must_change = bool(await conn.fetchval(
                    "SELECT must_change_password FROM users WHERE lower(reviewer)=lower($1) OR lower(email)=lower($1)",
                    user))
                member_ids = list((await _user_project_roles(conn, user)).keys())
                default_project_id = await _resolve_default_project(conn, user, member_ids)
        except Exception:
            log.exception("/api/me RBAC lookup failed")
    is_platadmin = single_user or (rbac.P_PLATFORM_ADMIN in privileges)
    proj_admin = await _is_project_admin(user)
    return {
        "reviewer": user,
        "authenticated": True,
        "role": role,
        "privileges": privileges,
        "roles": roles_out,
        "is_admin": proj_admin,            # back-compat: gates the Users & Access UI
        "is_project_admin": proj_admin,
        "is_platform_admin": is_platadmin,
        "can_manage_uc_sources": await _can_manage_uc_sources(user),
        "approved": _is_approved(user),
        "ldap_enabled": _ldap_is_configured(),
        "must_change_password": must_change,
        "sessions_enabled": local_auth.sessions_enabled(),
        "default_project_id": default_project_id,
        "active_project_id": active_project_id,
    }


@app.get("/api/presence")
async def presence(request: Request, detail: bool = Query(False)):
    """Live presence gauge for the platform-admin status bar: how many distinct
    identities are currently online (a tab seen in the last 2 min) and actively
    using the system (a real, non-poll request in the last 5 min). `detail=1`
    also returns the per-identity list (who's online) for the popover."""
    await require_role(request, "admin")  # platform admin only
    out = _presence_counts()
    if detail:
        now = time.time()
        # Include anyone online (pinged ≤2 min) OR active (real request ≤5 min), so the popover
        # accounts for EVERY identity in the counts — incl. an "active but no longer polling" tab
        # (active without online), which previously showed in the count but not the list.
        ids = {uid for uid, t in _presence_seen.items() if now - t <= _PRESENCE_ONLINE_WINDOW}
        ids |= {uid for uid, t in _presence_active.items() if now - t <= _PRESENCE_ACTIVE_WINDOW}
        users = []
        for uid in ids:
            seen = _presence_seen.get(uid, 0)
            last_active = _presence_active.get(uid, 0)
            online = bool(seen) and (now - seen) <= _PRESENCE_ONLINE_WINDOW
            users.append({
                "id": uid,
                "idle_secs": int(now - seen) if seen else None,
                "online": online,
                "active": bool(last_active) and (now - last_active) <= _PRESENCE_ACTIVE_WINDOW,
            })
        users.sort(key=lambda u: (not u["active"], not u["online"], u["idle_secs"] if u["idle_secs"] is not None else 1e9))
        out["users"] = users
    return out


class DefaultProjectIn(BaseModel):
    project_id: Optional[int] = None


@app.put("/api/me/default-project")
async def set_my_default_project(payload: DefaultProjectIn, request: Request):
    """Set the caller's default project (the one selected on login). Must be a
    project they're a member of (platform admins may set any)."""
    user = get_user(request)
    pid = payload.project_id
    async with pool.acquire() as conn:
        if pid is not None and _multiuser():
            if not await _is_project_member(conn, user, pid) and not await _has_priv(user, rbac.P_PLATFORM_ADMIN):
                raise HTTPException(403, "you are not a member of that project")
        await conn.execute(
            "UPDATE users SET default_project_id=$2 WHERE lower(reviewer)=lower($1) OR lower(email)=lower($1)",
            user, pid)
    return {"ok": True, "default_project_id": pid}


# ========================= RBAC: accounts / roles / matrix =====================
# Accounts × roles × privileges. Source-agnostic accounts; roles bundle
# privileges; assignments are platform (project-independent) or project-scoped.

class AccountCreateIn(BaseModel):
    email: str
    display_name: Optional[str] = ""
    password: Optional[str] = None
    enabled: bool = True
    kind: str = "person"   # 'person' | 'agent' — agents are login-less (PAT-only) identities


class AccountPatchIn(BaseModel):
    enabled: Optional[bool] = None
    display_name: Optional[str] = None
    password: Optional[str] = None


class RoleCreateIn(BaseModel):
    key: str
    name: str
    description: Optional[str] = ""
    scope: str = "project"
    privileges: list[str] = Field(default_factory=list)


class RolePatchIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    privileges: Optional[list[str]] = None


class RoleAssignIn(BaseModel):
    role_id: int
    project_id: Optional[int] = None
    tenant_id: Optional[int] = None   # tenancy Phase 1: required for tenant-scoped roles


def _acct_roles_out(roles: list) -> list:
    return [{"id": x["id"], "role_id": x["role_id"], "key": x["key"], "name": x["name"],
             "scope": x["scope"], "project_id": x["project_id"],
             "project_name": x.get("project_name")} for x in roles]


@app.get("/api/accounts")
async def list_accounts(request: Request):
    """All accounts with their role assignments (platform admin)."""
    await require_role(request, "admin")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT reviewer, email, display_name, enabled, source, last_seen, "
            "       COALESCE(kind, 'person') AS kind, "
            "       (password_hash IS NOT NULL) AS has_password "
            "FROM users ORDER BY enabled DESC, reviewer")
        # #39: aliases per account, one grouped query (no N+1).
        alias_rows = await conn.fetch(
            "SELECT lower(reviewer) AS reviewer, array_agg(alias ORDER BY alias) AS aliases "
            "FROM account_identities GROUP BY lower(reviewer)")
        aliases_by = {r["reviewer"]: list(r["aliases"] or []) for r in alias_rows}
        out = []
        for r in rows:
            roles = await rbac.roles_for(conn, r["reviewer"])
            out.append({
                "reviewer": r["reviewer"], "email": r["email"],
                "display_name": r["display_name"], "enabled": r["enabled"],
                "source": r["source"], "has_password": r["has_password"],
                "kind": r["kind"],
                "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
                "is_default_admin": r["reviewer"].lower() == _DEFAULT_ADMIN_EMAIL,
                "roles": _acct_roles_out(roles),
                "aliases": aliases_by.get(r["reviewer"].lower(), []),
            })
    return {"accounts": out}


def _public_base(request: Request) -> str:
    """Public base URL for building links (e.g. invites). Precedence:
      1. DAV_PUBLIC_BASE_URL — config-derived (hostname + custom port), authoritative.
      2. SMTP base_url — operator-set in the UI.
      3. the request's own Host (with custom port via nginx $http_host) — fallback."""
    cfg = (os.environ.get("DAV_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if cfg:
        return cfg
    base = (_smtp_cfg.get("base_url") or "").strip().rstrip("/")
    if base:
        return base
    host = request.headers.get("host", "")
    if host:
        proto = request.headers.get("x-forwarded-proto", "https")
        return f"{proto}://{host}"
    return ""


async def _check_repo_ref(repo_url: str, branch: str, pat: Optional[str] = None) -> Optional[str]:
    """Best-effort reachability check (git ls-remote, no clone). Returns a human
    warning string if the repo is unreachable or the branch doesn't exist, else
    None. Uses the inline PAT when provided (for private repos)."""
    if not repo_url or not branch:
        return None
    # A branch starting with '-' would be parsed by git as an option
    # (--upload-pack=…), i.e. argument injection. Reject it; the `--` separator
    # below is belt-and-suspenders.
    if branch.startswith("-"):
        return f"invalid branch name {branch!r}"

    def _run():
        import subprocess
        u = repo_url
        if pat and repo_url.startswith("https://"):
            u = repo_url.replace("https://", f"https://x-access-token:{pat}@", 1)
        try:
            r = subprocess.run(
                ["git", "ls-remote", "--heads", "--", u, branch],
                capture_output=True, text=True, timeout=20,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
        except Exception as e:
            return f"could not reach {repo_url}: {e}"
        if r.returncode != 0:
            # Sanitize: never echo git stderr verbatim — it can contain the
            # PAT-in-URL form. Return a generic reachability message.
            return f"repo not reachable or branch '{branch}' missing in {repo_url}"
        if not r.stdout.strip():
            return f"branch '{branch}' not found in {repo_url}"
        return None

    return await asyncio.to_thread(_run)


async def _create_account_invite(conn, email: str, inviter: str, base: str = "") -> dict:
    """Create an account-activation invitation (no project/roles — roles are
    assigned separately in the RBAC UI) and return the accept link."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    await conn.execute(
        "INSERT INTO user_invitations (token, email, display_name, project_id, "
        "  project_role, global_role, invited_by, expires_at) "
        "VALUES ($1,$2,'',NULL,'viewer','viewer',$3,$4)",
        token, email, inviter, expires)
    base = (base or "").rstrip("/")
    return {"token": token, "link": (f"{base}/?invite={token}" if base else f"/?invite={token}")}


@app.post("/api/accounts")
async def create_account(payload: AccountCreateIn, request: Request):
    inviter = await require_role(request, "admin")
    email = (payload.email or "").strip().lower()
    if len(email) < 2:
        raise HTTPException(400, "valid email/username required")
    is_agent = (payload.kind or "person").strip().lower() == "agent"
    # Agents are login-less identities — never take a password and never get a human
    # activation invite; they authenticate only via a PAT minted against this account.
    pw_hash = None if is_agent else (local_auth.hash_password(payload.password) if payload.password else None)
    _source = "agent" if is_agent else ("internal" if pw_hash else "manual")
    _kind = "agent" if is_agent else "person"
    invite = None
    async with pool.acquire() as conn:
        if await conn.fetchval("SELECT 1 FROM users WHERE lower(reviewer)=$1 OR lower(email)=$1", email):
            raise HTTPException(409, "account already exists")
        await conn.execute(
            "INSERT INTO users (reviewer, email, display_name, role, approved, source, enabled, "
            "                   password_hash, must_change_password, kind) "
            "VALUES ($1,$1,$2,'viewer',true,$3,$4,$5,$6,$7)",
            email, payload.display_name or "", _source,
            payload.enabled, pw_hash, bool(pw_hash), _kind)
        # No password → email an activation invite so they set their own. Agents are
        # login-less, so they get no invite (the admin mints a PAT for them instead).
        if not is_agent and not pw_hash and "@" in email:
            invite = await _create_account_invite(conn, email, inviter, _public_base(request))
    await _reload_approved()
    emailed, email_error = False, ""
    if invite:
        emailed, email_error = await _send_email_audited(
            email, "Activate your DAV account",
            f"You've been added to DAV. Open this link to set your password and sign in:\n\n"
            f"{invite['link']}\n\nThis link expires in 7 days.",
            actor=inviter, action="invite.email")
    return {"ok": True, "reviewer": email, "kind": _kind,
            "invited": bool(invite), "emailed": emailed, "email_error": email_error,
            "link": invite["link"] if invite else None}


# ── agent / pipeline access tokens (PATs) ────────────────────────────────────
class TokenMintIn(BaseModel):
    email: str                          # the RBAC account the token acts as
    label: str = ""
    expires_at: Optional[datetime] = None


@app.post("/api/tokens")
async def mint_api_token(payload: TokenMintIn, request: Request):
    """Mint a Personal Access Token for non-interactive / agent auth. Returns the
    plaintext token ONCE (not stored in the clear). Platform-admin only."""
    minter = await require_role(request, "platform-admin")
    email = (payload.email or "").strip().lower()
    if len(email) < 2:
        raise HTTPException(400, "valid account email/username required")
    token = await api_tokens.mint(pool, email, payload.label, minter, payload.expires_at)
    return {"ok": True, "email": email, "token": token,
            "note": "Store this now — it is not shown again. "
                    "Use it as: Authorization: Bearer <token>"}


@app.get("/api/tokens")
async def list_api_tokens(request: Request, email: Optional[str] = None):
    """List token metadata (never the secret). Platform-admin only."""
    await require_role(request, "platform-admin")
    return {"tokens": await api_tokens.listing(pool, email)}


@app.delete("/api/tokens/{token_id}")
async def revoke_api_token(token_id: int, request: Request):
    """Revoke a token immediately (drops it from the in-memory cache too)."""
    await require_role(request, "platform-admin")
    if not await api_tokens.revoke(pool, token_id):
        raise HTTPException(404, "token not found or already revoked")
    return {"ok": True, "revoked": token_id}


@app.post("/api/accounts/{reviewer}/invite")
async def invite_existing_account(reviewer: str, request: Request):
    """(Re)send an activation invite to an existing account that has no password."""
    inviter = await require_role(request, "admin")
    r = reviewer.strip().lower()
    if "@" not in r:
        raise HTTPException(400, "account has no email address to invite")
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM users WHERE lower(reviewer)=$1", r):
            raise HTTPException(404, "account not found")
        invite = await _create_account_invite(conn, r, inviter, _public_base(request))
    emailed, email_error = await _send_email_audited(
        r, "Activate your DAV account",
        f"Open this link to set your password and sign in to DAV:\n\n{invite['link']}\n\n"
        f"This link expires in 7 days.",
        actor=inviter, action="invite.email")
    return {"ok": True, "link": invite["link"], "emailed": emailed, "email_error": email_error}


@app.patch("/api/accounts/{reviewer}")
async def patch_account(reviewer: str, payload: AccountPatchIn, request: Request):
    await require_role(request, "admin")
    r = reviewer.strip().lower()
    sets, args = [], []
    if payload.enabled is not None:
        args.append(payload.enabled); sets.append(f"enabled=${len(args)}")
    if payload.display_name is not None:
        args.append(payload.display_name); sets.append(f"display_name=${len(args)}")
    if payload.password:
        args.append(local_auth.hash_password(payload.password)); sets.append(f"password_hash=${len(args)}")
        args.append(True); sets.append(f"must_change_password=${len(args)}")
    if not sets:
        return {"ok": True}
    args.append(r)
    async with pool.acquire() as conn:
        res = await conn.execute(
            f"UPDATE users SET {', '.join(sets)} WHERE lower(reviewer)=${len(args)}", *args)
        if res.endswith(" 0"):
            raise HTTPException(404, "account not found")
        warn = await _reconcile_admin(conn)
    await _reload_approved()
    return {"ok": True, "warning": warn} if warn else {"ok": True}


@app.delete("/api/accounts/{reviewer}")
async def delete_account(reviewer: str, request: Request):
    actor = await require_role(request, "admin")
    r = reviewer.strip().lower()
    # You can't delete the account you're signed in as.
    if r == (actor or "").strip().lower():
        raise HTTPException(400, "you cannot delete the account you are signed in as")
    # The break-glass default is never truly deleted — it must remain so the
    # reconcile can re-activate it if all platform admins vanish. "Delete"
    # deactivates it instead (and immediately cuts off its sessions via the gate).
    if r == _DEFAULT_ADMIN_EMAIL:
        async with pool.acquire() as conn:
            res = await conn.execute("UPDATE users SET enabled=false WHERE lower(reviewer)=$1", r)
            if res.endswith(" 0"):
                raise HTTPException(404, "account not found")
            warn = await _reconcile_admin(conn)
        await _reload_approved()
        return {"ok": True, "deactivated": True,
                "warning": warn or "The default admin is the break-glass account — deactivated, not deleted."}
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM rbac_account_roles WHERE lower(reviewer)=$1", r)
        # #186 (Chain C): a deleted account must NOT retain API access via a still-valid PAT.
        # Revoke every PAT minted for this email before removing the user, then refresh the
        # in-memory token cache so the revocation takes effect immediately (not on next reload).
        await conn.execute(
            "UPDATE api_tokens SET revoked_at=now() WHERE lower(email)=$1 AND revoked_at IS NULL", r)
        await conn.execute("DELETE FROM users WHERE lower(reviewer)=$1", r)
        warn = await _reconcile_admin(conn)
    await api_tokens.load_cache(pool)
    await _reload_approved()
    return {"ok": True, "warning": warn} if warn else {"ok": True}


@app.get("/api/rbac/roles")
async def list_rbac_roles(request: Request):
    await require_role(request, "admin")
    async with pool.acquire() as conn:
        return {"roles": await rbac.list_roles(conn)}


@app.get("/api/rbac/privileges")
async def list_rbac_privileges(request: Request):
    await require_role(request, "admin")
    async with pool.acquire() as conn:
        return {"privileges": await rbac.list_privileges(conn)}


@app.post("/api/rbac/roles")
async def create_rbac_role(payload: RoleCreateIn, request: Request):
    await require_role(request, "admin")
    key = (payload.key or "").strip().lower()
    if not re.match(r"^[a-z0-9][a-z0-9-]{1,40}$", key):
        raise HTTPException(400, "invalid role key (lowercase, hyphens)")
    if payload.scope not in ("platform", "cross-project", "project"):
        raise HTTPException(400, "scope must be 'platform', 'cross-project' or 'project'")
    async with pool.acquire() as conn:
        try:
            rid = await conn.fetchval(
                "INSERT INTO rbac_roles (key, name, description, scope, is_system) "
                "VALUES ($1,$2,$3,$4,false) RETURNING id",
                key, payload.name.strip() or key, payload.description or "", payload.scope)
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, "role key already exists")
        await rbac.set_role_privileges(conn, rid, payload.privileges)
    return {"ok": True, "id": rid}


@app.put("/api/rbac/roles/{role_id}")
async def update_rbac_role(role_id: int, payload: RolePatchIn, request: Request):
    await require_role(request, "admin")
    async with pool.acquire() as conn:
        if not await conn.fetchrow("SELECT 1 FROM rbac_roles WHERE id=$1", role_id):
            raise HTTPException(404, "role not found")
        sets, args = [], []
        if payload.name is not None:
            args.append(payload.name); sets.append(f"name=${len(args)}")
        if payload.description is not None:
            args.append(payload.description); sets.append(f"description=${len(args)}")
        if sets:
            args.append(role_id)
            await conn.execute(f"UPDATE rbac_roles SET {', '.join(sets)} WHERE id=${len(args)}", *args)
        if payload.privileges is not None:
            await rbac.set_role_privileges(conn, role_id, payload.privileges)
    return {"ok": True}


@app.delete("/api/rbac/roles/{role_id}", status_code=204)
async def delete_rbac_role(role_id: int, request: Request):
    await require_role(request, "admin")
    async with pool.acquire() as conn:
        role = await conn.fetchrow("SELECT is_system FROM rbac_roles WHERE id=$1", role_id)
        if not role:
            raise HTTPException(404, "role not found")
        if role["is_system"]:
            raise HTTPException(400, "cannot delete a built-in role")
        await conn.execute("DELETE FROM rbac_roles WHERE id=$1", role_id)  # cascades
        await _reconcile_admin(conn)
    await _reload_approved()


@app.post("/api/accounts/{reviewer}/roles")
async def assign_account_role(reviewer: str, payload: RoleAssignIn, request: Request):
    """Grant a role to an account. Platform roles → platform admin; project roles
    → admin of that project (project_id required)."""
    async with pool.acquire() as conn:
        role = await conn.fetchrow("SELECT scope FROM rbac_roles WHERE id=$1", payload.role_id)
    if not role:
        raise HTTPException(404, "role not found")
    tenant_id = None
    if role["scope"] in ("platform", "cross-project"):
        # Platform & cross-project roles bind globally (no project) and are
        # granted by a platform admin.
        granter = await require_role(request, "admin")
        project_id = None
    elif role["scope"] == "tenant":
        # Tenancy Phase 1b: tenant roles bind to a tenant; a tenant-admin of THAT tenant (or a
        # platform admin) may grant within it. The escalation guard below still blocks granting
        # privileges the granter doesn't already hold in the tenant.
        if payload.tenant_id is None:
            raise HTTPException(400, "tenant_id required for a tenant-scoped role")
        granter = await require_tenant_admin(request, payload.tenant_id)
        project_id = None
        tenant_id = payload.tenant_id
    else:
        if payload.project_id is None:
            raise HTTPException(400, "project_id required for a project-scoped role")
        granter = await require_project_admin(request, payload.project_id)
        project_id = payload.project_id
    r = reviewer.strip().lower()
    async with pool.acquire() as conn:
        # Escalation guard: you may only grant a role whose privileges you already
        # hold in this scope — never a higher level of control than your own.
        # Platform admins hold everything, so they bypass. Example: a project
        # editor who also has project.members can grant editor/viewer, not admin.
        granter_privs = await rbac.privileges_for(conn, granter, project_id, tenant_id=tenant_id)
        if rbac.P_PLATFORM_ADMIN not in granter_privs:
            role_privs = {x["privilege_key"] for x in await conn.fetch(
                "SELECT privilege_key FROM rbac_role_privileges WHERE role_id=$1", payload.role_id)}
            escalated = role_privs - granter_privs
            if escalated:
                raise HTTPException(
                    403, "you can only grant a role whose privileges you already hold "
                         f"(this role adds: {', '.join(sorted(escalated))})")
        await conn.execute(
            "INSERT INTO users (reviewer,email,role,approved,source,enabled) "
            "VALUES ($1,$1,'viewer',true,'manual',true) ON CONFLICT (reviewer) DO NOTHING", r)
        await rbac.assign_role(conn, r, payload.role_id, project_id, granter, tenant_id=tenant_id)
        await _reconcile_admin(conn)
    await _reload_approved()
    return {"ok": True}


@app.delete("/api/accounts/{reviewer}/roles", status_code=204)
async def revoke_account_role(reviewer: str, request: Request,
                              role_id: int = Query(...),
                              project_id: Optional[int] = Query(None),
                              tenant_id: Optional[int] = Query(None)):
    async with pool.acquire() as conn:
        role = await conn.fetchrow("SELECT scope FROM rbac_roles WHERE id=$1", role_id)
    if not role:
        raise HTTPException(404, "role not found")
    if role["scope"] in ("platform", "cross-project"):
        await require_role(request, "admin")
        project_id = None
    elif role["scope"] == "tenant":
        await require_role(request, "admin")
        project_id = None
    else:
        await require_project_admin(request, project_id)
    async with pool.acquire() as conn:
        await rbac.revoke_role(conn, reviewer.strip().lower(), role_id, project_id, tenant_id=tenant_id)
        await _reconcile_admin(conn)
    await _reload_approved()


# ── Tenants (tenancy Phase 1) — the hard isolation owner; platform-admin managed ──
class TenantCreateIn(BaseModel):
    slug: str
    name: str = ""
    description: str = ""
    isolation_level: str = "hard"     # hard (schema-per-tenant target) | soft
    declared_regime: str = "none"     # none | secnumcloud | bsi_c5 | eu_data_boundary | ...


@app.get("/api/tenants")
async def list_tenants(request: Request):
    """All tenants with project counts (platform admin)."""
    await require_role(request, "admin")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT t.id, t.slug, t.name, t.description, t.isolation_level, t.declared_regime, "
            "       t.archived, t.created_at, "
            "       (SELECT count(*) FROM projects p WHERE p.tenant_id = t.id) AS project_count "
            "FROM tenants t ORDER BY t.name")
    return {"tenants": [
        {**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]}


@app.post("/api/tenants")
async def create_tenant(payload: TenantCreateIn, request: Request):
    """Create a tenant (the hard isolation owner). Phase 1 = logical entity + RBAC tier;
    the schema-per-tenant data plane (Phase 2) provisions later."""
    creator = await require_role(request, "admin")
    slug = (payload.slug or "").strip().lower()
    if not re.match(r'^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$', slug):
        raise HTTPException(400, "invalid tenant slug (lowercase alphanumeric + hyphens, 2-63 chars)")
    async with pool.acquire() as conn:
        if await conn.fetchval("SELECT 1 FROM tenants WHERE slug=$1", slug):
            raise HTTPException(409, "tenant slug already exists")
        row = await conn.fetchrow(
            "INSERT INTO tenants (slug, name, description, isolation_level, declared_regime, created_by) "
            "VALUES ($1,$2,$3,$4,$5,$6) RETURNING id",
            slug, (payload.name or slug), payload.description or "",
            payload.isolation_level or "hard", payload.declared_regime or "none", creator)
    return {"ok": True, "id": row["id"], "slug": slug}


# ── Groups (tenancy Phase 1b) — users → groups → roles; platform-admin managed ──
_GROUP_SCOPES = {"platform", "tenant", "project", "customer"}


class GroupCreateIn(BaseModel):
    name: str
    scope: str                          # platform | tenant | project | customer
    description: str = ""
    tenant_id: Optional[int] = None
    project_id: Optional[int] = None
    customer_id: Optional[int] = None


class GroupMemberIn(BaseModel):
    reviewer: str


class GroupRoleIn(BaseModel):
    role_id: int


@app.get("/api/groups")
async def list_groups(request: Request, scope: Optional[str] = None,
                      tenant_id: Optional[int] = None, project_id: Optional[int] = None,
                      customer_id: Optional[int] = None):
    """List groups (optionally filtered by scope/scope-id), with member + role counts."""
    await require_role(request, "admin")
    where, args = [], []
    for col, val in (("scope", scope), ("tenant_id", tenant_id),
                     ("project_id", project_id), ("customer_id", customer_id)):
        if val is not None:
            args.append(val); where.append(f"g.{col} = ${len(args)}")
    wc = (" WHERE " + " AND ".join(where)) if where else ""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT g.id, g.name, g.description, g.scope, g.tenant_id, g.project_id, g.customer_id, "
            "       g.source, g.created_at, "
            "       (SELECT count(*) FROM rbac_group_members m WHERE m.group_id=g.id) AS member_count, "
            "       (SELECT count(*) FROM rbac_group_roles  r WHERE r.group_id=g.id) AS role_count "
            f"FROM rbac_groups g{wc} ORDER BY g.scope, g.name", *args)
    return {"groups": [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]}


@app.post("/api/groups")
async def create_group(payload: GroupCreateIn, request: Request):
    scope = (payload.scope or "").strip().lower()
    if scope not in _GROUP_SCOPES:
        raise HTTPException(400, f"scope must be one of {sorted(_GROUP_SCOPES)}")
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(400, "group name required")
    # The scope id must match the scope (platform groups carry none).
    tid = payload.tenant_id if scope == "tenant" else None
    pid = payload.project_id if scope == "project" else None
    cid = payload.customer_id if scope == "customer" else None
    if scope == "tenant" and tid is None: raise HTTPException(400, "tenant_id required for a tenant group")
    if scope == "project" and pid is None: raise HTTPException(400, "project_id required for a project group")
    if scope == "customer" and cid is None: raise HTTPException(400, "customer_id required for a customer group")
    # Phase 1b: a tenant-admin may create groups within their tenant; else platform admin.
    creator = await require_tenant_admin(request, tid) if scope == "tenant" else await require_role(request, "admin")
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                "INSERT INTO rbac_groups (name, description, scope, tenant_id, project_id, customer_id, created_by) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id",
                name, payload.description or "", scope, tid, pid, cid, creator)
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, "a group with this name already exists in this scope")
    return {"ok": True, "id": row["id"]}


@app.delete("/api/groups/{group_id}", status_code=204)
async def delete_group(group_id: int, request: Request):
    await _require_group_admin(request, group_id)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM rbac_groups WHERE id=$1", group_id)  # cascades members+roles
    await _reload_approved()


@app.get("/api/groups/{group_id}/members")
async def list_group_members(group_id: int, request: Request):
    await require_role(request, "admin")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT reviewer, added_by, added_at FROM rbac_group_members WHERE group_id=$1 ORDER BY reviewer",
            group_id)
    return {"members": [{**dict(r), "added_at": r["added_at"].isoformat()} for r in rows]}


@app.post("/api/groups/{group_id}/members")
async def add_group_member(group_id: int, payload: GroupMemberIn, request: Request):
    adder = await _require_group_admin(request, group_id)
    r = (payload.reviewer or "").strip().lower()
    if len(r) < 2:
        raise HTTPException(400, "reviewer required")
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM rbac_groups WHERE id=$1", group_id):
            raise HTTPException(404, "group not found")
        await conn.execute(
            "INSERT INTO users (reviewer,email,role,approved,source,enabled) "
            "VALUES ($1,$1,'viewer',true,'manual',true) ON CONFLICT (reviewer) DO NOTHING", r)
        await conn.execute(
            "INSERT INTO rbac_group_members (group_id, reviewer, added_by) VALUES ($1,$2,$3) "
            "ON CONFLICT DO NOTHING", group_id, r, adder)
    await _reload_approved()
    return {"ok": True}


@app.delete("/api/groups/{group_id}/members/{reviewer}", status_code=204)
async def remove_group_member(group_id: int, reviewer: str, request: Request):
    await _require_group_admin(request, group_id)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM rbac_group_members WHERE group_id=$1 AND lower(reviewer)=lower($2)",
                           group_id, reviewer)
    await _reload_approved()


@app.get("/api/groups/{group_id}/roles")
async def list_group_roles(group_id: int, request: Request):
    await require_role(request, "admin")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT gr.role_id, ro.key, ro.name, ro.scope FROM rbac_group_roles gr "
            "JOIN rbac_roles ro ON ro.id=gr.role_id WHERE gr.group_id=$1 ORDER BY ro.name", group_id)
    return {"roles": [dict(r) for r in rows]}


@app.post("/api/groups/{group_id}/roles")
async def bind_group_role(group_id: int, payload: GroupRoleIn, request: Request):
    granter = await _require_group_admin(request, group_id)
    async with pool.acquire() as conn:
        g = await conn.fetchrow("SELECT scope FROM rbac_groups WHERE id=$1", group_id)
        if not g:
            raise HTTPException(404, "group not found")
        role = await conn.fetchrow("SELECT scope FROM rbac_roles WHERE id=$1", payload.role_id)
        if not role:
            raise HTTPException(404, "role not found")
        # The role's scope must be compatible with the group's: a platform/cross-project role
        # binds to a platform group; otherwise the role scope must equal the group scope.
        if role["scope"] in ("platform", "cross-project"):
            if g["scope"] != "platform":
                raise HTTPException(400, "platform/cross-project roles bind only to a platform group")
        elif role["scope"] != g["scope"]:
            raise HTTPException(400, f"a {g['scope']} group can only bind a {g['scope']}-scoped role")
        await conn.execute(
            "INSERT INTO rbac_group_roles (group_id, role_id, granted_by) VALUES ($1,$2,$3) "
            "ON CONFLICT DO NOTHING", group_id, payload.role_id, granter)
    await _reload_approved()
    return {"ok": True}


@app.delete("/api/groups/{group_id}/roles/{role_id}", status_code=204)
async def unbind_group_role(group_id: int, role_id: int, request: Request):
    await _require_group_admin(request, group_id)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM rbac_group_roles WHERE group_id=$1 AND role_id=$2", group_id, role_id)
    await _reload_approved()


# ── #39 identity unification: aliases (uid / old key / 2nd email) → one canonical account ──
class IdentityLinkIn(BaseModel):
    alias: str
    migrate: bool = True   # move the alias's existing role bindings + settings onto the canonical account


@app.get("/api/accounts/{reviewer}/identities")
async def list_account_identities(reviewer: str, request: Request):
    await require_role(request, "admin")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT alias, source, created_by, created_at FROM account_identities "
            "WHERE lower(reviewer)=lower($1) ORDER BY alias", reviewer)
    return {"reviewer": reviewer, "identities": [
        {**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]}


@app.post("/api/accounts/{reviewer}/identities")
async def link_account_identity(reviewer: str, payload: IdentityLinkIn, request: Request):
    """Link an ALIAS identity to this (canonical) account, so any auth path using the alias
    resolves here. With migrate=true, the alias's existing role bindings + settings move onto
    this account and the alias's duplicate account row is removed."""
    granter = await require_role(request, "admin")
    canon = reviewer.strip()
    alias = (payload.alias or "").strip().lower()
    if not alias:
        raise HTTPException(400, "alias required")
    if alias == canon.lower():
        raise HTTPException(400, "an account can't alias itself")
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM users WHERE lower(reviewer)=lower($1)", canon):
            raise HTTPException(404, "canonical account not found")
        owner = await conn.fetchval("SELECT reviewer FROM account_identities WHERE alias=$1", alias)
        if owner and owner.lower() != canon.lower():
            raise HTTPException(409, f"{alias!r} is already an alias of {owner!r}")
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO account_identities (alias, reviewer, source, created_by)
                   VALUES ($1,$2,'manual',$3)
                   ON CONFLICT (alias) DO UPDATE SET reviewer=EXCLUDED.reviewer""",
                alias, canon, granter)
            if payload.migrate:
                # Move role bindings (drop ones that would duplicate a canonical binding), then settings.
                await conn.execute(
                    """DELETE FROM rbac_account_roles a WHERE lower(a.reviewer)=$1
                       AND EXISTS (SELECT 1 FROM rbac_account_roles b WHERE lower(b.reviewer)=lower($2)
                                   AND b.role_id=a.role_id
                                   AND COALESCE(b.project_id,0)=COALESCE(a.project_id,0)
                                   AND COALESCE(b.customer_id,0)=COALESCE(a.customer_id,0))""",
                    alias, canon)
                await conn.execute("UPDATE rbac_account_roles SET reviewer=$2 WHERE lower(reviewer)=$1", alias, canon)
                await conn.execute(
                    "DELETE FROM user_settings WHERE lower(reviewer)=$1 AND EXISTS "
                    "(SELECT 1 FROM user_settings WHERE lower(reviewer)=lower($2))", alias, canon)
                await conn.execute("UPDATE user_settings SET reviewer=$2 WHERE lower(reviewer)=$1", alias, canon)
                # Remove the alias's now-redundant standalone account row (it lives on as an alias).
                await conn.execute("DELETE FROM users WHERE lower(reviewer)=$1", alias)
        await _reconcile_admin(conn)
    await _load_aliases()
    await _reload_approved()
    return {"ok": True, "alias": alias, "reviewer": canon, "migrated": payload.migrate}


@app.delete("/api/accounts/{reviewer}/identities/{alias}", status_code=204)
async def unlink_account_identity(reviewer: str, alias: str, request: Request):
    """Remove an alias (does NOT restore the old account or un-migrate bindings)."""
    await require_role(request, "admin")
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM account_identities WHERE alias=$1 AND lower(reviewer)=lower($2)",
                           alias.strip().lower(), reviewer)
    await _load_aliases()


# ── Role bindings (the "who has what, where" view) + LDAP group mapper ────────
@app.get("/api/rbac/bindings")
async def list_rbac_bindings(request: Request):
    """All role bindings: account → role (× project) plus LDAP/OCP group → role
    mappings. Platform admin."""
    await require_role(request, "admin")
    async with pool.acquire() as conn:
        # Surface the customer axis + spans_all too (2b-iv): customer-scoped grants were
        # invisible here, and the grant matrix needs every binding's full scope.
        accts = await conn.fetch(
            """SELECT ar.id AS binding_id, lower(ar.reviewer) AS subject, u.display_name,
                      ar.role_id, ro.key AS role_key, ro.name AS role_name, ro.scope,
                      ar.project_id, p.name AS project_name,
                      ar.customer_id, c.name AS customer_name, ar.spans_all
               FROM rbac_account_roles ar
               JOIN rbac_roles ro ON ro.id=ar.role_id
               LEFT JOIN users u ON lower(u.reviewer)=lower(ar.reviewer)
               LEFT JOIN projects p ON p.id=ar.project_id
               LEFT JOIN customers c ON c.id=ar.customer_id
               ORDER BY ro.scope DESC, ro.name, ar.reviewer""")
        groups = await conn.fetch(
            """SELECT g.id AS mapping_id, g.source, g.group_key AS subject,
                      g.role_id, ro.key AS role_key, ro.name AS role_name, ro.scope,
                      g.project_id, p.name AS project_name,
                      g.customer_id, c.name AS customer_name, g.spans_all
               FROM rbac_group_role_mappings g
               JOIN rbac_roles ro ON ro.id=g.role_id
               LEFT JOIN projects p ON p.id=g.project_id
               LEFT JOIN customers c ON c.id=g.customer_id
               ORDER BY g.source, g.group_key""")
    return {"account_bindings": [dict(r) for r in accts],
            "group_mappings": [dict(r) for r in groups]}


class GroupMappingIn(BaseModel):
    source: str = "ldap"
    group_key: str
    role_id: int
    project_id: Optional[int] = None


@app.post("/api/rbac/group-mappings")
async def create_group_mapping(payload: GroupMappingIn, request: Request):
    """Map an external group (LDAP/OCP) → a role. Platform admin. The sync that
    *applies* these to members is a later slice; this manages the mappings."""
    await require_role(request, "admin")
    gk = (payload.group_key or "").strip()
    if not gk:
        raise HTTPException(400, "group_key required")
    async with pool.acquire() as conn:
        role = await conn.fetchrow("SELECT scope FROM rbac_roles WHERE id=$1", payload.role_id)
        if not role:
            raise HTTPException(404, "role not found")
        project_id = payload.project_id if role["scope"] == "project" else None
        if role["scope"] == "project" and project_id is None:
            raise HTTPException(400, "project_id required for a project-scoped role")
        try:
            await conn.execute(
                "INSERT INTO rbac_group_role_mappings (source, group_key, role_id, project_id, created_by) "
                "VALUES ($1,$2,$3,$4,$5)",
                (payload.source or "ldap").strip(), gk, payload.role_id, project_id, get_user(request))
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, "that group→role mapping already exists")
    return {"ok": True}


@app.delete("/api/rbac/group-mappings/{mapping_id}", status_code=204)
async def delete_group_mapping(mapping_id: int, request: Request):
    await require_role(request, "admin")
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM rbac_group_role_mappings WHERE id=$1", mapping_id)


# ========================= USERS & ACCESS (multi-user) =========================


@app.get("/api/ldap/status")
async def ldap_status(request: Request):
    """LDAP configuration + last sync state. Admin-gated when enforcing."""
    if _ldap_is_configured() and _ldap_enforcing():
        await require_role(request, "admin")
    return {
        "configured": _ldap_is_configured(),
        "enforcing": _ldap_enforcing(),
        "url": _ldap_cfg.get("url", ""),
        "group_dn": _ldap_cfg.get("group_dn", ""),
        "synced_ok": _ldap_state["synced_ok"],
        "group_member_count": _ldap_state["count"],
        "approved_identity_count": len(_approved_lower),
        "last_error": _ldap_state["last_error"],
        "bootstrap_admins": ldap_auth.BOOTSTRAP_ADMINS,
    }


@app.post("/api/ldap/sync")
async def ldap_sync_now(request: Request):
    """Force an immediate LDAP approval sync (admin)."""
    await require_role(request, "admin")
    if not _ldap_is_configured():
        raise HTTPException(400, "LDAP not configured")
    await _sync_ldap_users()
    return {"ok": True, "synced_ok": _ldap_state["synced_ok"],
            "approved_identity_count": len(_approved_lower),
            "last_error": _ldap_state["last_error"]}


@app.get("/api/users")
async def list_users(request: Request):
    """All known users with roles + approval (admin)."""
    await require_role(request, "admin")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT reviewer, email, display_name, role, approved, source, last_seen "
            "FROM users ORDER BY approved DESC, role, reviewer")
    return {"users": [dict(r) for r in rows]}


class UserRoleIn(BaseModel):
    role: str


@app.put("/api/users/{reviewer}/role")
async def set_user_role(reviewer: str, payload: UserRoleIn, request: Request):
    """Set a user's role (admin)."""
    await require_role(request, "admin")
    if payload.role not in _ASSIGNABLE_GLOBAL_ROLES:
        raise HTTPException(400, f"invalid role; must be one of {sorted(_ASSIGNABLE_GLOBAL_ROLES)}")
    async with pool.acquire() as conn:
        res = await conn.execute(
            "UPDATE users SET role=$2 WHERE lower(reviewer)=lower($1)", reviewer, payload.role)
    if res.endswith("0"):
        raise HTTPException(404, "user not found")
    return {"ok": True, "reviewer": reviewer, "role": payload.role}


@app.get("/api/ldap/approved")
async def ldap_approved_users(request: Request):
    """Approved users for member pickers (any approved caller)."""
    get_user(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT reviewer, email, display_name, role FROM users WHERE approved "
            "ORDER BY display_name, reviewer")
    return {"users": [dict(r) for r in rows]}


# ── In-app settings: LDAP + SMTP (platform-admin) ────────────────────────────
class LdapSettingsIn(BaseModel):
    url: str = ""
    bind_dn: str = ""
    bind_password: Optional[str] = None   # write-only; omit/empty = leave unchanged
    user_base: str = ""
    group_dn: str = ""
    user_attr: str = "uid"
    mail_attr: str = "mail"
    name_attr: str = "cn"
    member_attr: str = "member"
    start_tls: bool = False
    enforce: bool = False


@app.get("/api/settings/ldap")
async def get_ldap_settings(request: Request):
    await require_role(request, "platform-admin")
    d = await _load_setting("ldap")
    e = _ldap_cfg
    g = lambda k, dflt="": d.get(k, e.get(k, dflt))
    return {
        "url": g("url"), "bind_dn": g("bind_dn"), "user_base": g("user_base"),
        "group_dn": g("group_dn"), "user_attr": g("user_attr", "uid"),
        "mail_attr": g("mail_attr", "mail"), "name_attr": g("name_attr", "cn"),
        "member_attr": g("member_attr", "member"),
        "start_tls": bool(g("start_tls", False)), "enforce": bool(g("enforce", False)),
        "bind_password_set": bool(d.get("bind_password_enc") or e.get("bind_password")),
        "from_env": not d,
    }


@app.put("/api/settings/ldap")
async def put_ldap_settings(payload: LdapSettingsIn, request: Request):
    user = await require_role(request, "platform-admin")
    cur = await _load_setting("ldap")
    val = {
        "url": payload.url.strip(), "bind_dn": payload.bind_dn.strip(),
        "user_base": payload.user_base.strip(), "group_dn": payload.group_dn.strip(),
        "user_attr": payload.user_attr.strip() or "uid",
        "mail_attr": payload.mail_attr.strip() or "mail",
        "name_attr": payload.name_attr.strip() or "cn",
        "member_attr": payload.member_attr.strip() or "member",
        "start_tls": bool(payload.start_tls), "enforce": bool(payload.enforce),
    }
    if payload.bind_password:
        if not crypto.is_available():
            raise HTTPException(503, "encryption key not configured (DAV_FERNET_KEY)")
        val["bind_password_enc"] = crypto.encrypt(payload.bind_password)
    elif cur.get("bind_password_enc"):
        val["bind_password_enc"] = cur["bind_password_enc"]
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES ('ldap',$1,$2,now()) "
            "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_by=EXCLUDED.updated_by, updated_at=now()",
            json.dumps(val), user)
    await _load_ldap_cfg()
    if _ldap_is_configured():
        await _sync_ldap_users()
    return {"ok": True, "configured": _ldap_is_configured(),
            "synced_ok": _ldap_state["synced_ok"],
            "approved_identity_count": len(_approved_lower),
            "last_error": _ldap_state["last_error"]}


class SmtpSettingsIn(BaseModel):
    host: str = ""
    port: int = 587
    user: str = ""
    password: Optional[str] = None
    from_addr: str = ""
    tls: bool = True
    verify_cert: bool = True   # verify the STARTTLS server cert (off for internal/self-signed relays)
    base_url: str = ""


@app.get("/api/settings/smtp")
async def get_smtp_settings(request: Request):
    await require_role(request, "platform-admin")
    d = await _load_setting("smtp")
    e = _smtp_cfg
    return {
        "host": d.get("host", e.get("host", "")), "port": int(d.get("port", e.get("port", 587))),
        "user": d.get("user", e.get("user", "")), "from_addr": d.get("from", e.get("from", "")),
        "tls": bool(d.get("tls", e.get("tls", True))),
        "verify_cert": bool(d.get("verify_cert", e.get("verify_cert", True))),
        "base_url": d.get("base_url", e.get("base_url", "")),
        "password_set": bool(d.get("password_enc") or e.get("password")),
        "from_env": not d,
    }


@app.put("/api/settings/smtp")
async def put_smtp_settings(payload: SmtpSettingsIn, request: Request):
    user = await require_role(request, "platform-admin")
    cur = await _load_setting("smtp")
    val = {"host": payload.host.strip(), "port": int(payload.port), "user": payload.user.strip(),
           "from": payload.from_addr.strip(), "tls": bool(payload.tls),
           "verify_cert": bool(payload.verify_cert), "base_url": payload.base_url.strip()}
    if payload.password:
        if not crypto.is_available():
            raise HTTPException(503, "encryption key not configured (DAV_FERNET_KEY)")
        val["password_enc"] = crypto.encrypt(payload.password)
    elif cur.get("password_enc"):
        val["password_enc"] = cur["password_enc"]
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES ('smtp',$1,$2,now()) "
            "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_by=EXCLUDED.updated_by, updated_at=now()",
            json.dumps(val), user)
    await _load_smtp_cfg()
    return {"ok": True}


@app.post("/api/settings/ldap/test")
async def test_ldap_settings(payload: LdapSettingsIn, request: Request):
    """Test the supplied LDAP settings (form values, not necessarily saved):
    bind + read the approval group. Returns {ok, count, sample} or {ok:false, error}."""
    await require_role(request, "platform-admin")
    cfg = {
        "url": payload.url.strip(), "bind_dn": payload.bind_dn.strip(),
        "user_base": payload.user_base.strip(), "group_dn": payload.group_dn.strip(),
        "user_attr": payload.user_attr.strip() or "uid",
        "mail_attr": payload.mail_attr.strip() or "mail",
        "name_attr": payload.name_attr.strip() or "cn",
        "member_attr": payload.member_attr.strip() or "member",
        "start_tls": bool(payload.start_tls),
    }
    if payload.bind_password:
        cfg["bind_password"] = payload.bind_password
    else:
        cur = await _load_setting("ldap")
        if cur.get("bind_password_enc"):
            try: cfg["bind_password"] = crypto.decrypt(cur["bind_password_enc"]) or ""
            except Exception: cfg["bind_password"] = ""
        else:
            cfg["bind_password"] = _ldap_cfg.get("bind_password", "")
    if not ldap_auth.is_configured(cfg):
        raise HTTPException(400, "Server URL and approval group DN are required")
    try:
        users = await asyncio.to_thread(ldap_auth.fetch_approved_users, cfg)
        return {"ok": True, "count": len(users),
                "sample": [u.get("username") or u.get("email") for u in users[:8] if (u.get("username") or u.get("email"))]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class SmtpTestIn(SmtpSettingsIn):
    test_to: str = ""


@app.post("/api/settings/smtp/test")
async def test_smtp_settings(payload: SmtpTestIn, request: Request):
    """Send a test email using the supplied SMTP settings (form values)."""
    await require_role(request, "platform-admin")
    cfg = {"host": payload.host.strip(), "port": int(payload.port), "user": payload.user.strip(),
           "from": payload.from_addr.strip(), "tls": bool(payload.tls), "verify_cert": bool(payload.verify_cert)}
    if payload.password:
        cfg["password"] = payload.password
    else:
        cur = await _load_setting("smtp")
        if cur.get("password_enc"):
            try: cfg["password"] = crypto.decrypt(cur["password_enc"]) or ""
            except Exception: cfg["password"] = ""
        else:
            cfg["password"] = _smtp_cfg.get("password", "")
    if not cfg["host"]:
        raise HTTPException(400, "SMTP host is required")
    to = (payload.test_to or cfg["from"] or "").strip()
    if "@" not in to:
        raise HTTPException(400, "a test recipient (or a valid From address) is required")

    def _send():
        import smtplib, ssl
        msg = _smtp_message(cfg.get("from", "dav@localhost"), to, "DAV SMTP test",
                            "This is a test message from DAV. If you received it, SMTP is configured correctly.")
        with smtplib.SMTP(cfg["host"], int(cfg.get("port", 587)), timeout=30) as s:
            if cfg.get("tls"):
                if cfg.get("verify_cert", True):
                    s.starttls()
                else:
                    s.starttls(context=ssl._create_unverified_context())
            if cfg.get("user"):
                s.login(cfg["user"], cfg.get("password", ""))
            s.send_message(msg)
    try:
        await asyncio.to_thread(_send)
        return {"ok": True, "sent_to": to}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class ChangePwIn(BaseModel):
    current_password: Optional[str] = None
    new_password: str


@app.post("/api/auth/change-password")
async def change_password(payload: ChangePwIn, request: Request):
    """Set/change the caller's internal password (used for the default-admin
    must-change flow, and for any user who wants a local password)."""
    if not local_auth.sessions_enabled():
        raise HTTPException(503, "app sessions not configured")
    user = get_user(request)
    if len(payload.new_password or "") < 8:
        raise HTTPException(400, "password must be at least 8 characters")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT password_hash FROM users WHERE lower(reviewer)=lower($1) OR lower(email)=lower($1)", user)
        if row and row["password_hash"]:
            if not (payload.current_password and local_auth.verify_password(row["password_hash"], payload.current_password)):
                raise HTTPException(403, "current password is incorrect")
        await conn.execute(
            "UPDATE users SET password_hash=$2, must_change_password=false "
            "WHERE lower(reviewer)=lower($1) OR lower(email)=lower($1)",
            user, local_auth.hash_password(payload.new_password))
    return {"ok": True}


# ── Invitations + app-native login (internal users) ─────────────────────────
DAV_SMTP_HOST = os.environ.get("DAV_SMTP_HOST", "").strip()
DAV_SMTP_PORT = int(os.environ.get("DAV_SMTP_PORT", "587"))
DAV_SMTP_USER = os.environ.get("DAV_SMTP_USER", "")
DAV_SMTP_PASSWORD = os.environ.get("DAV_SMTP_PASSWORD", "")
DAV_SMTP_FROM = os.environ.get("DAV_SMTP_FROM", "dav@localhost")
DAV_SMTP_TLS = os.environ.get("DAV_SMTP_TLS", "true").lower() == "true"
DAV_SMTP_VERIFY = os.environ.get("DAV_SMTP_VERIFY", "true").lower() == "true"
DAV_BASE_URL = os.environ.get("DAV_BASE_URL", "").rstrip("/")


def _send_email(to: str, subject: str, body: str) -> bool:
    """Best-effort SMTP send (sync; call via to_thread) using the runtime SMTP
    config (in-app setting → env). Returns False if SMTP is unconfigured so
    callers can fall back to sharing the link manually."""
    cfg = _smtp_cfg
    if not cfg.get("host"):
        return False
    import smtplib
    msg = _smtp_message(cfg.get("from", "dav@localhost"), to, subject, body)
    # Generous timeout: some hardened relays (e.g. Postfix postscreen pre-greet) delay
    # the 220 banner ~10-15s to deter spambots; a short timeout aborts before EHLO and
    # the mail never sends (logs only a connect/disconnect on the server).
    with smtplib.SMTP(cfg["host"], int(cfg.get("port", 587)), timeout=30) as s:
        if cfg.get("tls"):
            if cfg.get("verify_cert", True):
                s.starttls()
            else:
                import ssl
                s.starttls(context=ssl._create_unverified_context())
        if cfg.get("user"):
            s.login(cfg["user"], cfg.get("password", ""))
        s.send_message(msg)
    return True


async def _send_email_audited(to: str, subject: str, body: str, *, actor: str, action: str):
    """Send an email; on failure (incl. SMTP unconfigured) record a platform-admin-visible
    audit event and return (ok, error). Makes silent SMTP problems loud — see the email
    message-queue TODO (reliable delivery + retries + admin notification)."""
    err = ""
    try:
        ok = await asyncio.to_thread(_send_email, to, subject, body)
        if not ok:
            err = "SMTP not configured"
    except Exception as e:
        ok, err = False, str(e)
    if not ok:
        log.warning("email send failed (%s -> %s): %s", action, to, err)
        try:
            _fire(audit.record(pool, action=action, actor=actor, outcome="failure",
                               object_type="email", object_id=to,
                               summary=f"email not sent: {err}",
                               detail={"to": to, "subject": subject, "error": err}))
        except Exception:
            log.exception("audit of email failure failed (non-fatal)")
    return ok, err


async def _reload_approved() -> None:
    """Rebuild the in-memory approved set from the users table. Source-agnostic:
    'approved' == an ENABLED account exists (roles decide what they can do)."""
    global _approved_lower
    if pool is None:
        return
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT lower(reviewer) r, lower(email) e FROM users WHERE enabled")
    _approved_lower = {x["r"] for x in rows} | {x["e"] for x in rows if x["e"]}


async def _ensure_account(user: str) -> bool:
    """Source-agnostic just-in-time provisioning: the first time an authenticated
    identity is seen, map it in as an ENABLED, role-less account so a platform
    admin can assign roles (it sees nothing until then). Returns the account's
    enabled state; never re-enables a deliberately-disabled account."""
    u = (user or "").strip().lower()
    if not u or pool is None:
        return False
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT enabled FROM users WHERE lower(reviewer)=$1 OR lower(email)=$1", u)
        if row is not None:
            return bool(row["enabled"])
        await conn.execute(
            "INSERT INTO users (reviewer, email, display_name, role, approved, source, enabled) "
            "VALUES ($1,$1,'','viewer',true,'auto',true) ON CONFLICT (reviewer) DO NOTHING", u)
    await _reload_approved()
    return True


async def _reconcile_admin(conn) -> Optional[str]:
    """Run the default-admin invariant. On auto-reactivation (no enabled platform
    admin remained), email + log a notice and return a warning string for the
    caller to surface in its response; otherwise None."""
    try:
        res = await rbac.reconcile_default_admin(conn, _DEFAULT_ADMIN_EMAIL)
    except Exception:
        log.exception("default-admin reconcile failed")
        return None
    if res and res.get("reactivated"):
        de = res.get("default_email")
        msg = (f"No enabled platform admin remained — the default admin "
               f"account '{de}' was automatically re-activated.")
        log.warning(msg)
        try:
            await asyncio.to_thread(
                _send_email, de, "DAV: default admin re-activated",
                msg + "\n\nSign in to the default account and restore a platform admin.")
        except Exception as e:
            log.warning("reactivation notice email failed: %s", e)
        return msg
    return None


def _set_session_cookie(resp: JSONResponse, email: str) -> None:
    resp.set_cookie(
        local_auth.SESSION_COOKIE, local_auth.make_session(email),
        httponly=True, secure=True, samesite="lax", max_age=local_auth.SESSION_TTL, path="/")


class InviteIn(BaseModel):
    email: str
    display_name: str = ""
    project_id: Optional[int] = None
    project_role: str = "editor"
    global_role: str = "editor"


@app.post("/api/invites")
async def create_invite(payload: InviteIn, request: Request):
    """Invite a user (by email) into a project. Platform/project admin only.
    Emails a tokened accept link; if SMTP is unconfigured the link is returned
    so the admin can share it manually."""
    # Project-scoped invite → project admin (of that project) may send it; a
    # project-less (global) invite requires a global admin.
    if payload.project_id is not None:
        inviter = await require_project_admin(request, payload.project_id)
    else:
        inviter = await require_role(request, "admin")
    email = (payload.email or "").strip().lower()
    if "@" not in email:
        raise HTTPException(400, "valid email required")
    if payload.project_role not in _ASSIGNABLE_PROJECT_ROLES or payload.global_role not in _ASSIGNABLE_GLOBAL_ROLES:
        raise HTTPException(400, "invalid role")
    # A non-platform inviter cannot grant a global role above their own.
    if _multiuser():
        inviter_rank = _ROLE_RANK.get(await _user_role(inviter), 0)
        if _ROLE_RANK.get(payload.global_role, 0) > inviter_rank:
            raise HTTPException(403, "cannot grant a global role above your own")
        # uc-admin is off the rank ladder — only someone who already holds the
        # use-case-admin capability may grant it globally.
        if payload.global_role == "uc-admin" and not await _can_manage_uc_sources(inviter):
            raise HTTPException(403, "cannot grant use-case admin without holding it")
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO user_invitations
                 (token, email, display_name, project_id, project_role, global_role, invited_by, expires_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
            token, email, payload.display_name, payload.project_id,
            payload.project_role, payload.global_role, inviter, expires)
    _base = _public_base(request)
    link = f"{_base}/?invite={token}" if _base else f"/?invite={token}"
    emailed, email_error = await _send_email_audited(
        email, "You're invited to DAV",
        f"{inviter} invited you to DAV. Open this link to set a password and join:\n\n{link}\n\n"
        f"This invite expires in 7 days.",
        actor=inviter, action="invite.email")
    return {"ok": True, "token": token, "link": link, "emailed": emailed, "email_error": email_error}


@app.get("/api/invites")
async def list_invites(request: Request):
    """Pending invitations (admin)."""
    await require_role(request, "admin")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT i.token, i.email, i.display_name, i.project_role, i.global_role,
                      i.invited_by, i.created_at, i.expires_at, i.accepted_at, p.name AS project_name
               FROM user_invitations i LEFT JOIN projects p ON p.id=i.project_id
               WHERE i.accepted_at IS NULL ORDER BY i.created_at DESC""")
    return {"invites": [{**dict(r),
                         "created_at": r["created_at"].isoformat(),
                         "expires_at": r["expires_at"].isoformat()} for r in rows]}


@app.delete("/api/invites/{token}", status_code=204)
async def revoke_invite(token: str, request: Request):
    await require_role(request, "admin")
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM user_invitations WHERE token=$1", token)


@app.get("/api/invites/{token}")
async def get_invite(token: str):
    """Public: view an invite's target so the accept page can show context."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT i.email, i.display_name, i.expires_at, i.accepted_at, p.name AS project_name
               FROM user_invitations i LEFT JOIN projects p ON p.id=i.project_id
               WHERE i.token=$1""", token)
    if not row or row["accepted_at"] or row["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(404, "invite invalid, already used, or expired")
    return {"email": row["email"], "display_name": row["display_name"],
            "project_name": row["project_name"], "sessions_enabled": local_auth.sessions_enabled()}


class AcceptInviteIn(BaseModel):
    password: str
    display_name: str = ""


@app.post("/api/invites/{token}/accept")
async def accept_invite(token: str, payload: AcceptInviteIn):
    """Public: accept an invite — create the internal user (argon2 password),
    join the project, and log in (session cookie)."""
    if not local_auth.sessions_enabled():
        raise HTTPException(503, "app sessions not configured (set DAV_SESSION_SECRET or DAV_FERNET_KEY)")
    if len(payload.password or "") < 8:
        raise HTTPException(400, "password must be at least 8 characters")
    async with pool.acquire() as conn:
        inv = await conn.fetchrow("SELECT * FROM user_invitations WHERE token=$1", token)
        if not inv or inv["accepted_at"] or inv["expires_at"] < datetime.now(timezone.utc):
            raise HTTPException(404, "invite invalid, already used, or expired")
        email = inv["email"].strip().lower()
        name = (payload.display_name or inv["display_name"] or email).strip()
        pwhash = local_auth.hash_password(payload.password)
        await conn.execute(
            """INSERT INTO users (reviewer, email, display_name, role, approved, source, enabled, password_hash, must_change_password)
               VALUES ($1,$1,$2,'viewer',true,'internal',true,$3,false)
               ON CONFLICT (reviewer) DO UPDATE
                 SET password_hash=EXCLUDED.password_hash, approved=true, enabled=true,
                     source='internal', display_name=EXCLUDED.display_name, must_change_password=false""",
            email, name, pwhash)
        # Map any legacy invite roles into the RBAC model (account-activation
        # invites carry none — roles are assigned in the Users & roles UI).
        if inv["project_id"] is not None and inv["project_role"]:
            rolekey = {"admin": "project-admin", "uc-admin": "project-admin",
                       "editor": "project-edit", "viewer": "project-viewer"}.get(
                           inv["project_role"], "project-viewer")
            rid = await conn.fetchval("SELECT id FROM rbac_roles WHERE key=$1", rolekey)
            if rid:
                await rbac.assign_role(conn, email, rid, inv["project_id"], inv["invited_by"] or "invite")
        if inv["global_role"] == "platform-admin":
            rid = await conn.fetchval("SELECT id FROM rbac_roles WHERE key='platform-admin'")
            if rid:
                await rbac.assign_role(conn, email, rid, None, inv["invited_by"] or "invite")
        await conn.execute("UPDATE user_invitations SET accepted_at=now() WHERE token=$1", token)
        await _reconcile_admin(conn)
    await _reload_approved()
    resp = JSONResponse({"ok": True, "email": email})
    _set_session_cookie(resp, email)
    return resp


class LoginIn(BaseModel):
    email: str
    password: str


@app.post("/api/auth/login")
async def auth_login(payload: LoginIn, request: Request):
    """App-native login for internal users (email + password)."""
    if not local_auth.sessions_enabled():
        raise HTTPException(503, "app sessions not configured")
    email = (payload.email or "").strip().lower()

    async def _audit_login(outcome: str, summary: str) -> None:
        await audit.record(
            pool, action="auth.login", actor=email, actor_source="session",
            method="POST", path="/api/auth/login", outcome=outcome,
            ip=_client_ip(request), user_agent=request.headers.get("user-agent"),
            summary=summary)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT password_hash, approved FROM users WHERE lower(reviewer)=$1 OR lower(email)=$1", email)
    if not row or not row["password_hash"] or not local_auth.verify_password(row["password_hash"], payload.password):
        await _audit_login("denied", "invalid email or password")
        raise HTTPException(401, "invalid email or password")
    if not row["approved"]:
        await _audit_login("denied", "account not approved")
        raise HTTPException(403, "account not approved")
    resp = JSONResponse({"ok": True, "email": email})
    _set_session_cookie(resp, email)
    await _audit_login("success", "login")
    return resp


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    email = local_auth.read_session(request.cookies.get(local_auth.SESSION_COOKIE, ""))
    if not email:
        try:
            email = get_user(request)
        except Exception:
            email = None
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(local_auth.SESSION_COOKIE, path="/")
    await audit.record(
        pool, action="auth.logout", actor=email, actor_source="session",
        method="POST", path="/api/auth/logout", outcome="success",
        ip=_client_ip(request), user_agent=request.headers.get("user-agent"),
        summary="logout")
    return resp


@app.get("/api/audit")
async def get_audit(
    request: Request,
    actor: Optional[str] = Query(None, description="substring match on actor"),
    action: Optional[str] = Query(None, description="substring match on action"),
    outcome: Optional[str] = Query(None, description="success|denied|error|failure"),
    hours: Optional[int] = Query(None, ge=1, le=8760, description="last N hours"),
    limit: int = Query(200, ge=1, le=1000),
    before_id: Optional[int] = Query(None, description="paginate: id < before_id"),
):
    """Audit log — who did what + auth events. Platform-admin (all actors)."""
    await require_priv(request, rbac.P_PLATFORM_ADMIN)
    since = None
    if hours:
        from datetime import datetime, timedelta, timezone
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with pool.acquire() as conn:
        events = await audit.query(conn, actor=actor, action=action, outcome=outcome,
                                   since=since, limit=limit, before_id=before_id)
    return {"events": events}


@app.get("/api/auth/sso")
async def auth_sso(request: Request):
    """Mint an app session from the oauth-proxy identity (OCP/FreeIPA). Under a
    relaxed proxy this is the ONE path that stays proxy-protected, so it carries
    the X-Forwarded headers; the SPA navigates here to establish a session.

    Cross-pollinates by email (reuses an existing user row with the same email),
    provisions the user, but does NOT auto-approve — OCP users are enabled
    explicitly by an admin. Redirects back to the app afterwards.
    """
    raw = (request.headers.get("X-Forwarded-User")
           or request.headers.get("X-Forwarded-Email")
           or request.headers.get("X-Auth-Request-User")
           or request.headers.get("X-Auth-Request-Email"))
    if not raw:
        raise HTTPException(401, "no upstream identity (is this path proxy-protected?)")
    email = (request.headers.get("X-Forwarded-Email")
             or request.headers.get("X-Auth-Request-Email")
             or (raw if "@" in raw else "")).strip().lower()
    name = request.headers.get("X-Forwarded-Preferred-Username") or raw
    canonical = (email or raw).strip().lower()
    if pool is not None:
        async with pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT reviewer FROM users WHERE lower(email)=$1 LIMIT 1", email) if email else None
            reviewer = (existing or canonical).lower()
            await conn.execute(
                """INSERT INTO users (reviewer, email, display_name, source)
                   VALUES ($1,$2,$3,'oauth')
                   ON CONFLICT (reviewer) DO UPDATE
                     SET email=COALESCE(NULLIF(EXCLUDED.email,''), users.email),
                         display_name=COALESCE(NULLIF(EXCLUDED.display_name,''), users.display_name)""",
                reviewer, email, name)
    else:
        reviewer = canonical
    resp = RedirectResponse(url="/", status_code=303)
    _set_session_cookie(resp, reviewer)
    return resp


# ========================= RUNS =========================


@app.get("/api/runs")
async def list_runs(request: Request, limit: int = Query(50, ge=1, le=200), show_archived: bool = Query(False)):
    """List recent PipelineRuns, enriched with run_sessions metadata when available.

    Archived runs (run_sessions.archived) are hidden unless show_archived=true.
    Scoped to the active project: a run belongs to a project via its session's
    project_id; runs with no session (orphan Tekton runs) appear only under the
    default project so they're never lost."""
    if not validations.ENABLED:
        return {"runs": [], "enabled": False}
    try:
        runs = await asyncio.to_thread(validations.list_recent, limit)
    except Exception as e:
        log.exception("list runs failed")
        raise HTTPException(500, f"list failed: {e}")
    # Bulk-fetch session rows by run_name; the table is small (one row per run)
    names = [r.get("name") for r in runs if r.get("name")]
    sessions_by_name: dict[str, dict] = {}
    runid_by_name: dict[str, str] = {}   # PipelineRun name → its ingested analysis run_id
    active_pid = default_pid = None
    async with pool.acquire() as _pc:
        active_pid = await _active_project_id(request, _pc)
        default_pid = await _default_project_id(_pc)
    if names:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT run_name, name, description, category, tags, "
                    "gpu_energy_joules, total_gen_tokens, total_prompt_tokens, "
                    "mode, trigger_payload, "
                    # Resolve the set's CURRENT name by joining use_case_sets on set_id — so a set
                    # rename reflects everywhere. The stored set_name is a provenance fallback only
                    # (covers a deleted set [FK is ON DELETE SET NULL] + the synthetic "All Use Cases"
                    # / custom-selection pseudo-sets that have no use_case_sets row).
                    "set_id, set_name, "
                    "(SELECT name FROM use_case_sets WHERE id = run_sessions.set_id) AS set_live_name, "
                    "selection_mode, "
                    "uc_total, uc_succeeded, uc_failed, archived, project_id, "
                    "corpus_repo_branch, spec_repo_branch, corpus_repo_sha, spec_repo_sha "
                    "FROM run_sessions WHERE run_name = ANY($1::text[])",
                    names,
                )
                rid_rows = await conn.fetch(
                    "SELECT DISTINCT ON (run_name) run_name, run_id, "
                    "total_ucs, successful, failed FROM analysis_runs "
                    "WHERE run_name = ANY($1::text[]) ORDER BY run_name, ingested_at DESC",
                    names,
                )
            for row in rows:
                sessions_by_name[row["run_name"]] = dict(row)
            for row in rid_rows:
                runid_by_name[row["run_name"]] = dict(row)
        except Exception as e:
            log.warning("list_runs session join failed: %s", e)
    for r in runs:
        ar = runid_by_name.get(r.get("name")) or {}
        r["run_id"] = ar.get("run_id")   # null until analysis is ingested
        s = sessions_by_name.get(r.get("name"))
        # Field lift (Wave 0): consumers were reading `status` / `inference_model`
        # as None because the row only carries `phase` + kebab-case Tekton params.
        # Surface them top-level in snake_case: PipelineRun params (the resolved
        # values) win; run_sessions.trigger_payload is the fallback for pruned runs.
        p = r.get("params") or {}
        tp = (_parse_jsonb(s["trigger_payload"]) or {}) if (s and s.get("trigger_payload")) else {}
        r["status"] = r.get("phase")
        r["inference_model"]    = p.get("inference-model")    or tp.get("inference_model")
        r["inference_endpoint"] = p.get("inference-endpoint") or tp.get("inference_endpoint")
        r["mode"] = p.get("mode") or (s.get("mode") if s else None) or tp.get("mode")
        if s:
            r["session_name"] = s.get("name") or None
            r["category"] = s.get("category")
            r["gpu_energy_joules"] = s.get("gpu_energy_joules")
            r["total_gen_tokens"]  = s.get("total_gen_tokens")
            r["total_prompt_tokens"] = s.get("total_prompt_tokens")
            r["set_id"]        = s.get("set_id")
            # live name (join on set_id) wins; stored snapshot is the fallback (deleted/synthetic set).
            r["set_name"]      = s.get("set_live_name") or s.get("set_name") or None
            r["selection_mode"] = s.get("selection_mode") or None
            # run_sessions' uc_* columns are unpopulated today (the finalizer
            # defers them); the ingested analysis row is the authoritative
            # source — without this fallback a 31/32 run shows a bare red
            # "Failed" with no counts and reads as "all UCs failed"
            # (observed: dav-stage2-console-787069, 2026-06-07).
            r["uc_total"]      = s.get("uc_total")     if s.get("uc_total")     is not None else ar.get("total_ucs")
            r["uc_succeeded"]  = s.get("uc_succeeded") if s.get("uc_succeeded") is not None else ar.get("successful")
            r["uc_failed"]     = s.get("uc_failed")    if s.get("uc_failed")    is not None else ar.get("failed")
            r["archived"]      = bool(s.get("archived"))
            r["project_id"]    = s.get("project_id")
            # #branch-targeting: evaluated git ref provenance for results + decisions.
            r["corpus_repo_branch"] = s.get("corpus_repo_branch")
            r["spec_repo_branch"]   = s.get("spec_repo_branch")
            r["corpus_repo_sha"]    = s.get("corpus_repo_sha")
            r["spec_repo_sha"]      = s.get("spec_repo_sha")
        else:
            r["project_id"] = None
            r["uc_total"]     = ar.get("total_ucs")
            r["uc_succeeded"] = ar.get("successful")
            r["uc_failed"]    = ar.get("failed")
    if not show_archived:
        runs = [r for r in runs if not r.get("archived")]
    # Scope to the active project. A sessioned run shows under its project; an
    # orphan (no session) shows only under the default project.
    if active_pid is not None:
        runs = [r for r in runs
                if (r["project_id"] == active_pid)
                or (r["project_id"] is None and active_pid == default_pid)]
    return {"runs": runs, "enabled": True}


class RunArchiveIn(BaseModel):
    archived: bool = True


async def _run_project_id(conn, name: str) -> Optional[int]:
    """The project a run belongs to: its run_sessions.project_id, else its
    analysis_runs.project_id, else the default project (for orphan runs)."""
    pid = await conn.fetchval("SELECT project_id FROM run_sessions WHERE run_name=$1", name)
    if pid is None:
        pid = await conn.fetchval(
            "SELECT project_id FROM analysis_runs WHERE run_name=$1 AND project_id IS NOT NULL LIMIT 1", name)
    if pid is None:
        pid = await _default_project_id(conn)
    return pid


@app.post("/api/runs/{name}/archive")
async def archive_run(name: str, payload: RunArchiveIn, request: Request):
    """Soft-archive (hide) or unarchive a run. Reversible; all data is kept.

    Upserts the archived flag so any run can be hidden — runs that were never
    triggered through the console have no run_sessions row, so we create a minimal
    one (carrying the run's resolved project_id) to hold the flag."""
    user = get_user(request)
    async with pool.acquire() as conn:
        rpid = await _run_project_id(conn, name)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_RUNS_MANAGE, rpid)
        await conn.execute(
            """INSERT INTO run_sessions (run_name, created_by, archived, project_id)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (run_name) DO UPDATE SET archived = EXCLUDED.archived""",
            name, user, payload.archived, rpid)
    return {"ok": True, "name": name, "archived": payload.archived}


@app.post("/api/runs/{name}/cancel")
async def stop_run(name: str, request: Request):
    """Stop an in-flight run: gracefully cancel its Tekton PipelineRun (the
    finally task GCs the per-run workspace). Any ingested results + DB rows are
    kept — use DELETE to remove a run entirely. Gated on runs.execute (whoever
    can start a run can stop one)."""
    async with pool.acquire() as conn:
        rpid = await _run_project_id(conn, name)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_RUNS_EXECUTE, rpid)
    ok = await asyncio.to_thread(validations.cancel_run, name)
    if not ok:
        raise HTTPException(404, "run not found or already finished")
    return {"ok": True, "name": name, "status": "Cancelled"}


class RunTimeoutIn(BaseModel):
    seconds: int


@app.post("/api/runs/{name}/timeout")
async def set_run_time_allowed(name: str, payload: RunTimeoutIn, request: Request):
    """Edit a run's 'time allowed' (pipeline timeout) — extend or shorten it
    mid-run. The failsafe is a safety net, not a budget; this lets the operator
    say "don't go past this long" live. Gated on runs.execute."""
    clamped = max(3600, min(86400, int(payload.seconds)))
    async with pool.acquire() as conn:
        rpid = await _run_project_id(conn, name)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_RUNS_EXECUTE, rpid)
        # Tekton rejects spec updates on a started PipelineRun (the old spec-patch
        # here 500'd on the admission webhook), so "time allowed" is CONSOLE-
        # enforced: stored per run, checked by the finalizer-loop watchdog, which
        # cancels (a status update — allowed) when elapsed exceeds it. Extend and
        # shorten both work, up to the run's immutable 24h Tekton failsafe.
        row = await conn.fetchrow(
            "UPDATE run_sessions SET trigger_payload = jsonb_set("
            "  coalesce(trigger_payload, '{}'::jsonb),"
            "  '{effective_timeout_seconds}', to_jsonb($2::int))"
            " WHERE run_name=$1 AND finalized_at IS NULL"
            " RETURNING run_name", name, clamped)
    if not row:
        raise HTTPException(404, "run not found or already finished")
    return {"ok": True, "name": name, "timeout_seconds": clamped,
            "enforced_by": "console (finalizer watchdog); Tekton failsafe fixed at 24h"}


@app.delete("/api/runs/{name}")
async def delete_run(name: str, request: Request):
    """Completely and irreversibly remove a run: its ingested analysis (cascades to
    uc_analyses/gaps/capabilities), its workspace result files, its run_session
    record, and the Tekton PipelineRun object. Archive instead to merely hide it."""
    get_user(request)
    removed = {"analysis_runs": 0, "run_sessions": 0, "workspace_dirs": 0, "pipelinerun": False}
    async with pool.acquire() as conn:
        rpid = await _run_project_id(conn, name)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_RUNS_MANAGE, rpid)
        run_ids = [r["run_id"] for r in await conn.fetch(
            "SELECT run_id FROM analysis_runs WHERE run_name=$1", name)]
        for rid in run_ids:
            try:
                if _results.delete_run_dir(rid):
                    removed["workspace_dirs"] += 1
            except Exception as e:
                log.warning("delete workspace dir %s failed: %s", rid, e)
        r1 = await conn.execute("DELETE FROM analysis_runs WHERE run_name=$1", name)
        r2 = await conn.execute("DELETE FROM run_sessions WHERE run_name=$1", name)
    removed["analysis_runs"] = int(r1.split()[-1]) if r1.startswith("DELETE") else 0
    removed["run_sessions"]  = int(r2.split()[-1]) if r2.startswith("DELETE") else 0
    try:
        removed["pipelinerun"] = await asyncio.to_thread(validations.delete_run, name)
    except Exception as e:
        log.warning("delete pipelinerun %s failed: %s", name, e)
    log.info("Deleted run %s completely: %s", name, removed)
    return {"ok": True, "name": name, "removed": removed}


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
    async with pool.acquire() as conn:
        _trigpid = await _active_project_id(request, conn)
        # No-silent-orphan guard: a run with no resolvable project lands in
        # run_sessions with project_id=NULL — invisible in the project-scoped
        # runs list and unattributable. Reject BEFORE launching the PipelineRun
        # (cheaper than launching then orphaning). The service token now resolves
        # via the X-DAV-Project header (see _active_project_id), so this only
        # fires on a genuinely projectless caller.
        if _trigpid is None:
            raise HTTPException(
                400, "no active project for this run; set the X-DAV-Project "
                "header (or a default project) so the run can be attributed")
        await _require_priv_conn(conn, request, rbac.P_PROJECT_RUNS_EXECUTE, _trigpid)
    # Server-side scope enforcement. set_id / selection_mode were stored as
    # run_sessions lineage ONLY — the engine is scoped exclusively by
    # uc_handles / uc_uuids / managed_uc_uuids, which the UI resolves
    # client-side. An API caller sending {"selection_mode":"set","set_id":N}
    # with no UC lists therefore got a SILENT full-corpus run (repro:
    # dav-stage2-console-853521 — set_id=29, executed all 420 corpus UCs).
    # Resolve the set here exactly like the UI does; refuse a declared
    # narrowed scope that would otherwise fall through to the full corpus.
    _has_selection = bool(payload.uc_handles or payload.uc_uuids
                          or payload.managed_uc_uuids)
    _sel_action = _run_selection.selection_action(
        payload.selection_mode, payload.set_id,
        is_all_set=(payload.set_id is not None and _is_all_set(payload.set_id)),
        has_explicit_selection=_has_selection)
    if _sel_action == _run_selection.RESOLVE_SET:
        _sel_sid = _real_set_id(payload.set_id)
        async with pool.acquire() as conn:
            if not await conn.fetchval(
                    "SELECT 1 FROM use_case_sets WHERE id=$1", _sel_sid):
                raise HTTPException(404, f"set {_sel_sid} not found")
            _sel_rows = await conn.fetch(
                "SELECT uc_uuid, uc_source, uc_handle FROM use_case_set_members "
                "WHERE set_id=$1 ORDER BY added_at", _sel_sid)
        _flt = _run_selection.member_filter([dict(r) for r in _sel_rows])
        if not any(_flt.values()):
            raise HTTPException(
                400, f"set {_sel_sid} has no members; a set-scoped run would "
                     "otherwise execute the full corpus. Add UCs to the set, "
                     "or drop set_id/selection_mode for a full-corpus run.")
        payload.uc_handles       = _flt["uc_handles"] or None
        payload.uc_uuids         = _flt["uc_uuids"] or None
        payload.managed_uc_uuids = _flt["managed_uc_uuids"] or None
    elif _sel_action == _run_selection.REJECT:
        raise HTTPException(
            400, f"selection_mode={payload.selection_mode!r} declares a "
                 "narrowed scope but carries no uc_handles/uc_uuids/"
                 "managed_uc_uuids and no resolvable set_id; refusing to "
                 "silently run the full corpus. Use selection_mode='corpus' "
                 "(or set_id='__all__') for a full-corpus run.")
    params = _resolve_run_params(payload)
    # Per-(model, use) override system (DAV migration 014). Resolve
    # capabilities + use_profile from DB by inference_model. use_key
    # maps from the run mode: verification → evaluation_verification,
    # explore → evaluation_explore, reproduce → evaluation_reproduce.
    # arch_review / uc_assist / enhancement go through their own
    # endpoints, not /api/runs.
    use_key = f"evaluation_{payload.mode}" if payload.mode in {"verification", "explore", "reproduce"} else None
    capabilities_json: Optional[str] = None
    use_profile_json: Optional[str] = None
    if params["inference_model"]:
        async with pool.acquire() as conn:
            mc_row = await conn.fetchrow(
                "SELECT id, capabilities FROM model_configs "
                "WHERE model_id=$1 AND enabled AND (project_id=$2 OR project_id IS NULL) "
                "ORDER BY (project_id IS NULL), id LIMIT 1",  # scope-aware (#107 2b): project caps preferred, platform fallback
                params["inference_model"], _trigpid,
            )
            if mc_row:
                caps_raw = mc_row["capabilities"]
                if caps_raw:
                    capabilities_json = (
                        caps_raw if isinstance(caps_raw, str) else json.dumps(caps_raw)
                    )
                if use_key:
                    profile_row = await conn.fetchrow(
                        "SELECT params FROM model_use_profiles "
                        "WHERE model_config_id=$1 AND use_key=$2",
                        mc_row["id"], use_key,
                    )
                    if profile_row and profile_row["params"]:
                        prm = profile_row["params"]
                        use_profile_json = (
                            prm if isinstance(prm, str) else json.dumps(prm)
                        )
    # Time budget: planned UC count → the failsafe "time allowed". Use the
    # operator's explicit value if given, else ETA (uc_count × data-driven
    # per-UC estimate) + failsafe buffer. 0 (full corpus) → None, so
    # _mk_pipelinerun applies its generous fixed default.
    _trig_uc_count = (len(payload.uc_handles or []) + len(payload.uc_uuids or [])
                      + len(payload.managed_uc_uuids or []))
    _trig_time_allowed = payload.time_allowed_seconds
    if not _trig_time_allowed and _trig_uc_count > 0:
        _est, _ = await _est_per_uc_seconds()
        _trig_time_allowed = _trig_uc_count * _est + validations.FAILSAFE_BUFFER_SEC
    # Sample concurrency: explicit, else auto = min(effective sample count, cap).
    # The ensemble samples are independent, so running them in parallel is a pure
    # throughput win that batches on the (typically idle) GPU.
    _eff_samples = payload.sample_count or {"verification": 3, "explore": 10,
                                            "reproduce": 1}.get(payload.mode, 1)
    _trig_sample_conc = (payload.sample_concurrency if payload.sample_concurrency is not None
                         else min(_eff_samples, int(os.environ.get("DAV_MAX_SAMPLE_CONCURRENCY", "4"))))
    # #93 promotion: inject the project's Evaluation (stage-2) prompt into NORMAL runs iff it's been
    # promoted live (applied=true) — set after a winning A/B. Held prompts stay A/B-only (byte-identical).
    _stage2_ctx = None
    async with pool.acquire() as conn:
        _s2 = await conn.fetchval(
            "SELECT content FROM project_stage_context "
            "WHERE project_id=$1 AND stage='stage2-analysis' AND applied=true", _trigpid)
        if _s2 and _s2.strip():
            _stage2_ctx = _s2.strip()
    try:
        result = await asyncio.to_thread(validations.trigger_run,
            triggered_by=reviewer,
            branch=payload.branch,
            commit_sha=payload.commit_sha,
            inference_endpoint=params["inference_endpoint"],
            inference_model=params["inference_model"],
            mode=payload.mode,
            sample_count=payload.sample_count,
            sample_concurrency=_trig_sample_conc,
            uc_concurrency=payload.uc_concurrency,
            corpus_subpath=params["corpus_subpath"],
            corpus_repo_url=params["corpus_repo_url"],
            corpus_repo_branch=params["corpus_repo_branch"],
            spec_repo_url=params["spec_repo_url"],
            spec_repo_branch=params["spec_repo_branch"],
            halt_on_error=payload.halt_on_error,
            uc_handles=payload.uc_handles,
            uc_uuids=payload.uc_uuids,
            managed_uc_uuids=payload.managed_uc_uuids,
            corpus_namespaces=payload.corpus_namespaces,
            spec_namespaces=payload.spec_namespaces,
            use_key=use_key,
            capabilities_json=capabilities_json,
            use_profile_json=use_profile_json,
            stage2_two_pass=payload.stage2_two_pass,
            max_tokens=payload.max_tokens,
            grounding_nudge=(None if payload.grounding_nudge is None
                             else ("true" if payload.grounding_nudge else "false")),
            request_timeout_seconds=payload.request_timeout_seconds,
            stage2_context=_stage2_ctx,
            uc_count=_trig_uc_count,
            # Explicit "time allowed" from the modal, else the data-driven
            # default (ETA = uc_count × median per-UC + buffer); None for full
            # corpus → _mk_pipelinerun's generous fixed fallback.
            time_allowed_seconds=_trig_time_allowed,
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
            _run_pid = await _active_project_id(request, conn)
            await conn.execute(
                """INSERT INTO run_sessions
                   (run_name, name, description, category, tags, mode,
                    created_by, started_at,
                    baseline_gen_tokens, baseline_prompt_tokens,
                    set_id, set_name, selection_mode, uc_state_snapshot,
                    spec_namespaces, corpus_namespaces, project_id,
                    trigger_payload, corpus_repo_branch, spec_repo_branch)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, now(), $8, $9,
                           $10, $11, $12, $13::jsonb, $14, $15, $16,
                           $17::jsonb, $18, $19)""",
                result["name"], payload.name, payload.description,
                payload.category or "ad-hoc", payload.tags or [],
                payload.mode, reviewer,
                baseline_gen, baseline_prompt,
                # The synthetic "All Use Cases" set (id 0) has no use_case_sets
                # row, so set_id must be NULL (FK run_sessions.set_id →
                # use_case_sets.id) — the lineage is carried by set_name. Sending
                # 0 here would violate the FK and drop the whole session row,
                # making the run invisible in the list.
                (None if (payload.set_id is None or _is_all_set(payload.set_id)) else int(payload.set_id)),
                payload.set_name, payload.selection_mode,
                json.dumps(uc_state_snapshot) if uc_state_snapshot else None,
                payload.spec_namespaces, payload.corpus_namespaces, _run_pid,
                # Durable rerun record — survives Tekton PipelineRun pruning.
                # effective_timeout_seconds = the console-enforced "time allowed"
                # (user value or the ETA-derived failsafe); the Tekton spec timeout
                # is fixed at the 24h cap (immutable once started).
                json.dumps({**payload.model_dump(),
                            "effective_timeout_seconds": _trig_time_allowed}),
                # Evaluated branch (resolved override → registry default); the HEAD
                # SHA is filled in at ingest once the repos are actually cloned.
                params.get("corpus_repo_branch"), params.get("spec_repo_branch"),
            )
    except Exception as e:
        log.warning("run_sessions insert failed for %s: %s", result.get("name"), e)

    return {"ok": True, "run": result, "resolved_params": params}


@app.get("/api/runs/{name}/rerun-config")
async def get_rerun_config(name: str):
    """The configuration Rerun must reproduce. Source order: the stored
    trigger payload (durable — survives PipelineRun pruning), else the live
    PipelineRun params (legacy runs from before trigger_payload existed),
    else nothing — the UI must then say so rather than silently open defaults."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT trigger_payload, name, description, category, set_id, "
            "set_name, (SELECT name FROM use_case_sets WHERE id = run_sessions.set_id) AS set_live_name, "
            "selection_mode FROM run_sessions WHERE run_name=$1", name)
    if not row:
        raise HTTPException(404, f"run {name!r} not found")
    cfg = _parse_jsonb(row["trigger_payload"]) if row["trigger_payload"] else None
    params = None
    if cfg is None and validations.ENABLED:
        try:
            detail = await asyncio.to_thread(validations.get_run_detail, name)
            params = detail.get("params")
        except Exception:
            params = None   # PipelineRun pruned — nothing to fall back to
    return {
        "run_name": name,
        "config": cfg,                       # RunTriggerIn-shaped, or null
        "params": params,                    # legacy fallback, or null
        "session": {"name": row["name"], "description": row["description"],
                     "category": row["category"], "set_id": row["set_id"],
                     "set_name": row["set_live_name"] or row["set_name"],
                     "selection_mode": row["selection_mode"]},
    }


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
        result = await asyncio.to_thread(validations.get_task_logs, name, task, tail)
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


async def _correlate_inflight_progress(name: str, started_iso: Optional[str], conn,
                                       tolerance_seconds: int = 900) -> Optional[dict]:
    """Correlate a PipelineRun to its DISTINCT in-flight workspace run-dir among concurrent runs.

    The engine generates its own workspace run_id and does NOT record the PipelineRun name, so there
    is no direct link. Correlating by start time alone is unreliable (variable pod-init delay made
    two concurrent runs cross — a 6-UC run showed a 15-UC run's stats and vice-versa). So we correlate
    primarily by the run's KNOWN SCOPE SIZE: each run's trigger payload fixes how many UCs it
    evaluates (len(uc_uuids) or its set's member count), which must equal the workspace dir's
    `total_ucs`. That's deterministic whenever concurrent runs have different scope sizes. Start-time
    proximity is only a tiebreak (same-size runs) + the fallback for runs with no recorded scope.
    Each dir is claimed once, so two runs never share a dir.

    TODO (fully deterministic, incl. same-size runs): stamp `$(context.pipelineRun.name)` into
    run-progress.yaml via the Tekton run-corpus task and match on it.
    """
    from datetime import datetime
    def _p(s):
        try:
            return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        except Exception:
            return None
    dirs = _results.list_inflight_progress()
    if not dirs:
        return None
    try:
        active = await asyncio.to_thread(validations.list_recent, 50)
    except Exception:
        active = []
    runs: list[tuple] = [(r.get("name"), _p(r.get("started_at") or r.get("created_at")))
                         for r in active if r.get("phase") not in TERMINAL_PHASES and r.get("name")]
    if not any(n == name for (n, _t) in runs):
        runs.append((name, _p(started_iso)))
    # Expected UC count per run (the deterministic key) from its trigger payload / set.
    exp: dict = {}
    try:
        rows = await conn.fetch(
            "SELECT run_name, trigger_payload, set_id FROM run_sessions WHERE run_name = ANY($1::text[])",
            [n for (n, _t) in runs])
        for r in rows:
            cfg = _parse_jsonb(r["trigger_payload"]) or {}
            ucu = cfg.get("uc_uuids") or cfg.get("managed_uc_uuids")
            cnt = len(ucu) if isinstance(ucu, list) and ucu else None
            if cnt is None and r["set_id"]:
                cnt = await conn.fetchval(
                    "SELECT count(*) FROM use_case_set_members WHERE set_id=$1", r["set_id"])
            exp[r["run_name"]] = cnt
    except Exception as e:
        log.info("scope-size lookup failed during correlation: %s", e)

    dlist = [(d["_run_dir"], _p(d.get("started_at")), d.get("total_ucs"), d) for d in dirs]
    assigned: dict = {}
    claimed: set = set()

    # Pass 1 — exact scope-size match (deterministic when concurrent runs differ in size).
    for rn, rt in runs:
        ec = exp.get(rn)
        if ec is None:
            continue
        cands = [(drid, dt, dd) for (drid, dt, dtot, dd) in dlist
                 if drid not in claimed and dtot == ec]
        if not cands:
            continue
        if rt is not None:
            cands.sort(key=lambda c: abs((c[1] - rt).total_seconds()) if c[1] else 9e18)
        drid, _dt, dd = cands[0]
        assigned[rn] = dd
        claimed.add(drid)

    # Pass 2 — remaining runs by closest start time within tolerance (unique).
    pairs = []
    for rn, rt in runs:
        if rn in assigned or rt is None:
            continue
        for (drid, dt, dtot, dd) in dlist:
            if drid in claimed or dt is None:
                continue
            diff = abs((rt - dt).total_seconds())
            if diff <= tolerance_seconds:
                pairs.append((diff, rn, drid, dd))
    pairs.sort(key=lambda x: x[0])
    for diff, rn, drid, dd in pairs:
        if rn in assigned or drid in claimed:
            continue
        assigned[rn] = dd
        claimed.add(drid)
    return assigned.get(name)


@app.get("/api/runs/{name}/turns")
async def get_run_turns(
    name: str,
    request: Request,
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
    # #186: turns JSONL carries NDA prompt content — require an authenticated user
    # with read on the active project (was unauthenticated). Cross-project run IDOR
    # is the tracked P1 tenancy item.
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
    # Resolve PipelineRun → workspace run_id via timestamp correlation
    try:
        detail = await asyncio.to_thread(validations.get_run_detail, name)
    except KeyError:
        raise HTTPException(404, f"run {name!r} not found")
    started = detail.get("started_at") or detail.get("created_at")
    if not started or not _results.is_available():
        return {"files": [], "records": []}
    # Unique correlation across concurrent runs; fall back to single-nearest for terminal/historical
    # runs (whose in-flight progress file is gone).
    async with pool.acquire() as _c:
        progress = await _correlate_inflight_progress(name, started, _c)
    if not progress:
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


_est_per_uc_cache: dict = {"val": None, "is_default": True, "exp": 0.0}


async def _est_per_uc_seconds() -> tuple[int, bool]:
    """Per-UC wall-time estimate, data-driven: median(wall_time/uc_total) over
    finalized runs. With NO history yet it returns the env default (30 min) and
    is_default=True so the UI can note "adjusts as runs complete". Self-improves
    as real runs finish (cached ~5 min)."""
    now = time.monotonic()
    if _est_per_uc_cache["val"] is not None and _est_per_uc_cache["exp"] > now:
        return _est_per_uc_cache["val"], _est_per_uc_cache["is_default"]
    val = int(os.environ.get("DAV_EST_SEC_PER_UC", "1800"))
    is_default = True
    try:
        async with pool.acquire() as conn:
            med = await conn.fetchval(
                "SELECT percentile_cont(0.5) WITHIN GROUP "
                "(ORDER BY wall_time_seconds / NULLIF(uc_succeeded, 0)) "
                "FROM run_sessions "
                "WHERE finalized_at IS NOT NULL AND wall_time_seconds > 0 "
                # Per-UC pace = total time ÷ UCs that ACTUALLY completed. Using
                # uc_succeeded (not uc_total) means a timed-out run that did 5 of
                # 30 still contributes a correct per-UC rate instead of skewing
                # the estimate low — so history accrues from partial runs too.
                "  AND COALESCE(uc_succeeded, 0) > 0")
        if med and med > 0:
            val, is_default = int(med), False
    except Exception as e:
        log.debug("est_per_uc median query failed: %s", e)
    _est_per_uc_cache.update(val=val, is_default=is_default, exp=now + 300)
    return val, is_default


@app.get("/api/runs/estimate")
async def run_estimate():
    """Per-UC time estimate (data-driven median, else 30-min default) + failsafe
    buffer, for the New Run modal to suggest a 'time allowed' = uc_count × per-UC
    + buffer and note when it's still the default. Defined before /{name} so the
    literal path wins route matching."""
    est, is_default = await _est_per_uc_seconds()
    return {"est_per_uc_seconds": est, "est_per_uc_is_default": is_default,
            "failsafe_buffer_seconds": validations.FAILSAFE_BUFFER_SEC}


@app.get("/api/runs/preflight-hint")
async def runs_preflight_hint(
    set_id: Optional[str] = Query(None, description="UC set the operator is about to run (int id or '__all__')"),
    lookback_runs: int = Query(5, ge=1, le=20),
):
    """Pre-flight hint for the New Ingestion modal — Phase C of the
    infrastructure-confidence work.

    Looks at the last N runs for the same set_id (or globally if no set),
    counts how many had any UC flagged with infrastructure_confidence
    label = 'low' or 'compromised', and returns a structured hint object
    when the threshold is crossed. The UI renders this as an inline
    banner suggesting a long-context model or per-UC spec_namespaces.

    NB: declared BEFORE /api/runs/{name} — FastAPI matches in definition order, so the
    static path must come first or "preflight-hint" is swallowed as a run name (404)."""
    # '__all__' (synthetic set) runs are stored with set_id NULL — treat the
    # sentinel as a global lookback rather than failing int coercion.
    if set_id is not None and _is_all_set(set_id):
        set_id = None
    elif set_id is not None:
        set_id = _real_set_id(set_id)
    if set_id is None:
        return {"hint": None}
    async with pool.acquire() as conn:
        # Find recent runs that included this set
        recent_runs = await conn.fetch(
            """SELECT DISTINCT ar.run_id
               FROM analysis_runs ar
               JOIN run_sessions rs ON rs.run_name = ar.run_id
               WHERE rs.set_id = $1
               ORDER BY ar.run_id DESC LIMIT $2""",
            set_id, lookback_runs,
        )
        if not recent_runs:
            return {"hint": None}
        run_ids = [r["run_id"] for r in recent_runs]
        # For each run, count UCs by infra_confidence_label
        stats = await conn.fetch(
            """SELECT run_id, infra_confidence_label, COUNT(*) AS n
               FROM uc_analyses
               WHERE run_id = ANY($1::text[])
               GROUP BY run_id, infra_confidence_label""",
            run_ids,
        )
    from collections import defaultdict
    per_run = defaultdict(lambda: defaultdict(int))
    for r in stats:
        per_run[r["run_id"]][r["infra_confidence_label"] or "unscored"] += r["n"]
    # Threshold: ≥2 of the last N runs had at least one low or compromised UC
    triggering_runs = [
        rid for rid, counts in per_run.items()
        if counts.get("low", 0) + counts.get("compromised", 0) > 0
    ]
    if len(triggering_runs) < 2:
        return {"hint": None}
    worst = max(
        (counts.get("compromised", 0), counts.get("low", 0), rid)
        for rid, counts in per_run.items()
    )
    return {
        "hint": {
            "severity": "warning",
            "headline": (
                f"Heads up: {len(triggering_runs)} of the last "
                f"{len(per_run)} run(s) of this set had at least one UC "
                f"flagged with low or compromised infrastructure confidence."
            ),
            "detail": (
                "Consider running on a long-context model (Sonnet 4.6 / "
                "Opus 4.7 — 200K context) for this set, or narrowing each "
                "UC's spec_namespaces field to reduce exploration depth. "
                "The current local Qwen3-32B at 86K context may force "
                "early commits on deep-exploration UCs."
            ),
            "triggering_runs": triggering_runs,
            "set_id": set_id,
        }
    }


@app.get("/api/runs/{name}")
async def get_run_detail(name: str):
    """Return Tekton PipelineRun spec + per-TaskRun status + session metadata
    + per-UC progress (in-flight) + live session token deltas for the
    run-detail UI. Lazy-finalizes power/token stats on the first view after
    the run reaches a terminal phase."""
    if not validations.ENABLED:
        raise HTTPException(403, "pipeline trigger disabled")
    try:
        detail = await asyncio.to_thread(validations.get_run_detail, name)
    except KeyError:
        raise HTTPException(404, f"run {name!r} not found")
    except Exception as e:
        log.exception("run detail fetch failed")
        raise HTTPException(500, f"detail failed: {e}")
    session = await _maybe_finalize_session(detail)
    if session is not None:
        detail["session"] = session

    # "Time allowed" shown to the UI = the CONSOLE-ENFORCED effective timeout
    # (run_sessions trigger_payload.effective_timeout_seconds), not the Tekton
    # spec value — the spec is pinned at the immutable 24h failsafe.
    try:
        async with pool.acquire() as conn:
            eff = await conn.fetchval(
                "SELECT (trigger_payload->>'effective_timeout_seconds')::int "
                "FROM run_sessions WHERE run_name=$1", name)
        if eff:
            detail["timeout_seconds"] = eff
    except Exception:
        pass

    # Per-UC outcome counts from the ingested analysis (authoritative once the
    # run completes; run_sessions.uc_* stay NULL — the finalizer defers them).
    # Lets the header show "31/32 ok · 1 fail" instead of just a red Failed.
    try:
        async with pool.acquire() as conn:
            ar = await conn.fetchrow(
                "SELECT run_id, total_ucs, successful, failed FROM analysis_runs "
                "WHERE run_name=$1 ORDER BY ingested_at DESC LIMIT 1", name)
        if ar:
            detail["uc_total"]     = ar["total_ucs"]
            detail["uc_succeeded"] = ar["successful"]
            detail["uc_failed"]    = ar["failed"]
    except Exception as e:
        log.info("analysis counts lookup failed for %s: %s", name, e)

    # Per-UC progress: find the matching workspace run-dir's run-progress.yaml
    # by timestamp correlation. Only useful while the run is in flight.
    if detail.get("phase") not in TERMINAL_PHASES:
        started = detail.get("started_at") or detail.get("created_at")
        if started and _results.is_available():
            try:
                # Unique per-run correlation — never share a workspace dir between two concurrent
                # runs (which made the live drawer show the same UC stats for both).
                async with pool.acquire() as _c:
                    progress = await _correlate_inflight_progress(name, started, _c)
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
    # Per-UC time estimate (median over finalized runs, else 30-min default) —
    # the UI multiplies by the UC count to show est total + ETA in the header,
    # and notes "adjusts as runs complete" while it's still the default.
    est_per_uc, est_is_default = await _est_per_uc_seconds()
    detail["est_per_uc_seconds"] = est_per_uc
    detail["est_per_uc_is_default"] = est_is_default
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
async def list_results(request: Request):
    """List all run result directories found on the workspace PVC.

    Each result is enriched with the human-readable `session_name` /
    `session_description` / `session_category` from `run_sessions` when
    the workspace run_id has been correlated to a Tekton PipelineRun
    (via `analysis_runs.run_name` ↔ `run_sessions.run_name`).

    Scoped to the active project via analysis_runs.project_id; workspace dirs
    with no ingested analysis appear only under the default project.
    """
    if not _results.is_available():
        return {"results": [], "available": False,
                "workspace_path": _results.WORKSPACE_PATH}
    try:
        runs = _results.list_runs()
        active_pid = default_pid = None
        if runs and pool is not None:
            run_ids = [r["run_id"] for r in runs]
            async with pool.acquire() as conn:
                active_pid = await _active_project_id(request, conn)
                default_pid = await _default_project_id(conn)
                meta_rows = await conn.fetch(
                    """SELECT ar.run_id, ar.run_name, ar.project_id,
                              rs.name AS session_name,
                              rs.description AS session_description,
                              rs.category AS session_category
                       FROM analysis_runs ar
                       LEFT JOIN run_sessions rs ON rs.run_name = ar.run_name
                       WHERE ar.run_id = ANY($1::text[])""",
                    run_ids,
                )
            meta_by_id = {m["run_id"]: m for m in meta_rows}
            if active_pid is not None:
                runs = [r for r in runs
                        if (meta_by_id.get(r["run_id"]) is not None
                            and meta_by_id[r["run_id"]]["project_id"] == active_pid)
                        or (meta_by_id.get(r["run_id"]) is None and active_pid == default_pid)]
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
    request: Request,
    a: str = Query(..., description="first run_id (baseline)"),
    b: str = Query(..., description="second run_id (the newer run)"),
):
    """Compare two workspace runs side-by-side.

    Returns per-UC verdict diff, added/removed gap IDs, and summary-level
    deltas (wall time, pass/fail counts, verdict change count).
    """
    if not _results.is_available():
        raise HTTPException(503, "workspace PVC not mounted")
    # #186 follow-up: this was the one run_id-addressed read the sweep missed —
    # unauthenticated + unscoped, so any caller could diff ANY two projects'
    # runs. Same sovereignty guard as get_result, applied to BOTH run ids.
    async with pool.acquire() as _c:
        await _require_run_in_project(_c, request, a, allow_uningested=True)
        await _require_run_in_project(_c, request, b, allow_uningested=True)
    try:
        return _results.compare_runs(a, b)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        log.exception("compare_runs failed")
        raise HTTPException(500, f"compare failed: {e}")


@app.get("/api/results/uc-latest")
async def results_uc_latest(request: Request, set_id: str = None, uc_uuids: str = None):
    """Latest cached evaluation PER UC, run-agnostic, for a UC/Set scope — the heart of UC-scoped
    output (uc-scoped-evaluation-design.md 3a). Resolves the scope to UC uuids, then returns each
    UC's newest uc_analyses row (DISTINCT ON uc_uuid ORDER BY ingested_at DESC) + content-staleness.
    A Set's results may therefore span multiple runs (decision 4b).

    NB: declared BEFORE /api/results/{run_id} — FastAPI matches in definition order, so the static
    path must come first or "uc-latest" is swallowed as a run_id (404 "run 'uc-latest' not found")."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        uuids = await _resolve_scope_uc_uuids(conn, pid, set_id, uc_uuids)
        if not uuids:
            return {"ucs": [], "total": 0, "evaluated": 0}
        rows = await conn.fetch(
            """
            WITH latest AS (
              SELECT DISTINCT ON (a.uc_uuid)
                     a.id, a.uc_uuid, a.uc_handle, a.verdict, a.overall_assessment,
                     a.analyzed_at, a.ingested_at, a.run_id, a.model, a.eval_fingerprint,
                     a.status, a.error_reason, a.error_phase, a.source_repo_shas
              FROM uc_analyses a
              WHERE a.uc_uuid = ANY($1)
              ORDER BY a.uc_uuid, a.ingested_at DESC
            )
            SELECT u.uuid AS uc_uuid, u.title, u.updated_at,
                   l.id AS analysis_id, l.uc_handle, l.verdict, l.overall_assessment,
                   l.analyzed_at, l.ingested_at, l.run_id, l.model, l.eval_fingerprint,
                   l.status, l.error_reason, l.error_phase, l.source_repo_shas
            FROM managed_use_cases u
            LEFT JOIN latest l ON l.uc_uuid = u.uuid
            WHERE u.uuid = ANY($1)
            ORDER BY COALESCE(NULLIF(u.title,''), u.uuid)
            """, uuids)
        current = await _current_project_repo_shas_cached(conn, pid)   # #114
        dep = await _dep_drift_map(conn, [r["analysis_id"] for r in rows])  # #128 dependency-aware
        ucs, evaluated, failed = [], 0, 0
        for r in rows:
            has = r["analysis_id"] is not None
            # #121: a 'failed' latest row is NOT a successful evaluation — it's a failure that
            # needs re-ingestion. Legacy NULL status counts as success (don't regress coverage).
            is_failed = has and r["status"] == "failed"
            ok = has and not is_failed
            if ok:
                evaluated += 1
            if is_failed:
                failed += 1
            # Two staleness axes — UC edited OR a spec file it DEPENDED ON drifted (#128 dependency-aware,
            # targeted). _repo_drifted (whole-repo HEAD moved) is kept only as the informational
            # `stale_repo_moved` flag — it deliberately does NOT drive `stale` (it over-flags every UC).
            _dd = dep.get(r["analysis_id"], {})
            edited  = bool(ok and r["analyzed_at"] and r["updated_at"] and r["updated_at"] > r["analyzed_at"])
            drifted = bool(ok and _dd.get("drifted"))
            repo_moved = bool(ok and _repo_drifted(r["source_repo_shas"], current))
            ucs.append({
                "uc_uuid": r["uc_uuid"], "title": r["title"], "uc_handle": r["uc_handle"],
                "verdict": r["verdict"], "overall_assessment": r["overall_assessment"],
                "run_id": r["run_id"], "model": r["model"], "eval_fingerprint": r["eval_fingerprint"],
                "evaluated": ok, "failed": is_failed,
                "stale": (edited or drifted), "stale_edited": edited, "stale_drifted": drifted,
                "drifted_files": _dd.get("files", []), "stale_repo_moved": repo_moved,
                "status": r["status"], "error_reason": r["error_reason"], "error_phase": r["error_phase"],
                "analyzed_at": r["analyzed_at"].isoformat() if r["analyzed_at"] else None,
                "ingested_at": r["ingested_at"].isoformat() if r["ingested_at"] else None,
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            })
        return {"ucs": ucs, "total": len(ucs), "evaluated": evaluated, "failed": failed}


@app.get("/api/results/{run_id}")
async def get_result(run_id: str, request: Request):
    """Return the run-summary.yaml content for a specific run, enriched with
    per-UC verdicts from the analysis files AND per-UC lineage/state from
    the DB (R2: lifecycle_state_at_run, source_kind, session-level set
    context)."""
    if not _results.is_available():
        raise HTTPException(503, "workspace PVC not mounted")
    async with pool.acquire() as _c:   # sovereignty: an ingested run must belong to the active project
        await _require_run_in_project(_c, request, run_id, allow_uningested=True)
    summary = _results.get_run_summary_enriched(run_id)
    if summary is None:
        raise HTTPException(404, f"run {run_id!r} not found")
    # R2: enrich with DB-stored lineage + state
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                meta = await conn.fetchrow(
                    """SELECT ar.run_name,
                              rs.set_id,
                              -- current set name via the set_id join; stored snapshot is fallback only
                              COALESCE(ucs.name, rs.set_name) AS set_name,
                              rs.selection_mode,
                              rs.name AS session_name
                       FROM analysis_runs ar
                       LEFT JOIN run_sessions rs  ON rs.run_name = ar.run_name
                       LEFT JOIN use_case_sets ucs ON ucs.id = rs.set_id
                       WHERE ar.run_id = $1""",
                    run_id,
                )
                rows = await conn.fetch(
                    """SELECT uc_uuid, lifecycle_state_at_run, source_kind, error_reason, error_phase
                       FROM uc_analyses WHERE run_id = $1""",
                    run_id,
                )
            if meta:
                summary["session_name"]    = meta["session_name"] or None
                summary["set_id"]          = meta["set_id"]
                summary["set_name"]        = meta["set_name"] or None
                summary["selection_mode"]  = meta["selection_mode"] or None
            state_by_uuid = {r["uc_uuid"]: r for r in rows}
            for uc in (summary.get("ucs") or []):
                s = state_by_uuid.get(uc.get("uc_uuid"))
                if s:
                    uc["lifecycle_state_at_run"] = s["lifecycle_state_at_run"]
                    uc["source_kind"] = s["source_kind"]
                    uc["error_reason"] = s["error_reason"]      # #121
                    uc["error_phase"]  = s["error_phase"]
            # A dropped UC has a DB row but no entry in the workspace summary — append it so the
            # drawer shows it instead of silently omitting it.
            _summary_uuids = {uc.get("uc_uuid") for uc in (summary.get("ucs") or [])}
            for r in rows:
                if r["error_phase"] == "not_emitted" and r["uc_uuid"] not in _summary_uuids:
                    summary.setdefault("ucs", []).append({
                        "uc_uuid": r["uc_uuid"], "status": "failed",
                        "error_reason": r["error_reason"], "error_phase": r["error_phase"],
                        "lifecycle_state_at_run": r["lifecycle_state_at_run"], "source_kind": r["source_kind"],
                    })
        except Exception as e:
            log.warning("get_result: lineage enrichment failed for %s: %s", run_id, e)
    return summary


@app.get("/api/results/{run_id}/uc/{uc_uuid:path}")
async def get_result_uc(run_id: str, uc_uuid: str, request: Request):
    """Return the analysis output for a specific UC within a run."""
    if not _results.is_available():
        raise HTTPException(503, "workspace PVC not mounted")
    async with pool.acquire() as _c:   # sovereignty guard (#cross-project IDOR)
        await _require_run_in_project(_c, request, run_id, allow_uningested=True)
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


class UCBulkExtractIn(BaseModel):
    # M12a / ADR-008: paste a transcript / notes / requirements doc; the
    # endpoint returns proposed UC stubs. Client decides which to save.
    text: str = Field(..., min_length=1, max_length=120000)
    context: Optional[str] = Field(None, max_length=4000)
    model_config_id: Optional[int] = None
    endpoint_url: Optional[str] = None
    model_id: Optional[str] = None


class MCPServerIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    sse_url: str = Field(..., min_length=1, max_length=512)
    description: str = Field("", max_length=512)
    enabled: bool = True
    use_uc_assist: bool = False
    # Bearer token DAV sends to the server. Optional; blank on update preserves
    # the stored value. Fernet-encrypted at rest, masked on GET.
    auth_token: Optional[str] = Field(None, max_length=4096)


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
    # Static facts about the server side of this endpoint. Recognized keys:
    #   speculative_decoding: bool  — when true, engine drops min_p+logit_bias
    #   supports_min_p: bool        — defaults true; false forces engine to drop
    #   supports_logit_bias: bool   — defaults true; false forces engine to drop
    #   max_tokens_default: int     — applied when no per-use profile overrides
    # See `model_configs.capabilities` (migration 014).
    capabilities: dict = Field(default_factory=dict)

class ModelProbeIn(BaseModel):
    """Connection-test an endpoint and list its models, before saving a config."""
    provider: str = Field("openai", pattern="^(openai|anthropic)$")
    endpoint_url: str = Field(..., min_length=1, max_length=512)
    api_key: str = Field("", max_length=512)


_VALID_USE_KEYS = {
    "evaluation_verification",
    "evaluation_explore",
    "evaluation_reproduce",
    "arch_review",
    "uc_assist",
    "enhancement",
}


class ModelUseProfileIn(BaseModel):
    # JSONB body of sampling overrides. Any subset of:
    #   top_k, top_p, min_p, temperature, max_tokens, seed,
    #   chat_template_kwargs, ...
    # Engine drops a key entirely if the model's capabilities flag it
    # unsupported, regardless of whether this profile sets it.
    params: dict = Field(default_factory=dict)
    notes: str = Field("", max_length=2048)


class ArchReviewIn(BaseModel):
    scope: str = Field(..., pattern="^(uc|run|set)$")
    model_config_id: Optional[int] = None
    endpoint_url: Optional[str] = None
    model_id: Optional[str] = None
    run_id: Optional[str] = None
    uc_uuid: Optional[str] = None
    set_id: Optional[str] = None   # Scoping Set id / '__all__' / '__unassigned__' for scope='set'

class EnhancementIn(BaseModel):
    scope: str = Field(..., pattern="^(uc|run|set)$")
    model_config_id: Optional[int] = None
    endpoint_url: Optional[str] = None
    model_id: Optional[str] = None
    run_id: Optional[str] = None
    uc_uuid: Optional[str] = None
    set_id: Optional[str] = None   # Scoping Set id / '__all__' / '__unassigned__' for scope='set'

class CodeRepoIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    provider: str = Field(..., pattern="^(github|gitlab)$")
    repo_url: str = Field(..., min_length=1, max_length=512)
    default_branch: str = Field("main", max_length=256)
    token: str = Field("", max_length=512)
    enabled: bool = True

class PrCreateIn(BaseModel):
    # Post-ADR-006: enhancement PR target is a managed_repos row with
    # role=enhancement-target (looked up by uuid or namespace). The
    # legacy code_repo_configs.id-based `repo_config_id` field is gone.
    repo_uuid: str = Field(..., min_length=1, max_length=128)
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
async def list_uc_assist_models(request: Request):
    """List the active project's enabled model configs (api_key masked)."""
    if pool is None:
        raise HTTPException(503, "pool not initialized")
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        cat = await _active_use_category(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        rows = await conn.fetch(
            """SELECT id, name, provider, endpoint_url, model_id,
                      CASE WHEN api_key != '' THEN '••••••••' ELSE '' END AS api_key,
                      enabled, is_local, use_arch_review, use_uc_assist,
                      created_by, created_at, updated_at
               FROM model_configs
               WHERE enabled AND (project_id IS NULL OR project_id=$1) AND (use_category IS NULL OR use_category=$2)
               ORDER BY (project_id IS NULL), name""", pid, cat
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
    if pool is None:
        raise HTTPException(503, "pool not initialized")
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_USECASES, pid)
        if payload.model_config_id is not None:
            row = await conn.fetchrow(
                "SELECT * FROM model_configs WHERE id=$1 AND (project_id IS NULL OR project_id=$2) AND enabled",  # scope-aware (#107 2b): platform models too
                payload.model_config_id, pid,
            )
            if not row:
                raise HTTPException(404, "Model config not found or disabled in this project")
            cfg = dict(row)
        elif payload.endpoint_url and payload.model_id:
            base = await conn.fetchrow(
                "SELECT provider, api_key FROM model_configs WHERE endpoint_url=$1 AND (project_id IS NULL OR project_id=$2) AND enabled ORDER BY (project_id IS NULL), id LIMIT 1",  # scope-aware (#107 2b): project key preferred, platform fallback
                payload.endpoint_url, pid,
            )
            cfg = {
                "provider":     base["provider"] if base else "openai",
                "endpoint_url": payload.endpoint_url,
                "model_id":     payload.model_id,
                "api_key":      base["api_key"] if base else "",
            }
        else:
            # No explicit model — use the project UC-authoring default if set;
            # else leave cfg=None so uc_assist.chat falls back to env config.
            cfg = await _model_default_row(conn, "uc-authoring", project_id=pid)
        _uctx = await _stage_context(conn, "uc-authoring", pid)   # #125 prompt management (append-live)
    # Merge the architect-set UC-authoring prompt context with the per-request context.
    _merged_ctx = "\n\n".join(p for p in [(payload.context or "").strip(), _uctx] if p) or None
    result = await uc_assist.chat(
        user_message=payload.message,
        current_yaml=payload.current_yaml,
        context=_merged_ctx,
        cfg=cfg,
        pool=pool,
    )
    if "error" in result and not result.get("explanation"):
        raise HTTPException(503, result["error"])
    return result


@app.post("/api/use-cases/bulk-from-text")
async def uc_bulk_extract(payload: UCBulkExtractIn, request: Request):
    """M12a / ADR-008 — extract N distinct UC drafts from free-form text.

    Returns {"items": [{yaml_content, rationale, source_excerpt}, ...]}.
    The client is responsible for iterating POST /api/use-cases to persist
    the items the reviewer keeps. This endpoint never writes to the DB.
    """
    get_user(request)
    cfg: Optional[dict] = None
    if pool is None:
        raise HTTPException(503, "pool not initialized")
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_USECASES, pid)
        if payload.model_config_id is not None:
            row = await conn.fetchrow(
                "SELECT * FROM model_configs WHERE id=$1 AND (project_id IS NULL OR project_id=$2) AND enabled",  # scope-aware (#107 2b): platform models too
                payload.model_config_id, pid,
            )
            if not row:
                raise HTTPException(404, "Model config not found or disabled in this project")
            cfg = dict(row)
        elif payload.endpoint_url and payload.model_id:
            base = await conn.fetchrow(
                "SELECT provider, api_key FROM model_configs WHERE endpoint_url=$1 AND (project_id IS NULL OR project_id=$2) AND enabled ORDER BY (project_id IS NULL), id LIMIT 1",  # scope-aware (#107 2b): project key preferred, platform fallback
                payload.endpoint_url, pid,
            )
            cfg = {
                "provider":     base["provider"] if base else "openai",
                "endpoint_url": payload.endpoint_url,
                "model_id":     payload.model_id,
                "api_key":      base["api_key"] if base else "",
            }
        else:
            # No explicit model — use the project UC-authoring default.
            cfg = await _model_default_row(conn, "uc-authoring", project_id=pid)
    result = await uc_assist.extract_bulk(
        text=payload.text,
        context=payload.context,
        cfg=cfg,
    )
    if "error" in result:
        raise HTTPException(503, result["error"])
    return result


# ========================= INBOX (M7 of #28) =========================
# Operator-facing curation API for ingested pr_comments. Reads from
# pr_comments (poller M5 + webhook M6) and exposes:
#   - GET /api/inbox                — list with filters
#   - GET /api/inbox/{uuid}         — single comment
#   - POST /api/inbox/{uuid}/status — dismiss / mark drafted-to-uc
#   - POST /api/inbox/{uuid}/draft-uc — LLM-draft a UC YAML
#
# UI (M8) consumes these. The draft-uc endpoint reuses the existing
# UC Assist plumbing with a tailored system + user message.


class InboxStatusIn(BaseModel):
    # Closed to {dismissed, drafted_to_uc, new} — 'new' supports un-dismissing
    # a comment if the operator changed their mind.
    status: str = Field(..., min_length=1, max_length=32)
    # Required when status='drafted_to_uc' — records the link in
    # uc_pr_comment_links so we have UC ↔ comment provenance.
    uc_uuid: Optional[str] = Field(None, max_length=64)
    notes: Optional[str] = Field(None, max_length=2048)


class InboxDraftUCIn(BaseModel):
    # Same model-resolution options as POST /api/uc-assist
    model_config_id: Optional[int] = None
    endpoint_url: Optional[str] = Field(None, max_length=512)
    model_id: Optional[str] = Field(None, max_length=256)


@app.get("/api/inbox")
async def list_inbox_api(
    status: Optional[str] = Query(
        "new",
        description="Filter by status. 'new' (default), 'dismissed', "
                    "'drafted_to_uc', or 'all' to disable the filter.",
    ),
    repo_uuid: Optional[str] = Query(None, description="filter by source repo uuid"),
    tenant_id: Optional[str] = Query(None, description="filter by tenant_id"),
    limit: int = Query(200, ge=1, le=1000),
):
    """List ingested PR comments for the curation Inbox. Newest first."""
    status_filter: Optional[str] = status if status and status != "all" else None
    try:
        async with pool.acquire() as conn:
            return {
                "comments": await _pr_comments.list_comments(
                    conn, status=status_filter, repo_uuid=repo_uuid,
                    tenant_id=tenant_id, limit=limit,
                ),
            }
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/inbox/{uuid}")
async def get_inbox_comment_api(uuid: str):
    async with pool.acquire() as conn:
        comment = await _pr_comments.get_comment(conn, uuid)
        if not comment:
            raise HTTPException(404, f"comment {uuid!r} not found")
        # Also fetch any existing UC links so the UI can show "drafted into X"
        links = await conn.fetch(
            "SELECT uc_uuid, linked_at, linked_by, notes "
            "FROM uc_pr_comment_links WHERE pr_comment_uuid::text = $1 "
            "ORDER BY linked_at DESC",
            uuid,
        )
    comment["uc_links"] = [
        {
            "uc_uuid": str(r["uc_uuid"]),
            "linked_at": r["linked_at"].isoformat(),
            "linked_by": r["linked_by"],
            "notes": r["notes"],
        }
        for r in links
    ]
    return comment


@app.post("/api/inbox/{uuid}/status")
async def set_inbox_status_api(uuid: str, payload: InboxStatusIn, request: Request):
    """Transition a comment's status. If status='drafted_to_uc', also record
    the link in uc_pr_comment_links (uc_uuid required). Idempotent on the
    link (ON CONFLICT DO NOTHING) so reapplying the same status is safe."""
    reviewer = get_user(request)
    if payload.status == "drafted_to_uc" and not payload.uc_uuid:
        raise HTTPException(400, "uc_uuid is required when status='drafted_to_uc'")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                updated = await _pr_comments.set_status(
                    conn, uuid, payload.status, changed_by=reviewer,
                )
                if not updated:
                    raise HTTPException(404, f"comment {uuid!r} not found")
                if payload.status == "drafted_to_uc":
                    await conn.execute(
                        "INSERT INTO uc_pr_comment_links "
                        "(uc_uuid, pr_comment_uuid, linked_by, notes) "
                        "VALUES ($1, $2, $3, $4) "
                        "ON CONFLICT (uc_uuid, pr_comment_uuid) DO NOTHING",
                        payload.uc_uuid, uuid, reviewer, payload.notes,
                    )
        return updated
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/inbox/{uuid}/draft-uc")
async def draft_uc_from_comment_api(uuid: str, payload: InboxDraftUCIn, request: Request):
    """LLM-draft a DAV use case YAML from a PR comment.

    Reuses the UC Assist pipeline with a tailored user message that
    frames the comment as scenario source material. Returns
    {explanation, yaml_suggestion, raw, comment} so the UI can open
    the UC editor with the draft and the source context side-by-side.

    The UI is expected to follow up with POST /api/inbox/{uuid}/status
    {status: 'drafted_to_uc', uc_uuid: <new UC uuid>} after the
    operator saves the resulting UC.
    """
    get_user(request)

    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_USECASES, pid)
        comment = await _pr_comments.get_comment(conn, uuid)
        if not comment:
            raise HTTPException(404, f"comment {uuid!r} not found")
        repo_row = await conn.fetchrow(
            "SELECT namespace, display_name FROM managed_repos "
            "WHERE uuid::text = $1",
            comment["repo_uuid"],
        )
    repo_ns = repo_row["namespace"] if repo_row else "(unknown)"
    repo_name = (
        repo_row["display_name"]
        if (repo_row and repo_row["display_name"]) else repo_ns
    )

    # Model config resolution mirrors POST /api/uc-assist (scoped to the project).
    cfg: Optional[dict] = None
    async with pool.acquire() as conn:
        if payload.model_config_id is not None:
            row = await conn.fetchrow(
                "SELECT * FROM model_configs WHERE id=$1 AND (project_id IS NULL OR project_id=$2) AND enabled",  # scope-aware (#107 2b): platform models too
                payload.model_config_id, pid,
            )
            if not row:
                raise HTTPException(404, "Model config not found or disabled in this project")
            cfg = dict(row)
        elif payload.endpoint_url and payload.model_id:
            base = await conn.fetchrow(
                "SELECT provider, api_key FROM model_configs "
                "WHERE endpoint_url=$1 AND (project_id IS NULL OR project_id=$2) AND enabled "
                "ORDER BY (project_id IS NULL), id LIMIT 1",  # scope-aware (#107 2b)
                payload.endpoint_url, pid,
            )
            cfg = {
                "provider":     base["provider"] if base else "openai",
                "endpoint_url": payload.endpoint_url,
                "model_id":     payload.model_id,
                "api_key":      base["api_key"] if base else "",
            }
        else:
            # No explicit model — use the project UC-authoring default.
            cfg = await _model_default_row(conn, "uc-authoring", project_id=pid)

    # M12 "E" pass: when the source repo has a recognizable namespace
    # (i.e., the inbox row joined managed_repos cleanly), pre-scope the
    # drafted UC to that namespace via the spec_namespaces field. The
    # operator can edit it before saving; the inbox UI surfaces the
    # auto_scoped_namespaces response field so the pre-scope is visible.
    auto_scoped = [repo_ns] if (repo_ns and repo_ns != "(unknown)") else []
    scope_instruction = ""
    if auto_scoped:
        scope_instruction = (
            f"  - spec_namespaces: [{repo_ns}]  (auto-scoped — the comment "
            f"originated from the {repo_ns!r} repo, so the resulting UC's "
            f"stage-2 grounding is restricted to that namespace; per-UC "
            f"spec scope is a HARD constraint enforced by the engine)\n"
        )

    user_message = (
        f"Draft a DAV use case YAML from this PR comment.\n\n"
        f"Source repo: {repo_name} (namespace: {repo_ns})\n"
        f"PR #{comment['pr_number']}: {comment['pr_title'] or '(no title)'}\n"
        f"Author: @{comment['author_login']}\n"
        f"Comment URL: {comment['comment_url'] or '(none)'}\n"
        f"Comment type: {comment['github_comment_type']}\n\n"
        f"Comment body:\n---\n{comment['body']}\n---\n\n"
        f"Frame the UC around verifying the architecture handles the scenario, "
        f"gap, or concern the commenter raised. If the comment describes a bug, "
        f"the UC should exercise the path that would catch it. Use:\n"
        f"  - handle: `pr-derived/{repo_ns}/<short-descriptor>`\n"
        f"  - generated_by.mode: `pr-targeted` (this is PR-comment-derived)\n"
        f"  - generated_by.source: `llm-guided` (you generated it from the comment)\n"
        f"{scope_instruction}"
        f"Make reasonable assumptions where the comment is ambiguous and note "
        f"them in your explanation."
    )

    result = await uc_assist.chat(
        user_message=user_message,
        context=(
            "This UC is being drafted from a PR comment via the DAV review "
            "console Inbox (M7 of #28). The operator will review your draft "
            "in the UC editor before saving."
        ),
        cfg=cfg,
        pool=pool,
    )
    if "error" in result and not result.get("explanation"):
        raise HTTPException(503, result["error"])

    return {
        **result,
        "comment": comment,
        "auto_scoped_namespaces": auto_scoped,
    }


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
# errors at save time instead of at run time. These enums are the engine's
# generic reference profile (consumer_profile.get_generic_reference_profile()).
#
# Single source of truth (operating-model DR §6): the values live in the engine
# and are exported to `dcm_vocab.json` by `python -m dav.scripts.export_dcm_vocab`.
# We load that artifact here so the API never hand-copies (and drifts from) the
# engine vocabulary again. The hardcoded fallback below is a last resort if the
# JSON is missing at runtime; CI runs the exporter with --check to guarantee the
# committed JSON matches the engine.
_DCM_VOCAB_FALLBACK = {
    "lifecycle_phases": [
        "new_request", "modification", "decommission",
        "drift_detection", "brownfield_ingestion",
        "rehydration_faithful", "rehydration_provider_portable",
        "rehydration_historical_exact", "rehydration_historical_portable",
        "expiry_enforcement",
    ],
    "resource_complexities": [
        "single_no_deps", "hard_dependencies", "composite_service",
        "conditional_soft_deps", "process_resource", "cross_dependency_payload",
    ],
    "policy_complexities": [
        "system_defaults_only", "single_validation", "multi_policy_chain",
        "conflicting_policies", "orchestration_flow_static",
        "dynamic_conditional_flow", "cross_domain_constraint",
        "human_escalation_required", "governance_matrix_enforcement",
        "recovery_policy",
    ],
    "provider_landscapes": [
        "single_eligible", "multiple_eligible", "none_eligible",
        "peer_dcm_required", "process_provider", "mixed",
    ],
    "governance_contexts": [
        "no_governance", "standard_governance", "audit_heavy",
        "compliance_gated", "sovereignty_enforced",
    ],
    "failure_modes": [
        "happy_path", "provider_failure", "policy_violation",
        "peer_dcm_disconnect", "data_inconsistency", "rollback_required",
        "partial_fulfillment", "timeout", "resource_exhaustion",
    ],
    "profiles": ["homelab", "dev", "standard", "prod", "fsi", "sovereign"],
}


def _load_dcm_vocab() -> dict:
    """Load the engine-exported vocabulary from dcm_vocab.json (sibling of this
    module), falling back to the inline copy if absent. Returns a dict of
    field -> set[str]."""
    path = os.path.join(os.path.dirname(__file__), "dcm_vocab.json")
    raw = dict(_DCM_VOCAB_FALLBACK)
    try:
        with open(path) as f:
            loaded = json.load(f)
        for key in _DCM_VOCAB_FALLBACK:
            if isinstance(loaded.get(key), list):
                raw[key] = loaded[key]
    except FileNotFoundError:
        log.warning("dcm_vocab.json not found at %s; using inline fallback "
                    "vocabulary (run dav.scripts.export_dcm_vocab)", path)
    except Exception as e:
        log.warning("could not load dcm_vocab.json (%s); using inline "
                    "fallback vocabulary", e)
    return {key: set(vals) for key, vals in raw.items()}


_DCM_VOCAB = _load_dcm_vocab()
_DCM_LIFECYCLE_PHASES = _DCM_VOCAB["lifecycle_phases"]
_DCM_RESOURCE_COMPLEXITIES = _DCM_VOCAB["resource_complexities"]
_DCM_POLICY_COMPLEXITIES = _DCM_VOCAB["policy_complexities"]
_DCM_PROVIDER_LANDSCAPES = _DCM_VOCAB["provider_landscapes"]
_DCM_GOVERNANCE_CONTEXTS = _DCM_VOCAB["governance_contexts"]
_DCM_FAILURE_MODES = _DCM_VOCAB["failure_modes"]
_DCM_PROFILES = _DCM_VOCAB["profiles"]
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
    # handle — REQUIRED. The engine loader does uc['handle'] and KeyErrors without it (this gap let
    # 9 handle-less UCs save then fail to load). Auto-repairable via _derive_uc_handle (#122).
    hnd = parsed.get("handle")
    if not isinstance(hnd, str) or not hnd.strip():
        errors.append("handle is required and must be a non-empty string (e.g. namespace/profile/slug)")
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
    # priority (optional; spec 05 §6.8)
    if parsed.get("priority") is not None:
        try:
            _normalize_uc_priority(parsed.get("priority"))
        except ValueError as e:
            errors.append(str(e))
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


def _slugify(s: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return (s[:maxlen].strip("-")) or "use-case"

def _derive_uc_handle(parsed: dict) -> str:
    """Derive a `namespace/profile/slug` handle for a UC missing one (#122 repair). Profile comes
    from the actor/scenario profile (falls back to 'standard'); slug from the title."""
    sc = parsed.get("scenario") or {}
    profile = ((sc.get("actor") or {}).get("profile") or sc.get("profile") or "standard")
    if not isinstance(profile, str) or not profile.strip():
        profile = "standard"
    slug = _slugify(_derive_uc_title(parsed, parsed.get("uuid") or "use-case"))
    return f"managed/{profile.strip()}/{slug}"


# ── Semi-automated UC fix — deterministic suggester (TODO 2, docs/uc-fix-design.md) ──
# The six dimensions and their allowed value sets, in validation order. Safe defaults are the
# simplest/happy-path value for each — used when a dimension is missing or holds a value that can't
# be relocated. NB: kept in sync with _validate_uc_yaml's dimension loop.
_DIM_SETS = {
    "lifecycle_phase":     _DCM_LIFECYCLE_PHASES,
    "resource_complexity": _DCM_RESOURCE_COMPLEXITIES,
    "policy_complexity":   _DCM_POLICY_COMPLEXITIES,
    "provider_landscape":  _DCM_PROVIDER_LANDSCAPES,
    "governance_context":  _DCM_GOVERNANCE_CONTEXTS,
    "failure_mode":        _DCM_FAILURE_MODES,
}
_DIM_DEFAULTS = {
    "lifecycle_phase":     "new_request",
    "resource_complexity": "single_no_deps",
    "policy_complexity":   "system_defaults_only",
    "provider_landscape":  "single_eligible",
    "governance_context":  "standard_governance",
    "failure_mode":        "happy_path",
}
# Reverse index: value → the dimension whose set contains it (for enum relocation). Built once; if a
# value legitimately appears in two dimensions the first (validation-order) wins — a stable choice.
_DIM_VALUE_OWNER = {}
for _dn, _dvals in _DIM_SETS.items():
    for _v in _dvals:
        _DIM_VALUE_OWNER.setdefault(_v, _dn)

# Semantic errors a machine can't fix (need LLM/human — slice B). Matched by substring on the
# validator's messages so the suggester can partition remaining errors honestly.
_SEMANTIC_MARKERS = (
    "scenario.description", "scenario.intent", "success_criteria", "actor.persona",
    "scenario must be a mapping",
)


def _suggest_uc_fixes(parsed: dict):
    """Deterministic UC-fix suggester (dry-run; does NOT mutate `parsed`). Returns
    (proposed:dict, changes:list, remaining_errors:list, needs_semantic:list).

    Fixes, in order: missing `handle` (derive); invalid `generated_by.mode/source` (→ default);
    dimension enum errors (relocate a misplaced value to its owning dimension if that slot is
    empty/invalid, else set the offending dimension to its safe default; fill missing dimensions);
    profile mismatch (copy the valid twin, else 'standard'); invalid optional `priority` (drop).
    Semantic gaps (empty description/intent/criteria/persona) are left for tier 2/3 and reported.
    The result always re-validates to >= the original validity (never makes a UC more invalid)."""
    import copy
    proposed = copy.deepcopy(parsed) if isinstance(parsed, dict) else {}
    changes: list[dict] = []

    def _ch(field, frm, to, kind):
        changes.append({"field": field, "from": frm, "to": to, "kind": kind})

    # generated_by.mode / source
    gb = proposed.get("generated_by")
    if not isinstance(gb, dict):
        gb = {}
        proposed["generated_by"] = gb
    if gb.get("mode") not in _VALID_GEN_MODES:
        _ch("generated_by.mode", gb.get("mode"), "authoring", "default")
        gb["mode"] = "authoring"
    if gb.get("source") not in _VALID_GEN_SOURCES:
        _ch("generated_by.source", gb.get("source"), "human-authored", "default")
        gb["source"] = "human-authored"

    sc = proposed.get("scenario")
    if isinstance(sc, dict):
        # dimensions — two passes so relocation isn't lost to default-fill ordering.
        dims = sc.get("dimensions")
        if not isinstance(dims, dict):
            dims = {}
            sc["dimensions"] = dims
        orig = dict(dims)   # snapshot: judge "was this slot originally filled?" against the input
        # Pass 1 — relocate a misplaced value into its owning dimension IF that slot was originally
        # empty/invalid AND hasn't been claimed by an earlier relocation this pass.
        for name in _DIM_SETS:
            v = orig.get(name)
            if v in _DIM_SETS[name]:
                continue
            owner = _DIM_VALUE_OWNER.get(v) if isinstance(v, str) else None
            if (owner and owner != name
                    and orig.get(owner) not in _DIM_SETS[owner]
                    and dims.get(owner) not in _DIM_SETS[owner]):
                _ch(f"dimensions.{owner}", dims.get(owner), v, "relocate")
                dims[owner] = v
        # Pass 2 — fill any dimension still empty/invalid with its safe default (this also resets the
        # slot a value was relocated OUT of, unless that slot itself received a relocation).
        for name in _DIM_SETS:
            if dims.get(name) not in _DIM_SETS[name]:
                _ch(f"dimensions.{name}", dims.get(name), _DIM_DEFAULTS[name], "default")
                dims[name] = _DIM_DEFAULTS[name]

        # profile mismatch — copy the valid twin (actor.profile ↔ scenario.profile), else default.
        actor = sc.get("actor") if isinstance(sc.get("actor"), dict) else None
        ap = actor.get("profile") if actor else None
        spf = sc.get("profile")
        ap_ok, sp_ok = ap in _DCM_PROFILES, spf in _DCM_PROFILES
        if actor is not None and not ap_ok:
            newv = spf if sp_ok else "standard"
            _ch("scenario.actor.profile", ap, newv, "copy" if sp_ok else "default")
            actor["profile"] = newv
        if not sp_ok:
            newv = (actor.get("profile") if actor and actor.get("profile") in _DCM_PROFILES else "standard")
            _ch("scenario.profile", spf, newv, "copy" if (actor and actor.get("profile") in _DCM_PROFILES) else "default")
            sc["profile"] = newv

    # handle — derived LAST so it reflects the corrected profile/title (managed/<profile>/<slug>).
    h = proposed.get("handle")
    if not isinstance(h, str) or not h.strip():
        proposed["handle"] = _derive_uc_handle(proposed)
        _ch("handle", h, proposed["handle"], "derive")

    # priority (optional) — drop an invalid value rather than block.
    if proposed.get("priority") is not None:
        try:
            _normalize_uc_priority(proposed.get("priority"))
        except ValueError:
            _ch("priority", proposed.get("priority"), None, "drop")
            proposed.pop("priority", None)

    remaining = _validate_uc_yaml(proposed)
    needs_semantic = [e for e in remaining if any(m in e for m in _SEMANTIC_MARKERS)]
    return proposed, changes, remaining, needs_semantic


@app.get("/api/use-cases")
async def list_use_cases(
    request: Request,
    source: Optional[str] = Query(None, description="'managed', 'corpus', or None for both"),
    applied: Optional[int] = Query(None, description="managed-UC project scoping (#43): None/1 = UCs in the active project (home OR referenced via use_case_projects); 0 = the 'available to apply' pool (managed UCs from other projects in this tenant, not yet referenced here)"),
    sort: Optional[str] = Query(None, description="'priority' to order by roadmap weight; default is most-recently-updated"),
    priority: Optional[str] = Query(None, description="filter to a single priority label (critical/high/medium/low)"),
    customer_id: Optional[str] = Query(None, description="matrix #130: filter to managed UCs this customer has requested (corpus UCs carry no demand, so omitted when set)"),
    namespace: Optional[str] = Query(None, description="filter corpus UCs to a single repo namespace (#243); when set, corpus UCs from that repo are NOT collapsed against same-uuid UCs elsewhere, so a branch's distinct versions are visible"),
):
    """List use cases — from the managed DB, the corpus files, or both.

    Each row carries `set_ids: [int]` so the merged UC/Sets UI can
    filter the list by set membership without an N+1 query per UC.

    `sort=priority` orders by roadmap weight (priority.score) descending, with
    unranked UCs last (DCM feature #1). `priority=<label>` filters to one tier.
    """
    managed = []
    corpus_ucs = []

    prio_filter = priority.strip().lower() if isinstance(priority, str) and priority.strip() else None
    if prio_filter is not None and prio_filter not in _PRIORITY_DEFAULTS:
        raise HTTPException(400, f"priority filter '{priority}' not in {sorted(_PRIORITY_DEFAULTS)}")
    by_priority = (sort == "priority")
    by_demand = (sort == "demand")   # order by customer request count (highest first)
    cust_filter = int(customer_id) if (customer_id and str(customer_id).isdigit()) else None
    ns_filter = namespace.strip() if isinstance(namespace, str) and namespace.strip() else None  # #243

    # Pre-build uuid → [set_ids] map once for all UCs (managed + corpus).
    async with pool.acquire() as conn:
        member_rows = await conn.fetch(
            "SELECT uc_uuid, set_id FROM use_case_set_members"
        )
    set_ids_by_uuid: dict[str, list[int]] = {}
    for r in member_rows:
        set_ids_by_uuid.setdefault(r["uc_uuid"], []).append(int(r["set_id"]))

    # Demand rollup: distinct customers per UC (the multi-tenant importance signal),
    # one grouped query for the whole list (no N+1).
    async with pool.acquire() as conn:
        dc_rows = await conn.fetch(
            "SELECT uc_uuid, COUNT(DISTINCT customer) AS dc, "
            "array_agg(DISTINCT customer ORDER BY customer) AS names "
            "FROM uc_customer_requests GROUP BY uc_uuid")
    distinct_customers_by_uuid = {r["uc_uuid"]: int(r["dc"]) for r in dc_rows}
    # #130 2b-iii: the requesting customers per UC (names), for the customer column.
    customer_names_by_uuid = {r["uc_uuid"]: list(r["names"] or []) for r in dc_rows}

    if source in (None, "managed"):
        order = ("customer_requests DESC, updated_at DESC" if by_demand
                 else "priority_score DESC NULLS LAST, updated_at DESC" if by_priority
                 else "updated_at DESC")
        async with pool.acquire() as conn:
            pid = await _active_project_id(request, conn)
            conds, params = [], []
            if pid is not None:
                if applied == 0:
                    # "available to apply" pool (#43): managed UCs NOT in this project — neither
                    # home here nor already referenced. Same tenant (one schema), free to reference in.
                    params.append(pid)
                    conds.append(
                        f"(project_id IS DISTINCT FROM ${len(params)} "
                        f"AND uuid NOT IN (SELECT uc_uuid FROM use_case_projects WHERE project_id = ${len(params)}))")
                else:
                    # In this project = its home project OR referenced via use_case_projects (#199/#43).
                    params.append(pid)
                    conds.append(
                        f"(project_id = ${len(params)} "
                        f"OR uuid IN (SELECT uc_uuid FROM use_case_projects WHERE project_id = ${len(params)}))")
            if prio_filter:
                params.append(prio_filter); conds.append(f"priority = ${len(params)}")
            if cust_filter is not None:
                params.append(cust_filter)
                conds.append(f"uuid IN (SELECT uc_uuid FROM uc_customer_requests WHERE customer_id = ${len(params)})")
            where = ("WHERE " + " AND ".join(conds)) if conds else ""
            sql = (
                "SELECT uuid, title, tags, lifecycle_state, priority, priority_score, readiness_score, "
                "customer_requests, created_by, created_at, updated_by, updated_at, project_id "
                f"FROM managed_use_cases {where} ORDER BY {order}"
            )
            rows = await conn.fetch(sql, *params)
        managed = [
            {
                **dict(r),
                "source": "managed",
                "created_at": r["created_at"].isoformat(),
                "updated_at": r["updated_at"].isoformat(),
                "set_ids": set_ids_by_uuid.get(r["uuid"], []),
                "distinct_customers": distinct_customers_by_uuid.get(r["uuid"], 0),
                "customers": customer_names_by_uuid.get(r["uuid"], []),
                # home project (managed_use_cases.project_id); when != active project this UC is
                # REFERENCED here via use_case_projects (#43) — UI shows Remove vs native (no Remove).
                "home_project_id": r["project_id"],
                "referenced": (pid is not None and r["project_id"] != pid),
            }
            for r in rows
        ]

    if source in (None, "corpus") and cust_filter is None:
        # Corpus UC files — already seeded into the files table; filter to .yaml files
        # that look like UCs (have a uuid field when parsed as YAML). Corpus UCs are tenant
        # assets that show in a project only when APPLIED to it (#199): with applied=1/None they
        # are scoped to use_case_projects for the active project; applied=0 lists the ones NOT yet
        # applied ("available to apply").
        async with pool.acquire() as conn:
            _pid = await _active_project_id(request, conn)
            # namespace -> {branch, repo_url, display_name} for the project's corpus repos (#243),
            # so each corpus UC can carry its source repo/branch and be filtered by it.
            _corpus_repos = await conn.fetch(
                "SELECT namespace, repo_branch, repo_url, display_name FROM managed_repos "
                "WHERE project_id=$1 AND 'corpus'=ANY(roles)", _pid) if _pid else []
            _corpus_ns = {r["namespace"] for r in _corpus_repos}
            _ns_meta = {r["namespace"]: {"branch": r["repo_branch"], "repo_url": r["repo_url"],
                                         "display_name": r["display_name"]} for r in _corpus_repos}
            rows = await conn.fetch(
                "SELECT path, content, size_bytes, folder FROM files "
                "WHERE path LIKE '%.yaml' OR path LIKE '%.yml' ORDER BY path"
            )
        ns_filter = namespace.strip() if isinstance(namespace, str) and namespace.strip() else None
        for r in rows:
            try:
                # A project's corpus = UC files from its corpus-role repos (managed_repos roles @>
                # {corpus}), matched by the file's namespace (folder prefix). So corpus shows only in
                # projects whose repo list includes that corpus repo — not in every project. #199.
                ns = (r["folder"] or "").split("/", 1)[0]
                if ns not in _corpus_ns:
                    continue
                if ns_filter and ns != ns_filter:
                    continue
                data = _yaml.safe_load(r["content"])
                if not isinstance(data, dict) or "uuid" not in data:
                    continue
                uc_uuid = data.get("uuid")
                c_priority, c_priority_score = _derive_uc_priority(data)
                if prio_filter and c_priority != prio_filter:
                    continue
                corpus_ucs.append({
                    "uuid":    uc_uuid,
                    "title":   data.get("scenario", {}).get("description", "")[:80]
                               if isinstance(data.get("scenario"), dict) else "",
                    "handle":  data.get("handle"),
                    "tags":    data.get("tags", []),
                    "priority": c_priority,
                    "priority_score": c_priority_score,
                    "readiness_score": _uc_readiness.score_use_case(data)["score"],
                    "customer_requests": 0,   # demand is console-tracked; corpus files carry none
                    "distinct_customers": 0,
                    "path":    r["path"],
                    "source":  "corpus",
                    "namespace": ns,                                  # #243 source repo namespace
                    "branch":  (_ns_meta.get(ns) or {}).get("branch"),  # #243 source repo branch
                    "repo_url": (_ns_meta.get(ns) or {}).get("repo_url"),
                    "set_ids": set_ids_by_uuid.get(uc_uuid, []),
                })
            except Exception:
                continue

    # Collapse the same uuid appearing across multiple corpus paths and/or as a
    # managed row into one entry (managed preferred), surfacing corpus path_count.
    # When filtering to a single repo namespace (#243), return ONLY that repo/branch's
    # corpus UCs — managed UCs are DB-owned (no corpus repo namespace) so they don't
    # belong to a repo/branch view — and DON'T collapse, so a branch's edited
    # same-uuid versions are visible rather than hidden behind main/managed.
    if ns_filter:
        use_cases = corpus_ucs
    else:
        use_cases = _collapse_uc_duplicates(managed, corpus_ucs)
    if by_priority:
        # Stable global ordering across both sources: weight desc, unranked last.
        # (None != 0 — a valid low-band score of 0 still outranks unranked.)
        def _prio_key(u):
            s = u.get("priority_score")
            return (1, 0) if s is None else (0, -s)
        use_cases.sort(key=_prio_key)
    return {"use_cases": use_cases}


@app.get("/api/use-cases/health")
async def use_cases_health(request: Request):
    """Per-project UC validity (#122). For each managed UC: parse + run engine validation, so the
    list can FLAG invalid UCs and the editor can surface what's wrong + whether it's auto-repairable
    (currently a missing `handle`, which we can derive). NB: declared BEFORE /api/use-cases/{uuid}."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        rows = await conn.fetch(
            "SELECT uuid, title, yaml_content FROM managed_use_cases WHERE project_id=$1", pid)
    ucs = []
    for r in rows:
        data = None
        try:
            data = _parse_uc_yaml(r["yaml_content"])
            errs = _validate_uc_yaml(data)
        except ValueError as e:
            errs = [str(e)]
        repairable = bool(data is not None and any("handle is required" in e for e in errs))
        ucs.append({"uuid": r["uuid"], "title": r["title"], "valid": not errs,
                    "errors": errs, "repairable": repairable})
    return {"total": len(ucs), "invalid": sum(1 for u in ucs if not u["valid"]), "ucs": ucs}


@app.get("/api/use-cases/fix-suggestions")
async def bulk_fix_suggestions(request: Request, set_id: Optional[str] = Query(None)):
    """Bulk dry-run of the deterministic UC-fix suggester over every INVALID managed UC in the active
    project (optionally restricted to a Scoping Set). Powers the bulk "Fix N invalid" preview + count.
    Suggest only — never writes; apply is per-UC via POST …/suggest-fix?apply=true.
    NB: declared BEFORE /api/use-cases/{uuid} so it isn't swallowed by the greedy uuid route."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        if set_id and set_id not in ("", "__all__"):
            uuids = await _resolve_scope_uc_uuids(conn, pid, set_id, None)
            rows = await conn.fetch(
                "SELECT uuid, title, yaml_content FROM managed_use_cases WHERE project_id=$1 AND uuid=ANY($2)",
                pid, uuids) if uuids else []
        else:
            rows = await conn.fetch(
                "SELECT uuid, title, yaml_content FROM managed_use_cases WHERE project_id=$1", pid)
    items = []
    fixable_clean = partial = needs_semantic_n = 0
    for r in rows:
        try:
            data = _parse_uc_yaml(r["yaml_content"])
            errors_before = _validate_uc_yaml(data)
        except ValueError as e:
            errors_before = [str(e)]
            data = None
        if not errors_before:
            continue                              # already valid — skip
        if data is None:
            items.append({"uuid": r["uuid"], "title": r["title"], "parses": False,
                          "errors_before": errors_before, "changes": [], "valid_after": False,
                          "remaining_errors": errors_before, "needs_semantic": errors_before})
            needs_semantic_n += 1
            continue
        proposed, changes, remaining, needs_semantic = _suggest_uc_fixes(data)
        if not remaining:
            fixable_clean += 1
        elif needs_semantic:
            needs_semantic_n += 1
        else:
            partial += 1
        items.append({
            "uuid": r["uuid"], "title": r["title"], "parses": True,
            "errors_before": errors_before, "changes": changes,
            "valid_after": not remaining, "remaining_errors": remaining,
            "needs_semantic": needs_semantic,
        })
    return {"total_invalid": len(items), "fixable_clean": fixable_clean,
            "partial": partial, "needs_semantic": needs_semantic_n, "items": items}


@app.get("/api/use-cases/{uuid}")
async def get_use_case(uuid: str, request: Request):
    """Return a single use case by uuid — managed DB first, then corpus files.

    Route uses `{uuid}` (NOT `{uuid:path}`) so sibling routes like
    `/api/use-cases/{uuid}/runs` and `/api/use-cases/{uuid}/lifecycle`
    don't get swallowed by greedy path matching. UC UUIDs follow the
    `uc-<hex with dashes>` format and never contain slashes.
    """
    # Check managed DB
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM managed_use_cases WHERE uuid = $1", uuid
        )
        if row:
            # A managed UC is project-owned — require read in ITS project (a
            # member of another project gets 404, not the cross-project content).
            await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, row["project_id"])
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


@app.post("/api/use-cases/readiness")
async def readiness_use_case(payload: ManagedUCIn):
    """Score a UC definition's readiness/quality without saving (DCM feature #4).

    Author-facing complement to /validate: validate says "is it legal?",
    readiness says "is it well-defined enough to analyze well?". Returns the
    full checklist with hints so the editor can guide the author.
    """
    try:
        data = _parse_uc_yaml(payload.yaml_content)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, **_uc_readiness.score_use_case(data)}


@app.post("/api/use-cases/{uuid}/repair")
async def repair_use_case(uuid: str, request: Request):
    """Auto-repair common UC issues without hand-editing YAML (#122). Currently: backfill a missing
    `handle` (derived `managed/<profile>/<slug>`). Re-validates + saves; returns what changed and any
    errors that still need a manual fix."""
    user = get_user(request)
    async with pool.acquire() as conn:
        await _gate_resource(conn, request, "managed_use_cases", "uuid", uuid,
                             rbac.P_PROJECT_USECASES, "use case not found")
        yc = await conn.fetchval("SELECT yaml_content FROM managed_use_cases WHERE uuid=$1", uuid)
    try:
        data = _parse_uc_yaml(yc)
    except ValueError as e:
        raise HTTPException(400, f"cannot auto-repair — YAML does not parse: {e}")
    repaired = []
    h = data.get("handle")
    if not isinstance(h, str) or not h.strip():
        data["handle"] = _derive_uc_handle(data)
        repaired.append(f"backfilled handle → {data['handle']}")
    remaining = _validate_uc_yaml(data)
    if not repaired:
        return {"ok": not remaining, "repaired": [], "remaining_errors": remaining,
                "message": "Nothing to auto-repair." if not remaining
                           else "No auto-repair is available for the remaining issues — edit manually."}
    new_yaml = _yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    title = _derive_uc_title(data, uuid)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE managed_use_cases SET yaml_content=$2, title=$3, updated_by=$4, updated_at=now() WHERE uuid=$1",
            uuid, new_yaml, title, user)
    return {"ok": not remaining, "repaired": repaired, "remaining_errors": remaining, "yaml_content": new_yaml}


@app.post("/api/use-cases/{uuid}/suggest-fix")
async def suggest_fix_use_case(uuid: str, request: Request, apply: bool = Query(False)):
    """Semi-automated UC fix (TODO 2, docs/uc-fix-design.md). Deterministic tier: identify validation
    errors → propose a concrete corrected YAML (enum relocation/defaults, handle, generated_by,
    profile, priority) → return the change list + proposed YAML + what still needs a human/LLM.

    Dry-run by default (suggest only). With ?apply=true it saves the proposal — but ONLY if it
    strictly improves validity (fewer errors), so applying can never make a UC more broken."""
    user = get_user(request)
    async with pool.acquire() as conn:
        await _gate_resource(conn, request, "managed_use_cases", "uuid", uuid,
                             rbac.P_PROJECT_USECASES if apply else rbac.P_PROJECT_READ,
                             "use case not found")
        yc = await conn.fetchval("SELECT yaml_content FROM managed_use_cases WHERE uuid=$1", uuid)
    try:
        data = _parse_uc_yaml(yc)
    except ValueError as e:
        raise HTTPException(400, f"cannot suggest a fix — YAML does not parse: {e}")
    errors_before = _validate_uc_yaml(data)
    proposed, changes, remaining, needs_semantic = _suggest_uc_fixes(data)
    proposed_yaml = _yaml.safe_dump(proposed, sort_keys=False, allow_unicode=True)
    result = {
        "uuid": uuid, "method": "deterministic",
        "valid_before": not errors_before, "errors_before": errors_before,
        "changes": changes, "proposed_yaml": proposed_yaml,
        "valid_after": not remaining, "remaining_errors": remaining,
        "needs_semantic": needs_semantic, "applied": False,
    }
    if apply:
        if not changes:
            raise HTTPException(400, "nothing to fix")
        if len(remaining) >= len(errors_before):   # never save a non-improvement
            raise HTTPException(409, "the deterministic fix would not improve validity — review manually")
        title = _derive_uc_title(proposed, uuid)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE managed_use_cases SET yaml_content=$2, title=$3, updated_by=$4, updated_at=now() WHERE uuid=$1",
                uuid, proposed_yaml, title, user)
        result["applied"] = True
    return result


@app.post("/api/use-cases/{uuid}/suggest-fix-llm")
async def suggest_fix_use_case_llm(uuid: str, request: Request, apply: bool = Query(False)):
    """LLM-assisted UC fix (TODO 2, slice B / docs/uc-fix-design.md tier 2). For the *semantic* gaps
    the deterministic tier can't invent (empty description/intent/success_criteria/persona): run the
    deterministic fix first, then ask the project's UC-authoring model to fill ONLY those fields,
    inferring plausible content from the rest of the UC. Dry-run by default; ?apply=true saves only
    if the model's YAML re-validates to strictly better than the original. Preserves the UC uuid."""
    user = get_user(request)
    if pool is None:
        raise HTTPException(503, "pool not initialized")
    async with pool.acquire() as conn:
        await _gate_resource(conn, request, "managed_use_cases", "uuid", uuid,
                             rbac.P_PROJECT_USECASES, "use case not found")
        pid = await _active_project_id(request, conn)
        yc = await conn.fetchval("SELECT yaml_content FROM managed_use_cases WHERE uuid=$1", uuid)
        cfg = await _model_default_row(conn, "uc-authoring", project_id=pid)   # project UC-authoring model (env fallback in uc_assist)
        _uctx = await _stage_context(conn, "uc-authoring", pid)                 # #125 append-live prompt context
    try:
        data = _parse_uc_yaml(yc)
    except ValueError as e:
        raise HTTPException(400, f"cannot fix — YAML does not parse: {e}")
    errors_before = _validate_uc_yaml(data)
    # Deterministic pass first, so the model only has to fill the written-content gaps.
    proposed, changes, det_remaining, needs_semantic = _suggest_uc_fixes(data)
    det_yaml = _yaml.safe_dump(proposed, sort_keys=False, allow_unicode=True)
    if not needs_semantic:
        # Nothing needs the model — the deterministic fix already covers it.
        return {"uuid": uuid, "method": "deterministic", "valid_before": not errors_before,
                "errors_before": errors_before, "changes": changes, "proposed_yaml": det_yaml,
                "valid_after": not det_remaining, "remaining_errors": det_remaining,
                "needs_semantic": [], "applied": False,
                "explanation": "No written-content gaps — the deterministic fix is sufficient."}
    msg = ("This use case fails validation on fields that require written content. Fill in ONLY the "
           "following fields so it becomes valid, inferring plausible, specific content from the rest "
           "of the use case; keep every other field exactly as-is: " + "; ".join(needs_semantic))
    result = await uc_assist.chat(user_message=msg, current_yaml=det_yaml, context=_uctx, cfg=cfg, pool=pool)
    if "error" in result and not result.get("yaml_suggestion"):
        raise HTTPException(503, result["error"])
    llm_yaml = result.get("yaml_suggestion")
    if not llm_yaml:
        raise HTTPException(502, "the model did not return a YAML suggestion")
    explanation = result.get("explanation", "")
    try:
        llm_data = _parse_uc_yaml(llm_yaml)
    except ValueError as e:
        return {"uuid": uuid, "method": "llm", "explanation": explanation,
                "valid_before": not errors_before, "errors_before": errors_before, "changes": changes,
                "proposed_yaml": llm_yaml, "valid_after": False, "parses": False,
                "remaining_errors": [f"model YAML did not parse: {e}"], "needs_semantic": [], "applied": False}
    llm_data["uuid"] = uuid                          # the server owns UC identity — never let the model change it
    llm_remaining = _validate_uc_yaml(llm_data)
    proposed_yaml = _yaml.safe_dump(llm_data, sort_keys=False, allow_unicode=True)
    resp = {"uuid": uuid, "method": "llm", "explanation": explanation,
            "valid_before": not errors_before, "errors_before": errors_before, "changes": changes,
            "proposed_yaml": proposed_yaml, "valid_after": not llm_remaining,
            "remaining_errors": llm_remaining,
            "needs_semantic": [e for e in llm_remaining if any(m in e for m in _SEMANTIC_MARKERS)],
            "applied": False}
    if apply:
        if len(llm_remaining) >= len(errors_before):   # never save a non-improvement
            raise HTTPException(409, "the model's fix would not improve validity — review manually")
        title = _derive_uc_title(llm_data, uuid)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE managed_use_cases SET yaml_content=$2, title=$3, updated_by=$4, updated_at=now() WHERE uuid=$1",
                uuid, proposed_yaml, title, user)
        resp["applied"] = True
    return resp


@app.post("/api/use-cases")
async def create_use_case(payload: ManagedUCIn, request: Request):
    """Create a managed use case. UUID is taken from the YAML content."""
    user = get_user(request)
    try:
        data = _parse_uc_yaml(payload.yaml_content)
    except ValueError as e:
        raise HTTPException(400, str(e))

    uc_uuid = data.get("uuid")
    if not uc_uuid or not isinstance(uc_uuid, str) or not uc_uuid.strip():
        # No uuid in the draft (assist/extraction no longer emit one — the server owns UC identity).
        # Assign a real uc-<uuid4> and stamp it into the stored YAML so the row + content agree (#199).
        import uuid as _uuidm
        uc_uuid = f"uc-{_uuidm.uuid4()}"
        _m = re.search(r"(?m)^[ \t]*uuid:[ \t]*.*$", payload.yaml_content)
        payload.yaml_content = (
            payload.yaml_content[:_m.start()] + f"uuid: {uc_uuid}" + payload.yaml_content[_m.end():]
            if _m else f"uuid: {uc_uuid}\n" + payload.yaml_content
        )
        data["uuid"] = uc_uuid

    # Handle is REQUIRED by the engine but is mechanically derivable (namespace/profile/slug).
    # Extraction/assist drafts sometimes omit it; derive + stamp it (like /repair) so a missing
    # handle is auto-fixed, not a hard save failure. Semantic fields (enums/intent/criteria) stay
    # strict below — the prompt owns those, this only backfills the computable identity field (#199).
    hnd = data.get("handle")
    if not isinstance(hnd, str) or not hnd.strip():
        hnd = _derive_uc_handle(data)
        data["handle"] = hnd
        _mh = re.search(r"(?m)^[ \t]*handle:[ \t]*.*$", payload.yaml_content)
        payload.yaml_content = (
            payload.yaml_content[:_mh.start()] + f"handle: {hnd}" + payload.yaml_content[_mh.end():]
            if _mh else f"handle: {hnd}\n" + payload.yaml_content
        )

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
    priority, priority_score = _derive_uc_priority(data)
    readiness = _uc_readiness.score_use_case(data)["score"]

    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT 1 FROM managed_use_cases WHERE uuid = $1", uc_uuid
        )
        if existing:
            raise HTTPException(409, f"use case {uc_uuid!r} already exists; use PUT to update")
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_USECASES, pid)
        await conn.execute(
            """
            INSERT INTO managed_use_cases
              (uuid, title, yaml_content, created_by, updated_by, tags, priority, priority_score, readiness_score, project_id)
            VALUES ($1, $2, $3, $4, $4, $5, $6, $7, $8, $9)
            """,
            uc_uuid, title, payload.yaml_content, user, tags, priority, priority_score, readiness, pid,
        )
        # Apply the new UC to its project — the M:N membership (#199; project_id retained too).
        if pid is not None:
            await conn.execute(
                "INSERT INTO use_case_projects(uc_uuid, project_id, applied_by) VALUES($1,$2,$3) "
                "ON CONFLICT DO NOTHING", uc_uuid, pid, user)
    return {"ok": True, "uuid": uc_uuid, "title": title, "priority": priority, "readiness_score": readiness}


class UCApplyIn(BaseModel):
    uc_uuids: list[str]
    project_id: Optional[int] = None


@app.post("/api/use-case-projects")
async def apply_use_cases(payload: UCApplyIn, request: Request):
    """Apply tenant use cases (managed or corpus) to a project — the UC↔project M:N (#199).
    The UC is a tenant asset; applying associates it with a project within the tenant."""
    user = get_user(request)
    async with pool.acquire() as conn:
        pid = payload.project_id or await _active_project_id(request, conn)
        if pid is None:
            raise HTTPException(400, "no active project")
        await _require_priv_conn(conn, request, rbac.P_PROJECT_USECASES, pid)
        n = 0
        for u in payload.uc_uuids:
            if isinstance(u, str) and u.strip():
                await conn.execute(
                    "INSERT INTO use_case_projects(uc_uuid, project_id, applied_by) VALUES($1,$2,$3) "
                    "ON CONFLICT DO NOTHING", u.strip(), pid, user)
                n += 1
    return {"ok": True, "applied": n, "project_id": pid}


@app.post("/api/use-case-projects/remove")
async def unapply_use_cases(payload: UCApplyIn, request: Request):
    """Remove tenant use cases from a project (un-apply the M:N). The UC itself is untouched."""
    async with pool.acquire() as conn:
        pid = payload.project_id or await _active_project_id(request, conn)
        if pid is None:
            raise HTTPException(400, "no active project")
        await _require_priv_conn(conn, request, rbac.P_PROJECT_USECASES, pid)
        uuids = [u.strip() for u in payload.uc_uuids if isinstance(u, str) and u.strip()]
        await conn.execute(
            "DELETE FROM use_case_projects WHERE project_id=$1 AND uc_uuid = ANY($2)", pid, uuids)
    return {"ok": True, "project_id": pid, "removed": len(uuids)}


@app.put("/api/use-cases/{uuid}")
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
    priority, priority_score = _derive_uc_priority(data)
    readiness = _uc_readiness.score_use_case(data)["score"]

    async with pool.acquire() as conn:
        owner = await conn.fetchval("SELECT project_id FROM managed_use_cases WHERE uuid=$1", uuid)
        if owner is None:
            raise HTTPException(404, f"use case {uuid!r} not found in managed DB")
        await _require_priv_conn(conn, request, rbac.P_PROJECT_USECASES, owner)
        result = await conn.execute(
            """
            UPDATE managed_use_cases
            SET yaml_content=$2, title=$3, updated_by=$4, updated_at=now(), tags=$5,
                priority=$6, priority_score=$7, readiness_score=$8
            WHERE uuid=$1 AND project_id=$9
            """,
            uuid, payload.yaml_content, title, user, tags, priority, priority_score, readiness, owner,
        )
    if result == "UPDATE 0":
        raise HTTPException(404, f"use case {uuid!r} not found in managed DB")
    return {"ok": True, "uuid": uuid, "title": title, "priority": priority, "readiness_score": readiness}


class CustomerRequestIn(BaseModel):
    """Log a customer's request for a UC (the dedup-on-ingest substrate). `customer`
    is required — importance is measured by DISTINCT customers, so attributing the
    request is what keeps one customer asking 10× from poisoning priority."""
    customer: str = Field(..., min_length=1, max_length=200)
    source:   str = Field("manual", max_length=40)
    note:     str = Field("", max_length=2000)


def _customer_slug(name: str) -> str:
    """Stable customer key — MUST match the schema.sql backfill so picker-created
    customers reconcile with migrated ones."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


async def _get_or_create_customer(conn, name: str, by: str = "system") -> Optional[int]:
    """Resolve a customer by slug, creating it if absent (the 'select-or-create' the
    demand picker uses). Returns the customer id, or None for an empty name."""
    slug = _customer_slug(name)
    if not slug:
        return None
    return await conn.fetchval(
        "INSERT INTO customers (slug, name, created_by) VALUES ($1,$2,$3) "
        "ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug RETURNING id",
        slug, (name or "").strip(), by)


async def _sync_uc_demand_total(conn, uuid: str) -> int:
    """Re-derive managed_use_cases.customer_requests from the request log (the source
    of truth), so the denormalized total never drifts. Returns the new total."""
    return await conn.fetchval(
        "UPDATE managed_use_cases SET customer_requests = "
        "(SELECT COUNT(*) FROM uc_customer_requests WHERE uc_uuid=$1) "
        "WHERE uuid=$1 RETURNING customer_requests", uuid)


async def _uc_demand_summary(conn, uuid: str) -> dict:
    """Demand rollup for a UC: total requests, DISTINCT customers (the multi-tenant
    importance signal), and per-customer counts."""
    rows = await conn.fetch(
        "SELECT customer, COUNT(*) AS n FROM uc_customer_requests WHERE uc_uuid=$1 "
        "GROUP BY customer ORDER BY n DESC, customer", uuid)
    by_customer = [{"customer": r["customer"], "count": int(r["n"])} for r in rows]
    total = sum(c["count"] for c in by_customer)
    return {"total_requests": total, "distinct_customers": len(by_customer),
            "multi_tenant": len(by_customer) > 1, "by_customer": by_customer}


@app.get("/api/use-cases/{uuid}/customer-requests")
async def list_uc_customer_requests(uuid: str, request: Request):
    """The demand log for a UC: every attributed request + the rollup (total /
    distinct customers / per-customer)."""
    async with pool.acquire() as conn:
        owner = await conn.fetchval("SELECT project_id FROM managed_use_cases WHERE uuid=$1", uuid)
        if owner is None:
            raise HTTPException(404, f"use case {uuid!r} not found in managed DB")
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, owner)
        rows = await conn.fetch(
            "SELECT id, customer, source, note, created_by, requested_at "
            "FROM uc_customer_requests WHERE uc_uuid=$1 ORDER BY requested_at DESC", uuid)
        summary = await _uc_demand_summary(conn, uuid)
    return {
        "uuid": uuid,
        **summary,
        "requests": [
            {**dict(r), "requested_at": r["requested_at"].isoformat()} for r in rows
        ],
    }


@app.post("/api/use-cases/{uuid}/customer-requests")
async def log_uc_customer_request(uuid: str, payload: CustomerRequestIn, request: Request):
    """Record that a customer requested this UC. Importance = distinct customers, so
    re-logging the same customer is allowed (it's a real signal of repeated demand) but
    does NOT increase the multi-tenant count. Operational metadata — does not touch the
    UC YAML or eval staleness."""
    user = get_user(request)
    customer = payload.customer.strip()
    if not customer:
        raise HTTPException(400, "customer is required")
    async with pool.acquire() as conn:
        owner = await conn.fetchval("SELECT project_id FROM managed_use_cases WHERE uuid=$1", uuid)
        if owner is None:
            raise HTTPException(404, f"use case {uuid!r} not found in managed DB")
        await _require_priv_conn(conn, request, rbac.P_PROJECT_USECASES, owner)
        cid = await _get_or_create_customer(conn, customer, by=user)
        await conn.execute(
            "INSERT INTO uc_customer_requests (uc_uuid, project_id, customer_id, customer, source, note, created_by) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7)",
            uuid, owner, cid, customer, (payload.source or "manual")[:40], payload.note or "", user)
        await _sync_uc_demand_total(conn, uuid)
        summary = await _uc_demand_summary(conn, uuid)
    return {"ok": True, "uuid": uuid, **summary}


@app.delete("/api/use-cases/{uuid}/customer-requests/{rid}")
async def delete_uc_customer_request(uuid: str, rid: int, request: Request):
    """Remove a logged request (correct a mis-attribution / accidental double-count)."""
    get_user(request)
    async with pool.acquire() as conn:
        owner = await conn.fetchval("SELECT project_id FROM managed_use_cases WHERE uuid=$1", uuid)
        if owner is None:
            raise HTTPException(404, f"use case {uuid!r} not found in managed DB")
        await _require_priv_conn(conn, request, rbac.P_PROJECT_USECASES, owner)
        await conn.execute("DELETE FROM uc_customer_requests WHERE id=$1 AND uc_uuid=$2", rid, uuid)
        await _sync_uc_demand_total(conn, uuid)
        summary = await _uc_demand_summary(conn, uuid)
    return {"ok": True, "uuid": uuid, **summary}


async def _uc_delete_impact(conn, uuid: str) -> dict:
    """What a managed-UC delete propagates to. `removed` = gone with the UC (FK cascade for
    lifecycle/customer_requests; the join rows we clean explicitly). `retained` = historical records
    kept for provenance (keyed by uc_uuid, no FK — corpus UCs share these tables)."""
    async def n(sql: str) -> int:
        return int(await conn.fetchval(sql, uuid) or 0)
    return {
        "removed": {
            "set_memberships":   await n("SELECT count(*) FROM use_case_set_members WHERE uc_uuid=$1"),
            "project_refs":      await n("SELECT count(*) FROM use_case_projects WHERE uc_uuid=$1"),
            "customer_requests": await n("SELECT count(*) FROM uc_customer_requests WHERE uc_uuid=$1"),
            "lifecycle_events":  await n("SELECT count(*) FROM lifecycle_events WHERE uc_uuid=$1"),
        },
        "retained": {
            "past_analyses":     await n("SELECT count(*) FROM uc_analyses WHERE uc_uuid=$1"),
        },
    }


async def _set_delete_impact(conn, set_id: int) -> dict:
    """What a scoping-set delete propagates to. Members are detached (the UCs themselves are kept);
    past runs keep their recorded set name as provenance but lose the live link (set_id → NULL)."""
    async def n(sql: str) -> int:
        return int(await conn.fetchval(sql, set_id) or 0)
    return {
        "removed":  {"memberships": await n("SELECT count(*) FROM use_case_set_members WHERE set_id=$1")},
        "detached": {"past_runs":   await n("SELECT count(*) FROM run_sessions WHERE set_id=$1")},
    }


@app.get("/api/use-cases/{uuid}/delete-impact")
async def use_case_delete_impact(uuid: str, request: Request):
    """Preview the propagation of deleting this managed UC (powers the delete-confirm warning)."""
    async with pool.acquire() as conn:
        owner = await conn.fetchval("SELECT project_id FROM managed_use_cases WHERE uuid=$1", uuid)
        if owner is None:
            raise HTTPException(404, f"use case {uuid!r} not found")
        await _require_priv_conn(conn, request, rbac.P_PROJECT_USECASES, owner)
        return {"uuid": uuid, "impact": await _uc_delete_impact(conn, uuid)}


@app.delete("/api/use-cases/{uuid}")
async def delete_use_case(
    uuid: str,
    request: Request,
    purge_analyses: bool = Query(False, description="sovereignty erasure: also permanently delete this UC's historical analysis results (uc_analyses + cascaded capabilities/gaps/deps + analysis_output_cache). Default keeps them as a historical record."),
):
    """Delete a managed use case. Right-to-erase (sovereignty/security) is honoured — the delete is
    allowed — but the propagation is computed, audited for visibility, and the dangling join rows
    (set memberships + project references, which have NO FK to managed_use_cases) are removed
    explicitly so nothing orphans. Historical analyses are retained by default (provenance) and
    surfaced; pass purge_analyses=true for full sovereignty erasure (also delete them, audited)."""
    user = get_user(request)
    async with pool.acquire() as conn:
        owner = await conn.fetchval("SELECT project_id FROM managed_use_cases WHERE uuid=$1", uuid)
        if owner is None:
            raise HTTPException(404, f"use case {uuid!r} not found in managed DB")
        await _require_priv_conn(conn, request, rbac.P_PROJECT_USECASES, owner)
        impact = await _uc_delete_impact(conn, uuid)
        purged = 0
        async with conn.transaction():
            # No FK to managed_use_cases (corpus UCs share these tables) — clean explicitly.
            await conn.execute("DELETE FROM use_case_set_members WHERE uc_uuid=$1", uuid)
            await conn.execute("DELETE FROM use_case_projects   WHERE uc_uuid=$1", uuid)
            if purge_analyses:
                # Sovereignty erasure: drop the analysis results too. Deleting uc_analyses rows
                # cascades to uc_capabilities / uc_gaps / uc_capability_deps (FK analysis_id ON DELETE
                # CASCADE); analysis_output_cache is keyed by uc_uuid with no FK, so clear it directly.
                r = await conn.execute("DELETE FROM uc_analyses WHERE uc_uuid=$1", uuid)
                purged = int(r.split()[-1]) if r.startswith("DELETE") else 0
                await conn.execute("DELETE FROM analysis_output_cache WHERE uc_uuid=$1", uuid)
            result = await conn.execute(
                "DELETE FROM managed_use_cases WHERE uuid = $1 AND project_id = $2", uuid, owner
            )
    if result == "DELETE 0":
        raise HTTPException(404, f"use case {uuid!r} not found in managed DB")
    log.info("Use case %s deleted by %s (impact: %s, purge_analyses=%s, purged=%s)",
             uuid, user, impact, purge_analyses, purged)
    await audit.record(
        pool, action="use_case.delete", actor=user, actor_source="session",
        object_type="use_case", object_id=uuid, project_id=owner,
        summary=f"deleted use case {uuid}" + (f" + purged {purged} analyses" if purge_analyses else ""),
        detail={"impact": impact, "purge_analyses": purge_analyses, "analyses_purged": purged,
                "note": ("right-to-erase; join rows removed; "
                         + ("historical analyses PURGED (sovereignty erasure)" if purge_analyses
                            else "historical analyses retained"))})
    return {"ok": True, "uuid": uuid, "impact": impact,
            "purged_analyses": purged if purge_analyses else None}


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
            "SELECT lifecycle_state, project_id FROM managed_use_cases WHERE uuid = $1", uuid
        )
        if not row:
            raise HTTPException(404, f"use case {uuid!r} not found in managed DB")
        await _require_priv_conn(conn, request, rbac.P_PROJECT_USECASES, row["project_id"])
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
                      ua.infra_confidence_label, ua.infra_confidence_score,
                      ua.infra_confidence_signals, ua.infra_confidence_explanation,
                      ua.infra_confidence_recommendations,
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
                "infrastructure_confidence": (
                    {
                        "label": r["infra_confidence_label"],
                        "score": r["infra_confidence_score"],
                        "signals": _parse_jsonb(r["infra_confidence_signals"]) or [],
                        "explanation": r["infra_confidence_explanation"],
                        "recommendations": _parse_jsonb(r["infra_confidence_recommendations"]) or [],
                    } if r["infra_confidence_label"] else None
                ),
            }
            for r in rows
        ],
    }


@app.get("/api/runs/{run_name}/infra-confidence-aggregate")
async def get_run_infra_confidence_aggregate(run_name: str):
    """Aggregate per-run infrastructure-confidence breakdown.

    Counts UCs by label so the run detail drawer can show "12 of 15 UCs
    ran cleanly, 2 had budget caps, 1 was compromised." Surfaces the union
    of recommended actions across all UCs (deduplicated).
    """
    async with pool.acquire() as conn:
        # Find the workspace run_id corresponding to this Tekton run_name
        rid_row = await conn.fetchrow(
            "SELECT run_id FROM analysis_runs WHERE run_id = $1 OR "
            "(SELECT 1 FROM run_sessions WHERE run_name = $1) IS NOT NULL LIMIT 1",
            run_name,
        )
        # Caller may pass either the Tekton run_name or workspace run_id;
        # the analysis_runs table is keyed by workspace run_id.
        rows = await conn.fetch(
            """SELECT infra_confidence_label, infra_confidence_score,
                      infra_confidence_recommendations
               FROM uc_analyses
               WHERE run_id = $1""",
            (rid_row["run_id"] if rid_row else run_name),
        )
    if not rows:
        return {"run_id": run_name, "total_ucs": 0, "breakdown": {}, "recommendations": []}
    from collections import Counter
    breakdown = Counter()
    rec_seen: set = set()
    recommendations: list[str] = []
    for r in rows:
        label = r["infra_confidence_label"] or "unscored"
        breakdown[label] += 1
        for rec in (_parse_jsonb(r["infra_confidence_recommendations"]) or []):
            if rec not in rec_seen:
                rec_seen.add(rec)
                recommendations.append(rec)
    return {
        "run_id": run_name,
        "total_ucs": sum(breakdown.values()),
        "breakdown": dict(breakdown),
        "recommendations": recommendations,
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
        await _require_priv_conn(conn, request, rbac.P_PROJECT_USECASES, row["project_id"])
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


# The synthetic, immutable "All Use Cases" set. NOT a stored row — its membership
# is always every managed UC in the active project (dynamic, never drifts).
# Surfaced in /api/sets under a reserved id so it rides the standard set machinery
# (run / arch-review / export, etc.) everywhere, but it can't be edited or deleted
# and is the project default selection for "run against everything".
ALL_SET_ID = "__all__"
ALL_SET_NAME = "All Use Cases"


def _is_all_set(set_id) -> bool:
    # Canonical sentinel is "__all__"; legacy 0/"0" accepted for back-compat
    # (the old numeric id was falsy in JS and kept causing silent breakage).
    return str(set_id) in (ALL_SET_ID, "0")


def _real_set_id(set_id) -> int:
    """set_id path params are str so the synthetic '__all__' sentinel can flow;
    anything that reaches SQL must be a real (int) use_case_sets id."""
    try:
        return int(set_id)
    except (TypeError, ValueError):
        raise HTTPException(400, f"invalid set id {set_id!r}")


async def _all_set_members(conn, pid) -> list:
    """Every UC the project has, as set-member dicts: managed (DB) + corpus
    (parsed from the corpus files). Corpus UCs already present as a managed UC
    (pushed to corpus) are de-duped to the managed row."""
    members, seen = [], set()
    for r in await conn.fetch(
            "SELECT uuid FROM managed_use_cases WHERE project_id=$1 ORDER BY title", pid):
        members.append({"uc_uuid": r["uuid"], "uc_source": "managed", "uc_handle": None,
                        "uc_path": None, "added_by": "system", "added_at": None})
        seen.add(r["uuid"])
    for r in await conn.fetch(
            "SELECT path, content FROM files WHERE path LIKE '%.yaml' OR path LIKE '%.yml'"):
        try:
            data = _yaml.safe_load(r["content"])
            if not isinstance(data, dict) or "uuid" not in data:
                continue
            u = data.get("uuid")
            if u in seen:
                continue
            seen.add(u)
            members.append({"uc_uuid": u, "uc_source": "corpus", "uc_handle": data.get("handle"),
                            "uc_path": r["path"], "added_by": "system", "added_at": None})
        except Exception:
            continue
    return members


def _all_set_dict(member_count: int) -> dict:
    return {
        "id": ALL_SET_ID, "name": ALL_SET_NAME,
        "description": "Every use case in this project (managed + corpus) — auto-maintained, read-only.",
        "is_default": False, "is_system": True, "member_count": member_count,
        "created_by": "system", "created_at": None, "updated_at": None,
    }


def _reject_all_set_edit(set_id) -> None:
    if _is_all_set(set_id):
        raise HTTPException(400, "the 'All Use Cases' set is read-only (auto-maintained)")


def _set_row(r, member_count: int = 0) -> dict:
    return {
        "id": r["id"],
        "name": r["name"],
        "description": r["description"],
        "is_default": bool(r["is_default"]) if "is_default" in r else False,
        "is_system": bool(r["is_system"]) if "is_system" in r else False,
        "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat(),
        "updated_at": r["updated_at"].isoformat(),
        "member_count": member_count,
    }


@app.get("/api/sets")
async def list_sets(request: Request):
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        rows = await conn.fetch(
            """SELECT s.*, COUNT(m.uc_uuid) AS member_count
               FROM use_case_sets s
               LEFT JOIN use_case_set_members m ON m.set_id = s.id
               WHERE ($1::bigint IS NULL OR s.project_id = $1)
               GROUP BY s.id ORDER BY s.name""",
            pid,
        )
        all_set = _all_set_dict(len(await _all_set_members(conn, pid)))
    # The synthetic "All Use Cases" set is always first.
    return {"sets": [all_set] + [_set_row(r, r["member_count"]) for r in rows]}


@app.post("/api/sets")
async def create_set(payload: SetIn, request: Request):
    user = get_user(request)
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_USECASES, pid)
        try:
            row = await conn.fetchrow(
                "INSERT INTO use_case_sets(name, description, created_by, project_id) "
                "VALUES ($1, $2, $3, $4) RETURNING *",
                payload.name, payload.description, user, pid,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, f"a set named {payload.name!r} already exists")
    return _set_row(row)


@app.get("/api/sets/{set_id}")
async def get_set(set_id: str, request: Request):
    set_id = set_id if _is_all_set(set_id) else _real_set_id(set_id)
    async with pool.acquire() as conn:
        # The synthetic All set: members are every managed UC in the active
        # project (computed live), so a run/arch-review over it is "everything".
        if _is_all_set(set_id):
            pid = await _active_project_id(request, conn)
            await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
            members = await _all_set_members(conn, pid)
            return {**_all_set_dict(len(members)), "members": members}
        await _gate_resource(conn, request, "use_case_sets", "id", set_id,
                             rbac.P_PROJECT_READ, f"set {set_id} not found")
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
async def update_set(set_id: str, payload: SetIn, request: Request):
    set_id = set_id if _is_all_set(set_id) else _real_set_id(set_id)
    user = get_user(request)  # noqa: F841 — auth check
    _reject_all_set_edit(set_id)
    async with pool.acquire() as conn:
        owner = await _gate_resource(conn, request, "use_case_sets", "id", set_id,
                                     rbac.P_PROJECT_USECASES, f"set {set_id} not found")
        try:
            result = await conn.execute(
                "UPDATE use_case_sets SET name=$2, description=$3, updated_at=now() WHERE id=$1 AND project_id=$4",
                set_id, payload.name, payload.description, owner,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, f"a set named {payload.name!r} already exists")
    if result == "UPDATE 0":
        raise HTTPException(404, f"set {set_id} not found")
    return {"ok": True, "id": set_id}


@app.get("/api/sets/{set_id}/delete-impact")
async def set_delete_impact_preview(set_id: str, request: Request):
    """Preview the propagation of deleting this scoping set (powers the delete-confirm warning)."""
    set_id = _real_set_id(set_id)
    async with pool.acquire() as conn:
        await _gate_resource(conn, request, "use_case_sets", "id", set_id,
                             rbac.P_PROJECT_USECASES, f"set {set_id} not found")
        return {"set_id": set_id, "impact": await _set_delete_impact(conn, set_id)}


@app.delete("/api/sets/{set_id}")
async def delete_set(set_id: str, request: Request):
    set_id = set_id if _is_all_set(set_id) else _real_set_id(set_id)
    user = get_user(request)
    _reject_all_set_edit(set_id)
    async with pool.acquire() as conn:
        owner = await _gate_resource(conn, request, "use_case_sets", "id", set_id,
                                     rbac.P_PROJECT_USECASES, f"set {set_id} not found")
        impact = await _set_delete_impact(conn, set_id)
        result = await conn.execute("DELETE FROM use_case_sets WHERE id=$1 AND project_id=$2", set_id, owner)
    if result == "DELETE 0":
        raise HTTPException(404, f"set {set_id} not found")
    log.info("Set %s deleted by %s (impact: %s)", set_id, user, impact)
    await audit.record(
        pool, action="use_case_set.delete", actor=user, actor_source="session",
        object_type="use_case_set", object_id=str(set_id), project_id=owner,
        summary=f"deleted scoping set {set_id}",
        detail={"impact": impact, "note": "members detached (UCs kept); past runs keep recorded set "
                "name, live link cleared (set_id→NULL)"})
    return {"ok": True, "id": set_id, "impact": impact}


@app.put("/api/sets/{set_id}/default")
async def set_default_set(set_id: str, request: Request):
    set_id = set_id if _is_all_set(set_id) else _real_set_id(set_id)
    """Mark this Set as the project default. Clears the previous default.

    Used by the New Run modal to pre-populate UC selection and (later) by
    out-of-band run scheduling that needs an implicit UC set.
    """
    get_user(request)  # auth check
    _reject_all_set_edit(set_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            owner = await _gate_resource(conn, request, "use_case_sets", "id", set_id,
                                         rbac.P_PROJECT_USECASES, f"set {set_id} not found")
            await conn.execute(
                "UPDATE use_case_sets SET is_default=FALSE WHERE is_default AND id<>$1 AND project_id=$2",
                set_id, owner,
            )
            await conn.execute(
                "UPDATE use_case_sets SET is_default=TRUE, updated_at=now() WHERE id=$1",
                set_id,
            )
    return {"ok": True, "id": set_id, "is_default": True}


@app.delete("/api/sets/{set_id}/default")
async def clear_default_set(set_id: str, request: Request):
    set_id = set_id if _is_all_set(set_id) else _real_set_id(set_id)
    """Unmark this Set as the project default, leaving no default."""
    get_user(request)
    _reject_all_set_edit(set_id)
    async with pool.acquire() as conn:
        owner = await _gate_resource(conn, request, "use_case_sets", "id", set_id,
                                     rbac.P_PROJECT_USECASES, f"set {set_id} not found")
        result = await conn.execute(
            "UPDATE use_case_sets SET is_default=FALSE, updated_at=now() WHERE id=$1 AND project_id=$2",
            set_id, owner,
        )
    if result == "UPDATE 0":
        raise HTTPException(404, f"set {set_id} not found")
    return {"ok": True, "id": set_id, "is_default": False}


@app.post("/api/sets/{set_id}/members")
async def add_set_member(set_id: str, payload: SetMemberIn, request: Request):
    set_id = set_id if _is_all_set(set_id) else _real_set_id(set_id)
    user = get_user(request)
    _reject_all_set_edit(set_id)
    if payload.uc_source not in ("managed", "corpus"):
        raise HTTPException(400, "uc_source must be 'managed' or 'corpus'")
    async with pool.acquire() as conn:
        await _gate_resource(conn, request, "use_case_sets", "id", set_id,
                             rbac.P_PROJECT_USECASES, f"set {set_id} not found")
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
async def remove_set_member(set_id: str, uc_uuid: str, request: Request):
    set_id = set_id if _is_all_set(set_id) else _real_set_id(set_id)
    user = get_user(request)  # noqa: F841
    _reject_all_set_edit(set_id)
    async with pool.acquire() as conn:
        await _gate_resource(conn, request, "use_case_sets", "id", set_id,
                             rbac.P_PROJECT_USECASES, f"set {set_id} not found")
        result = await conn.execute(
            "DELETE FROM use_case_set_members WHERE set_id=$1 AND uc_uuid=$2",
            set_id, uc_uuid,
        )
    if result == "DELETE 0":
        raise HTTPException(404, "member not found in set")
    return {"ok": True}


@app.get("/api/sets/{set_id}/corpus-subpath")
async def set_corpus_subpath(set_id: str, request: Request):
    set_id = set_id if _is_all_set(set_id) else _real_set_id(set_id)
    """Return the common corpus path prefix for corpus UCs in this set.
    Used by the UI to pre-fill corpus_subpath when triggering a set run."""
    # The All set spans managed + corpus — compute both counts + the corpus subpath.
    if _is_all_set(set_id):
        async with pool.acquire() as conn:
            # Pre-run validation: reconcile the corpus cache if stale so the UC
            # count + membership reflect the current corpus before the run.
            await _ensure_corpus_fresh(conn)
            pid = await _active_project_id(request, conn)
            members = await _all_set_members(conn, pid)
        managed_count = sum(1 for m in members if m["uc_source"] == "managed")
        corpus_paths = [m["uc_path"] for m in members if m["uc_source"] == "corpus" and m.get("uc_path")]
        subpath = None
        if corpus_paths:
            from os.path import commonpath
            from pathlib import Path as _Path
            try:
                cp = _Path(commonpath(corpus_paths))
                subpath = str(cp) if cp.is_dir() else str(cp.parent)
            except ValueError:
                subpath = ""
        return {"subpath": subpath or None, "corpus_count": len(corpus_paths),
                "managed_count": managed_count}
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
async def promote_set_members(set_id: str, payload: SetPromoteIn, request: Request):
    set_id = set_id if _is_all_set(set_id) else _real_set_id(set_id)
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
        if _is_all_set(set_id):
            pid = await _active_project_id(request, conn)
            members = await conn.fetch(
                "SELECT uuid FROM managed_use_cases WHERE project_id=$1 AND lifecycle_state=$2",
                pid, payload.from_state)
        else:
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


@app.get("/api/sets/{set_id}/readiness")
async def set_readiness(set_id: str, request: Request):
    set_id = set_id if _is_all_set(set_id) else _real_set_id(set_id)
    """Readiness scorecard for a Set's managed UCs (DCM feature #4).

    The "batch check on a Set before triggering a run" from the meeting: score
    each member's definition so weak UCs can be fixed before they produce weak
    analyses. Returns per-UC scores (with the failing check ids) and a rollup.
    """
    async with pool.acquire() as conn:
        if _is_all_set(set_id):
            pid = await _active_project_id(request, conn)
            rows = await conn.fetch(
                "SELECT uuid, title, yaml_content FROM managed_use_cases "
                "WHERE project_id=$1 ORDER BY title", pid)
        else:
            exists = await conn.fetchval("SELECT 1 FROM use_case_sets WHERE id=$1", set_id)
            if not exists:
                raise HTTPException(404, f"set {set_id} not found")
            rows = await conn.fetch(
                """SELECT uc.uuid, uc.title, uc.yaml_content FROM managed_use_cases uc
                   JOIN use_case_set_members m ON m.uc_uuid = uc.uuid AND m.uc_source = 'managed'
                   WHERE m.set_id = $1 ORDER BY uc.title""",
                set_id,
            )

    items = []
    for r in rows:
        try:
            parsed = _parse_uc_yaml(r["yaml_content"] or "")
        except ValueError:
            items.append({"uuid": r["uuid"], "title": r["title"], "score": None,
                          "band": None, "ready": False, "failing": ["unparseable_yaml"]})
            continue
        res = _uc_readiness.score_use_case(parsed)
        items.append({
            "uuid": r["uuid"], "title": r["title"],
            "score": res["score"], "band": res["band"], "ready": res["ready"],
            "failing": [c["id"] for c in res["checks"] if not c["ok"]],
        })

    scored = [it["score"] for it in items if it["score"] is not None]
    by_band: dict[str, int] = {}
    for it in items:
        if it["band"]:
            by_band[it["band"]] = by_band.get(it["band"], 0) + 1
    items.sort(key=lambda it: (it["score"] is not None, it["score"] if it["score"] is not None else -1))
    return {
        "set_id": set_id,
        "count": len(items),
        "avg_score": round(sum(scored) / len(scored), 1) if scored else None,
        "ready_count": sum(1 for it in items if it["ready"]),
        "by_band": by_band,
        "use_cases": items,  # lowest score first — worst offenders surface
    }


# ========================= EXPORT / IMPORT =========================


@app.get("/api/export")
async def export_use_cases(
    request: Request,
    format: str = Query("tar.gz", description="Archive format: tar.gz, zip, or tar"),
    state: Optional[str] = Query(None, description="Filter by lifecycle state"),
    set_id: Optional[int] = Query(None, description="Export members of this set only"),
):
    """Export the active project's managed use cases as an archive.

    Archive structure: {lifecycle_state}/{set_name_or__ungrouped}/{uc_uuid}.yaml
    """
    if format not in ("tar.gz", "zip", "tar"):
        raise HTTPException(400, "format must be one of: tar.gz, zip, tar")
    if state and state not in UC_STATES:
        raise HTTPException(400, f"invalid state; must be one of {sorted(UC_STATES)}")

    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        if set_id is not None:
            # The set must belong to the active project (no cross-project export).
            exists = await conn.fetchval(
                "SELECT 1 FROM use_case_sets WHERE id=$1 AND project_id=$2", set_id, pid)
            if not exists:
                raise HTTPException(404, f"set {set_id} not found")
            set_name_row = await conn.fetchrow("SELECT name FROM use_case_sets WHERE id=$1", set_id)
            export_set_name = set_name_row["name"] if set_name_row else str(set_id)

            if state:
                uc_rows = await conn.fetch(
                    """SELECT uc.uuid, uc.yaml_content, uc.lifecycle_state
                       FROM managed_use_cases uc
                       JOIN use_case_set_members m ON m.uc_uuid = uc.uuid AND m.uc_source = 'managed'
                       WHERE m.set_id = $1 AND uc.lifecycle_state = $2 AND uc.project_id = $3
                       ORDER BY uc.lifecycle_state, uc.uuid""",
                    set_id, state, pid,
                )
            else:
                uc_rows = await conn.fetch(
                    """SELECT uc.uuid, uc.yaml_content, uc.lifecycle_state
                       FROM managed_use_cases uc
                       JOIN use_case_set_members m ON m.uc_uuid = uc.uuid AND m.uc_source = 'managed'
                       WHERE m.set_id = $1 AND uc.project_id = $2
                       ORDER BY uc.lifecycle_state, uc.uuid""",
                    set_id, pid,
                )
        else:
            export_set_name = None
            if state:
                uc_rows = await conn.fetch(
                    "SELECT uuid, yaml_content, lifecycle_state FROM managed_use_cases "
                    "WHERE lifecycle_state = $1 AND project_id = $2 ORDER BY lifecycle_state, uuid",
                    state, pid,
                )
            else:
                uc_rows = await conn.fetch(
                    "SELECT uuid, yaml_content, lifecycle_state FROM managed_use_cases "
                    "WHERE project_id = $1 ORDER BY lifecycle_state, uuid", pid
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

    # Archive-bomb / OOM guards: cap the compressed upload, the entry count, and
    # the cumulative DECOMPRESSED bytes (a small zip can expand to gigabytes).
    _MAX_UPLOAD = 32 * 1024 * 1024        # 32 MiB compressed
    _MAX_ENTRIES = 5000
    _MAX_DECOMPRESSED = 256 * 1024 * 1024  # 256 MiB total decompressed
    if len(data) > _MAX_UPLOAD:
        raise HTTPException(413, f"archive too large ({len(data)} bytes; max {_MAX_UPLOAD})")

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
    _total = 0
    try:
        if fmt == "zip":
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                infos = [i for i in zf.infolist() if not i.filename.endswith("/")]
                if len(infos) > _MAX_ENTRIES:
                    raise HTTPException(413, f"archive has too many entries (max {_MAX_ENTRIES})")
                # Reject before reading if the declared uncompressed total is huge.
                if sum(i.file_size for i in infos) > _MAX_DECOMPRESSED:
                    raise HTTPException(413, "archive decompresses to too much data")
                for info in infos:
                    _total += info.file_size
                    if _total > _MAX_DECOMPRESSED:
                        raise HTTPException(413, "archive decompresses to too much data")
                    entries.append((info.filename, zf.read(info).decode("utf-8")))
        else:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
                count = 0
                for member in tf.getmembers():
                    if not member.isfile():
                        continue
                    count += 1
                    if count > _MAX_ENTRIES:
                        raise HTTPException(413, f"archive has too many entries (max {_MAX_ENTRIES})")
                    _total += member.size
                    if _total > _MAX_DECOMPRESSED:
                        raise HTTPException(413, "archive decompresses to too much data")
                    fobj = tf.extractfile(member)
                    if fobj is None:
                        continue
                    entries.append((member.name, fobj.read().decode("utf-8")))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"could not read archive: {e}")

    created = 0
    updated = 0
    transitioned = 0
    skipped = 0
    errors: list[str] = []

    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_USECASES, pid)
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
            priority, priority_score = _derive_uc_priority(uc_data)
            readiness = _uc_readiness.score_use_case(uc_data)["score"]

            try:
                async with conn.transaction():
                    existing = await conn.fetchrow(
                        "SELECT lifecycle_state FROM managed_use_cases WHERE uuid=$1", uc_uuid
                    )
                    if existing is None:
                        await conn.execute(
                            """INSERT INTO managed_use_cases
                               (uuid, title, yaml_content, lifecycle_state, created_by, updated_by, tags, priority, priority_score, readiness_score, project_id)
                               VALUES ($1, $2, $3, $4, $5, $5, $6, $7, $8, $9, $10)""",
                            uc_uuid, title, content, target_state, user, tags, priority, priority_score, readiness, pid,
                        )
                        if pid is not None:   # apply to its project — M:N membership (#199)
                            await conn.execute(
                                "INSERT INTO use_case_projects(uc_uuid, project_id, applied_by) VALUES($1,$2,$3) "
                                "ON CONFLICT DO NOTHING", uc_uuid, pid, user)
                        await conn.execute(
                            "INSERT INTO lifecycle_events(uc_uuid, from_state, to_state, actor, notes) "
                            "VALUES ($1, NULL, $2, $3, 'imported')",
                            uc_uuid, target_state, user,
                        )
                        created += 1
                    else:
                        await conn.execute(
                            """UPDATE managed_use_cases SET yaml_content=$2, title=$3,
                               updated_by=$4, updated_at=now(), tags=$5,
                               priority=$6, priority_score=$7, readiness_score=$8 WHERE uuid=$1""",
                            uc_uuid, content, title, user, tags, priority, priority_score, readiness,
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


@app.post("/api/corpus/resync")
async def corpus_resync(request: Request):
    """Reconcile the corpus-files cache from the registered corpus repos (the
    same source the engine clones), with mark-and-sweep. Manual trigger for when
    you know the corpus changed; boot + hourly loop + the corpus webhook keep it
    fresh otherwise. Platform-admin gated."""
    await require_role(request, "admin")
    async with pool.acquire() as conn:
        return await sync_corpus_files(conn, reason="manual")


@app.get("/api/corpus/sync-status")
async def corpus_sync_status():
    """Last corpus-cache reconcile: when it ran + per-repo file counts + pruned.
    Drives the freshness indicator + Resync button in Config."""
    at = _last_corpus_sync["at"]
    return {
        "last_sync_at": (datetime.fromtimestamp(at, tz=timezone.utc).isoformat()
                         if at else None),
        "age_seconds": (int(time.time() - at) if at else None),
        "result": _last_corpus_sync["result"],
    }


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


def _derive_gap_namespace(gap: dict) -> Optional[str]:
    """Best-effort namespace tag for a gap based on the spec docs it cites.

    Looks at `spec_refs` (resolved citations) first, falls back to
    `spec_refs_missing` (docs the model said should exist but didn't).
    Doc handles are `<namespace>/<path>`; we take the first segment of
    the first ref. Returns None if no refs present.
    """
    for key in ("spec_refs", "spec_refs_missing"):
        refs = gap.get(key) or []
        if isinstance(refs, str):
            refs = [refs]
        for r in refs:
            if not isinstance(r, str):
                continue
            head = r.split("/", 1)[0].strip()
            if head:
                return head
    return None


# ── Dependency-aware staleness (migrate_t003): collect the spec refs an analysis
# actually leaned on, resolve them to corpus file paths, so we can flag a UC stale
# only when a file it DEPENDS ON changed (not when any repo moves). #128 ──────
_SPEC_EXTS = (".md", ".json", ".yaml", ".yml")


def _collect_emitted_spec_refs(analysis: dict) -> set:
    """Walk an analysis dict and collect every `spec_refs` / `spec_ref` value
    (the docs the model cited). Recursive + defensive — the schema nests refs on
    components, data entities, capabilities_invoked, policies, and gaps."""
    out: set = set()

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("spec_refs", "spec_ref") and v:
                    for r in ([v] if isinstance(v, str) else v):
                        if isinstance(r, str) and r.strip():
                            out.add(r.strip())
                else:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(analysis or {})
    return out


def _resolve_spec_ref_to_path(ref: str, known_paths: set, basename_index: dict) -> Optional[str]:
    """Resolve a doc-handle spec_ref (e.g. 'udlm/contracts/policy-contract' or
    '.../policy-contract/some-section') to a corpus file path. Strategy: try the
    ref (and progressively shorter prefixes, dropping trailing /section segments)
    as a path with each known extension; fall back to a unique basename match.
    Returns None if unresolvable (callers must treat None as 'never drifts')."""
    if not ref:
        return None
    cand = ref.strip().lstrip("/")
    parts = cand.split("/")
    for stop in range(len(parts), 0, -1):
        base = "/".join(parts[:stop])
        for ext in _SPEC_EXTS:
            p = base + ext
            if p in known_paths:
                return p
        if base in known_paths:
            return base
    leaf = parts[-1]
    hits = basename_index.get(leaf)
    if hits and len(hits) == 1:
        return next(iter(hits))
    return None


def _eval_fingerprint(content_sha, model, engine_version, engine_commit, repo_shas) -> str:
    """Per-UC evaluation fingerprint (uc-scoped-evaluation-design.md): the inputs an evaluation
    depended on. Staleness = stored fingerprint != recomputed-from-current. Stable field order;
    repo_shas is a dict (sorted) — null/{} until step 1b captures the project repo HEADs."""
    h = hashlib.sha256()
    for part in (content_sha, model, engine_version, engine_commit,
                 json.dumps(repo_shas or {}, sort_keys=True)):
        h.update((part or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


async def _resolve_repo_head(repo: dict, token: str):
    """Best-effort current HEAD SHA of a managed repo's branch (GitHub only for now). Returns the
    SHA or None and NEVER raises — drift is best-effort and must not break ingest (step 1b)."""
    try:
        url = repo.get("repo_url") or ""
        if "github.com" not in url:
            return None  # non-GitHub providers (gitlab/generic): 1b-drift follow-up
        owner, name = corpus_push.parse_github_url(url)
        branch = repo.get("repo_branch") or "main"
        r = await corpus_push._gh(
            "GET", f"https://api.github.com/repos/{owner}/{name}/git/refs/heads/{branch}", token)
        if r.status_code == 200:
            return (r.json().get("object") or {}).get("sha")
    except Exception:
        log.warning("repo HEAD resolve failed for %s", repo.get("namespace"))
    return None


async def _resolve_project_repo_shas(conn, project_id):
    """{role:namespace -> HEAD sha} for the project's spec/corpus repos at eval time, for the eval
    fingerprint (uc-scoped-evaluation-design.md step 1b). Best-effort, GitHub-only, fully guarded."""
    if not project_id:
        return None
    token = corpus_push.push_token()
    if not token:
        return None
    out = {}
    try:
        for role in ("spec", "corpus"):
            for rp in await _repos.list_repos(conn, role=role, project_id=project_id):
                sha = await _resolve_repo_head(rp, token)
                if sha:
                    out[f"{role}:{rp.get('namespace')}"] = sha
    except Exception:
        log.warning("project repo SHA resolve failed for project %s", project_id)
    return out or None


# #114 drift detection: a UC is stale not only when its content was edited, but when the CODE it
# was evaluated against has moved (captured source_repo_shas != current repo HEADs). Resolving HEADs
# is a live GitHub call, so cache per project with a short TTL — /api/freshness is polled.
_repo_head_cache: dict = {}   # project_id -> (epoch_ts, {role:ns -> sha})
_REPO_HEAD_TTL = 120          # seconds

async def _current_project_repo_shas_cached(conn, project_id):
    """Current project repo HEADs, cached (TTL). {} on any failure — drift then degrades to false."""
    if not project_id:
        return {}
    ent = _repo_head_cache.get(project_id)
    if ent and (time.time() - ent[0]) < _REPO_HEAD_TTL:
        return ent[1]
    shas = await _resolve_project_repo_shas(conn, project_id) or {}
    _repo_head_cache[project_id] = (time.time(), shas)
    return shas

def _parse_jsonb(v):
    """asyncpg returns JSONB as a str by default — parse defensively to a dict."""
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v:
        try:
            return json.loads(v)
        except Exception:
            return {}
    return {}

def _repo_drifted(captured, current):
    """True iff any repo the eval captured has a DIFFERENT current HEAD. Only compares keys present
    (and resolvable) on both sides, so an unresolved/removed repo never falsely drifts a UC."""
    captured = _parse_jsonb(captured)
    if not captured or not current:
        return False
    for k, was in captured.items():
        now = current.get(k)
        if was and now and was != now:
            return True
    return False


async def _dep_drift_map(conn, analysis_ids) -> dict:
    """Dependency-aware drift per analysis (migrate_t003) — the TARGETED staleness signal (#128).
    Returns {analysis_id: {"drifted": bool, "files": [paths]}} from uc_spec_drift: a UC is drift-stale
    iff a spec FILE its eval depended on changed content since (not when any repo merely moved, the
    coarse _repo_drifted check). Degrades to {} (→ nothing drifts) if the view/deps aren't present yet
    — e.g. before the first re-ingest captures dependencies."""
    aids = [a for a in (analysis_ids or []) if a is not None]
    if not aids:
        return {}
    try:
        rows = await conn.fetch(
            """SELECT analysis_id,
                      bool_or(is_drifted) AS drifted,
                      array_agg(DISTINCT file_path) FILTER (WHERE is_drifted) AS files
               FROM uc_spec_drift WHERE analysis_id = ANY($1::bigint[])
               GROUP BY analysis_id""",
            aids,
        )
    except Exception as e:  # noqa: BLE001 — view may not exist pre-migration; never break freshness
        log.warning("dep-drift lookup failed: %s", e)
        return {}
    return {
        r["analysis_id"]: {"drifted": bool(r["drifted"]), "files": list(r["files"] or [])}
        for r in rows
    }


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
        _ws_total = summary.get("total_ucs")
        if run_started_ts:
            # SOVEREIGNTY-CRITICAL: this picks which run_session (and therefore which PROJECT) owns
            # the ingested results. Nearest-by-time alone misattributes concurrent runs in different
            # projects (a 6-UC DAV run vs a 15-UC DCM run started minutes apart). So prefer the
            # session whose SCOPE SIZE (len(uc_uuids) or its set's member count) matches the workspace
            # run's total_ucs — deterministic when concurrent runs differ in size — and fall back to
            # nearest-by-time only if no size match. (Fully deterministic fix: stamp the PipelineRun
            # name into run-summary.yaml from the engine and match on it.)
            cands = await conn.fetch(
                """SELECT run_name, uc_state_snapshot, set_name, selection_mode, project_id,
                          trigger_payload, set_id
                   FROM run_sessions
                   WHERE started_at BETWEEN $1::timestamptz - interval '15 minutes'
                                        AND $1::timestamptz + interval '15 minutes'
                   ORDER BY ABS(EXTRACT(EPOCH FROM (started_at - $1::timestamptz))) ASC""",
                run_started_ts,
            )
            if _ws_total is not None:
                for c in cands:
                    cfg = _parse_jsonb(c["trigger_payload"]) or {}
                    ucu = cfg.get("uc_uuids") or cfg.get("managed_uc_uuids")
                    cnt = len(ucu) if isinstance(ucu, list) and ucu else None
                    if cnt is None and c["set_id"]:
                        cnt = await conn.fetchval(
                            "SELECT count(*) FROM use_case_set_members WHERE set_id=$1", c["set_id"])
                    if cnt == _ws_total:
                        run_session = c
                        break
            if run_session is None and cands:
                run_session = cands[0]   # fall back to nearest-by-time
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

        # Inherit the project from the run's session; fall back to default.
        _ar_pid = None
        if run_session is not None:
            try: _ar_pid = run_session["project_id"]
            except (KeyError, TypeError): _ar_pid = None
        if _ar_pid is None:
            _ar_pid = await _default_project_id(conn)
        await conn.execute(
            """INSERT INTO analysis_runs
               (run_id, run_name, mode, started_at, finished_at, total_ucs,
                successful, failed, total_samples, project_id)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
            run_id,
            run_name_for_analysis,
            summary.get("mode"),
            _parse_ts(summary.get("started_at")),
            _parse_ts(summary.get("finished_at")),
            summary.get("total_ucs", 0),
            summary.get("successful", 0),
            summary.get("failed", 0),
            summary.get("total_samples", 0),
            _ar_pid,
        )

        # Step 1b: resolve the project's spec/corpus repo HEAD SHAs ONCE per ingest, for the eval
        # fingerprint (a repo change then makes the cache stale). Best-effort, guarded.
        _run_repo_shas = await _resolve_project_repo_shas(conn, _ar_pid)

        # #branch-targeting: roll the cloned HEAD SHAs up to the run so the run row,
        # results, and the decision/roadmap pipeline can show "evaluated against
        # <branch>@<sha>". Keys are "<role>:<namespace>"; take the first of each role.
        if run_name_for_analysis and _run_repo_shas:
            try:
                _corpus_sha = next((v for k, v in _run_repo_shas.items() if k.startswith("corpus:")), None)
                _spec_sha   = next((v for k, v in _run_repo_shas.items() if k.startswith("spec:")), None)
                if _corpus_sha or _spec_sha:
                    await conn.execute(
                        "UPDATE run_sessions SET corpus_repo_sha = COALESCE($2, corpus_repo_sha), "
                        "spec_repo_sha = COALESCE($3, spec_repo_sha) WHERE run_name = $1",
                        run_name_for_analysis, _corpus_sha, _spec_sha,
                    )
            except Exception:
                log.warning("run-level repo SHA rollup failed for %s", run_name_for_analysis)

        ingested_ucs = 0
        ingested_gaps = 0
        ingested_caps = 0
        ingested_deps = 0
        ingested_spec_deps = 0
        # migrate_t003: snapshot current corpus file SHAs once so we can record, per UC, the content
        # SHA of each depended-on spec file AT EVAL TIME (dependency-aware staleness, #128).
        _files_rows = await conn.fetch("SELECT path, content_sha256 FROM files")
        _file_sha = {r["path"]: r["content_sha256"] for r in _files_rows}
        _known_paths = set(_file_sha.keys())
        _basename_index: dict = {}
        for _p in _known_paths:
            _leaf = _p.rsplit("/", 1)[-1]
            for _e in _SPEC_EXTS:
                if _leaf.endswith(_e):
                    _leaf = _leaf[: -len(_e)]
                    break
            _basename_index.setdefault(_leaf, set()).add(_p)
        emitted_uuids = set()   # #121: track which UCs the engine actually produced
        _dup_uuids = 0          # duplicate uc_uuid rows in this run-summary (see guard below)
        for uc in (summary.get("ucs") or []):
            uc_uuid = uc.get("uc_uuid")
            if not uc_uuid:
                continue
            # De-dup uc_uuid WITHIN a run. uc_analyses is uniquely keyed on (run_id, uc_uuid), so a
            # run-summary that lists the same uc_uuid twice would make the second INSERT violate
            # uc_analyses_run_id_uc_uuid_key — and since the whole ingest is one transaction, that
            # rolls back EVERY row, so the run never lands in analysis_runs and the 5-min ingest loop
            # retries it forever (observed: the Piotr-feedback corpus emits the sentinel
            # `uc_uuid: <load-failed>` once per unparseable file → 9 identical keys → permanent wedge).
            # First occurrence wins; the rest are counted + logged (the underlying corpus parse
            # failures are a separate data-quality issue, tracked elsewhere).
            if uc_uuid in emitted_uuids:
                _dup_uuids += 1
                continue
            emitted_uuids.add(uc_uuid)
            # Load full analysis for this UC
            analysis = _results.get_analysis(run_id, uc_uuid)
            meta = {}
            overall = None
            analyzed_at = None
            model = None
            endpoint_url = None
            engine_version = None
            engine_commit = None
            consumer_version = None
            gaps = []
            caps = []          # capabilities_invoked (DCM feature #2)
            infra: dict = {}   # infrastructure_confidence object from analysis metadata
            if analysis and analysis.get("_source") == "single":
                a_meta = analysis.get("analysis_metadata") or {}
                summary_block = analysis.get("summary") or {}
                meta = a_meta
                overall = summary_block.get("overall_assessment") or analysis.get("overall_assessment")
                analyzed_at = _parse_ts(a_meta.get("analyzed_at"))
                model = a_meta.get("model")
                endpoint_url = a_meta.get("endpoint_url")
                engine_version = a_meta.get("engine_version")
                engine_commit = a_meta.get("engine_commit")
                consumer_version = a_meta.get("consumer_version")
                infra = a_meta.get("infrastructure_confidence") or {}
                gaps = analysis.get("gaps_identified") or []
                caps = analysis.get("capabilities_invoked") or []
            elif analysis and analysis.get("_source") == "explore":
                # Use first sample's metadata
                first = (analysis.get("samples") or [{}])[0] if analysis.get("samples") else {}
                a_meta = first.get("analysis_metadata") or {}
                model = a_meta.get("model")
                endpoint_url = a_meta.get("endpoint_url")
                engine_version = a_meta.get("engine_version")
                engine_commit = a_meta.get("engine_commit")
                consumer_version = a_meta.get("consumer_version")
                infra = a_meta.get("infrastructure_confidence") or {}
                # Collect gaps from all samples (deduplicated by gap_id)
                seen_gap_ids = set()
                seen_cap_ids = set()
                for sample in (analysis.get("samples") or []):
                    for g in (sample.get("gaps_identified") or []):
                        gid = g.get("gap_id")
                        if gid and gid not in seen_gap_ids:
                            gaps.append(g)
                            seen_gap_ids.add(gid)
                    # Capabilities dedup by id within a UC — a capability invoked
                    # across multiple samples still counts once for this UC.
                    for c in (sample.get("capabilities_invoked") or []):
                        cid = c.get("id")
                        if cid and cid not in seen_cap_ids:
                            caps.append(c)
                            seen_cap_ids.add(cid)

            # R2: state-at-run from the snapshot; if not in the snapshot,
            # the UC was corpus-source (no managed lifecycle).
            state_at_run = uc_state_snapshot.get(uc_uuid)
            source_kind = "managed" if state_at_run else "corpus"
            # Step 1 fingerprint: UC content hash + eval config (repo SHAs = step 1b → null).
            # Managed UCs hash their stored yaml_content; corpus UCs have no managed row, so their
            # content_sha stays null (their staleness will ride the repo SHAs once 1b lands).
            uc_content_sha = None
            _yc = await conn.fetchval("SELECT yaml_content FROM managed_use_cases WHERE uuid=$1", uc_uuid)
            if _yc:
                uc_content_sha = hashlib.sha256(_yc.encode("utf-8")).hexdigest()
            source_repo_shas = _run_repo_shas   # step 1b: project repo HEAD SHAs at eval time
            eval_fp = _eval_fingerprint(uc_content_sha, model, engine_version, engine_commit, source_repo_shas)
            # #121: capture why a UC failed (and the stage), or flag a low-confidence success.
            _status = uc.get("status")
            _infra_label = (infra.get("label") if infra else None)
            err_reason, err_phase = None, None
            if _status == "failed":
                err_phase = "engine"
                err_reason = (uc.get("error") or (analysis or {}).get("error")
                              or overall or (infra.get("explanation") if infra else None)
                              or "The engine reported a failure for this use case (no detail provided).")
            elif _infra_label in ("low", "compromised"):
                err_phase = "unreliable"
                err_reason = (infra.get("explanation") if infra else None) or f"Infrastructure confidence: {_infra_label}."
            row = await conn.fetchrow(
                """INSERT INTO uc_analyses
                   (run_id, uc_uuid, uc_handle, status, verdict, overall_assessment,
                    wall_time_seconds, sample_count, engine_version, model,
                    endpoint_url, analyzed_at,
                    lifecycle_state_at_run, source_kind,
                    infra_confidence_label, infra_confidence_score,
                    infra_confidence_signals, infra_confidence_explanation,
                    infra_confidence_recommendations,
                    engine_commit, consumer_version, uc_content_sha,
                    source_repo_shas, eval_fingerprint,
                    error_reason, error_phase)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,
                           $15,$16,$17::jsonb,$18,$19::jsonb,
                           $20,$21,$22,$23::jsonb,$24,$25,$26)
                   RETURNING id""",
                run_id, uc_uuid,
                uc.get("uc_handle"),
                _status,
                uc.get("verdict"),
                overall,
                uc.get("wall_time_seconds"),
                uc.get("sample_count"),
                engine_version, model, endpoint_url, analyzed_at,
                state_at_run, source_kind,
                _infra_label,
                (infra.get("score") if infra else None),
                json.dumps(infra.get("signals") or []) if infra else None,
                (infra.get("explanation") if infra else None),
                json.dumps(infra.get("recommendations") or []) if infra else None,
                engine_commit, consumer_version, uc_content_sha,
                (json.dumps(source_repo_shas) if source_repo_shas is not None else None), eval_fp,
                err_reason, err_phase,
            )
            analysis_id = row["id"]
            ingested_ucs += 1

            # migrate_t003: capture this analysis's spec-file dependencies + the file SHA at eval
            # time, so /api/freshness can flag the UC stale only when a file it relied on changes
            # (dependency-aware staleness, #128). Never break ingest on a capture error.
            try:
                for ref in _collect_emitted_spec_refs(analysis):
                    fp = _resolve_spec_ref_to_path(ref, _known_paths, _basename_index)
                    await conn.execute(
                        """INSERT INTO uc_analysis_spec_deps
                             (analysis_id, run_id, uc_uuid, spec_ref, file_path,
                              file_sha256_at_eval, source)
                           VALUES ($1,$2,$3,$4,$5,$6,'emitted')
                           ON CONFLICT (analysis_id, spec_ref, source) DO NOTHING""",
                        analysis_id, run_id, uc_uuid, ref, fp,
                        (_file_sha.get(fp) if fp else None),
                    )
                    ingested_spec_deps += 1
            except Exception as _e:  # noqa: BLE001 — dep capture must never break ingest
                log.warning("spec-dep capture failed for %s: %s", uc_uuid, _e)

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
                # Derive namespace from any spec_refs the engine emitted on
                # this gap. Each ref is doc-handle-shaped (`<ns>/<path>`), so
                # the leading segment is the namespace. Multiple distinct
                # namespaces → store the first; cross-namespace gaps are
                # currently rare but should be surfaced explicitly in a
                # future schema bump (uc_gaps.namespaces TEXT[]).
                namespace = _derive_gap_namespace(gap)
                await conn.execute(
                    """INSERT INTO uc_gaps
                       (analysis_id, run_id, uc_uuid, gap_id, title,
                        description, severity, recommendation, rationale,
                        namespace)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                    analysis_id, run_id, uc_uuid,
                    gap_id, title, desc, sev,
                    gap.get("recommendation"),
                    gap.get("rationale"),
                    namespace,
                )
                ingested_gaps += 1

            # Capabilities the UC demands (DCM feature #2). Dedup by id within
            # this UC so a capability counts once per UC regardless of how many
            # findings cite it — density is measured across UCs, not findings.
            seen_caps_this_uc = set()
            for cap in caps:
                if not isinstance(cap, dict):
                    continue
                cap_id = cap.get("id")
                if not cap_id or cap_id in seen_caps_this_uc:
                    continue
                seen_caps_this_uc.add(cap_id)
                conf = cap.get("confidence")
                conf_label = conf.get("label") if isinstance(conf, dict) else conf
                conf_score = conf.get("score") if isinstance(conf, dict) else None
                await conn.execute(
                    """INSERT INTO uc_capabilities
                       (analysis_id, run_id, uc_uuid, capability_id, usage,
                        confidence, confidence_score, rationale, namespace)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                    analysis_id, run_id, uc_uuid,
                    cap_id, cap.get("usage"),
                    conf_label, conf_score,
                    cap.get("rationale"),
                    _derive_gap_namespace(cap),
                )
                ingested_caps += 1
                # Dependency edges (DCM feature #3). Each depends_on target is a
                # capability this one requires; dedup per (cap, dep) within a UC.
                seen_edges = set()
                for dep in (cap.get("depends_on") or []):
                    if not isinstance(dep, str) or not dep or dep == cap_id:
                        continue
                    if dep in seen_edges:
                        continue
                    seen_edges.add(dep)
                    await conn.execute(
                        """INSERT INTO uc_capability_deps
                           (analysis_id, run_id, uc_uuid, capability_id, depends_on_id)
                           VALUES ($1,$2,$3,$4,$5)""",
                        analysis_id, run_id, uc_uuid, cap_id, dep,
                    )
                    ingested_deps += 1

        # #121 — DROPPED-UC diff: the run's intended managed scope (uc_state_snapshot, captured
        # at trigger) minus what the engine actually emitted. The remainder silently fell out
        # (timeout / OOM / skipped). Write a stub 'not_emitted' failed row so the audit can SEE it
        # instead of it looking like "never attempted".
        _dropped = 0
        intended_uuids = set(uc_state_snapshot.keys()) if uc_state_snapshot else set()
        for d_uuid in (intended_uuids - emitted_uuids):
            _yc2 = await conn.fetchval("SELECT yaml_content FROM managed_use_cases WHERE uuid=$1", d_uuid)
            _csha2 = hashlib.sha256(_yc2.encode("utf-8")).hexdigest() if _yc2 else None
            _fp2 = _eval_fingerprint(_csha2, None, None, None, _run_repo_shas)
            await conn.execute(
                """INSERT INTO uc_analyses
                   (run_id, uc_uuid, status, verdict, lifecycle_state_at_run, source_kind,
                    uc_content_sha, source_repo_shas, eval_fingerprint, error_reason, error_phase)
                   VALUES ($1,$2,'failed',NULL,$3,'managed',$4,$5::jsonb,$6,$7,'not_emitted')""",
                run_id, d_uuid, uc_state_snapshot.get(d_uuid),
                _csha2, (json.dumps(_run_repo_shas) if _run_repo_shas is not None else None), _fp2,
                "Use case was in the ingestion scope but the engine produced no result — it likely "
                "timed out, errored, or was skipped before completion.",
            )
            ingested_ucs += 1
            _dropped += 1
        if _dropped:
            # Keep the run's failed tally honest so the masthead/list reflect the dropped UCs too.
            await conn.execute(
                "UPDATE analysis_runs SET failed = COALESCE(failed,0) + $2 WHERE run_id=$1",
                run_id, _dropped)
        # Wave 0: stamp the session's UC counts now that they're authoritative.
        # They were never written (the finalizer defers them), so
        # _est_per_uc_seconds' `uc_succeeded > 0` filter never matched and the
        # per-UC ETA stayed the 30-min env constant — which now sizes the
        # auto-cancel budget. Mirrors the analysis_runs tallies (incl. dropped).
        if run_name_for_analysis:
            await conn.execute(
                "UPDATE run_sessions SET uc_total=$2, uc_succeeded=$3, uc_failed=$4 "
                "WHERE run_name=$1",
                run_name_for_analysis,
                summary.get("total_ucs", 0),
                summary.get("successful", 0),
                (summary.get("failed", 0) or 0) + _dropped,
            )
        if _dup_uuids:
            log.warning(
                "ingest %s: skipped %d duplicate uc_uuid row(s) in run-summary "
                "(e.g. repeated '<load-failed>' sentinels); first occurrence kept per uc_uuid",
                run_id, _dup_uuids)

    return {
        "run_id": run_id,
        "ingested_ucs": ingested_ucs,
        "duplicate_uc_uuids_skipped": _dup_uuids,
        "ingested_gaps": ingested_gaps,
        "ingested_capabilities": ingested_caps,
        "ingested_capability_deps": ingested_deps,
        "ingested_spec_deps": ingested_spec_deps,
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


@app.get("/api/freshness")
async def project_freshness(request: Request):
    """Active-project analysis FRESHNESS summary (uc-scoped-evaluation-design.md, step 2) for the
    masthead chip: UC coverage (ingested/total), staleness, and last-eval age. Staleness now has TWO
    axes (#114): the UC was **edited** since its eval (updated_at > eval_at), OR the **code drifted**
    (captured source_repo_shas != current repo HEADs). Either makes it stale."""
    # #239 / TODO3: Coverage follows the masthead Scope. An optional `set_id` (the active
    # Scoping Set, '__unassigned__', or '__all__'/empty for the whole project) restricts the
    # coverage corpus to that set's use cases — so the pill reflects exactly what the consumer
    # views (Results, Roadmaps, Cap Map) are scoped to, not the whole project.
    set_id = (request.query_params.get("set_id") or "").strip()
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        # scope_uuids: None = whole project; otherwise the set of UC uuids (managed + corpus) the
        # Scope restricts to. Membership is keyed by uuid (use_case_set_members), so it covers both.
        scope_uuids = None
        if set_id and set_id != "__all__":
            if set_id == "__unassigned__":
                srows = await conn.fetch(
                    "SELECT uuid FROM managed_use_cases u WHERE project_id=$1 AND lifecycle_state<>'deprecated' "
                    "AND NOT EXISTS (SELECT 1 FROM use_case_set_members m WHERE m.uc_uuid = u.uuid)", pid)
                scope_uuids = {r["uuid"] for r in srows}
            else:
                try:
                    _sid = int(set_id)
                    srows = await conn.fetch("SELECT uc_uuid FROM use_case_set_members WHERE set_id=$1", _sid)
                    scope_uuids = {r["uc_uuid"] for r in srows}
                except ValueError:
                    scope_uuids = set()          # unparseable scope → empty (coverage of nothing)
        rows = await conn.fetch(
            """
            WITH ucs AS (
              SELECT uuid, updated_at FROM managed_use_cases
              WHERE project_id = $1 AND lifecycle_state <> 'deprecated'
            ),
            latest AS (
              SELECT DISTINCT ON (a.uc_uuid) a.uc_uuid, a.id AS analysis_id, a.status, a.source_repo_shas,
                     COALESCE(a.analyzed_at, a.ingested_at) AS eval_at
              FROM uc_analyses a JOIN ucs u ON u.uuid = a.uc_uuid
              ORDER BY a.uc_uuid, a.ingested_at DESC
            )
            SELECT u.uuid, u.updated_at, l.analysis_id, l.status, l.eval_at, l.source_repo_shas
            FROM ucs u LEFT JOIN latest l ON l.uc_uuid = u.uuid
            """, pid)
        if scope_uuids is not None:           # restrict the managed corpus to the active Scope
            rows = [r for r in rows if r["uuid"] in scope_uuids]
        current = await _current_project_repo_shas_cached(conn, pid)
        dep = await _dep_drift_map(conn, [r["analysis_id"] for r in rows])   # #128 dependency-aware
        # Deprecated UCs are excluded from the analyzable corpus above; surface the count
        # so the masthead's `total` reconciles with the Use Cases view (which lists all).
        if scope_uuids is None:
            deprecated = await conn.fetchval(
                "SELECT count(*) FROM managed_use_cases WHERE project_id=$1 AND lifecycle_state='deprecated'", pid)
        else:
            deprecated = await conn.fetchval(
                "SELECT count(*) FROM managed_use_cases WHERE project_id=$1 AND lifecycle_state='deprecated' "
                "AND uuid = ANY($2)", pid, list(scope_uuids))
        # Corpus UCs from the project's corpus-role repos (managed_repos roles @> {corpus}), matched
        # by namespace — included in the total so the pill is the complete story (drift re-runs). #199.
        corpus_rows = await conn.fetch(
            "SELECT content, folder FROM files WHERE path LIKE '%.yaml' OR path LIKE '%.yml'")
        corpus_ns = set(r["namespace"] for r in await conn.fetch(
            "SELECT namespace FROM managed_repos WHERE project_id=$1 AND 'corpus'=ANY(roles)", pid)) if pid else set()
    managed_n = len(rows)
    ingested = failed = stale = stale_edited = stale_drifted = stale_repo_moved = 0
    last_eval = oldest_stale_eval = None
    affected_files: set = set()
    for r in rows:
        eval_at = r["eval_at"]
        if eval_at is None:
            continue                              # never evaluated → uncovered
        if r["status"] == "failed":
            failed += 1
            continue                              # #121: failures aren't coverage
        ingested += 1
        if last_eval is None or eval_at > last_eval:
            last_eval = eval_at
        edited  = bool(r["updated_at"] and r["updated_at"] > eval_at)
        # #128: drift is dependency-aware (a spec file this UC DEPENDED ON changed) — targeted, not
        # the coarse whole-repo-HEAD check. _repo_drifted is kept only as informational stale_repo_moved.
        _dd = dep.get(r["analysis_id"], {})
        drifted = bool(_dd.get("drifted"))
        repo_moved = _repo_drifted(r["source_repo_shas"], current)
        if repo_moved:
            stale_repo_moved += 1
        if drifted:
            affected_files.update(_dd.get("files", []))
        if edited or drifted:
            stale += 1
            if edited:  stale_edited += 1
            if drifted: stale_drifted += 1
            if oldest_stale_eval is None or eval_at < oldest_stale_eval:
                oldest_stale_eval = eval_at
    # Corpus UCs from the project's corpus repos count toward the total — the pill is the complete
    # story (drift in these should re-run). Scoped by namespace so corpus shows only in projects whose
    # repo list includes that corpus repo (not every project). #199.
    managed_uuids = {r["uuid"] for r in rows}
    corpus_n = 0
    for cr in corpus_rows:
        ns = (cr["folder"] or "").split("/", 1)[0]
        if ns not in corpus_ns:
            continue
        try:
            d = _yaml.safe_load(cr["content"])
        except Exception:
            continue
        u = d.get("uuid") if isinstance(d, dict) else None
        if u and u not in managed_uuids and (scope_uuids is None or u in scope_uuids):
            corpus_n += 1
    total_available = managed_n + corpus_n          # managed + the project's corpus (complete story)
    return {
        "total": total_available,               # masthead denominator: managed + project corpus
        "managed": managed_n,                    # analyzable UCs ingested into the DB (this project)
        "corpus": corpus_n,                      # corpus UCs from the project's corpus repos
        "deprecated": int(deprecated or 0),     # excluded from the analyzable corpus (reconciles with the UC view)
        "ingested": ingested,                    # evaluated managed UCs (have a current, non-failed analysis)
        "failed": failed,
        "uncovered": max(0, managed_n - ingested),  # managed UCs not yet evaluated (the ingest-button target)
        "stale": stale,
        "stale_edited": stale_edited,           # #114: UC content changed
        "stale_drifted": stale_drifted,         # #128: a spec file the UC DEPENDED ON changed (targeted)
        "stale_repo_moved": stale_repo_moved,   # informational: whole-repo HEAD moved (NOT folded into stale)
        "affected_files": sorted(affected_files),  # #128: which spec files drove the drift ("affected by")
        "last_eval": last_eval.isoformat() if last_eval else None,
        "oldest_stale_eval": oldest_stale_eval.isoformat() if oldest_stale_eval else None,
        "drift": stale_drifted or None,         # commits-since (ahead_by) = #114 Pass B
        "scope_set_id": set_id or None,         # #239: which Scope this coverage reflects ('' = whole project)
    }


async def _resolve_scope_uc_uuids(conn, project_id, set_id=None, uc_uuids=None):
    """Resolve a UC/Set scope to the active project's UC uuids (uc-scoped-evaluation-design.md 3a).
    Explicit `uc_uuids` (csv) → those (project-scoped); a numeric `set_id` → its members; else (or
    `__all__`) all the project's non-deprecated managed UCs."""
    if uc_uuids:
        want = [u.strip() for u in str(uc_uuids).split(",") if u.strip()]
        if not want:
            return []
        rows = await conn.fetch(
            "SELECT uuid FROM managed_use_cases WHERE project_id=$1 AND uuid = ANY($2)", project_id, want)
        return [r["uuid"] for r in rows]
    if set_id and str(set_id) == "__unassigned__":
        # Synthetic "Unassigned" scope: non-deprecated managed UCs in NO Scoping Set.
        rows = await conn.fetch(
            "SELECT u.uuid FROM managed_use_cases u "
            "WHERE u.project_id=$1 AND u.lifecycle_state <> 'deprecated' "
            "AND NOT EXISTS (SELECT 1 FROM use_case_set_members m WHERE m.uc_uuid = u.uuid)",
            project_id)
        return [r["uuid"] for r in rows]
    if set_id and str(set_id) != "__all__":
        try:
            sid = int(set_id)
        except (TypeError, ValueError):
            return []
        rows = await conn.fetch(
            "SELECT m.uc_uuid FROM use_case_set_members m "
            "JOIN managed_use_cases u ON u.uuid = m.uc_uuid AND u.project_id=$1 WHERE m.set_id=$2",
            project_id, sid)
        return [r["uc_uuid"] for r in rows]
    rows = await conn.fetch(
        "SELECT uuid FROM managed_use_cases WHERE project_id=$1 AND lifecycle_state <> 'deprecated'",
        project_id)
    return [r["uuid"] for r in rows]


# ── Set-scoped roadmap generation (arch review / enhancement over a Scoping Set) ──
# The Architecture roadmap now scopes to the masthead Scoping Set instead of a single
# run (the run picker is retired). We reuse the run-style generation/cache machinery
# by minting a synthetic run token `set:<id>` for keying, and gather the *latest eval
# per UC* across the set (so it may span runs) — exactly how the Engineering/Cap-Map
# scope views already aggregate. No version comparison is kept (by request).
def _set_token(set_id) -> str:
    """Synthetic run-id token for a Scoping-Set scope, used as the cache/generation key."""
    s = "" if set_id is None else str(set_id)
    return "set:" + (s if s else "__all__")


def _parse_set_token(run_id: str) -> Optional[str]:
    """'set:123' → '123'; 'set:__all__' → '__all__'. None when `run_id` isn't a set token."""
    if isinstance(run_id, str) and run_id.startswith("set:"):
        return run_id[4:] or "__all__"
    return None


async def _set_label(conn, set_id) -> str:
    """Human label for a set scope, for the generation prompt header."""
    s = str(set_id) if set_id not in (None, "") else "__all__"
    if s == "__all__":
        return "all use cases in the project"
    if s == "__unassigned__":
        return "use cases not in any Scoping Set"
    try:
        nm = await conn.fetchval("SELECT name FROM use_case_sets WHERE id=$1", int(s))
    except (TypeError, ValueError):
        nm = None
    return f"Scoping Set '{nm}'" if nm else f"Scoping Set {s}"


async def _set_latest_analyses(conn, project_id, set_id) -> list[dict]:
    """Latest *successful* eval per UC across a Scoping Set → run-style analyses (each
    with its gaps), for set-scoped arch review / enhancement / PR context."""
    uuids = await _resolve_scope_uc_uuids(conn, project_id, set_id, None)
    if not uuids:
        return []
    rows = await conn.fetch(
        "SELECT DISTINCT ON (uc_uuid) * FROM uc_analyses "
        "WHERE uc_uuid = ANY($1) AND COALESCE(status,'success')='success' "
        "ORDER BY uc_uuid, ingested_at DESC",
        uuids)
    out: list[dict] = []
    for ua in rows:
        gaps = await conn.fetch("SELECT * FROM uc_gaps WHERE analysis_id=$1 ORDER BY id", ua["id"])
        out.append({**dict(ua), "gaps": [dict(g) for g in gaps]})
    return out


async def _require_run_in_project(conn, request, run_id: str, *, allow_uningested: bool = False):
    """Sovereignty guard for run_id-addressed analysis reads: the run must belong to the ACTIVE
    project, else it's a cross-project leak (one project's results showing in another). Orphan runs
    (project_id NULL) are visible only under the default project (mirrors list_runs). 404s otherwise.
    Returns the active project_id. In single-user mode (pid None) everything is visible.

    `allow_uningested=True` (workspace-backed reads): a run not yet in analysis_runs has no DB
    project link — allow it (it's a live/fresh run; the project-scoped runs list is what surfaces it)."""
    pid = await _active_project_id(request, conn)
    await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
    row = await conn.fetchrow("SELECT project_id FROM analysis_runs WHERE run_id=$1 LIMIT 1", run_id)
    if row is None:
        if allow_uningested:
            return pid
        raise HTTPException(404, f"run {run_id!r} not ingested; ingest it first")
    if pid is None:
        return pid
    owner = row["project_id"]
    if owner is None:
        owner = await _default_project_id(conn)
    if owner != pid:
        # Don't confirm the run exists elsewhere — 404 as if not in this project.
        raise HTTPException(404, f"run {run_id!r} not found in this project")
    return pid


@app.get("/api/analysis/capability-density")
async def capability_density(
    request: Request,
    run_id: str = Query(None, description="workspace run_id; OMIT for latest-eval-per-UC across a Scoping Set"),
    set_id: Optional[str] = Query(None, description="Scoping Set id, or '__all__' / '__unassigned__'"),
):
    """Cross-UC capability demand density (DCM feature #2). Two modes: a single `run_id`
    (legacy run-scoped), or — the UC-scoped roadmap path (3b) — a **Scoping Set** with no run_id,
    aggregating the **latest eval per UC** (so it may span runs). Denominator = successfully-analyzed
    UCs in scope, so the ratio reflects real demand coverage.
    """
    async with pool.acquire() as conn:
        if run_id:
            await _require_run_in_project(conn, request, run_id)
            set_uuids: Optional[set] = None
            _sid = int(set_id) if (set_id is not None and str(set_id).isdigit()) else None
            if _sid is not None:
                member_rows = await conn.fetch("SELECT uc_uuid FROM use_case_set_members WHERE set_id=$1", _sid)
                set_uuids = {r["uc_uuid"] for r in member_rows}
                if not set_uuids:
                    return {"run_id": run_id, "set_id": set_id, "total_ucs": 0, "capabilities": []}
            if set_uuids is not None:
                total_ucs = await conn.fetchval(
                    "SELECT COUNT(DISTINCT uc_uuid) FROM uc_analyses WHERE run_id=$1 AND status='success' AND uc_uuid = ANY($2)",
                    run_id, list(set_uuids))
                cap_rows = await conn.fetch(
                    "SELECT capability_id, uc_uuid, confidence_score, namespace FROM uc_capabilities WHERE run_id=$1 AND uc_uuid = ANY($2)",
                    run_id, list(set_uuids))
            else:
                total_ucs = await conn.fetchval(
                    "SELECT COUNT(DISTINCT uc_uuid) FROM uc_analyses WHERE run_id=$1 AND status='success'", run_id)
                cap_rows = await conn.fetch(
                    "SELECT capability_id, uc_uuid, confidence_score, namespace FROM uc_capabilities WHERE run_id=$1", run_id)
            usage_map = await _capability_usage_map(conn, run_id)
        else:
            # UC-scoped roadmap (3b): latest eval per UC across the Scoping Set.
            pid = await _active_project_id(request, conn)
            uuids = await _resolve_scope_uc_uuids(conn, pid, set_id, None)
            if not uuids:
                return {"run_id": None, "set_id": set_id, "total_ucs": 0, "capabilities": []}
            _latest = ("SELECT DISTINCT ON (uc_uuid) uc_uuid, run_id, status FROM uc_analyses "
                       "WHERE uc_uuid = ANY($1) ORDER BY uc_uuid, ingested_at DESC")
            total_ucs = await conn.fetchval(f"SELECT count(*) FROM ({_latest}) l WHERE l.status='success'", uuids)
            cap_rows = await conn.fetch(
                "SELECT c.capability_id, c.uc_uuid, c.confidence_score, c.namespace FROM uc_capabilities c "
                f"JOIN ({_latest}) l ON l.uc_uuid=c.uc_uuid AND l.run_id=c.run_id", uuids)
            # Representative usage gloss per capability across the set's latest evals, so the
            # roadmap shows the descriptive sentence (not just the terse id) even cross-run.
            _um = await conn.fetch(
                "SELECT DISTINCT ON (c.capability_id) c.capability_id, c.usage FROM uc_capabilities c "
                f"JOIN ({_latest}) l ON l.uc_uuid=c.uc_uuid AND l.run_id=c.run_id "
                "WHERE COALESCE(c.usage,'') <> '' ORDER BY c.capability_id, length(c.usage) DESC", uuids)
            usage_map = {r["capability_id"]: r["usage"] for r in _um}
        _cat_pid = await _default_project_id(conn)
        name_map = await _catalog_name_map(conn, _cat_pid)
        meta_map = await _catalog_meta_map(conn, _cat_pid)   # #132 subdomain/disposition lens
        # m-v capability-level funding: the distinct customers demanding each capability (union
        # across its UCs). Reuses the demand log's free-text `customer` (same metric as the UC list),
        # so investment can be weighed at the capability — the method's "fund the capability, not the UC".
        _scope_uuids = list({r["uc_uuid"] for r in cap_rows})
        cust_by_uuid: dict = {}
        if _scope_uuids:
            for r in await conn.fetch(
                "SELECT uc_uuid, customer FROM uc_customer_requests WHERE uc_uuid = ANY($1)", _scope_uuids):
                cust_by_uuid.setdefault(r["uc_uuid"], set()).add(r["customer"])

    capabilities = _capability_density.aggregate_density(
        [dict(r) for r in cap_rows], int(total_ucs or 0)
    )
    for c in capabilities:
        c["name"] = name_map.get(c["capability_id"])   # catalog name, or None → UI falls back to id
        c["usage"] = usage_map.get(c["capability_id"])  # readable gloss from analysis (already stored)
        _m = meta_map.get(c["capability_id"]) or {}
        c["subdomain"] = _m.get("subdomain")            # core | supporting | generic (or None)
        c["disposition"] = _m.get("disposition")        # reuse | refurbish | replace | retire (or None)
        _custs: set = set()
        for _u in (c.get("uc_uuids") or []):
            _custs |= cust_by_uuid.get(_u, set())
        c["distinct_customers"] = len(_custs)           # m-v capability-level demand (distinct customers)
    return {
        "run_id": run_id,
        "set_id": set_id,
        "total_ucs": int(total_ucs or 0),
        "capabilities": capabilities,
    }


@app.get("/api/analysis/foundational-capabilities")
async def foundational_capabilities(
    request: Request,
    run_id: str = Query(None, description="workspace run_id; OMIT for latest-eval-per-UC across a Scoping Set"),
    set_id: Optional[str] = Query(None, description="Scoping Set id, or '__all__' / '__unassigned__'"),
):
    """Foundational capability detection (DCM feature #3) — ranks capabilities by how many others
    transitively depend on them. Two modes: a single `run_id` (legacy), or — the UC-scoped roadmap
    path (3b) — a **Scoping Set** with no run_id, over the **latest eval per UC** (may span runs).
    Empty until analyses carry capability `depends_on` edges. `edge_count` explains an empty result.
    """
    async with pool.acquire() as conn:
        if run_id:
            await _require_run_in_project(conn, request, run_id)
            set_uuids: Optional[set] = None
            _sid = int(set_id) if (set_id is not None and str(set_id).isdigit()) else None
            if _sid is not None:
                member_rows = await conn.fetch("SELECT uc_uuid FROM use_case_set_members WHERE set_id=$1", _sid)
                set_uuids = {r["uc_uuid"] for r in member_rows}
                if not set_uuids:
                    return {"run_id": run_id, "set_id": set_id, "edge_count": 0, "capabilities": []}
            if set_uuids is not None:
                edge_rows = await conn.fetch(
                    "SELECT capability_id, depends_on_id FROM uc_capability_deps WHERE run_id=$1 AND uc_uuid = ANY($2)",
                    run_id, list(set_uuids))
                demand_rows = await conn.fetch(
                    "SELECT capability_id, COUNT(DISTINCT uc_uuid) AS n FROM uc_capabilities WHERE run_id=$1 AND uc_uuid = ANY($2) GROUP BY capability_id",
                    run_id, list(set_uuids))
            else:
                edge_rows = await conn.fetch(
                    "SELECT capability_id, depends_on_id FROM uc_capability_deps WHERE run_id=$1", run_id)
                demand_rows = await conn.fetch(
                    "SELECT capability_id, COUNT(DISTINCT uc_uuid) AS n FROM uc_capabilities WHERE run_id=$1 GROUP BY capability_id", run_id)
        else:
            # UC-scoped roadmap (3b): latest eval per UC across the Scoping Set.
            pid = await _active_project_id(request, conn)
            uuids = await _resolve_scope_uc_uuids(conn, pid, set_id, None)
            if not uuids:
                return {"run_id": None, "set_id": set_id, "edge_count": 0, "capabilities": []}
            _latest = ("SELECT DISTINCT ON (uc_uuid) uc_uuid, run_id FROM uc_analyses "
                       "WHERE uc_uuid = ANY($1) ORDER BY uc_uuid, ingested_at DESC")
            edge_rows = await conn.fetch(
                "SELECT d.capability_id, d.depends_on_id FROM uc_capability_deps d "
                f"JOIN ({_latest}) l ON l.uc_uuid=d.uc_uuid AND l.run_id=d.run_id", uuids)
            demand_rows = await conn.fetch(
                "SELECT c.capability_id, COUNT(DISTINCT c.uc_uuid) AS n FROM uc_capabilities c "
                f"JOIN ({_latest}) l ON l.uc_uuid=c.uc_uuid AND l.run_id=c.run_id GROUP BY c.capability_id", uuids)

    edges = [(r["capability_id"], r["depends_on_id"]) for r in edge_rows]
    demand = {r["capability_id"]: int(r["n"]) for r in demand_rows}
    capabilities = _capability_graph.foundational_ranking(edges, demand)
    async with pool.acquire() as _c:
        name_map = await _catalog_name_map(_c, await _default_project_id(_c))
        usage_map = await _capability_usage_map(_c, run_id) if run_id else {}
    for c in capabilities:
        c["name"] = name_map.get(c["capability_id"])    # catalog name, or None → UI falls back to id
        c["usage"] = usage_map.get(c["capability_id"])  # readable gloss from analysis (already stored)
    return {
        "run_id": run_id,
        "set_id": set_id,
        "edge_count": len(edges),
        "capabilities": capabilities,
    }


@app.get("/api/analysis/uc-capability-map")
async def uc_capability_map(
    request: Request,
    run_id: str = Query(None, description="workspace run_id; OMIT for latest-eval-per-UC across a Scoping Set"),
    set_id: Optional[str] = Query(None, description="Scoping Set id, or '__all__' / '__unassigned__'"),
):
    """Bidirectional UC ↔ capability map: the bipartite edges plus per-capability demand +
    foundational flag and per-UC labels. Drives the matrix (rows=UCs, cols=capabilities). Two modes:
    a single `run_id` (legacy run-scoped), or — the UC-scoped path (3b) — a **Scoping Set** with no
    run_id, building from the **latest eval per UC** (so the map may span runs).
    """
    from collections import Counter, defaultdict
    async with pool.acquire() as conn:
        if run_id:
            await _require_run_in_project(conn, request, run_id)
            set_uuids: Optional[list] = None
            _sid = int(set_id) if (set_id is not None and str(set_id).isdigit()) else None
            if _sid is not None:
                mrows = await conn.fetch("SELECT uc_uuid FROM use_case_set_members WHERE set_id=$1", _sid)
                set_uuids = [r["uc_uuid"] for r in mrows]
                if not set_uuids:
                    return {"run_id": run_id, "set_id": set_id, "ucs": [], "capabilities": [], "edges": []}
            if set_uuids is not None:
                cap_rows = await conn.fetch(
                    "SELECT capability_id, uc_uuid, usage FROM uc_capabilities WHERE run_id=$1 AND uc_uuid = ANY($2)", run_id, set_uuids)
                dep_rows = await conn.fetch(
                    "SELECT capability_id, depends_on_id FROM uc_capability_deps WHERE run_id=$1 AND uc_uuid = ANY($2)", run_id, set_uuids)
                label_rows = await conn.fetch(
                    "SELECT uc_uuid, uc_handle FROM uc_analyses WHERE run_id=$1 AND uc_uuid = ANY($2)", run_id, set_uuids)
            else:
                cap_rows = await conn.fetch("SELECT capability_id, uc_uuid, usage FROM uc_capabilities WHERE run_id=$1", run_id)
                dep_rows = await conn.fetch("SELECT capability_id, depends_on_id FROM uc_capability_deps WHERE run_id=$1", run_id)
                label_rows = await conn.fetch("SELECT uc_uuid, uc_handle FROM uc_analyses WHERE run_id=$1", run_id)
        else:
            # UC-scoped (3b): latest eval per UC across the Scoping Set — the map may span runs.
            pid = await _active_project_id(request, conn)
            uuids = await _resolve_scope_uc_uuids(conn, pid, set_id, None)
            if not uuids:
                return {"run_id": None, "set_id": set_id, "ucs": [], "capabilities": [], "edges": []}
            _latest = ("SELECT DISTINCT ON (uc_uuid) uc_uuid, run_id, uc_handle FROM uc_analyses "
                       "WHERE uc_uuid = ANY($1) ORDER BY uc_uuid, ingested_at DESC")
            label_rows = await conn.fetch(_latest, uuids)
            cap_rows = await conn.fetch(
                "SELECT c.capability_id, c.uc_uuid, c.usage FROM uc_capabilities c "
                f"JOIN ({_latest}) l ON l.uc_uuid=c.uc_uuid AND l.run_id=c.run_id", uuids)
            dep_rows = await conn.fetch(
                "SELECT d.capability_id, d.depends_on_id FROM uc_capability_deps d "
                f"JOIN ({_latest}) l ON l.uc_uuid=d.uc_uuid AND l.run_id=d.run_id", uuids)
        _cat_pid = await _default_project_id(conn)
        name_map = await _catalog_name_map(conn, _cat_pid)
        meta_map = await _catalog_meta_map(conn, _cat_pid)   # #132 subdomain/disposition lens

    # Representative usage gloss per capability (longest sentence wins) — gives the matrix
    # headers a descriptive hover instead of just the terse capability id.
    usage_map: dict = {}
    for r in cap_rows:
        u = (r["usage"] or "").strip()
        if u and len(u) > len(usage_map.get(r["capability_id"], "")):
            usage_map[r["capability_id"]] = u
    edge_w = Counter((r["uc_uuid"], r["capability_id"]) for r in cap_rows)
    demand: dict = defaultdict(set)
    for r in cap_rows:
        demand[r["capability_id"]].add(r["uc_uuid"])
    found = {c["capability_id"]: c for c in _capability_graph.foundational_ranking(
        [(r["capability_id"], r["depends_on_id"]) for r in dep_rows],
        {k: len(v) for k, v in demand.items()})}
    labels = {r["uc_uuid"]: (r["uc_handle"] or r["uc_uuid"]) for r in label_rows}

    uc_ids = sorted({u for (u, _c) in edge_w})
    cap_ids = sorted(demand.keys(), key=lambda c: (-len(demand[c]), c))
    ucs = [{"uuid": u, "label": labels.get(u, u)} for u in uc_ids]
    capabilities = [{
        "id": c,
        "name": name_map.get(c) or c,
        "usage": usage_map.get(c),
        "demand": len(demand[c]),
        "transitive_dependents": int((found.get(c) or {}).get("transitive_dependents", 0) or 0),
        "foundational": bool((found.get(c) or {}).get("transitive_dependents", 0)),
        "leverage": (found.get(c) or {}).get("leverage"),
        "subdomain": (meta_map.get(c) or {}).get("subdomain"),
        "disposition": (meta_map.get(c) or {}).get("disposition"),
    } for c in cap_ids]
    edges = [{"uc": u, "cap": c, "weight": w} for ((u, c), w) in edge_w.items()]
    return {"run_id": run_id, "set_id": set_id,
            "uc_count": len(ucs), "capability_count": len(capabilities),
            "ucs": ucs, "capabilities": capabilities, "edges": edges}


# ---------------------------------------------------------------------------
# Capability catalog ↔ taxonomy (UDLM Knowledge family).
# Catalog = the unified capability_catalog (curated + observed, migration 020);
# taxonomy (capability_taxonomy_terms, migration 017) = normalization authority;
# the catalog back-fills taxonomy gaps. Seeded from the DCM taxonomy on startup.
# See capability_catalog.py and docs/capability-catalog-design.md.
# ---------------------------------------------------------------------------
@app.get("/api/capabilities/stats")
async def capabilities_stats():
    async with pool.acquire() as conn:
        return await _capability_catalog.stats(conn)


@app.get("/api/capabilities/taxonomy")
async def capabilities_taxonomy(
    family: str = Query("dcm"),
    q: Optional[str] = Query(None, description="substring filter on the term"),
    state: Optional[str] = Query(None, description="lifecycle_state filter"),
    limit: int = Query(500, ge=1, le=5000),
):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id::text AS id, handle, definition, pillar, domain_prefix, domain,
                      category, lifecycle_state, family, scope_tier
               FROM capability_taxonomy_terms
               WHERE is_current AND family=$1
                 AND ($2::text IS NULL OR handle ILIKE '%'||$2||'%')
                 AND ($3::text IS NULL OR lifecycle_state=$3)
               ORDER BY category NULLS LAST, lower(handle)
               LIMIT $4""",
            family, q, state, limit,
        )
    return {"family": family, "count": len(rows), "terms": [dict(r) for r in rows]}


@app.get("/api/capabilities/catalog")
async def capabilities_catalog(
    family: str = Query("dcm"),
    status: Optional[str] = Query(None, description="normalization_status filter"),
    limit: int = Query(500, ge=1, le=5000),
):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT c.id::text AS id, c.cap_key AS handle, c.name, c.domain_prefix,
                      c.status AS lifecycle_state, c.normalization_status, c.family,
                      c.created_via, c.project_id,
                      t.handle AS normalized_to
               FROM capability_catalog c
               LEFT JOIN capability_taxonomy_terms t ON t.id = c.normalized_to_term_id
               WHERE c.family=$1
                 AND ($2::text IS NULL OR c.normalization_status=$2)
               ORDER BY lower(c.cap_key)
               LIMIT $3""",
            family, status, limit,
        )
    return {"family": family, "count": len(rows), "capabilities": [dict(r) for r in rows]}


@app.get("/api/capabilities/normalize")
async def capabilities_normalize(name: str = Query(...), family: str = Query("dcm")):
    """Resolve a free-form capability name onto a canonical term, or report a gap."""
    async with pool.acquire() as conn:
        res = await _capability_catalog.normalize(conn, name, family=family)
        term = None
        if res["term_id"]:
            term = await conn.fetchval(
                "SELECT handle FROM capability_taxonomy_terms WHERE id=$1", res["term_id"])
    return {"name": name, "family": family, "status": res["status"],
            "term_id": str(res["term_id"]) if res["term_id"] else None, "term": term}


@app.post("/api/capabilities/resolve-uc-capabilities")
async def capabilities_resolve_uc(request: Request, family: str = Query("dcm")):
    """Project existing free-form uc_capabilities strings into the catalog
    (OBSERVED, normalized or flagged as gaps). Platform-admin."""
    await require_priv(request, rbac.P_PLATFORM_ADMIN)
    async with pool.acquire() as conn:
        return await _capability_catalog.resolve_uc_capabilities(conn, family=family)


@app.post("/api/capabilities/reseed")
async def capabilities_reseed(request: Request):
    """Re-run the idempotent DCM-taxonomy seed. Platform-admin."""
    await require_priv(request, rbac.P_PLATFORM_ADMIN)
    async with pool.acquire() as conn:
        return await _capability_catalog.seed_dcm_taxonomy(conn)


# ---------------------------------------------------------------------------
# Assessment ingestion (F7) — UDLM Knowledge family · Assessment + Finding.
# Consume the outputs of an existing assessment process (automation strategy,
# hybrid-cloud, AI, DCM); land each finding on capability_catalog as OBSERVED and
# normalize onto the taxonomy. Gap = OBSERVED vs CANONICAL = the roadmap signal.
# Generic mechanism only — confidential per-format parsers live inside work.
# See assessment_ingest.py and docs/capability-catalog-design.md.
# ---------------------------------------------------------------------------
@app.post("/api/assessments/ingest")
async def assessments_ingest(request: Request):
    """Ingest an assessment payload (canonical/automation format) as an Assessment
    + Findings, scoped to the active project. Body may set {"use_fixture": true}
    to load the synthetic example (no confidential data). Requires assessment.edit."""
    body = await request.json() if await request.body() else {}
    if body.get("use_fixture"):
        payload = _assessment_ingest.synthetic_fixture()
    else:
        payload = body.get("assessment") or body
    actor = get_user(request)
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_ASSESSMENT_EDIT, pid)
        return await _assessment_ingest.ingest(conn, payload, actor=actor, project_id=pid)


@app.get("/api/assessments")
async def assessments_list(request: Request):
    """List assessments in the active project. Requires assessment.view."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_ASSESSMENT_VIEW, pid)
        return {"assessments": await _assessment_ingest.list_assessments(conn, project_id=pid)}


@app.get("/api/assessments/{assessment_id}")
async def assessments_get(assessment_id: str, request: Request):
    """One assessment with findings + gap summary. Requires assessment.view."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_ASSESSMENT_VIEW, pid)
        out = await _assessment_ingest.get_assessment(conn, assessment_id)
    if out is None:
        raise HTTPException(404, "assessment not found")
    return out


# ══════════════ Maturity Wall (#147) — read side (slice 2) ══════════════
async def _framework_structure(conn, fid) -> Optional[dict]:
    """The configurable framework as a nested wall skeleton: bands → categories → capabilities
    (all ordered), plus the appraisal scale + the rendered states. Scores overlay separately."""
    fw = await conn.fetchrow(
        "SELECT id, key, name, version, status, is_seed, scale, project_id FROM assessment_frameworks WHERE id=$1", fid)
    if fw is None:
        return None
    states = await conn.fetch(
        "SELECT key, label, ord, kind FROM framework_states WHERE framework_id=$1 ORDER BY ord", fid)
    cats = await conn.fetch(
        """SELECT id, key, label, band, ord, inflection_side
           FROM framework_categories WHERE framework_id=$1 ORDER BY ord""", fid)
    caps = await conn.fetch(
        """SELECT fc.id, fc.category_id, fc.key, fc.label, fc.ord, fc.catalog_capability_id
           FROM framework_capabilities fc JOIN framework_categories c ON c.id=fc.category_id
           WHERE c.framework_id=$1 ORDER BY fc.ord""", fid)
    caps_by_cat: dict = {}
    for c in caps:
        caps_by_cat.setdefault(c["category_id"], []).append({
            "id": str(c["id"]), "key": c["key"], "label": c["label"], "ord": c["ord"],
            "catalog_capability_id": c["catalog_capability_id"],
        })
    # Preserve band order as first seen across ordered categories.
    bands: list = []
    band_idx: dict = {}
    for c in cats:
        band = c["band"] or ""
        if band not in band_idx:
            band_idx[band] = len(bands)
            bands.append({"band": band, "categories": []})
        bands[band_idx[band]]["categories"].append({
            "id": str(c["id"]), "key": c["key"], "label": c["label"], "ord": c["ord"],
            "inflection_side": c["inflection_side"],
            "capabilities": caps_by_cat.get(c["id"], []),
        })
    scale = fw["scale"]
    if isinstance(scale, str):
        try: scale = json.loads(scale)
        except Exception: scale = []
    return {
        "id": str(fw["id"]), "key": fw["key"], "name": fw["name"], "version": fw["version"],
        "status": fw["status"], "is_seed": fw["is_seed"], "project_id": fw["project_id"],
        "scale": scale,
        "states": [{"key": s["key"], "label": s["label"], "ord": s["ord"], "kind": s["kind"]} for s in states],
        "bands": bands,
    }


async def _findings_wall(conn, assessment_id) -> Optional[dict]:
    """Pre-filled CURRENT-state wall built straight from the assessment's own findings — category
    columns, capability rows, maturity 1–5, notes/evidence as rationale. Reuses MATURITY_SCALE so
    it renders identically to the Assessments detail view. None if the assessment has no findings."""
    rows = await conn.fetch(
        """SELECT id, category, capability_handle, maturity, state, notes, evidence
           FROM assessment_findings WHERE assessment_id=$1
           ORDER BY category NULLS LAST, capability_handle""", assessment_id)
    if not rows:
        return None
    cats_order: list = []
    cap_by_cat: dict = {}
    all_vals: list = []
    for r in rows:
        cat = r["category"] or "Uncategorized"
        if cat not in cap_by_cat:
            cap_by_cat[cat] = []; cats_order.append(cat)
        m = r["maturity"]
        cap_by_cat[cat].append({
            "id": str(r["id"]), "finding_id": str(r["id"]),
            "key": (r["capability_handle"] or "").lower(), "label": r["capability_handle"],
            "maturity": m, "state": r["state"],
            "rationale": (r["notes"] or r["evidence"] or ""), "source": "finding",
        })
        if m is not None:
            all_vals.append(m)
    categories: list = []
    for cat in cats_order:
        vals = [c["maturity"] for c in cap_by_cat[cat] if c["maturity"] is not None]
        categories.append({
            "id": None, "key": cat, "label": cat, "band": "", "inflection_side": "pre",
            "capabilities": cap_by_cat[cat],
            "rollup": round(sum(vals) / len(vals), 1) if vals else None, "assessed": len(vals),
        })
    return {
        "id": None, "key": "from-findings", "name": "From assessment findings",
        "derived_from": "findings",
        "scale": _assessment_ingest.MATURITY_SCALE,
        "maturity_target": _assessment_ingest.MATURITY_TARGET,
        "states": [{"key": "current", "label": "Current State", "ord": 0, "kind": "current"}],
        "bands": [{"band": "", "categories": categories}],
        "state": "current",
        "overall": round(sum(all_vals) / len(all_vals), 1) if all_vals else None,
        "assessed": len(all_vals),
    }


@app.get("/api/assessment-frameworks")
async def assessment_frameworks_list(request: Request):
    """List maturity frameworks visible to the active project: the global seed templates
    (project_id NULL) + the project's own. Requires assessment.view."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_ASSESSMENT_VIEW, pid)
        rows = await conn.fetch(
            """SELECT f.id, f.key, f.name, f.version, f.status, f.is_seed, f.project_id,
                      (SELECT count(*) FROM framework_categories c WHERE c.framework_id=f.id) AS category_count,
                      (SELECT count(*) FROM framework_capabilities fc JOIN framework_categories c
                         ON c.id=fc.category_id WHERE c.framework_id=f.id) AS capability_count
               FROM assessment_frameworks f
               WHERE f.project_id IS NULL OR f.project_id=$1
               ORDER BY f.is_seed DESC, f.name""", pid)
        return {"frameworks": [dict(r) | {"id": str(r["id"])} for r in rows]}


@app.get("/api/assessment-frameworks/{framework_id}")
async def assessment_framework_get(framework_id: str, request: Request):
    """One framework's full wall skeleton (bands → categories → capabilities + scale + states)."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_ASSESSMENT_VIEW, pid)
        out = await _framework_structure(conn, framework_id)
    if out is None:
        raise HTTPException(404, "framework not found")
    return out


@app.get("/api/assessments/{assessment_id}/maturity-wall")
async def assessment_maturity_wall(assessment_id: str, request: Request, state: str = "current"):
    """The maturity wall for an assessment in one state. CURRENT state is PRE-FILLED straight from
    the assessment's own findings (category/capability/maturity/notes) when present, so it renders
    populated — and identically to the Assessments detail view (same MATURITY_SCALE). Target/desired
    states overlay the configurable framework + per-state scores. Category + overall rollups = mean
    of assessed cells."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_ASSESSMENT_VIEW, pid)
        if state == "current":
            fwall = await _findings_wall(conn, assessment_id)
            if fwall is not None:
                return fwall   # pre-filled from findings (the common case)
        fid = await conn.fetchval(
            "SELECT framework_id FROM assessment_framework_link WHERE assessment_id=$1", assessment_id)
        if fid is None:
            fid = await conn.fetchval(
                "SELECT id FROM assessment_frameworks WHERE key='platform-maturity-v1' AND project_id IS NULL")
        if fid is None:
            raise HTTPException(404, "no framework linked or seeded")
        struct = await _framework_structure(conn, fid)
        scores = await conn.fetch(
            """SELECT framework_capability_id, maturity, rationale, source
               FROM assessment_capability_scores WHERE assessment_id=$1 AND state_key=$2""",
            assessment_id, state)
    score_map = {str(s["framework_capability_id"]):
                 {"maturity": s["maturity"], "rationale": s["rationale"], "source": s["source"]}
                 for s in scores}
    all_vals: list = []
    for band in struct["bands"]:
        for cat in band["categories"]:
            cat_vals: list = []
            for cap in cat["capabilities"]:
                sc = score_map.get(cap["id"])
                cap["maturity"] = sc["maturity"] if sc else None
                cap["rationale"] = sc["rationale"] if sc else None
                cap["source"] = sc["source"] if sc else None
                if cap["maturity"] is not None:
                    cat_vals.append(cap["maturity"]); all_vals.append(cap["maturity"])
            cat["rollup"] = round(sum(cat_vals) / len(cat_vals), 1) if cat_vals else None
            cat["assessed"] = len(cat_vals)
    struct["state"] = state
    struct["overall"] = round(sum(all_vals) / len(all_vals), 1) if all_vals else None
    struct["assessed"] = len(all_vals)
    return struct


# ══════════════ Maturity Wall (#147) — write side (slice 2) ══════════════
# Framework CRUD + LLM scoring + human override. Reads live above (_framework_structure,
# _findings_wall, GET /maturity-wall). All writes are gated by assessment.edit in the
# OWNING project (a seed template, project_id NULL, is read-only — projects clone + edit).
# Scoring reuses the EXISTING model call path (_make_diagnosis_call_fn over a model_configs
# row); human overrides carry provenance (source='human', updated_by, updated_at) so they're
# always distinguishable from LLM scores and survive the next LLM pass. See maturity-scoring logic
# in maturity_scoring.py and docs/maturity-wall-design.md.

async def _gate_framework_edit(conn, request: Request, fid: str) -> int:
    """Resolve a framework's owning project, forbid editing a seed template (project_id NULL),
    and require assessment.edit in that project. Returns the project_id. 404 on missing/unseen."""
    row = await conn.fetchrow(
        "SELECT project_id, is_seed FROM assessment_frameworks WHERE id=$1::uuid", fid)
    if row is None:
        raise HTTPException(404, "framework not found")
    if row["project_id"] is None or row["is_seed"]:
        raise HTTPException(403, "seed templates are read-only — clone into your project to edit")
    await _require_priv_conn(conn, request, rbac.P_ASSESSMENT_EDIT, row["project_id"])
    return row["project_id"]


async def _gate_assessment_edit(conn, request: Request, assessment_id: str) -> int:
    """Resolve an assessment's owning project + require assessment.edit there. 404 if unseen."""
    pid = await conn.fetchval(
        "SELECT project_id FROM assessments WHERE id=$1::uuid", assessment_id)
    if pid is None:
        # An assessment may legitimately have a NULL project_id (legacy/global); fall back to
        # the active project so the platform admin / project editor can still score it.
        if not await conn.fetchval("SELECT 1 FROM assessments WHERE id=$1::uuid", assessment_id):
            raise HTTPException(404, "assessment not found")
        pid = await _active_project_id(request, conn)
    await _require_priv_conn(conn, request, rbac.P_ASSESSMENT_EDIT, pid)
    return pid


class FrameworkIn(BaseModel):
    name: str
    key: Optional[str] = None
    scale: Optional[list] = None
    status: Optional[str] = None
    clone_from: Optional[str] = None     # a framework id (e.g. a seed) to deep-copy structure from


class FrameworkPatchIn(BaseModel):
    name: Optional[str] = None
    scale: Optional[list] = None
    status: Optional[str] = None


class CategoryIn(BaseModel):
    label: str
    key: Optional[str] = None
    band: Optional[str] = None
    ord: int = 0
    inflection_side: str = "pre"


class CategoryPatchIn(BaseModel):
    label: Optional[str] = None
    band: Optional[str] = None
    ord: Optional[int] = None
    inflection_side: Optional[str] = None


class CapabilityIn(BaseModel):
    label: str
    key: Optional[str] = None
    ord: int = 0
    catalog_capability_id: Optional[int] = None


class CapabilityPatchIn(BaseModel):
    label: Optional[str] = None
    ord: Optional[int] = None
    catalog_capability_id: Optional[int] = None


class StateIn(BaseModel):
    label: str
    key: Optional[str] = None
    ord: int = 0
    kind: str = "target"


class ScoreOverrideIn(BaseModel):
    capability_id: str
    state: str
    maturity: Optional[int] = None       # 0..5, or null = '-' Not Assessed (a deliberate human one)
    rationale: Optional[str] = None


class ScoresPutIn(BaseModel):
    overrides: list[ScoreOverrideIn]


@app.post("/api/assessment-frameworks")
async def assessment_framework_create(payload: FrameworkIn, request: Request):
    """Create a PROJECT-scoped maturity framework. Pass `clone_from` (a seed/template framework id)
    to deep-copy its scale + states + categories + capabilities into an editable project copy
    (reuse-first; the platform-maturity seed is the suggested starting point). Requires assessment.edit."""
    actor = get_user(request)
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        if pid is None:
            raise HTTPException(400, "select a project before creating a framework")
        await _require_priv_conn(conn, request, rbac.P_ASSESSMENT_EDIT, pid)
        try:
            return await _maturity_scoring.create_framework(
                conn, project_id=pid, name=payload.name, key=payload.key, scale=payload.scale,
                status=payload.status or "active", created_by=actor, clone_from=payload.clone_from)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, "a framework with that key already exists in this project")


@app.put("/api/assessment-frameworks/{framework_id}")
async def assessment_framework_update(framework_id: str, payload: FrameworkPatchIn, request: Request):
    """Edit a project framework's name / scale / status. Requires assessment.edit (owning project)."""
    async with pool.acquire() as conn:
        await _gate_framework_edit(conn, request, framework_id)
        await _maturity_scoring.update_framework(
            conn, framework_id, name=payload.name, scale=payload.scale, status=payload.status)
        return await _framework_structure(conn, framework_id)


@app.delete("/api/assessment-frameworks/{framework_id}")
async def assessment_framework_delete(framework_id: str, request: Request):
    """Delete a project framework (categories/capabilities/states cascade). Seeds are protected."""
    async with pool.acquire() as conn:
        await _gate_framework_edit(conn, request, framework_id)
        await conn.execute("DELETE FROM assessment_frameworks WHERE id=$1::uuid", framework_id)
    return {"ok": True}


@app.post("/api/assessment-frameworks/{framework_id}/categories")
async def assessment_category_create(framework_id: str, payload: CategoryIn, request: Request):
    async with pool.acquire() as conn:
        await _gate_framework_edit(conn, request, framework_id)
        try:
            return await _maturity_scoring.add_category(
                conn, framework_id, label=payload.label, key=payload.key, band=payload.band,
                ord=payload.ord, inflection_side=payload.inflection_side)
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, "a category with that key already exists in this framework")


@app.put("/api/assessment-frameworks/{framework_id}/categories/{category_id}")
async def assessment_category_update(framework_id: str, category_id: str,
                                     payload: CategoryPatchIn, request: Request):
    async with pool.acquire() as conn:
        await _gate_framework_edit(conn, request, framework_id)
        await _maturity_scoring.update_category(
            conn, category_id, label=payload.label, band=payload.band, ord=payload.ord,
            inflection_side=payload.inflection_side)
    return {"ok": True}


@app.delete("/api/assessment-frameworks/{framework_id}/categories/{category_id}")
async def assessment_category_delete(framework_id: str, category_id: str, request: Request):
    async with pool.acquire() as conn:
        await _gate_framework_edit(conn, request, framework_id)
        await conn.execute(
            "DELETE FROM framework_categories WHERE id=$1::uuid AND framework_id=$2::uuid",
            category_id, framework_id)
    return {"ok": True}


@app.post("/api/assessment-frameworks/{framework_id}/categories/{category_id}/capabilities")
async def assessment_capability_create(framework_id: str, category_id: str,
                                       payload: CapabilityIn, request: Request):
    async with pool.acquire() as conn:
        await _gate_framework_edit(conn, request, framework_id)
        # The category must belong to this framework (path integrity).
        if not await conn.fetchval(
                "SELECT 1 FROM framework_categories WHERE id=$1::uuid AND framework_id=$2::uuid",
                category_id, framework_id):
            raise HTTPException(404, "category not found in this framework")
        try:
            return await _maturity_scoring.add_capability(
                conn, category_id, label=payload.label, key=payload.key, ord=payload.ord,
                catalog_capability_id=payload.catalog_capability_id)
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, "a capability with that key already exists in this category")


@app.put("/api/assessment-frameworks/{framework_id}/capabilities/{capability_id}")
async def assessment_capability_update(framework_id: str, capability_id: str,
                                       payload: CapabilityPatchIn, request: Request):
    async with pool.acquire() as conn:
        await _gate_framework_edit(conn, request, framework_id)
        await _maturity_scoring.update_capability(
            conn, capability_id, label=payload.label, ord=payload.ord,
            catalog_capability_id=payload.catalog_capability_id)
    return {"ok": True}


@app.delete("/api/assessment-frameworks/{framework_id}/capabilities/{capability_id}")
async def assessment_capability_delete(framework_id: str, capability_id: str, request: Request):
    async with pool.acquire() as conn:
        await _gate_framework_edit(conn, request, framework_id)
        await conn.execute(
            """DELETE FROM framework_capabilities fc USING framework_categories c
               WHERE fc.id=$1::uuid AND fc.category_id=c.id AND c.framework_id=$2::uuid""",
            capability_id, framework_id)
    return {"ok": True}


@app.post("/api/assessment-frameworks/{framework_id}/states")
async def assessment_state_create(framework_id: str, payload: StateIn, request: Request):
    async with pool.acquire() as conn:
        await _gate_framework_edit(conn, request, framework_id)
        return await _maturity_scoring.add_state(
            conn, framework_id, label=payload.label, key=payload.key, ord=payload.ord,
            kind=payload.kind)


@app.delete("/api/assessment-frameworks/{framework_id}/states/{state_key}")
async def assessment_state_delete(framework_id: str, state_key: str, request: Request):
    async with pool.acquire() as conn:
        await _gate_framework_edit(conn, request, framework_id)
        await conn.execute(
            "DELETE FROM framework_states WHERE framework_id=$1::uuid AND key=$2",
            framework_id, state_key)
    return {"ok": True}


async def _assessment_framework_id(conn, assessment_id: str, pid: int) -> Optional[str]:
    """The framework an assessment is scored against: its explicit link, else the project's
    own platform-maturity framework, else the global seed template. None if nothing seeded."""
    fid = await conn.fetchval(
        "SELECT framework_id FROM assessment_framework_link WHERE assessment_id=$1::uuid", assessment_id)
    if fid is None and pid is not None:
        fid = await conn.fetchval(
            "SELECT id FROM assessment_frameworks WHERE key='platform-maturity-v1' AND project_id=$1", pid)
    if fid is None:
        fid = await conn.fetchval(
            "SELECT id FROM assessment_frameworks WHERE key='platform-maturity-v1' AND project_id IS NULL")
    return str(fid) if fid is not None else None


@app.post("/api/assessments/{assessment_id}/score")
async def assessment_score(assessment_id: str, request: Request):
    """LLM scoring pass (#147 slice 2): read the assessment's findings + the linked framework, ask
    the project's model (the SAME call path arch-review / assessment-ingest use — resolved via the
    assessment-ingest → arch-review → evaluation default chain) to propose 0..5 scores for each
    capability in each TARGET state, and persist as source='llm'. Human-curated cells are NEVER
    clobbered (curated scores are the truth). Requires assessment.edit."""
    actor = get_user(request)
    async with pool.acquire() as conn:
        pid = await _gate_assessment_edit(conn, request, assessment_id)
        fid = await _assessment_framework_id(conn, assessment_id, pid)
        if fid is None:
            raise HTTPException(404, "no maturity framework linked or seeded to score against")
        struct = await _framework_structure(conn, fid)
        findings = [dict(r) for r in await conn.fetch(
            """SELECT capability_handle, category, maturity, state, notes, evidence
               FROM assessment_findings WHERE assessment_id=$1::uuid""", assessment_id)]
        if not findings:
            raise HTTPException(422, "assessment has no findings to score from")
        cfg = await _model_default_row(
            conn, "assessment-ingest", "arch-review", "evaluation", project_id=pid)
    if not cfg:
        raise HTTPException(400, "no assessment-ingest / arch-review / evaluation model is configured for this project")
    states = struct.get("states", [])
    valid_cap_ids = {cap["id"] for b in struct["bands"] for c in b["categories"] for cap in c["capabilities"]}
    valid_states = {s["key"] for s in states if s.get("kind") in ("target", "desired")}
    if not valid_states:
        raise HTTPException(422, "the framework defines no target/desired states to score")
    system, user = _maturity_scoring.build_scoring_prompt(struct, findings, states)
    try:
        raw = await _make_diagnosis_call_fn(cfg)(system, user)
    except Exception as e:
        raise HTTPException(502, f"scoring model call failed: {e}")
    try:
        scored = _maturity_scoring.parse_scoring_response(raw, valid_cap_ids, valid_states)
    except ValueError as e:
        raise HTTPException(422, str(e))
    async with pool.acquire() as conn:
        written = await _maturity_scoring.persist_llm_scores(
            conn, assessment_id, scored, updated_by=actor)
    return {"ok": True, "framework_id": fid, "model": cfg.get("model_id"),
            "proposed": len(scored), "written": written,
            "skipped_human": len(scored) - written}


@app.put("/api/assessments/{assessment_id}/scores")
async def assessment_scores_override(assessment_id: str, payload: ScoresPutIn, request: Request):
    """Human override of any wall cell(s). Persists with provenance (source='human', updated_by,
    updated_at) so a human score is always distinguishable from an LLM one and survives the next
    LLM pass. maturity=null clears a cell to '-' Not Assessed (a deliberate human 'not assessed').
    Requires assessment.edit."""
    actor = get_user(request)
    overrides = [o.model_dump() for o in payload.overrides]
    async with pool.acquire() as conn:
        await _gate_assessment_edit(conn, request, assessment_id)
        n = await _maturity_scoring.apply_overrides(
            conn, assessment_id, overrides, updated_by=actor)
    return {"ok": True, "updated": n}


def _strip_code_fences(s: str) -> str:
    """Strip a ```...``` (optionally ```json) wrapper a model may add despite instructions."""
    s = (s or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].rstrip()
    return s.strip()


class AssessmentModelIngestIn(BaseModel):
    content: str                       # the raw assessment artifact (text)
    handle: Optional[str] = None
    assessment_type: Optional[str] = None
    pillar: Optional[str] = None
    source: Optional[str] = None
    model_config_id: Optional[int] = None  # #105: override the project assessment-ingest default per ingest


def _extract_assessment_text(filename: str, data: bytes) -> str:
    """Turn an uploaded assessment artifact into text for the extractor model (#105).
    Text/structured (txt/md/csv/json/yaml/log) is decoded as-is; PDF is text-extracted via
    pypdf; images aren't supported yet (need a vision model — a #105 follow-up)."""
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        try:
            import pypdf
        except ImportError:
            raise HTTPException(400, "PDF ingestion requires the pypdf library (not installed)")
        try:
            reader = pypdf.PdfReader(io.BytesIO(data))
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as e:
            raise HTTPException(422, f"could not read the PDF: {e}")
        if not text.strip():
            raise HTTPException(422, "no extractable text in the PDF (a scanned image? image ingestion is a #105 follow-up)")
        return text
    if name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff")):
        raise HTTPException(400, "image ingestion isn't supported yet (needs a vision model — #105 follow-up); use text / structured / PDF")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def _extract_assessment_images(filename: str, data: bytes, max_pages: int = 25) -> list:
    """#113: turn an uploaded image or (image-based) PDF into base64 PNG/JPEG data URLs for a
    vision model. PDFs are rendered page→PNG via PyMuPDF (no poppler dep); images pass through.
    Pages are capped to bound the vision payload (a slide deck can be 50+ pages)."""
    import base64
    name = (filename or "").lower()
    if name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
        ext = name.rsplit(".", 1)[-1]
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        return [f"data:{mime};base64,{base64.b64encode(data).decode()}"]
    if name.endswith(".pdf"):
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise HTTPException(400, "image/PDF vision ingestion requires PyMuPDF (not installed)")
        try:
            doc = fitz.open(stream=data, filetype="pdf")
        except Exception as e:
            raise HTTPException(422, f"could not open the PDF: {e}")
        out: list = []
        mat = fitz.Matrix(1.4, 1.4)   # ~100 DPI — legible slides, bounded vision-token cost
        for i in range(min(len(doc), max_pages)):
            png = doc[i].get_pixmap(matrix=mat, alpha=False).tobytes("png")
            out.append(f"data:image/png;base64,{base64.b64encode(png).decode()}")
        n_total = len(doc)
        doc.close()
        if not out:
            raise HTTPException(422, "no pages rendered from the PDF")
        if n_total > max_pages:
            log.info("vision ingest: PDF has %s pages, capped to %s", n_total, max_pages)
        return out
    raise HTTPException(400, f"unsupported file type for vision ingestion: {filename}")


def _make_vision_call_fn(cfg: dict, max_tokens: int = 4096):
    """OpenAI-compatible vision call: call_fn(system, user_text, images)->text. images are data
    URLs sent as image_url content (vLLM qwen-vl). Mirrors _make_diagnosis_call_fn's OpenAI path.
    (Anthropic image blocks use a different shape — a follow-up; the homelab vision model is vLLM.)"""
    endpoint = (cfg.get("endpoint_url") or "").rstrip("/")
    model_id = cfg.get("model_id")
    api_key = cfg.get("api_key") or ""

    async def call_fn(system: str, user_text: str, images: list) -> str:
        base = endpoint[:-3] if endpoint.endswith("/v1") else endpoint
        content = [{"type": "text", "text": user_text}] \
            + [{"type": "image_url", "image_url": {"url": u}} for u in images]
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient(timeout=300.0) as cx:
            r = await cx.post(
                f"{base}/v1/chat/completions", headers=headers,
                json={"model": model_id, "max_tokens": max_tokens, "temperature": 0.1,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": content}]})
            if r.status_code >= 400:
                # Surface the model's ACTUAL reason (vLLM puts it in the body) instead of a bare
                # status — e.g. too many images/prompt, context length exceeded, model not loaded.
                body = (r.text or "").strip()[:500]
                raise RuntimeError(
                    f"vision model '{model_id}' returned HTTP {r.status_code} for {len(images)} "
                    f"image(s): {body}")
            return r.json()["choices"][0]["message"]["content"] or ""

    return call_fn


async def _vision_extract_batched(cfg, system, user, images, batch_pages=2):
    """Call the vision model in small page-batches — the model's context is tight (e.g. qwen2.5-vl
    at 8192 tokens, and each page-image costs ~1-2k vision tokens), so a whole deck can't go in one
    request. Each batch extracts a UDLM-assessment JSON; findings are merged across batches and
    deduped by (category, capability), keeping the highest maturity. Returns the merged dict.
    A bigger model context (--max-model-len) → larger batch_pages → fewer calls."""
    vcall = _make_vision_call_fn(cfg, max_tokens=2048)   # leave input room within an 8k window
    batches = [images[i:i + batch_pages] for i in range(0, len(images), batch_pages)]
    merged = None
    by_key: dict = {}
    last_err = None
    ok = 0
    for bi, batch in enumerate(batches):
        bmsg = user + (f"\n\n(Page batch {bi + 1} of {len(batches)} — extract every capability "
                       "visible in these page(s); omit nothing you can read.)")
        try:
            raw = await vcall(system, bmsg, batch)
            obj = json.loads(_strip_code_fences(raw))
            if not isinstance(obj, dict):
                raise ValueError("not a JSON object")
        except Exception as e:
            last_err = e
            log.warning("vision ingest batch %s/%s failed: %s", bi + 1, len(batches), e)
            continue
        ok += 1
        if merged is None:
            merged = {k: v for k, v in obj.items() if k != "findings"}
        for f in (obj.get("findings") or []):
            cap = (f.get("capability") or f.get("capability_handle") or "").strip()
            if not cap:
                continue
            key = ((f.get("category") or "").strip().lower(), cap.lower())
            cur = by_key.get(key)
            if cur is None or (f.get("maturity") or -1) > (cur.get("maturity") or -1):
                by_key[key] = f
    if merged is None:
        raise HTTPException(502, f"vision extraction failed for all {len(batches)} page batch(es): {last_err}")
    merged["findings"] = list(by_key.values())
    log.info("vision ingest: %s/%s batches ok, %s findings merged", ok, len(batches), len(by_key))
    return merged


async def _extract_and_ingest_assessment(request: Request, content: str, *, model_config_id=None,
                                         handle=None, assessment_type=None, pillar=None, source=None,
                                         images=None):
    """Shared core for model-based assessment ingestion (#105 text, #113 vision): resolve the
    extractor model (the per-ingest override if given, else the project's assessment-ingest default
    — scope-aware), emit UDLM-conformant structured output, and store via the normal pipeline.
    `images` (a list of data URLs) routes through a vision model instead of the text artifact."""
    content = (content or "").strip()
    if not content and not images:
        raise HTTPException(400, "content (the assessment artifact text) or images is required")
    actor = get_user(request)
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_ASSESSMENT_EDIT, pid)
        if model_config_id:
            row = await conn.fetchrow(
                "SELECT * FROM model_configs WHERE id=$1 AND (project_id IS NULL OR project_id=$2) AND enabled",
                model_config_id, pid)
            cfg = dict(row) if row else None
        else:
            cfg = await _model_default_row(
                conn, "assessment-ingest", "uc-authoring", "arch-review", "evaluation", project_id=pid)
        _actx = await _stage_context(conn, "assessment-ingest", pid)   # #125 prompt management
    if not cfg:
        raise HTTPException(400, "the selected extractor model is unavailable, and no assessment-ingest / authoring / evaluation model default is configured for this project")
    schema = _assessment_ingest.structured_output_schema()
    system = ("You are an assessment-extraction engine. Read the assessment artifact and emit "
              "ONLY a single JSON object conforming to the UDLM Knowledge-family Assessment "
              "contract below — no prose, no markdown code fences, no surrounding text.\n\n"
              "UDLM Assessment contract:\n" + json.dumps(schema, indent=2))
    _extract_instr = (
        "Extract every capability that was assessed into a finding. Capture its category, "
        "state ('n/a' for not-asked / not-applicable), maturity 1-5 (3 = target; null when there "
        "is no capability), and any notes/evidence. Output the JSON object only.")
    if images:
        user = ("The assessment artifact is the attached image"
                + (f"s ({len(images)} pages/slides)" if len(images) > 1 else "")
                + ". Read it/them — including any maturity 'wall' grids, scores, focus areas, "
                  "findings, and recommendations.\n"
                + (f"assessment_type hint: {assessment_type}\n" if assessment_type else "")
                + (f"handle hint: {handle}\n" if handle else "")
                + _extract_instr)
    else:
        user = (f"Assessment artifact:\n\n{content}\n\n"
                + (f"assessment_type hint: {assessment_type}\n" if assessment_type else "")
                + (f"handle hint: {handle}\n" if handle else "")
                + _extract_instr)
    user = _inject_context(user, _actx, "assessment ingestion")   # #125 prompt management (append-live)
    if images:
        # Vision: batched across pages (tight model context) + merged. Raises HTTPException itself.
        extracted = await _vision_extract_batched(cfg, system, user, images)
    else:
        try:
            raw = await _make_diagnosis_call_fn(cfg)(system, user)
        except Exception as e:
            raise HTTPException(502, f"extraction model call failed: {e}")
        try:
            extracted = json.loads(_strip_code_fences(raw))
            if not isinstance(extracted, dict):
                raise ValueError("not a JSON object")
        except Exception as e:
            raise HTTPException(422, f"extractor did not return valid UDLM-assessment JSON: {e}")
    for k, v in (("handle", handle), ("assessment_type", assessment_type), ("pillar", pillar), ("source", source)):
        if v and not extracted.get(k):
            extracted[k] = v
    extracted.setdefault("source", source or f"model-extract:{cfg.get('model_id')}")
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        result = await _assessment_ingest.ingest(conn, extracted, actor=actor, project_id=pid)
    result["model"] = cfg.get("model_id")
    result["udlm_version"] = schema["udlm_version"]
    result["via"] = "vision" if images else "text"      # so the UI can show which path ran
    if images:
        result["pages"] = len(images)
    return result


@app.post("/api/assessments/ingest-model")
async def assessments_ingest_model(payload: AssessmentModelIngestIn, request: Request):
    """Model-based assessment ingestion from pasted text: an extractor model emits STRUCTURED
    OUTPUT conforming to the UDLM Assessment/Finding contract, stored via the normal pipeline.
    Uses the per-ingest model override (model_config_id) or the project's assessment-ingest
    default (→ uc-authoring → arch-review → evaluation). Requires assessment.edit."""
    return await _extract_and_ingest_assessment(
        request, payload.content, model_config_id=payload.model_config_id,
        handle=payload.handle, assessment_type=payload.assessment_type,
        pillar=payload.pillar, source=payload.source)


@app.post("/api/assessments/ingest-file")
async def assessments_ingest_file(
    request: Request,
    file: UploadFile = File(...),
    model_config_id: Optional[int] = Form(None),
    assessment_type: Optional[str] = Form(None),
    handle: Optional[str] = Form(None),
    pillar: Optional[str] = Form(None),
    source: Optional[str] = Form(None),
    vision: bool = Form(False),
):
    """#105/#113: multi-format model ingestion. Text/structured (txt/md/csv/json/yaml/log) +
    text-PDF are text-extracted; **images and image/slide-deck PDFs go through a vision model**
    (`vision=true`, or auto when an image is uploaded / a PDF has no extractable text). The
    selected model must be vision-capable for the image path. Same UDLM pipeline as /ingest-model."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    name = (file.filename or "").lower()
    is_image = name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"))

    async def _vision():
        images = _extract_assessment_images(file.filename or "", data)
        return await _extract_and_ingest_assessment(
            request, "", images=images, model_config_id=model_config_id, handle=handle,
            assessment_type=assessment_type, pillar=pillar,
            source=source or f"vision:{file.filename}")

    if vision or is_image:
        return await _vision()
    # text / structured / text-PDF; an image-only PDF (no extractable text) auto-falls back to vision.
    try:
        text = _extract_assessment_text(file.filename or "", data)
    except HTTPException as e:
        if name.endswith(".pdf") and e.status_code == 422:
            return await _vision()
        raise
    return await _extract_and_ingest_assessment(
        request, text, model_config_id=model_config_id, handle=handle,
        assessment_type=assessment_type, pillar=pillar,
        source=source or f"file:{file.filename}")


# ---------------------------------------------------------------------------
# Self-improvement loop — Phase 1: diagnose & propose.
# failure_taxonomy.py classifies a run's failures into typed signatures;
# diagnose.py turns those into ranked, typed change proposals (rules layer
# encodes this session's fixes; optional LLM second opinion). Proposals are
# review artifacts — nothing is applied here (that is Phase 2).
# See docs/dav-self-improvement-vision.md.
# ---------------------------------------------------------------------------

_PROPOSAL_STATUSES = {"proposed", "accepted", "rejected", "applied", "superseded"}


def _resolve_run_id(id_or_name: str) -> Optional[str]:
    """Accept either a workspace run_id (timestamped results dir) or a Tekton
    PipelineRun name, and return the workspace run_id. Lets the UI pass whichever
    it has (the run drawer has the Tekton name; the queue stores the run_id)."""
    if not _results.is_available():
        return None
    if _results.get_run_summary(id_or_name) is not None:
        return id_or_name  # already a workspace run_id
    try:
        detail = validations.get_run_detail(id_or_name)  # sync helper (_resolve_run_id)
        started = detail.get("started_at") or detail.get("created_at")
        if started:
            # Sync path (no DB conn here) — single-nearest; the count-aware unique correlation runs
            # on the live async endpoints (get_run_detail / turns). _resolve_run_id is mostly used
            # for already-ingested runs, which resolve via get_run_summary above.
            prog = _results.find_progress_near(started, tolerance_seconds=600)
            if prog:
                return prog.get("_run_dir")
    except Exception:
        pass
    return None


async def _resolve_diagnosis_model(conn, project_id: int) -> Optional[dict]:
    """The model to diagnose with (within the run's project): the arch-review
    default, else any enabled arch-review-capable model. Row dict or None."""
    row = await conn.fetchrow(
        """SELECT mc.* FROM model_defaults md
             JOIN model_configs mc ON mc.id = md.model_config_id
            WHERE md.key = 'arch-review' AND mc.enabled
              AND md.project_id = $1 AND mc.project_id = $1""",
        project_id,
    )
    if not row:
        row = await conn.fetchrow(
            "SELECT * FROM model_configs WHERE enabled AND use_arch_review "
            "AND (project_id IS NULL OR project_id=$1) AND use_category IS NULL "
            "ORDER BY (project_id IS NULL), id LIMIT 1",  # scope-aware (#107 2b): project default first, platform fallback
            project_id,
        )
    return dict(row) if row else None


def _make_diagnosis_call_fn(cfg: dict):
    """Build an async `call_fn(system, user) -> text` for diagnose_llm from a
    model_configs row (mirrors uc_assist's non-streaming OpenAI/Anthropic client)."""
    provider = (cfg.get("provider") or "openai").lower()
    endpoint = (cfg.get("endpoint_url") or "").rstrip("/")
    model_id = cfg.get("model_id")
    api_key = cfg.get("api_key") or ""

    async def call_fn(system: str, user: str) -> str:
        async with httpx.AsyncClient(timeout=120.0) as cx:
            if provider == "anthropic":
                headers = {"anthropic-version": "2023-06-01", "content-type": "application/json"}
                if api_key:
                    headers["x-api-key"] = api_key
                r = await cx.post(
                    f"{endpoint}/v1/messages",
                    headers=headers,
                    json={"model": model_id, "max_tokens": 4096, "system": system,
                          "messages": [{"role": "user", "content": user}]},
                )
                r.raise_for_status()
                data = r.json()
                return "".join(b.get("text", "") for b in data.get("content", [])
                               if b.get("type") == "text")
            base = endpoint[:-3] if endpoint.endswith("/v1") else endpoint
            # Local vLLM endpoints have no api_key — an empty `Bearer ` header is
            # an illegal header value, so only send Authorization when keyed.
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            r = await cx.post(
                f"{base}/v1/chat/completions",
                headers=headers,
                json={"model": model_id, "max_tokens": 4096, "temperature": 0.1,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]},
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"] or ""

    return call_fn


class ProposalReviewIn(BaseModel):
    status: str                       # accepted | rejected
    note: Optional[str] = None


async def _store_diagnosis(conn, run_id, run_name, taxonomy, diagnosis, user):
    """Persist a diagnosis batch + its proposals. Returns (batch_id, stored)."""
    import uuid
    batch_id = uuid.uuid4().hex
    await conn.execute(
        """INSERT INTO run_diagnoses
             (batch_id, run_id, run_name, taxonomy, used_llm, rule_count, llm_count, created_by)
           VALUES ($1,$2,$3,$4::jsonb,$5,$6,$7,$8)""",
        batch_id, run_id, run_name, json.dumps(taxonomy),
        diagnosis["used_llm"], diagnosis["rule_count"], diagnosis["llm_count"], user,
    )
    stored = []
    for p in diagnosis["proposals"]:
        pid = await conn.fetchval(
            """INSERT INTO improvement_proposals
                 (batch_id, run_id, run_name, signature_class, kind, target, rationale,
                  proposed_change, predicted_effect, confidence, source, evidence,
                  change_spec, created_by)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::jsonb,$14)
               RETURNING id""",
            batch_id, run_id, run_name, p["signature_class"], p["kind"], p["target"],
            p["rationale"], p["proposed_change"], p["predicted_effect"], p["confidence"],
            p["source"], json.dumps(p.get("evidence") or {}),
            json.dumps(p["change_spec"]) if p.get("change_spec") else None, user,
        )
        stored.append({"id": pid, **p})
    return batch_id, stored


@app.post("/api/self-improve/scan")
async def self_improve_scan(request: Request,
                            limit_runs: int = Query(20, ge=1, le=100),
                            max_diagnose: int = Query(8, ge=1, le=50)):
    """Phase 3 — continual observe+diagnose. Scan recent workspace runs; for any
    that failed and haven't been diagnosed yet, run the rules diagnoser and file
    proposals into the review queue. Rules-only (no LLM) to stay cheap for a
    scheduled call. Idempotent: a run is diagnosed at most once here (operators
    can re-diagnose with the LLM from the Improve tab). Intended for the
    dav-self-improve-scan CronJob; nothing is applied — proposals are filed."""
    user = get_user(request)
    if not _results.is_available():
        raise HTTPException(503, "workspace PVC not mounted")
    scanned = diagnosed = filed = 0
    results = []
    async with pool.acquire() as conn:
        for run in _results.list_runs()[:limit_runs]:
            if diagnosed >= max_diagnose:
                break
            run_id = run.get("run_id")
            if not run_id:
                continue
            scanned += 1
            already = await conn.fetchval(
                "SELECT 1 FROM run_diagnoses WHERE run_id=$1 LIMIT 1", run_id)
            if already:
                continue
            failures = _results.get_failures(run_id)
            if not failures:
                continue                          # only diagnose runs that actually failed
            summary = _results.get_run_summary(run_id)
            taxonomy = _ft.build_taxonomy(summary, failures)
            diagnosis = await _diagnose.diagnose(taxonomy, call_fn=None)   # rules-only
            run_name = await conn.fetchval(
                "SELECT run_name FROM analysis_runs WHERE run_id=$1 LIMIT 1", run_id)
            _bid, stored = await _store_diagnosis(conn, run_id, run_name, taxonomy, diagnosis, user)
            diagnosed += 1
            filed += len(stored)
            results.append({"run_id": run_id, "run_name": run_name,
                            "signatures": [s["signature_class"] for s in taxonomy["signatures"]],
                            "proposals": len(stored)})
    log.info("self-improve scan: scanned=%d diagnosed=%d proposals=%d", scanned, diagnosed, filed)
    return {"ok": True, "scanned": scanned, "diagnosed": diagnosed,
            "proposals_filed": filed, "runs": results}


@app.get("/api/runs/{name}/shallowness")
async def get_run_shallowness_endpoint(name: str):
    """Per-UC shallow-analysis flags for a completed run (advisory grounding
    signal). The failure-driven diagnose loop only sees runs that *failed*; this
    surfaces successful-but-thin analyses — few distinct spec refs, mostly
    ungrounded claims, or a too-early commit. `name` accepts a workspace run_id
    or a Tekton PipelineRun name. See app.shallowness for the scoring.
    """
    if not _results.is_available():
        raise HTTPException(503, "workspace PVC not mounted")
    run_id = _resolve_run_id(name)
    if not run_id:
        raise HTTPException(404, f"no workspace run found for {name!r}")
    data = _results.get_run_shallowness(run_id)
    if data is None:
        raise HTTPException(404, f"run {run_id!r} has no readable summary")
    return data


@app.post("/api/diagnose/{run_id:path}")
async def diagnose_run(run_id: str, request: Request, use_llm: bool = Query(True)):
    """Diagnose a run's failures and file typed improvement proposals.

    Builds the failure taxonomy from the workspace artifacts, runs the rules
    diagnoser (+ optional LLM second opinion), and persists a proposal batch.
    Nothing is applied — proposals are filed for review.
    """
    import uuid
    user = get_user(request)
    if not _results.is_available():
        raise HTTPException(503, "workspace PVC not mounted")
    run_id = _resolve_run_id(run_id) or run_id
    summary = _results.get_run_summary(run_id)
    if summary is None:
        raise HTTPException(404, f"run {run_id!r} not found on workspace PVC")
    failures = _results.get_failures(run_id)
    taxonomy = _ft.build_taxonomy(summary, failures)

    async with pool.acquire() as conn:
        meta = await conn.fetchrow(
            "SELECT run_name, project_id FROM analysis_runs WHERE run_id=$1 LIMIT 1", run_id
        )
        run_name = meta["run_name"] if meta else None
        dpid = (meta["project_id"] if meta and meta["project_id"] is not None
                else await _default_project_id(conn))
        await _require_priv_conn(conn, request, rbac.P_PROJECT_ARCHREVIEW_EXECUTE, dpid)
        call_fn = None
        if use_llm:
            cfg = await _resolve_diagnosis_model(conn, dpid)
            if cfg:
                log.info("diagnose_run: LLM second opinion via model '%s' (%s)",
                         cfg.get("name"), cfg.get("model_id"))
                call_fn = _make_diagnosis_call_fn(cfg)
            else:
                log.warning("diagnose_run: use_llm requested but no arch-review "
                            "model resolved — rules-only")

        diagnosis = await _diagnose.diagnose(taxonomy, call_fn=call_fn)
        batch_id, stored = await _store_diagnosis(conn, run_id, run_name, taxonomy, diagnosis, user)

    return {
        "ok": True,
        "batch_id": batch_id,
        "run_id": run_id,
        "run_name": run_name,
        "llm_attempted": diagnosis["llm_attempted"],
        "used_llm": diagnosis["used_llm"],
        "taxonomy": taxonomy,
        "proposals": stored,
    }


@app.get("/api/diagnose/{run_id:path}")
async def get_run_diagnosis(run_id: str):
    """Return the latest stored diagnosis (taxonomy + proposals) for a run."""
    run_id = _resolve_run_id(run_id) or run_id
    async with pool.acquire() as conn:
        batch = await conn.fetchrow(
            """SELECT * FROM run_diagnoses WHERE run_id=$1 ORDER BY created_at DESC LIMIT 1""",
            run_id,
        )
        if not batch:
            return {"run_id": run_id, "diagnosed": False, "proposals": []}
        props = await conn.fetch(
            """SELECT * FROM improvement_proposals WHERE batch_id=$1
               ORDER BY (CASE confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END),
                        (CASE source WHEN 'rule' THEN 0 ELSE 1 END), id""",
            batch["batch_id"],
        )
    return {
        "run_id": run_id,
        "diagnosed": True,
        "batch_id": batch["batch_id"],
        "created_at": batch["created_at"].isoformat(),
        "used_llm": batch["used_llm"],
        "taxonomy": _parse_jsonb(batch["taxonomy"]),
        "proposals": [
            {**{k: r[k] for k in ("id", "signature_class", "kind", "target", "rationale",
                                  "proposed_change", "predicted_effect", "confidence",
                                  "source", "status", "reviewed_by", "review_note")},
             "evidence": _parse_jsonb(r["evidence"]),
             "change_spec": _parse_jsonb(r["change_spec"])}
            for r in props
        ],
    }


@app.get("/api/improvement-proposals")
async def list_improvement_proposals(
    status: Optional[str] = None,
    kind: Optional[str] = None,
    run_id: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
):
    """Cross-run review queue of filed proposals, newest first."""
    clauses, args = [], []
    for col, val in (("status", status), ("kind", kind), ("run_id", run_id)):
        if val:
            args.append(val)
            clauses.append(f"ip.{col} = ${len(args)}")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    args.append(limit)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT ip.id, ip.run_id, ip.run_name, ip.signature_class, ip.kind,
                       ip.target, ip.rationale, ip.proposed_change,
                       ip.predicted_effect, ip.confidence, ip.source, ip.status,
                       ip.created_at, ip.created_by, ip.reviewed_by, ip.reviewed_at,
                       ip.review_note, ip.change_spec,
                       rs.name AS session_name
                  FROM improvement_proposals ip
                  LEFT JOIN run_sessions rs ON rs.run_name = ip.run_name{where}
                 ORDER BY ip.created_at DESC LIMIT ${len(args)}""",
            *args,
        )
    out = []
    for r in rows:
        d = dict(r)
        for k in ("created_at", "reviewed_at"):
            if d.get(k):
                d[k] = d[k].isoformat()
        d["change_spec"] = _parse_jsonb(d.get("change_spec"))
        out.append(d)
    return {"proposals": out, "count": len(out)}


async def _audit_proposal_action(action: str, pid: int, actor: str, *,
                                 detail: Optional[dict] = None) -> None:
    """Record a proposal lifecycle action (accept/reject/apply) to the audit log
    as object_type='improvement_proposal'. This is what powers the proposal's
    Activity timeline — every action, by whom, and when. Fire-and-forget."""
    await audit.record(
        pool, action=action, actor=actor, actor_source="session",
        object_type="improvement_proposal", object_id=str(pid),
        detail=detail or {}, summary=f"{action.split('.')[-1]} proposal {pid}")


@app.post("/api/improvement-proposals/{pid}/review")
async def review_improvement_proposal(pid: int, payload: ProposalReviewIn, request: Request):
    """Accept or reject a proposal (Phase 1 review only — does NOT apply it)."""
    user = get_user(request)
    status = (payload.status or "").lower()
    if status not in {"accepted", "rejected"}:
        raise HTTPException(400, "status must be 'accepted' or 'rejected'")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE improvement_proposals
                  SET status=$1, reviewed_by=$2, reviewed_at=now(), review_note=$3
                WHERE id=$4 RETURNING id""",
            status, user, payload.note, pid,
        )
    if not row:
        raise HTTPException(404, "proposal not found")
    await _audit_proposal_action(f"proposal.{status}", pid, user,
                                 detail={"note": payload.note} if payload.note else {})
    return {"ok": True, "id": pid, "status": status, "reviewed_by": user}


@app.get("/api/improvement-proposals/{pid}/activity")
async def improvement_proposal_activity(pid: int, request: Request):
    """The proposal's full lifecycle: when it was proposed (and by which source)
    plus every action taken since (accept/reject/apply), oldest first. Backed by
    the audit log so the trail survives status overwrites."""
    async with pool.acquire() as conn:
        prow = await conn.fetchrow(
            "SELECT created_at, created_by, source, status, reviewed_at, reviewed_by, "
            "review_note FROM improvement_proposals WHERE id=$1", pid)
        if not prow:
            raise HTTPException(404, "proposal not found")
        events = await conn.fetch(
            "SELECT ts, actor, action, detail FROM audit_log "
            "WHERE object_type='improvement_proposal' AND object_id=$1 ORDER BY ts ASC, id ASC",
            str(pid))
    timeline = [{
        "action": "proposed",
        "actor": prow["created_by"] or (f"diagnosis ({prow['source']})" if prow["source"] else "diagnosis"),
        "at": prow["created_at"].isoformat() if prow["created_at"] else None,
        "detail": {},
    }]
    for e in events:
        timeline.append({
            "action": e["action"], "actor": e["actor"],
            "at": e["ts"].isoformat() if e["ts"] else None,
            "detail": _parse_jsonb(e["detail"]),
        })
    # Backfill the terminal action for proposals reviewed BEFORE audit logging
    # existed (the row's reviewed_* snapshot) — only if no audit event already
    # recorded that action, so newly-reviewed proposals aren't double-listed.
    status = prow["status"]
    if status in ("accepted", "rejected", "applied") and prow["reviewed_at"]:
        if not any(e["action"] == f"proposal.{status}" for e in events):
            timeline.append({
                "action": f"proposal.{status}",
                "actor": prow["reviewed_by"],
                "at": prow["reviewed_at"].isoformat(),
                "detail": {"note": prow["review_note"]} if prow["review_note"] else {},
            })
    return {"id": pid, "status": status, "activity": timeline}


# ---------------------------------------------------------------------------
# Self-improvement loop — Phase 2: A/B candidate experiments.
# Trigger a baseline + candidate run over the same eval set (candidate differs
# by one config delta, e.g. max_tokens via a per-run PipelineRun param — no
# production/profile mutation, so spamllm + the r9700-llm route are untouched),
# then experiment_eval.gate() scores both and decides promote/revert/inconclusive.
# Promotion of a max_tokens change is human-gated (its production home is the
# dav_stage2_max_tokens deploy var) — the A/B PROOF is automated, the apply is
# instructed. See docs/dav-self-improvement-vision.md §3.
# ---------------------------------------------------------------------------

class ExperimentIn(BaseModel):
    proposal_id: Optional[int] = None
    # {type:'max_tokens', candidate:<int>} OR {type:'sampling', param, candidate}
    # OR {type:'grounding_nudge'} (#45b — candidate flips the nudge on; baseline off)
    change_spec: dict
    # Optional: give the BASELINE arm its own per-run override too (head-to-head
    # of two configs instead of candidate-vs-production). Same shape as
    # change_spec. Both arms stay per-run isolated — production is never touched
    # during the runs regardless.
    baseline_change_spec: Optional[dict] = None
    set_id: Optional[int] = None
    managed_uc_uuids: Optional[list[str]] = None
    sample_count: int = 1
    title: Optional[str] = None
    # Only honored for runtime-applyable (sampling) changes: auto-write the
    # winning profile on a 'promote' verdict (reversible). Default off.
    auto_promote: bool = False


class StaticCompareIn(BaseModel):
    """Static A/B: semantically compare two EXISTING runs' analyses (no new runs)."""
    run_a: str
    run_b: str
    set_id: Optional[int] = None
    managed_uc_uuids: Optional[list[str]] = None
    title: Optional[str] = None


async def _eval_model_config(conn, project_id: int):
    """The (id, model_id, endpoint, use_key) for the project's evaluation default."""
    m = await conn.fetchrow(
        """SELECT mc.id, mc.model_id, mc.endpoint_url FROM model_defaults md
             JOIN model_configs mc ON mc.id = md.model_config_id
            WHERE md.key='evaluation' AND mc.enabled
              AND md.project_id=$1 AND mc.project_id=$1""", project_id)
    if not m:
        return None
    return {"id": m["id"], "model_id": m["model_id"], "endpoint": m["endpoint_url"],
            "use_key": "evaluation_verification"}


async def _current_profile(conn, model_config_id, use_key) -> Optional[dict]:
    """The production sampling profile params dict for (model, use_key), or None
    if no row exists (engine defaults apply)."""
    prof = await conn.fetchrow(
        "SELECT params FROM model_use_profiles WHERE model_config_id=$1 AND use_key=$2",
        model_config_id, use_key)
    if not prof or not prof["params"]:
        return None
    prm = prof["params"]
    return prm if isinstance(prm, dict) else json.loads(prm)


async def _trigger_eval_run(conn, *, mc, managed_uuids, sample_count,
                            max_tokens, reviewer, profile_override=None,
                            grounding_nudge=None, stage2_context=None):
    """Trigger one arm of an experiment. `mc` is _eval_model_config().

    max_tokens=None → production task default (baseline arm). profile_override
    (e.g. {'temperature': 0.1}) is MERGED onto the production profile and passed
    as the candidate's per-run use-profile-json — isolated, no DB mutation, so
    production + spamllm are untouched."""
    caps_json = None
    caps_row = await conn.fetchrow(
        "SELECT capabilities FROM model_configs WHERE id=$1", mc["id"])
    if caps_row and caps_row["capabilities"]:
        c = caps_row["capabilities"]
        caps_json = c if isinstance(c, str) else json.dumps(c)
    profile = (await _current_profile(conn, mc["id"], mc["use_key"])) or {}
    if profile_override:
        profile = {**profile, **profile_override}
    use_profile_json = json.dumps(profile) if profile else None
    result = await asyncio.to_thread(validations.trigger_run,
        triggered_by=reviewer, inference_endpoint=mc["endpoint"],
        inference_model=mc["model_id"], mode="verification",
        sample_count=sample_count, managed_uc_uuids=managed_uuids,
        use_key=mc["use_key"], capabilities_json=caps_json,
        use_profile_json=use_profile_json, max_tokens=max_tokens, halt_on_error=False,
        grounding_nudge=grounding_nudge, stage2_context=stage2_context,
    )
    return result["name"]


_SAMPLING_PARAMS = {"temperature", "top_k", "top_p", "min_p"}


def _score_experiment_arm(run_name: str):
    """(_expeval.score_run output, terminal?) for one arm. terminal=False while
    the run is still in flight (no final run-summary.yaml yet)."""
    if not run_name:
        return None, False
    run_id = _resolve_run_id(run_name)
    if not run_id:
        return None, False
    summary = _results.get_run_summary(run_id)
    if not summary or not summary.get("finished_at"):
        return None, False
    exploration = _results.get_run_exploration(run_id)
    grounding = _results.get_run_shallowness(run_id)
    return _expeval.score_run(summary, _results.get_failures(run_id),
                              exploration=exploration, grounding=grounding), True


async def _apply_sampling_promotion(conn, exp: dict, user: str) -> dict:
    """Write the winning sampling param into the production model_use_profiles
    (runtime upsert), recording the before-state in change_spec so it's
    reversible. Returns the updated change_spec dict."""
    spec = _parse_jsonb(exp.get("change_spec")) or {}
    mcid, use_key = spec.get("model_config_id"), spec.get("use_key")
    param, value = spec.get("param"), spec.get("candidate")
    cur = (await _current_profile(conn, mcid, use_key)) or {}
    new_params = {**cur, param: value}
    await conn.execute(
        """INSERT INTO model_use_profiles (model_config_id, use_key, params, notes, updated_by, updated_at)
           VALUES ($1,$2,$3::jsonb,$4,$5,NOW())
           ON CONFLICT (model_config_id, use_key) DO UPDATE
             SET params=EXCLUDED.params, notes=EXCLUDED.notes,
                 updated_by=EXCLUDED.updated_by, updated_at=NOW()""",
        mcid, use_key, json.dumps(new_params),
        f"self-improvement experiment #{exp['id']}: {param}→{value}", user)
    spec["applied"] = {"param": param, "to": value, "before_params": cur, "had_row": cur != {}}
    await conn.execute(
        "UPDATE experiments SET change_spec=$1::jsonb, status='promoted', updated_at=now() WHERE id=$2",
        json.dumps(spec), exp["id"])
    if exp.get("proposal_id"):
        await conn.execute(
            "UPDATE improvement_proposals SET status='applied', reviewed_by=$1, reviewed_at=now() WHERE id=$2",
            user, exp["proposal_id"])
        await _audit_proposal_action("proposal.applied", exp["proposal_id"], user,
                                     detail={"via": "experiment", "experiment_id": exp["id"]})
    log.info("experiment %d: promoted sampling %s→%s to production profile", exp["id"], param, value)
    return spec


async def _revert_sampling_promotion(conn, exp: dict) -> None:
    """Restore the production profile to its pre-promotion state."""
    spec = _parse_jsonb(exp.get("change_spec")) or {}
    applied = spec.get("applied") or {}
    mcid, use_key = spec.get("model_config_id"), spec.get("use_key")
    if applied.get("had_row"):
        await conn.execute(
            """UPDATE model_use_profiles SET params=$1::jsonb,
                   notes=$2, updated_at=now() WHERE model_config_id=$3 AND use_key=$4""",
            json.dumps(applied.get("before_params") or {}),
            f"reverted experiment #{exp['id']}", mcid, use_key)
    else:
        # There was no profile row before — remove the one promotion created.
        await conn.execute(
            "DELETE FROM model_use_profiles WHERE model_config_id=$1 AND use_key=$2",
            mcid, use_key)
    await conn.execute(
        "UPDATE experiments SET status='reverted', updated_at=now() WHERE id=$1", exp["id"])
    log.info("experiment %d: reverted sampling promotion", exp["id"])


async def _maybe_score_experiment(conn, exp: dict) -> dict:
    """If both arms have finished, score + gate + persist (and auto-promote a
    runtime-applyable win when opted in). Returns the updated experiment dict."""
    if exp["status"] != "running":
        return exp
    b_score, b_done = _score_experiment_arm(exp["baseline_run"])
    c_score, c_done = _score_experiment_arm(exp["candidate_run"])
    if not (b_done and c_done):
        # If an arm reached a terminal Tekton phase without producing a
        # run-summary (cancelled / crashed), it will never score — fail the
        # experiment rather than leave it stuck "running".
        dead = []
        for arm, done in (("baseline_run", b_done), ("candidate_run", c_done)):
            if done:
                continue
            try:
                phase = (await asyncio.to_thread(validations.get_run_detail, exp[arm])).get("phase")
            except Exception:
                phase = None
            if phase in {"Cancelled", "Failed", "PipelineRunCancelled",
                         "PipelineRunTimeout", "Error", "CouldntGetTask"}:
                dead.append(f"{arm}={phase}")
        if dead:
            reason = f"experiment arm(s) ended without results: {', '.join(dead)}"
            await conn.execute(
                "UPDATE experiments SET status='error', verdict_reason=$1, updated_at=now() WHERE id=$2",
                reason, exp["id"])
            exp = dict(exp); exp.update(status="error", verdict_reason=reason)
        return exp
    g = _expeval.gate(b_score, c_score)
    # Backport: attach the static comparator's semantic diff over both arms' analyses
    # as a result dimension (what actually changed in the output, severity-ranked).
    _spec0 = _parse_jsonb(exp.get("change_spec")) or {}
    _uuids = _spec0.get("eval_uuids") or []
    if _uuids and _analysis_compare.available():
        try:
            c_score = dict(c_score)
            c_score["semantic_diff"] = (await asyncio.to_thread(
                _analysis_compare.compare_runs, exp["baseline_run"], exp["candidate_run"], _uuids))["summary"]
        except Exception:
            log.exception("semantic diff failed for experiment %d", exp["id"])
    await conn.execute(
        """UPDATE experiments SET baseline_score=$1::jsonb, candidate_score=$2::jsonb,
               verdict=$3, verdict_reason=$4, status='scored', updated_at=now() WHERE id=$5""",
        json.dumps(b_score), json.dumps(c_score), g["verdict"], g["reason"], exp["id"],
    )
    exp = dict(exp)
    exp.update(baseline_score=b_score, candidate_score=c_score,
               verdict=g["verdict"], verdict_reason=g["reason"], status="scored")
    log.info("experiment %d scored: %s (%s)", exp["id"], g["verdict"], g["reason"])
    # Auto-promote a runtime-applyable (sampling) win when opted in — the gate
    # guarantees a real improvement with no new high-severity failure class, and
    # the write is reversible. max_tokens never auto-promotes (deploy-var gated).
    if g["verdict"] == "promote" and exp.get("auto_promote"):
        spec = _parse_jsonb(exp.get("change_spec")) or {}
        if spec.get("type") == "sampling":
            try:
                updated = await _apply_sampling_promotion(
                    conn, exp, exp.get("created_by") or "auto-promote")
                exp["status"] = "promoted"
                exp["change_spec"] = json.dumps(updated)
            except Exception:
                log.exception("auto-promote failed for experiment %d", exp["id"])
    return exp


def _exp_out(r) -> dict:
    d = dict(r)
    # Prefer the set's CURRENT name (resolved via the eval_set_id join in the query) over the stored
    # eval_set_name snapshot, so a set rename reflects in experiment listings too. Snapshot remains the
    # fallback for a deleted/synthetic set (no joinable row).
    if d.get("eval_set_live_name"):
        d["eval_set_name"] = d["eval_set_live_name"]
    d.pop("eval_set_live_name", None)
    for k in ("created_at", "updated_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    for k in ("change_spec", "baseline_score", "candidate_score"):
        d[k] = _parse_jsonb(d.get(k))
    return d


@app.post("/api/experiments", status_code=201)
async def create_experiment(payload: ExperimentIn, request: Request):
    user = get_user(request)
    spec = dict(payload.change_spec or {})
    stype = spec.get("type")
    candidate = spec.get("candidate")
    # Validate by type. max_tokens → per-run param (promote is deploy-var,
    # human-gated). sampling → per-run use_profile_json (promote is a runtime,
    # reversible model_use_profiles write → auto-promotable).
    max_tokens_arg = None
    profile_override = None
    grounding_nudge_arg = None
    stage2_context_arg = None   # #93/#125: resolved inside the conn block (needs the project)
    if stype == "max_tokens":
        if not isinstance(candidate, int) or candidate <= 0:
            raise HTTPException(400, "change_spec.candidate must be a positive int (max_tokens)")
        max_tokens_arg = candidate
        title = payload.title or f"max_tokens → {candidate}"
    elif stype == "sampling":
        param = spec.get("param")
        if param not in _SAMPLING_PARAMS:
            raise HTTPException(400, f"change_spec.param must be one of {sorted(_SAMPLING_PARAMS)}")
        if not isinstance(candidate, (int, float)):
            raise HTTPException(400, "change_spec.candidate must be numeric (sampling value)")
        profile_override = {param: candidate}
        title = payload.title or f"{param} → {candidate}"
    elif stype == "grounding_nudge":
        # #45b: candidate flips the OFF-by-default grounding nudge ON via the
        # per-run grounding-nudge param; baseline stays off (production prompt).
        # Promotion is human-gated (its home is the task default), like
        # max_tokens — the A/B PROOF is automated, the apply is instructed.
        grounding_nudge_arg = "1"
        title = payload.title or "grounding nudge → on"
    elif stype == "stage2_context":
        # #93/#125: candidate injects the project's stored Evaluation (stage-2) prompt
        # via the per-run stage2-context param; baseline is the production prompt (none).
        # The content is resolved inside the conn block (it needs the project). Promotion
        # is human-gated (a project flag flips normal runs to inject it), like grounding.
        title = payload.title or "evaluation prompt → on"
    else:
        raise HTTPException(400, "change_spec.type must be 'max_tokens', 'sampling', 'grounding_nudge', or 'stage2_context'")

    async with pool.acquire() as conn:
        exp_pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_RUNS_EXECUTE, exp_pid)
        # Resolve eval UCs.
        uuids = payload.managed_uc_uuids
        set_name = None
        if payload.set_id and not uuids:
            rows = await conn.fetch(
                "SELECT uc_uuid FROM use_case_set_members WHERE set_id=$1", payload.set_id)
            uuids = [r["uc_uuid"] for r in rows]
            sr = await conn.fetchrow("SELECT name FROM use_case_sets WHERE id=$1", payload.set_id)
            set_name = sr["name"] if sr else None
        if not uuids:
            raise HTTPException(400, "provide set_id or managed_uc_uuids")
        spec["eval_uuids"] = uuids  # for the backported semantic-diff dimension

        mc = await _eval_model_config(conn, exp_pid)
        if not mc:
            raise HTTPException(400, "no evaluation default model configured")

        # #93/#125: resolve the project's stored Evaluation (stage-2) prompt for the
        # candidate arm. (Use-category → project resolution lands when prompts gain the
        # category axis; today _stage_context is project-scoped.)
        if stype == "stage2_context":
            stage2_context_arg = await _stage_context(conn, "stage2-analysis", exp_pid)
            if not stage2_context_arg:
                raise HTTPException(400, "no Evaluation prompt set for this project — add one in "
                                         "Prompts → Evaluation, then launch the A/B")
            spec["stage2_context_preview"] = stage2_context_arg[:280]

        # For a sampling change, record where production lives + its current
        # value, so promote can write it and revert can restore it.
        if stype == "sampling":
            cur = (await _current_profile(conn, mc["id"], mc["use_key"])) or {}
            spec["model_config_id"] = mc["id"]
            spec["use_key"] = mc["use_key"]
            spec["baseline"] = cur.get(spec["param"], "engine default")
            spec["had_profile_row"] = cur != {}

        # Optional baseline-arm override (head-to-head of two configs). Both arms
        # stay per-run; production is untouched during the runs.
        b_max_tokens, b_override, b_grounding_nudge = None, None, None
        bspec = payload.baseline_change_spec
        if bspec:
            if bspec.get("type") == "max_tokens":
                b_max_tokens = bspec.get("candidate")
            elif bspec.get("type") == "sampling" and bspec.get("param") in _SAMPLING_PARAMS:
                b_override = {bspec["param"]: bspec.get("candidate")}
            elif bspec.get("type") == "grounding_nudge":
                b_grounding_nudge = "1"
            else:
                raise HTTPException(400, "invalid baseline_change_spec")
            spec["baseline_spec"] = bspec
        # A grounding-nudge experiment's baseline is the production prompt (nudge
        # explicitly OFF) unless the operator gave the baseline its own arm.
        if stype == "grounding_nudge" and b_grounding_nudge is None:
            b_grounding_nudge = "0"

        try:
            baseline_run = await _trigger_eval_run(
                conn, mc=mc, managed_uuids=uuids, sample_count=payload.sample_count,
                max_tokens=b_max_tokens, profile_override=b_override, reviewer=user,
                grounding_nudge=b_grounding_nudge)
            # PipelineRun names are derived from int(time.time())[-6:]; two
            # triggers in the same second collide (409). Space the arms out.
            await asyncio.sleep(1.3)
            candidate_run = await _trigger_eval_run(
                conn, mc=mc, managed_uuids=uuids, sample_count=payload.sample_count,
                max_tokens=max_tokens_arg, profile_override=profile_override, reviewer=user,
                grounding_nudge=grounding_nudge_arg, stage2_context=stage2_context_arg)
        except Exception as e:
            log.exception("experiment launch failed")
            raise HTTPException(500, f"failed to launch experiment runs: {e}")

        row = await conn.fetchrow(
            """INSERT INTO experiments
                 (proposal_id, title, change_spec, eval_set_id, eval_set_name, sample_count,
                  baseline_run, candidate_run, status, auto_promote, created_by)
               VALUES ($1,$2,$3::jsonb,$4,$5,$6,$7,$8,'running',$9,$10) RETURNING *""",
            payload.proposal_id, title, json.dumps(spec), payload.set_id, set_name,
            payload.sample_count, baseline_run, candidate_run, payload.auto_promote, user,
        )
    return _exp_out(row)


@app.get("/api/experiments")
async def list_experiments(limit: int = Query(50, ge=1, le=200)):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT *, (SELECT name FROM use_case_sets WHERE id = experiments.eval_set_id) "
            "AS eval_set_live_name FROM experiments ORDER BY created_at DESC LIMIT $1", limit)
        # Opportunistically finalize any that have completed.
        out = []
        for r in rows:
            out.append(_exp_out(await _maybe_score_experiment(conn, dict(r))))
    return {"experiments": out, "count": len(out)}


@app.post("/api/experiments/static-compare", status_code=201)
async def static_compare(payload: StaticCompareIn, request: Request):
    """Static A/B (backport of compare_analyses): semantically compare two existing
    runs' Stage-2 analyses and record it in the experiments framework — no new runs.
    Server-side: raw analyses stay on the cluster PVC; only the diff is returned."""
    user = get_user(request)
    if not _analysis_compare.available():
        raise HTTPException(503, "analysis comparator unavailable in this build")
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        uuids = list(payload.managed_uc_uuids or [])
        set_name = None
        if payload.set_id and not uuids:
            rows = await conn.fetch(
                "SELECT uc_uuid FROM use_case_set_members WHERE set_id=$1", payload.set_id)
            uuids = [r["uc_uuid"] for r in rows]
            sr = await conn.fetchrow("SELECT name FROM use_case_sets WHERE id=$1", payload.set_id)
            set_name = sr["name"] if sr else None
        if not uuids:
            raise HTTPException(400, "provide set_id or managed_uc_uuids")
        result = await asyncio.to_thread(
            _analysis_compare.compare_runs, payload.run_a, payload.run_b, uuids)
        s = result["summary"]
        title = payload.title or f"static compare: {payload.run_a} ↔ {payload.run_b}"
        reason = (f"{s['changed']} changed / {s['equivalent']} equivalent"
                  + (f" (max {s['max_severity']})" if s.get('max_severity') else "")
                  + (f"; {s['missing']} missing" if s.get('missing') else ""))
        spec = {"type": "static_compare", "eval_uuids": uuids,
                "run_a": payload.run_a, "run_b": payload.run_b}
        row = await conn.fetchrow(
            """INSERT INTO experiments
                 (title, change_spec, eval_set_id, eval_set_name, sample_count,
                  baseline_run, candidate_run, candidate_score, verdict, verdict_reason,
                  status, created_by)
               VALUES ($1,$2::jsonb,$3,$4,0,$5,$6,$7::jsonb,$8,$9,'scored',$10) RETURNING *""",
            title, json.dumps(spec), payload.set_id, set_name, payload.run_a, payload.run_b,
            json.dumps(result), s["verdict"], reason, user,
        )
    return _exp_out(row)


@app.get("/api/experiments/{exp_id}")
async def get_experiment(exp_id: int):
    async with pool.acquire() as conn:
        r = await conn.fetchrow(
            "SELECT *, (SELECT name FROM use_case_sets WHERE id = experiments.eval_set_id) "
            "AS eval_set_live_name FROM experiments WHERE id=$1", exp_id)
        if not r:
            raise HTTPException(404, "experiment not found")
        exp = await _maybe_score_experiment(conn, dict(r))
        # Live arm phases for the UI while running.
        phases = {}
        for arm in ("baseline_run", "candidate_run"):
            try:
                phases[arm] = (await asyncio.to_thread(validations.get_run_detail, exp[arm])).get("phase")
            except Exception:
                phases[arm] = None
    out = _exp_out(exp)
    out["arm_phases"] = phases
    return out


@app.post("/api/experiments/{exp_id}/promote")
async def promote_experiment(exp_id: int, request: Request):
    user = get_user(request)
    async with pool.acquire() as conn:
        r = await conn.fetchrow("SELECT * FROM experiments WHERE id=$1", exp_id)
        if not r:
            raise HTTPException(404, "experiment not found")
        exp = await _maybe_score_experiment(conn, dict(r))
        if exp.get("status") == "promoted":
            return {"ok": True, "id": exp_id, "already_promoted": True}
        if exp.get("verdict") != "promote":
            raise HTTPException(409, f"experiment verdict is {exp.get('verdict')!r}, not 'promote'")
        spec = _parse_jsonb(exp["change_spec"]) or {}
        if spec.get("type") == "sampling":
            # Runtime, reversible: write the production model_use_profiles row.
            updated = await _apply_sampling_promotion(conn, exp, user)
            return {"ok": True, "id": exp_id, "apply_method": "applied (model_use_profiles)",
                    "applied": updated.get("applied"),
                    "note": f"{spec['param']} → {spec['candidate']} is now the production "
                            f"profile for {spec['use_key']}. Reversible via "
                            f"POST /api/experiments/{exp_id}/revert."}
        # grounding_nudge & max_tokens both live in the Tekton task defaults, so
        # production apply is human-gated (the A/B PROOF is automated; the apply
        # is instructed).
        if spec.get("type") == "grounding_nudge":
            instructions = (
                f"Make the grounding nudge the production default: flip the "
                f"`grounding-nudge` param default from \"0\" to \"1\" in "
                f"ansible/roles/dav/templates/tekton-tasks/dav-run-corpus.yaml.j2 AND "
                f"ansible/roles/dav/templates/pipeline-stage2.yaml.j2 (or introduce a "
                f"dav_stage2_grounding_nudge deploy var), then re-apply: "
                f"`ansible-playbook playbook.yaml --tags engine,tekton`. "
                f"(Validated by experiment #{exp_id}: {exp.get('verdict_reason')})"
            )
        else:
            instructions = (
                f"Set `dav_stage2_max_tokens: {spec.get('candidate')}` in "
                f"ansible/inventory/group_vars/all/vars.yaml, then re-apply the engine "
                f"tasks: `ANSIBLE_VAULT_PASSWORD_FILE=… ansible-playbook -i inventory/hosts.yaml "
                f"playbook.yaml --tags engine -e dav_build_engine_image=false`. "
                f"(Validated by experiment #{exp_id}: {exp.get('verdict_reason')})"
            )
        await conn.execute(
            "UPDATE experiments SET status='promoted', updated_at=now() WHERE id=$1", exp_id)
        if exp.get("proposal_id"):
            await conn.execute(
                "UPDATE improvement_proposals SET status='applied', reviewed_by=$1, "
                "reviewed_at=now() WHERE id=$2", user, exp["proposal_id"])
            await _audit_proposal_action("proposal.applied", exp["proposal_id"], user,
                                         detail={"via": "experiment", "experiment_id": exp_id})
    return {"ok": True, "id": exp_id, "apply_method": "human-gated (deploy var)",
            "instructions": instructions}


@app.post("/api/experiments/{exp_id}/revert")
async def revert_experiment(exp_id: int, request: Request):
    """Restore a promoted SAMPLING change to its pre-promotion state."""
    get_user(request)
    async with pool.acquire() as conn:
        r = await conn.fetchrow("SELECT * FROM experiments WHERE id=$1", exp_id)
        if not r:
            raise HTTPException(404, "experiment not found")
        exp = dict(r)
        if exp.get("status") != "promoted":
            raise HTTPException(409, f"experiment status is {exp.get('status')!r}, not 'promoted'")
        spec = _parse_jsonb(exp["change_spec"]) or {}
        if spec.get("type") != "sampling" or "applied" not in spec:
            raise HTTPException(409, "only an applied sampling promotion is revertible "
                                     "(max_tokens promotions are deploy-var, revert manually)")
        await _revert_sampling_promotion(conn, exp)
    return {"ok": True, "id": exp_id, "status": "reverted"}


@app.get("/api/analysis/runs")
async def list_ingested_runs(request: Request, limit: int = Query(50, ge=1, le=500)):
    """List runs ingested into Postgres for the ACTIVE project, newest first. Project-scoped +
    auth-guarded so one project's runs never appear in another (was global + unauthenticated)."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        default_pid = await _default_project_id(conn)
        rows = await conn.fetch(
            """SELECT ar.run_id, ar.mode, ar.started_at, ar.finished_at,
                      ar.total_ucs, ar.successful, ar.failed, ar.total_samples,
                      ar.ingested_at, ar.run_name,
                      rs.name AS session_name
               FROM analysis_runs ar
               LEFT JOIN run_sessions rs ON rs.run_name = ar.run_name
               WHERE $2::bigint IS NULL OR ar.project_id = $2
                     OR (ar.project_id IS NULL AND $2 = $3)
               ORDER BY ar.started_at DESC NULLS LAST LIMIT $1""",
            limit, pid, default_pid,
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


# ------------------------- Managed Repos Registry (M1) -------------------------
# See ADR-003. The managed_repos table is the source-of-truth for repos DAV
# knows about. These endpoints provide CRUD over it. M2 wires the projection
# step (registry → dav-source-spec ConfigMap regeneration); right now writes
# update the DB only.


class RepoCreateIn(BaseModel):
    namespace: str = Field(..., min_length=2, max_length=63)
    repo_url: str = Field(..., max_length=512)
    repo_branch: str = Field("main", max_length=256)
    display_name: Optional[str] = Field(None, max_length=256)
    root_path: str = Field("", max_length=256)
    roles: list[str] = Field(default_factory=list)
    tenant_id: str = Field("default", max_length=64)
    ingestion_config: Optional[dict] = None
    metadata: Optional[dict] = None
    # Per-repo credentials (ADR-004 inline). Write-only — never returned by GET.
    # Pass plaintext; encrypted at write via crypto.encrypt().
    github_pat: Optional[str] = Field(None, max_length=512)
    github_webhook_secret: Optional[str] = Field(None, max_length=512)
    # Shared credentials (ADR-005). Reference an existing credentials row
    # by UUID or name. Wins over inline if both are set.
    github_pat_credential_ref: Optional[str] = Field(None, max_length=128)
    github_webhook_secret_credential_ref: Optional[str] = Field(None, max_length=128)


class RepoUpdateIn(BaseModel):
    repo_url: Optional[str] = Field(None, max_length=512)
    repo_branch: Optional[str] = Field(None, max_length=256)
    display_name: Optional[str] = Field(None, max_length=256)
    root_path: Optional[str] = Field(None, max_length=256)
    roles: Optional[list[str]] = None
    ingestion_config: Optional[dict] = None
    metadata: Optional[dict] = None
    # Per-repo credentials (ADR-004). None = don't touch; pass plaintext
    # to rotate. To explicitly clear inline + FK, use
    # DELETE /api/repos/{x}/secrets/{field}.
    github_pat: Optional[str] = Field(None, max_length=512)
    github_webhook_secret: Optional[str] = Field(None, max_length=512)
    # Shared credential FK (ADR-005):
    #   None or omitted        — don't touch
    #   string UUID/name       — link to that credential
    #   empty string ""        — unlink (set FK to NULL; inline column untouched)
    github_pat_credential_ref: Optional[str] = Field(None, max_length=128)
    github_webhook_secret_credential_ref: Optional[str] = Field(None, max_length=128)


class CredentialCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=63)
    credential_type: str = Field(..., min_length=1, max_length=64)
    value: str = Field(..., min_length=1, max_length=4096)  # plaintext, encrypted at write
    description: Optional[str] = Field(None, max_length=512)
    tenant_id: str = Field("default", max_length=64)
    metadata: Optional[dict] = None


class CredentialUpdateIn(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=63)
    description: Optional[str] = Field(None, max_length=512)
    value: Optional[str] = Field(None, min_length=1, max_length=4096)
    metadata: Optional[dict] = None


class ConvertToSharedIn(BaseModel):
    field: str = Field(..., min_length=1, max_length=64)  # 'github_pat' | 'github_webhook_secret'
    credential_name: str = Field(..., min_length=1, max_length=63)
    description: Optional[str] = Field(None, max_length=512)


@app.get("/api/repos")
async def list_repos_api(
    request: Request,
    role: Optional[str] = Query(None, description="filter by role (spec, corpus, issue-source)"),
    tenant_id: Optional[str] = Query(None, description="filter by tenant_id"),
):
    """List the active project's managed repos, optionally filtered by role/tenant."""
    try:
        async with pool.acquire() as conn:
            pid = await _active_project_id(request, conn)
            await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
            return {"repos": await _repos.list_repos(conn, role=role, tenant_id=tenant_id, project_id=pid)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/repos/{uuid_or_namespace}")
async def get_repo_api(uuid_or_namespace: str, request: Request):
    """Fetch one managed repo by UUID or namespace (in the active project)."""
    async with pool.acquire() as conn:
        repo = await _repos.get_repo(conn, uuid_or_namespace)
        if not repo:
            raise HTTPException(404, f"repo {uuid_or_namespace!r} not found")
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, repo["project_id"])
    return repo


@app.post("/api/repos")
async def create_repo_api(payload: RepoCreateIn, request: Request):
    """Create a new managed repo. If the new repo carries role=spec, also
    project the registry to the dav-source-spec ConfigMap + rollout-restart
    dav-docs-mcp so the new source is served (M2)."""
    reviewer = get_user(request)
    try:
        async with pool.acquire() as conn:
            pid = await _active_project_id(request, conn)
            await _require_priv_conn(conn, request, rbac.P_PROJECT_REPOS, pid)
            created = await _repos.create_repo(
                conn,
                namespace=payload.namespace,
                repo_url=payload.repo_url,
                repo_branch=payload.repo_branch,
                display_name=payload.display_name,
                root_path=payload.root_path,
                roles=payload.roles,
                tenant_id=payload.tenant_id,
                project_id=pid,
                ingestion_config=payload.ingestion_config,
                metadata=payload.metadata,
                github_pat=payload.github_pat,
                github_webhook_secret=payload.github_webhook_secret,
                github_pat_credential_ref=payload.github_pat_credential_ref,
                github_webhook_secret_credential_ref=payload.github_webhook_secret_credential_ref,
                created_by=reviewer,
            )
            projections = {}
            if _projector.repo_touches_spec(created):
                projections["spec"] = await _projector.project_spec_sources(
                    conn, applied_by=reviewer,
                )
            if _projector.repo_touches_corpus(created):
                projections["corpus"] = await _projector.project_corpus_sources(
                    conn, applied_by=reviewer,
                )
                # Auto-resync the corpus-files cache so a freshly-added corpus repo's UCs appear
                # immediately (otherwise they only show after the hourly loop / webhook / manual
                # resync). The new repo's files are pulled and upserted on the spot.
                try:
                    created["_corpus_sync"] = await sync_corpus_files(conn, reason="repo-added")
                except Exception as e:  # noqa: BLE001 — never fail repo creation on a sync hiccup
                    log.warning("auto corpus sync after repo add failed: %s", e)
                    created["_corpus_sync"] = {"error": str(e)}
            if projections:
                created["_projection"] = projections
            warn = await _check_repo_ref(payload.repo_url, payload.repo_branch, payload.github_pat)
            if warn:
                created["_warning"] = warn
            return created
    except ValueError as e:
        raise HTTPException(400, str(e))


# UC-repos PVC mount (DAV-hosted 'pvc-local' backend). Bare repos live here as
# <namespace>.git and are referenced as file:///uc-repos/<namespace>.git.
DAV_UC_REPOS_PATH = os.environ.get("DAV_UC_REPOS_PATH", "/uc-repos")


class PvcLocalRepoIn(BaseModel):
    namespace: str = Field(..., min_length=2, max_length=63)
    display_name: Optional[str] = Field(None, max_length=256)
    default_branch: str = Field("main", max_length=256)


@app.post("/api/repos/pvc-local")
async def create_pvc_local_repo(payload: PvcLocalRepoIn, request: Request):
    """Provision a DAV-hosted ('localized') bare git repo on the UC-repos PVC and
    register it as a managed_repos row with the uc-store role — the git home for
    use cases that don't need an external forge. Requires project.repos."""
    reviewer = get_user(request)
    ns = payload.namespace.strip().lower()
    repo_path = os.path.join(DAV_UC_REPOS_PATH, f"{ns}.git")
    # Path-traversal guard: the resolved bare-repo path must stay under the mount.
    base = os.path.realpath(DAV_UC_REPOS_PATH)
    if not os.path.realpath(repo_path).startswith(base + os.sep):
        raise HTTPException(400, "invalid namespace")
    if not os.path.isdir(DAV_UC_REPOS_PATH):
        raise HTTPException(503, f"UC-repos volume not mounted at {DAV_UC_REPOS_PATH} "
                                 "(deploy the dav-uc-repos PVC)")

    def _git_init():
        import subprocess
        if not os.path.exists(repo_path):
            subprocess.run(
                ["git", "init", "--bare", f"--initial-branch={payload.default_branch}", repo_path],
                check=True, capture_output=True, text=True, timeout=30)

    try:
        await asyncio.to_thread(_git_init)
    except Exception as e:
        raise HTTPException(500, f"git init failed: {e}")
    try:
        async with pool.acquire() as conn:
            pid = await _active_project_id(request, conn)
            await _require_priv_conn(conn, request, rbac.P_PROJECT_REPOS, pid)
            return await _repos.create_repo(
                conn, namespace=ns, repo_url=f"file://{repo_path}",
                repo_branch=payload.default_branch, display_name=payload.display_name,
                roles=["uc-store"], project_id=pid,
                metadata={"provider": "pvc-local", "managed": True},
                created_by=reviewer)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/repos/{uuid_or_namespace}")
async def update_repo_api(uuid_or_namespace: str, payload: RepoUpdateIn, request: Request):
    """Update fields on an existing managed repo. namespace and tenant_id are
    immutable through this endpoint; tenant transfers land as a dedicated
    endpoint later (see ADR-003).

    Projects to dav-source-spec ConfigMap if the row carried role=spec
    BEFORE or AFTER the update (covers add/remove/change-of-other-fields)."""
    reviewer = get_user(request)
    try:
        async with pool.acquire() as conn:
            before = await _repos.get_repo(conn, uuid_or_namespace)
            if not before:
                raise HTTPException(404, f"repo {uuid_or_namespace!r} not found")
            await _require_priv_conn(conn, request, rbac.P_PROJECT_REPOS, before["project_id"])
            # Translate empty-string credential_ref to the unlink sentinel
            # (None = don't touch; "" = explicit unlink; "<name>" = link)
            pat_ref = payload.github_pat_credential_ref
            if pat_ref == "":
                pat_ref = _repos._SENTINEL_UNLINK
            ws_ref = payload.github_webhook_secret_credential_ref
            if ws_ref == "":
                ws_ref = _repos._SENTINEL_UNLINK
            updated = await _repos.update_repo(
                conn,
                uuid_or_namespace,
                repo_url=payload.repo_url,
                repo_branch=payload.repo_branch,
                display_name=payload.display_name,
                root_path=payload.root_path,
                roles=payload.roles,
                ingestion_config=payload.ingestion_config,
                metadata=payload.metadata,
                github_pat=payload.github_pat,
                github_webhook_secret=payload.github_webhook_secret,
                github_pat_credential_ref=pat_ref,
                github_webhook_secret_credential_ref=ws_ref,
                updated_by=reviewer,
            )
            if not updated:
                raise HTTPException(404, f"repo {uuid_or_namespace!r} not found")
            projections = {}
            if (_projector.repo_touches_spec(before)
                    or _projector.repo_touches_spec(updated)):
                projections["spec"] = await _projector.project_spec_sources(
                    conn, applied_by=reviewer,
                )
            if (_projector.repo_touches_corpus(before)
                    or _projector.repo_touches_corpus(updated)):
                projections["corpus"] = await _projector.project_corpus_sources(
                    conn, applied_by=reviewer,
                )
                try:
                    updated["_corpus_sync"] = await sync_corpus_files(conn, reason="repo-updated")
                except Exception as e:  # noqa: BLE001
                    log.warning("auto corpus sync after repo update failed: %s", e)
                    updated["_corpus_sync"] = {"error": str(e)}
            if projections:
                updated["_projection"] = projections
            warn = await _check_repo_ref(updated.get("repo_url"), updated.get("repo_branch"), payload.github_pat)
            if warn:
                updated["_warning"] = warn
            return updated
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/repos/{uuid_or_namespace}")
async def delete_repo_api(uuid_or_namespace: str, request: Request):
    """Delete a managed repo. If the deleted repo carried role=spec, also
    project the registry to the dav-source-spec ConfigMap + rollout-restart
    dav-docs-mcp so the MCP stops serving the removed source.

    The projector refuses to write an empty sources list (would crash the
    MCP at init), so deleting the last role=spec repo logs a warning and
    leaves the ConfigMap untouched. Create a replacement first."""
    reviewer = get_user(request)
    async with pool.acquire() as conn:
        before = await _repos.get_repo(conn, uuid_or_namespace)
        if not before:
            raise HTTPException(404, f"repo {uuid_or_namespace!r} not found")
        await _require_priv_conn(conn, request, rbac.P_PROJECT_REPOS, before["project_id"])
        ok = await _repos.delete_repo(conn, uuid_or_namespace)
        if not ok:
            raise HTTPException(404, f"repo {uuid_or_namespace!r} not found")
        projections = {}
        if _projector.repo_touches_spec(before):
            projections["spec"] = await _projector.project_spec_sources(
                conn, applied_by=reviewer,
            )
        if _projector.repo_touches_corpus(before):
            projections["corpus"] = await _projector.project_corpus_sources(
                conn, applied_by=reviewer,
            )
    return {"deleted": uuid_or_namespace, "_projection": projections or None}


@app.post("/api/repos/project")
async def project_repos_api(
    request: Request,
    role: Optional[str] = Query(None, description="'spec' (default), 'corpus', or 'all'"),
):
    """Manually trigger registry → ConfigMap projection.

    `role` param:
      - 'spec' (default) — regenerate dav-source-spec + roll dav-docs-mcp
      - 'corpus' — regenerate dav-source-corpus (no rollout; PipelineRuns
        pick up fresh)
      - 'all' — both

    Idempotent: each projector skips if its ConfigMap already matches."""
    reviewer = get_user(request)
    target = (role or "spec").lower()
    if target not in ("spec", "corpus", "all"):
        raise HTTPException(400, f"unknown role {role!r}; valid: spec, corpus, all")
    async with pool.acquire() as conn:
        _pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_REPOS, _pid)
        results: dict = {}
        if target in ("spec", "all"):
            results["spec"] = await _projector.project_spec_sources(
                conn, applied_by=reviewer,
            )
        if target in ("corpus", "all"):
            results["corpus"] = await _projector.project_corpus_sources(
                conn, applied_by=reviewer,
            )
            try:
                results["corpus_sync"] = await sync_corpus_files(conn, reason="repo-projected")
            except Exception as e:  # noqa: BLE001
                log.warning("auto corpus sync after project failed: %s", e)
                results["corpus_sync"] = {"error": str(e)}
        return results


@app.delete("/api/repos/{uuid_or_namespace}/secrets/{field}")
async def clear_repo_secret_api(uuid_or_namespace: str, field: str, request: Request):
    """Explicitly clear one of a repo's per-repo credential fields.

    `field` must be one of repos.SECRET_FIELDS ('github_pat',
    'github_webhook_secret'). Setting a field to NULL via this endpoint
    is distinct from the PUT no-op behavior (where passing None means
    "don't touch"). Used by the UI's "Clear" button.
    """
    reviewer = get_user(request)
    try:
        async with pool.acquire() as conn:
            updated = await _repos.clear_repo_secret(
                conn, uuid_or_namespace, field, updated_by=reviewer,
            )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not updated:
        raise HTTPException(404, f"repo {uuid_or_namespace!r} not found")
    return updated


# ------------------------- Shared Credentials (M9 of #28, ADR-005) -------------------------


@app.get("/api/credentials")
async def list_credentials_api(
    request: Request,
    credential_type: Optional[str] = Query(None, alias="type", description="filter by credential_type"),
    tenant_id: Optional[str] = Query(None, description="filter by tenant_id"),
):
    """List credentials (metadata only — values are never returned). Requires the
    repo-management privilege (credentials exist to back managed repos).
    Each row includes a `used_by_count` for the UI's "used by N repo(s)" chip.
    """
    try:
        async with pool.acquire() as conn:
            pid = await _active_project_id(request, conn)
            await _require_priv_conn(conn, request, rbac.P_PROJECT_REPOS, pid)
            return {"credentials": await _credentials.list_credentials(
                conn, credential_type=credential_type, tenant_id=tenant_id,
            )}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/credentials/types/vocabulary")
async def list_credential_types_api():
    """Closed vocabulary of credential_type values."""
    return {"credential_types": sorted(_credentials.VALID_TYPES)}


@app.get("/api/credentials/{uuid_or_name}")
async def get_credential_api(
    uuid_or_name: str,
    request: Request,
    credential_type: Optional[str] = Query(None, alias="type"),
):
    """Fetch one credential with `used_by_repos` provenance. Value is
    never returned. Requires the repo-management privilege."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_REPOS, pid)
        c = await _credentials.get_credential(conn, uuid_or_name, credential_type=credential_type)
    if not c:
        raise HTTPException(404, f"credential {uuid_or_name!r} not found")
    return c


@app.post("/api/credentials")
async def create_credential_api(payload: CredentialCreateIn, request: Request):
    """Create a new shared credential. `value` is plaintext in the
    request body, Fernet-encrypted before write. Never returned."""
    reviewer = get_user(request)
    try:
        async with pool.acquire() as conn:
            return await _credentials.create_credential(
                conn,
                name=payload.name,
                credential_type=payload.credential_type,
                value=payload.value,
                description=payload.description,
                tenant_id=payload.tenant_id,
                metadata=payload.metadata,
                created_by=reviewer,
            )
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/credentials/{uuid_or_name}")
async def update_credential_api(
    uuid_or_name: str, payload: CredentialUpdateIn, request: Request,
):
    """Update a credential. `value` rotation propagates to all dependent
    repos automatically (next poll / webhook). credential_type and
    tenant_id are immutable through this endpoint."""
    reviewer = get_user(request)
    try:
        async with pool.acquire() as conn:
            updated = await _credentials.update_credential(
                conn,
                uuid_or_name,
                name=payload.name,
                description=payload.description,
                value=payload.value,
                metadata=payload.metadata,
                updated_by=reviewer,
            )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not updated:
        raise HTTPException(404, f"credential {uuid_or_name!r} not found")
    return updated


@app.delete("/api/credentials/{uuid_or_name}")
async def delete_credential_api(uuid_or_name: str, request: Request):
    """Delete a credential. Refuses with 409 if any repo references it;
    response body lists the dependent repos so the operator can
    reassign or unlink first. Requires the repo-management privilege on the
    active project (was unauthenticated — security remediation #186)."""
    try:
        async with pool.acquire() as conn:
            pid = await _active_project_id(request, conn)
            await _require_priv_conn(conn, request, rbac.P_PROJECT_REPOS, pid)
            ok = await _credentials.delete_credential(conn, uuid_or_name)
    except _credentials.CredentialInUseError as e:
        raise HTTPException(
            409,
            detail={
                "message": str(e),
                "dependent_repos": e.dependents,
            },
        )
    if not ok:
        raise HTTPException(404, f"credential {uuid_or_name!r} not found")
    return {"deleted": uuid_or_name}


@app.post("/api/repos/{uuid_or_namespace}/convert-credential")
async def convert_repo_inline_to_shared_api(
    uuid_or_namespace: str, payload: ConvertToSharedIn, request: Request,
):
    """Migrate one of a repo's inline credentials to a new shared
    credentials row. Creates the credential from the decrypted inline
    value, sets the FK, clears the inline. One-shot per (repo, field).
    Useful for operators with existing inline credentials wanting to
    consolidate (ADR-005 §5)."""
    reviewer = get_user(request)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                return await _repos.convert_inline_to_shared(
                    conn,
                    uuid_or_namespace,
                    field=payload.field,
                    credential_name=payload.credential_name,
                    description=payload.description,
                    updated_by=reviewer,
                )
    except ValueError as e:
        raise HTTPException(400, str(e))


# ------------------------- GitHub Webhook Receiver (M6 of #28) -------------------------
# Receives issue_comment and pull_request_review_comment events from
# GitHub webhooks configured on managed_repos rows with role=issue-source.
# Validates HMAC-SHA256 signature against the repo's github_webhook_secret
# (ADR-004). Upserts via the same path as the poller — webhook is primary
# (real-time), poller is fallback for missed deliveries.
#
# Path is under /api/webhooks/ which oauth-proxy is configured to skip
# auth on (no OAuth round-trip for GitHub).


def _repo_url_candidates(payload: dict) -> list[str]:
    """Build the list of possible repo_url strings that could match a
    managed_repos row, given a GitHub webhook payload. Caller queries
    `repo_url = ANY($1::text[])` to find the row regardless of which URL
    form the operator registered (HTTPS, HTTPS-with-.git, or SSH).
    """
    repo_info = payload.get("repository") or {}
    candidates: list[str] = []
    if repo_info.get("clone_url"):
        candidates.append(repo_info["clone_url"])
    if repo_info.get("html_url"):
        candidates.append(repo_info["html_url"])
    full = repo_info.get("full_name")
    if full and "/" in full:
        candidates.append(f"git@github.com:{full}.git")
        candidates.append(f"https://github.com/{full}")
        candidates.append(f"https://github.com/{full}.git")
    return candidates


async def _handle_corpus_push(body_bytes: bytes, sig_header: str) -> dict:
    """GitHub `push` to a registered corpus repo → reconcile the corpus-files
    cache (mark-and-sweep). HMAC-validated against the repo's per-repo webhook
    secret (ADR-004). Same payload URL as pr-comments — add 'Pushes' to the
    repo's webhook events. No-op for non-corpus / unregistered repos."""
    import hashlib
    import hmac
    import json
    try:
        payload = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise HTTPException(400, "body is not valid JSON")
    url_candidates = _repo_url_candidates(payload)
    if not url_candidates:
        raise HTTPException(400, "payload missing repository identifiers")
    async with pool.acquire() as conn:
        repo_row = await conn.fetchrow(
            "SELECT namespace, roles, github_webhook_secret_encrypted "
            "FROM managed_repos WHERE repo_url = ANY($1::text[]) LIMIT 1",
            url_candidates)
        if not repo_row:
            return {"status": "ignored", "reason": "repo not in managed_repos"}
        if "corpus" not in list(repo_row["roles"] or []):
            return {"status": "ignored", "reason": "not a corpus repo"}
        if not repo_row["github_webhook_secret_encrypted"]:
            raise HTTPException(400, "repo has no github_webhook_secret configured")
        try:
            secret = crypto.decrypt(repo_row["github_webhook_secret_encrypted"])
        except Exception:
            raise HTTPException(503, "cannot decrypt webhook secret")
        if not sig_header.startswith("sha256="):
            raise HTTPException(400, "missing or malformed X-Hub-Signature-256")
        expected = "sha256=" + hmac.new(
            secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig_header, expected):
            log.warning("corpus push webhook: HMAC mismatch for %s", repo_row["namespace"])
            raise HTTPException(403, "signature mismatch")
        result = await sync_corpus_files(conn, reason="webhook")
    return {"status": "synced", "corpus": result}


@app.post("/api/webhooks/github/pr-comments")
async def github_pr_comments_webhook(request: Request):
    """Receive GitHub issue_comment + pull_request_review_comment events.

    GitHub webhook setup (per repo with role=issue-source):
      - Payload URL: https://dav-review.<cluster>/api/webhooks/github/pr-comments
      - Content type: application/json
      - Secret: same value as managed_repos.github_webhook_secret for this repo
      - Events: Issue comments, Pull request review comments

    The endpoint validates the HMAC-SHA256 signature against the repo's
    per-repo webhook secret (ADR-004), then upserts the comment via the
    same code path as the poller.
    """
    import hashlib, hmac, json

    body_bytes = await request.body()
    event = request.headers.get("X-GitHub-Event", "")
    sig_header = request.headers.get("X-Hub-Signature-256", "")

    # Acknowledge GitHub's ping (sent at webhook creation) without
    # touching the DB. Also accept any event type we don't ingest with
    # a 200 + "ignored" so GitHub doesn't retry.
    if event == "ping":
        return {"status": "pong"}
    if event == "push":
        # A push to a registered corpus repo → reconcile the corpus-files cache.
        return await _handle_corpus_push(body_bytes, sig_header)
    if event not in ("issue_comment", "pull_request_review_comment"):
        return {"status": "ignored", "reason": f"unhandled event: {event}"}

    try:
        payload = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise HTTPException(400, "body is not valid JSON")

    action = payload.get("action")
    if action not in ("created", "edited"):
        # 'deleted' and other actions are not ingested in v1.
        return {"status": "ignored", "reason": f"unhandled action: {action}"}

    # Resolve the source repo from payload.repository.* against managed_repos
    url_candidates = _repo_url_candidates(payload)
    if not url_candidates:
        raise HTTPException(400, "payload missing repository identifiers")

    async with pool.acquire() as conn:
        repo_row = await conn.fetchrow(
            "SELECT uuid, namespace, tenant_id, roles, github_webhook_secret_encrypted "
            "FROM managed_repos WHERE repo_url = ANY($1::text[]) LIMIT 1",
            url_candidates,
        )
        if not repo_row:
            raise HTTPException(
                404,
                f"no managed_repos row matches the webhook source "
                f"(tried URLs: {url_candidates}). Add the repo via the "
                f"Repos UI with role=issue-source before configuring its "
                f"webhook on GitHub.",
            )
        repo_uuid = str(repo_row["uuid"])
        repo_ns = repo_row["namespace"]
        tenant_id = repo_row["tenant_id"] or "default"
        roles = list(repo_row["roles"] or [])

        if "issue-source" not in roles:
            return {
                "status": "ignored",
                "reason": f"repo {repo_ns} has roles={roles}; needs 'issue-source'",
            }

        # Validate HMAC against per-repo webhook secret (ADR-004)
        if not repo_row["github_webhook_secret_encrypted"]:
            raise HTTPException(
                400,
                f"repo {repo_ns} has no github_webhook_secret configured. "
                f"Set one via the Repos UI before enabling the GitHub webhook.",
            )
        try:
            from . import crypto as _crypto
            webhook_secret = _crypto.decrypt(repo_row["github_webhook_secret_encrypted"])
        except Exception as e:
            log.warning("webhook: cannot decrypt secret for %s: %s", repo_ns, e)
            raise HTTPException(503, f"cannot decrypt webhook secret: {e}")

        if not sig_header.startswith("sha256="):
            raise HTTPException(400, "missing or malformed X-Hub-Signature-256")
        expected = "sha256=" + hmac.new(
            webhook_secret.encode("utf-8"), body_bytes, hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig_header, expected):
            log.warning("webhook: HMAC mismatch for %s (action=%s)", repo_ns, action)
            raise HTTPException(403, "signature mismatch")

        # Parse comment by event type
        gh_comment = payload.get("comment") or {}
        if event == "issue_comment":
            # issue_comment fires for both regular issues and PRs; only
            # ingest if there's an attached pull_request.
            issue = payload.get("issue") or {}
            if "pull_request" not in issue:
                return {"status": "ignored", "reason": "issue_comment on a non-PR issue"}
            pr_number = issue.get("number")
            pr_title = issue.get("title")
            pr_url = issue.get("html_url")
            ctype = "issue_comment"
        else:  # pull_request_review_comment
            pr = payload.get("pull_request") or {}
            pr_number = pr.get("number")
            pr_title = pr.get("title")
            pr_url = pr.get("html_url")
            ctype = "pull_request_review_comment"

        if not pr_number:
            raise HTTPException(400, "could not determine PR number from payload")

        author = gh_comment.get("user") or {}
        body_text = gh_comment.get("body") or ""
        if not body_text.strip():
            return {"status": "ignored", "reason": "empty comment body"}

        created_at = _pr_comments._parse_ts(gh_comment.get("created_at"))
        updated_at = _pr_comments._parse_ts(gh_comment.get("updated_at"))
        if not created_at or not updated_at:
            raise HTTPException(400, "missing or malformed comment timestamps")

        comment_uuid, inserted = await _pr_comments.upsert_comment(
            conn,
            repo_uuid=repo_uuid,
            tenant_id=tenant_id,
            github_comment_id=gh_comment["id"],
            github_comment_type=ctype,
            pr_number=pr_number,
            pr_title=pr_title,
            pr_url=pr_url,
            author_login=author.get("login") or "unknown",
            author_url=author.get("html_url"),
            body=body_text,
            comment_url=gh_comment.get("html_url"),
            github_created_at=created_at,
            github_updated_at=updated_at,
            ingestion_source="webhook",
        )

    log.info(
        "webhook: %s %s %s comment %s (PR#%d, %s)",
        "inserted" if inserted else "updated", repo_ns, ctype,
        comment_uuid, pr_number, action,
    )
    return {
        "status": "ingested",
        "comment_uuid": comment_uuid,
        "inserted": inserted,
        "repo_namespace": repo_ns,
        "event": event,
        "action": action,
    }


@app.get("/api/repos/roles/vocabulary")
async def list_repo_roles():
    """Closed vocabulary of role names a repo can carry. Add a role in
    repos.py VALID_ROLES to extend; UI picks it up automatically."""
    return {"roles": sorted(_repos.VALID_ROLES)}


@app.get("/api/analysis/gaps")
async def query_gaps(
    request: Request,
    uc_uuid: Optional[str] = Query(None, description="filter by UC uuid"),
    gap_id: Optional[str] = Query(None, description="filter by gap ID"),
    run_id: Optional[str] = Query(None, description="filter by run ID"),
    limit: int = Query(200, ge=1, le=2000),
):
    """Query ingested gaps for the ACTIVE project (cross-run trend analysis). Project-scoped +
    auth-guarded so gaps from another project's runs never leak in (was global + unauthenticated)."""
    clauses = []
    args: list = []

    def _add(clause: str, val):
        args.append(val)
        clauses.append(clause.replace("?", f"${len(args)}"))

    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        default_pid = await _default_project_id(conn)
        # Sovereignty: limit to the active project's runs (orphans under default; single-user sees all).
        args.append(pid); _pid_i = len(args)
        args.append(default_pid); _def_i = len(args)
        clauses.append(f"(${_pid_i}::bigint IS NULL OR ar.project_id = ${_pid_i} "
                       f"OR (ar.project_id IS NULL AND ${_pid_i} = ${_def_i}))")
        if uc_uuid:
            _add("g.uc_uuid = ?", uc_uuid)
        if gap_id:
            _add("g.gap_id = ?", gap_id)
        if run_id:
            _add("g.run_id = ?", run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
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


# Keyword themes for the default 'theme' clustering — used when the capability catalog
# isn't curated (capability_ids are still raw model output). First-match wins.
_ROADMAP_THEMES = [
    ('Confidential computing / TEE / attestation', ['attestation', 'tee', 'tcb', 'confidential', 'key release', 'key custody', 'signing key', 'key material', 'secure-model', 'secure model']),
    ('Encryption & data integrity', ['encrypt', 'aes-gcm', 'per-file', 'file-level', 'integrity', 'decrypt']),
    ('Cross-cloud residency & data mobility', ['cross-cloud', 'residency', 'data mobility', 'brownfield', 'data-residency']),
    ('Sovereignty / federation / decommissioning', ['sovereign', 'federation', 'decommission', 'peer']),
    ('Audit, governance & policy', ['audit', 'governance', 'policy', 'override', 'compliance', 'merkle', 'profile', 'boundary']),
    ('AI intake / NLP workflows', ['nlp', 'natural language', 'ai-intake', 'ai intake', 'ai model', 'conversational', 'ai-assisted', 'ai workflow']),
    ('Metering / FinOps / cost', ['metric', 'metering', 'cost', 'billing', 'revenue', 'finops', 'pricing', 'quota', 'currency']),
    ('Workflow automation & orchestration', ['workflow', 'automation', 'aap', 'ansible', 'eda', 'orchestrat', 'approval gate', 'composite', 'f5', 'vip', 'ebonding', 'itsm', 'cms', 'snow', 'port provisioning', 'branch', 'dynamic inventory', 'ipam', 'cmdb', 'dc-port', 'survey', 'intake server']),
    ('Compute & storage provisioning', ['vm provision', 'provision', 'storage', 'persistent volume', 'idempoten', 'provider failure', 'requeue', 'orphan', 'partial-realization', 'tenancy', 'indeterminate', 'recovery', 'lifecycle']),
    ('Identity & drift', ['identity', 'auth-provider', 'drift']),
]
_ROADMAP_FOUNDATIONAL_THEMES = {'Audit, governance & policy'}


def _roadmap_theme_of(title, handle):
    t = ((title or '') + ' ' + (handle or '')).lower()
    for name, kws in _ROADMAP_THEMES:
        if any(k in t for k in kws):
            return name
    return 'Other / unclustered'


@app.get("/api/analysis/roadmap")
async def analysis_roadmap(request: Request,
                           set_id: Optional[str] = Query(None, description="Scoping Set id, '__all__' (default), or '__unassigned__'"),
                           group_by: str = Query("theme", description="theme (default) | capability | subdomain")):
    """Roadmap projection (#141): the engineering-capability roadmap, synthesized from the
    project's gap analysis. Clusters each gap by `group_by`, ranks clusters by severity-weighted
    gap load + demand (+ foundational standing for capability mode), and tiers them. Single-source
    analysis → a reproducible roadmap (replaces the hand/keyword pipeline). `theme` is the default
    because capability_ids are raw model output until the catalog is curated (#89); `capability`
    and `subdomain` become the principled modes once it is."""
    from collections import Counter, defaultdict
    if group_by not in ("theme", "capability", "subdomain"):
        group_by = "theme"
    SEV_W = {"critical": 20, "major": 6, "moderate": 1.5, "advisory": 0.5, "minor": 0.5}
    SEV_ORDER = {"critical": 0, "major": 1, "moderate": 2, "advisory": 3, "minor": 4}
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)  # #186: was unguarded
        uuids = await _resolve_scope_uc_uuids(conn, pid, set_id, None)
        if not uuids:
            return {"project_id": pid, "set_id": set_id, "group_by": group_by, "total_gaps": 0,
                    "severity_counts": {}, "critical_gaps": [], "tiers": [],
                    "unmapped_gap_count": 0, "cluster_count": 0}
        _latest = ("SELECT DISTINCT ON (uc_uuid) id, uc_uuid, run_id, uc_handle FROM uc_analyses "
                   "WHERE uc_uuid = ANY($1) ORDER BY uc_uuid, ingested_at DESC")
        latest = await conn.fetch(_latest, uuids)
        handle = {r["uc_uuid"]: (r["uc_handle"] or r["uc_uuid"]) for r in latest}
        analysis_ids = [r["id"] for r in latest]
        gap_rows = await conn.fetch(
            "SELECT g.uc_uuid, g.gap_id, g.title, g.severity, ua.verdict "
            "FROM uc_gaps g JOIN uc_analyses ua ON ua.id=g.analysis_id "
            "WHERE g.analysis_id = ANY($1)", analysis_ids)
        cap_rows = await conn.fetch(
            f"SELECT c.capability_id, c.uc_uuid FROM uc_capabilities c "
            f"JOIN ({_latest}) l ON l.uc_uuid=c.uc_uuid AND l.run_id=c.run_id", uuids)
        dep_rows = await conn.fetch(
            f"SELECT d.capability_id, d.depends_on_id FROM uc_capability_deps d "
            f"JOIN ({_latest}) l ON l.uc_uuid=d.uc_uuid AND l.run_id=d.run_id", uuids)
        _cat_pid = await _default_project_id(conn)
        name_map = await _catalog_name_map(conn, _cat_pid)
        meta_map = await _catalog_meta_map(conn, _cat_pid)

    gaps = [dict(r) for r in gap_rows]
    sev_counts = dict(Counter(g["severity"] for g in gaps))
    uc_caps = defaultdict(set)
    cap_demand = defaultdict(set)
    for r in cap_rows:
        uc_caps[r["uc_uuid"]].add(r["capability_id"])
        cap_demand[r["capability_id"]].add(r["uc_uuid"])
    found = {f["capability_id"]: f for f in _capability_graph.foundational_ranking(
        [(r["capability_id"], r["depends_on_id"]) for r in dep_rows],
        {k: len(v) for k, v in cap_demand.items()})}

    def keys_for(g):
        if group_by == "theme":
            return [_roadmap_theme_of(g["title"], handle.get(g["uc_uuid"]))]
        caps = uc_caps.get(g["uc_uuid"]) or set()
        if not caps:
            return []
        if group_by == "subdomain":
            return sorted({(meta_map.get(c) or {}).get("subdomain") or "Uncategorized" for c in caps})
        return sorted(caps)

    cluster_gaps = defaultdict(list)
    cluster_ucs = defaultdict(set)
    unmapped = 0
    for g in gaps:
        ks = keys_for(g)
        if not ks:
            unmapped += 1
            continue
        for k in ks:
            cluster_gaps[k].append(g)
            cluster_ucs[k].add(g["uc_uuid"])

    clusters = []
    for k, glist in cluster_gaps.items():
        sc = Counter(x["severity"] for x in glist)
        load = sum(SEV_W.get(x["severity"], 0) for x in glist)
        td = int((found.get(k) or {}).get("transitive_dependents") or 0) if group_by == "capability" else 0
        demand = len(cluster_ucs[k])
        has_crit = sc.get("critical", 0) > 0
        is_found_theme = group_by == "theme" and k in _ROADMAP_FOUNDATIONAL_THEMES
        tier = 1 if (has_crit or td > 0 or is_found_theme) else (2 if sc.get("major", 0) else 3)
        clusters.append({
            "key": k,
            "name": (name_map.get(k) or k) if group_by == "capability" else k,
            "disposition": (meta_map.get(k) or {}).get("disposition") if group_by == "capability" else None,
            "demand": demand, "foundational": td > 0, "transitive_dependents": td,
            "gap_count": len(glist), "severity_counts": dict(sc),
            "score": round(load + demand + td * 2.0, 2), "tier": tier,
            "gaps": sorted(
                [{"uc": x["uc_uuid"], "uc_handle": handle.get(x["uc_uuid"]),
                  "gap_id": x["gap_id"], "title": x["title"],
                  "severity": x["severity"], "verdict": x["verdict"]} for x in glist],
                key=lambda y: SEV_ORDER.get(y["severity"], 9)),
        })
    clusters.sort(key=lambda c: (c["tier"], -c["severity_counts"].get("critical", 0), -c["score"]))

    tier_label = {1: "Security & trust spine (criticals + foundational)",
                  2: "Majors + broad integration surface", 3: "Domain capabilities"}
    tiers = [{"tier": t, "label": tier_label[t],
              "clusters": [c for c in clusters if c["tier"] == t]}
             for t in (1, 2, 3) if any(c["tier"] == t for c in clusters)]
    critical_gaps = sorted(
        [{"uc": g["uc_uuid"], "uc_handle": handle.get(g["uc_uuid"]), "gap_id": g["gap_id"],
          "title": g["title"], "verdict": g["verdict"]} for g in gaps if g["severity"] == "critical"],
        key=lambda y: y["uc_handle"] or "")
    return {"project_id": pid, "set_id": set_id, "group_by": group_by, "total_gaps": len(gaps),
            "severity_counts": sev_counts, "critical_gaps": critical_gaps,
            "tiers": tiers, "unmapped_gap_count": unmapped, "cluster_count": len(clusters)}


# ========================= RECORDING → UC PIPELINE (#176) =========================
@app.post("/api/use-cases/from-recording")
async def submit_recording(request: Request,
                           file: UploadFile = File(...),
                           context: str = Form(""),
                           model_config_id: Optional[int] = Form(None)):
    """Accept an audio/video recording and enqueue a recording_jobs row. A dedicated
    dav-recording-worker transcribes locally (ffmpeg + whisper.cpp) and extracts UC drafts
    (uc_assist.extract_bulk) — NO recording data leaves the trust boundary. Returns a job_id
    to poll. Phase A stores the bytes in-row (TTL-cleared); PVC/object-store is the scale path."""
    import uuid as _uuid
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty upload")
    MAX = 200 * 1024 * 1024
    if len(data) > MAX:
        raise HTTPException(413, f"file too large ({len(data) // 1024 // 1024}MB > 200MB for Phase A)")
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        reviewer = (await _require_priv_conn(conn, request, rbac.P_PROJECT_USECASES, pid)) or get_user(request)
        job_id = "rec-" + _uuid.uuid4().hex[:12]
        await conn.execute(
            "INSERT INTO recording_jobs (job_id, project_id, submitted_by, status, phase, "
            "file_name, content_type, file_bytes, file_size, context, model_config_id, expires_at) "
            "VALUES ($1,$2,$3,'queued','queued',$4,$5,$6,$7,$8,$9, now() + interval '24 hours')",
            job_id, pid, reviewer, file.filename, file.content_type, data, len(data),
            (context or None), model_config_id)
    return {"job_id": job_id, "status": "queued",
            "message": f"Recording accepted ({len(data) // 1024} KB). "
                       f"Poll GET /api/use-cases/from-recording/{job_id}."}


@app.get("/api/use-cases/from-recording/{job_id}")
async def recording_status(job_id: str, request: Request):
    """Poll a recording job. Fields fill progressively (transcript ready before items)."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_USECASES, pid)
        r = await conn.fetchrow(
            "SELECT job_id, project_id, status, phase, progress, file_name, file_size, "
            "transcript, items, error, duration_seconds, created_at, updated_at, finished_at "
            "FROM recording_jobs WHERE job_id=$1", job_id)
    if not r or (r["project_id"] is not None and pid is not None and r["project_id"] != pid):
        raise HTTPException(404, "recording job not found")
    d = dict(r)
    its = d.get("items")
    d["items"] = (json.loads(its) if isinstance(its, str) else its) or []
    d["transcript_ready"] = bool(d.get("transcript"))
    for k in ("created_at", "updated_at", "finished_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    return d


@app.delete("/api/use-cases/from-recording/{job_id}")
async def cancel_recording(job_id: str, request: Request):
    """Cancel / clean up a recording job (drops the stored bytes immediately)."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_USECASES, pid)
        r = await conn.fetchrow("SELECT project_id FROM recording_jobs WHERE job_id=$1", job_id)
        if not r or (r["project_id"] is not None and pid is not None and r["project_id"] != pid):
            raise HTTPException(404, "recording job not found")
        await conn.execute("UPDATE recording_jobs SET status='cancelled', phase='cancelled', "
                           "file_bytes=NULL, updated_at=now() WHERE job_id=$1", job_id)
    return {"ok": True, "artifacts_cleaned": True}


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


# ========================= MCP DOCS REFRESH =========================
#
# Manual + scheduled refresh of the dav-docs-mcp deployment so the
# init container re-clones the spec/corpus repos and the MCP serves
# current content. The schedule is owned by a CronJob deployed via
# Ansible (mcp-refresh-cronjob.yaml.j2); this endpoint is the manual
# "Refresh Now" button surface for the Config UI.

_MCP_REFRESH_DEPLOYMENT = "dav-docs-mcp"
_MCP_REFRESH_NS = sources.NAMESPACE  # same namespace as the rest of dav-source-* logic


@app.post("/api/mcp/refresh-now")
async def mcp_refresh_now(request: Request):
    """Trigger an immediate rollout-restart of the dav-docs-mcp deployment.

    The standard kubectl-rollout-restart pattern: patch the pod template's
    annotations with a fresh restartedAt timestamp, which forces a new
    ReplicaSet and a rolling restart. The init container re-clones spec +
    corpus on the new pod's startup.

    Returns the timestamp the rollout was triggered at and the actor who
    triggered it.
    """
    user = get_user(request)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    body = {
        "metadata": {
            "annotations": {
                "dav.io/last-refreshed-at": now,
                "dav.io/last-refreshed-by": user,
                "dav.io/last-refreshed-source": "manual-ui-button",
            },
        },
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "dav.io/restartedAt": now,
                        "dav.io/restartReason": "manual-mcp-refresh",
                    },
                },
            },
        },
    }
    try:
        sources._apps().patch_namespaced_deployment(
            name=_MCP_REFRESH_DEPLOYMENT,
            namespace=_MCP_REFRESH_NS,
            body=body,
        )
        log.info("mcp-refresh-now triggered by=%s at=%s", user, now)
    except Exception as e:
        log.exception("mcp-refresh-now failed")
        raise HTTPException(500, f"refresh failed: {e}")
    return {"ok": True, "triggered_at": now, "triggered_by": user}


@app.get("/api/mcp/refresh-status")
async def mcp_refresh_status():
    """Return the current MCP deployment's refresh metadata.

    Reads annotations from the deployment to surface: when it was last
    refreshed (manual OR scheduled), by whom or what, and current
    rollout status (so the UI can show a spinner while pods are rolling).
    """
    try:
        dep = sources._apps().read_namespaced_deployment(
            name=_MCP_REFRESH_DEPLOYMENT,
            namespace=_MCP_REFRESH_NS,
        )
    except Exception as e:
        log.exception("mcp-refresh-status read failed")
        raise HTTPException(500, f"status read failed: {e}")
    annot = (dep.metadata.annotations or {}) if dep.metadata else {}
    pod_annot = (
        dep.spec.template.metadata.annotations or {}
        if dep.spec and dep.spec.template and dep.spec.template.metadata
        else {}
    )
    rollout = sources._deploy_to_rollout_state(dep)
    return {
        "deployment": _MCP_REFRESH_DEPLOYMENT,
        "last_refreshed_at": annot.get("dav.io/last-refreshed-at"),
        "last_refreshed_by": annot.get("dav.io/last-refreshed-by"),
        "last_refreshed_source": annot.get("dav.io/last-refreshed-source"),
        # The pod-template restartedAt is the truthful "this is when a roll
        # was triggered" marker — present whether the trigger was manual
        # or scheduled. The metadata annotations above are the manual-trigger
        # bookkeeping that the scheduled CronJob also writes when it runs.
        "last_pod_restart_at": pod_annot.get("dav.io/restartedAt"),
        "last_pod_restart_reason": pod_annot.get("dav.io/restartReason"),
        "rollout": rollout,
    }


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
        result = await asyncio.to_thread(validations.trigger_run,
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
        runs = await asyncio.to_thread(validations.list_recent, limit)
        return {"runs": runs, "enabled": True}
    except Exception as e:
        log.exception("list self-test runs failed")
        raise HTTPException(500, f"list failed: {e}")


# ========================= MCP SERVERS =========================


def _mcp_public(row) -> dict:
    """MCP-server row for API responses — never leak the token; expose only
    whether one is set. `from_bundle` flags rows materialized from an attached bundle
    (#107 4d): read-only in the UI, owned by the bundle attachment."""
    d = dict(row)
    enc = d.pop("auth_token_encrypted", "") or ""
    d["has_auth_token"] = bool(enc)
    d["from_bundle"] = bool(d.get("bundle_attachment_id"))
    return d


@app.get("/api/mcp-servers")
async def list_mcp_servers(request: Request):
    if pool is None:
        raise HTTPException(503, "pool not initialized")
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        cat = await _active_use_category(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        # Scope-aware (#107): the active project's MCP servers UNION platform-scoped
        # (project_id NULL) UNION the active use-category's — project items first.
        rows = await conn.fetch(
            f"SELECT * FROM mcp_server_configs WHERE {_scope_where('$1','$2')} "
            "ORDER BY (project_id IS NULL), name", pid, cat
        )
    return [_mcp_public(r) for r in rows]


@app.post("/api/mcp-servers", status_code=201)
async def create_mcp_server(payload: MCPServerIn, request: Request):
    user = get_user(request)
    if pool is None:
        raise HTTPException(503, "pool not initialized")
    token_enc = crypto.encrypt(payload.auth_token) if payload.auth_token else ""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_INTEGRATIONS, pid)
        row = await conn.fetchrow(
            """INSERT INTO mcp_server_configs
                 (name, sse_url, description, enabled, use_uc_assist, auth_token_encrypted, created_by, project_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING *""",
            payload.name, payload.sse_url.rstrip("/"),
            payload.description, payload.enabled, payload.use_uc_assist, token_enc, user, pid,
        )
    return _mcp_public(row)


@app.put("/api/mcp-servers/{mid}")
async def update_mcp_server(mid: int, payload: MCPServerIn, request: Request):
    if pool is None:
        raise HTTPException(503, "pool not initialized")
    # Blank/omitted auth_token preserves the stored value; a non-empty value
    # replaces it (encrypted). Pass NULL when preserving so the SQL keeps it.
    token_enc = crypto.encrypt(payload.auth_token) if payload.auth_token else None
    async with pool.acquire() as conn:
        cur = await conn.fetchrow("SELECT project_id, bundle_attachment_id FROM mcp_server_configs WHERE id=$1", mid)
        if cur is None:
            raise HTTPException(404, "MCP server not found")
        if cur["bundle_attachment_id"]:
            raise HTTPException(409, "this server is provided by an attached bundle — edit the bundle instead")
        owner = cur["project_id"]
        await _require_priv_conn(conn, request, rbac.P_PROJECT_INTEGRATIONS, owner)
        row = await conn.fetchrow(
            """UPDATE mcp_server_configs
               SET name=$1, sse_url=$2, description=$3, enabled=$4, use_uc_assist=$5,
                   auth_token_encrypted = COALESCE($7, auth_token_encrypted),
                   updated_at=now()
               WHERE id=$6 AND bundle_attachment_id IS NULL RETURNING *""",
            payload.name, payload.sse_url.rstrip("/"),
            payload.description, payload.enabled, payload.use_uc_assist, mid, token_enc,
        )
    if not row:
        raise HTTPException(404, "MCP server not found")
    return _mcp_public(row)


@app.delete("/api/mcp-servers/{mid}", status_code=204)
async def delete_mcp_server(mid: int, request: Request):
    if pool is None:
        raise HTTPException(503, "pool not initialized")
    async with pool.acquire() as conn:
        cur = await conn.fetchrow("SELECT project_id, bundle_attachment_id FROM mcp_server_configs WHERE id=$1", mid)
        if cur is None:
            return
        if cur["bundle_attachment_id"]:
            raise HTTPException(409, "this server is provided by an attached bundle — detach the bundle instead")
        await _require_priv_conn(conn, request, rbac.P_PROJECT_INTEGRATIONS, cur["project_id"])
        await conn.execute("DELETE FROM mcp_server_configs WHERE id=$1 AND bundle_attachment_id IS NULL", mid)


@app.get("/api/mcp-servers/health")
async def mcp_servers_health(request: Request):
    """Poll /health on each registered MCP server (active project); per-server status."""
    if pool is None:
        raise HTTPException(503, "pool not initialized")
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        cat = await _active_use_category(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        # Scope-aware (#107): health-check the project's + platform + use-category servers.
        rows = await conn.fetch(
            "SELECT id, name, sse_url, enabled, auth_token_encrypted "
            f"FROM mcp_server_configs WHERE {_scope_where('$1','$2')} "
            "ORDER BY (project_id IS NULL), name", pid, cat
        )

    async def check(row):
        if not row["enabled"]:
            return {"id": row["id"], "name": row["name"], "enabled": False, "healthy": False}
        base = row["sse_url"].rsplit("/sse", 1)[0]
        health_url = f"{base}/health"
        # Present the stored bearer token (servers behind the auth gate require it).
        headers = {}
        enc = row["auth_token_encrypted"] or ""
        if enc:
            try:
                tok = crypto.decrypt(enc)
                if tok:
                    headers["Authorization"] = f"Bearer {tok}"
            except Exception:
                pass
        t0 = time.monotonic()
        try:
            # Verify TLS — the poll carries the bearer token, so an unverified
            # connection would expose it to a MITM. MCP servers must present a
            # valid cert (dav-docs-mcp uses a Let's Encrypt cert via its LB).
            async with httpx.AsyncClient(timeout=5.0) as cx:
                resp = await cx.get(health_url, headers=headers)
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


# ========================= BUNDLES (#107 Phase 4b) =========================
# Versioned, immutable, attachable packages of config/capability/output items.
# Manage (create/edit/publish) = usecat.manage; attach-to-project = project.integrations;
# attach-to-platform/use-category = usecat.manage. Publishing SNAPSHOTS each referenced
# item's non-secret definition (secrets are NEVER snapshotted) so a version is frozen.

_BUNDLE_ITEM_TYPES = {"mcp_server", "model_config", "managed_repo", "output_template",
                      "model_default", "capability_term", "capability_entry"}


class BundleIn(BaseModel):
    name: str
    kind: str = "mixed"            # config | capability | output | mixed
    description: str = ""


class BundleItemIn(BaseModel):
    item_type: str
    source_id: Optional[int] = None     # snapshot from this row at publish
    item_data: Optional[dict] = None    # OR a hand-authored definition
    position: int = 0


class BundleAttachIn(BaseModel):
    project_id: Optional[int] = None    # NULL = platform-wide
    use_category: Optional[str] = None  # NULL = all categories


def _slugify(s: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return out or "bundle"


async def _snapshot_item(conn, item_type: str, source_id: int) -> dict:
    """Snapshot a source row's PUBLIC definition for an immutable bundle item — secrets
    (api_key / auth_token / PAT) are NEVER snapshotted."""
    if item_type == "mcp_server":
        r = await conn.fetchrow("SELECT name, sse_url, description, enabled, use_uc_assist, use_category FROM mcp_server_configs WHERE id=$1", source_id)
    elif item_type == "model_config":
        r = await conn.fetchrow("SELECT name, provider, endpoint_url, model_id, capabilities, enabled, is_local, use_arch_review, use_uc_assist, use_category FROM model_configs WHERE id=$1", source_id)
    elif item_type == "output_template":
        r = await conn.fetchrow("SELECT name, kind, description, content, use_category FROM output_templates WHERE id=$1", source_id)
    else:
        return {}
    if not r:
        return {}
    d = dict(r)
    if "capabilities" in d:
        d["capabilities"] = _parse_jsonb(d["capabilities"])
    return d


async def _bundle_public(conn, b) -> dict:
    """Bundle row + version / item / attachment counts."""
    d = dict(b)
    bid = d["id"]
    d["versions"] = await conn.fetchval("SELECT count(*) FROM bundle_versions WHERE bundle_id=$1", bid)
    d["attachments"] = await conn.fetchval("SELECT count(*) FROM bundle_attachments WHERE bundle_id=$1", bid)
    cur = d.get("current_version_id")
    d["item_count"] = (await conn.fetchval("SELECT count(*) FROM bundle_items WHERE bundle_version_id=$1", cur)) if cur else 0
    return d


async def _bundle_editable_version(conn, bundle_id: int, actor: str) -> int:
    """The bundle's editable DRAFT version id — create one (carrying the published version's
    items forward) if the latest version is already published."""
    latest = await conn.fetchrow(
        "SELECT id, version_no, status FROM bundle_versions WHERE bundle_id=$1 ORDER BY version_no DESC LIMIT 1", bundle_id)
    if latest and latest["status"] == "draft":
        return latest["id"]
    next_no = (latest["version_no"] + 1) if latest else 1
    vid = await conn.fetchval(
        "INSERT INTO bundle_versions (bundle_id, version_no, status, created_by) VALUES ($1,$2,'draft',$3) RETURNING id",
        bundle_id, next_no, actor)
    if latest:
        await conn.execute(
            "INSERT INTO bundle_items (bundle_version_id, item_type, item_data, source_id, position) "
            "SELECT $1, item_type, item_data, source_id, position FROM bundle_items WHERE bundle_version_id=$2",
            vid, latest["id"])
    return vid


@app.get("/api/bundles")
async def list_bundles(request: Request):
    """All bundles (cross-project shared resources) + counts. Any project member may browse
    (to attach); managing requires usecat.manage."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        rows = await conn.fetch("SELECT * FROM bundles ORDER BY name")
        return [await _bundle_public(conn, b) for b in rows]


@app.get("/api/bundles/attached")
async def list_attached_bundles(request: Request):
    """Bundles attached to the active (project × use-category) scope + their effective items."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        cat = await _active_use_category(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        atts = await conn.fetch(
            "SELECT ba.id, ba.bundle_id, ba.bundle_version_id, ba.project_id, ba.use_category, "
            "       b.name, b.slug, bv.version_no "
            "FROM bundle_attachments ba JOIN bundles b ON b.id=ba.bundle_id "
            "JOIN bundle_versions bv ON bv.id=ba.bundle_version_id "
            f"WHERE {_scope_where('$1','$2')}", pid, cat)
        out = []
        for a in atts:
            items = await conn.fetch(
                "SELECT item_type, item_data FROM bundle_items WHERE bundle_version_id=$1 ORDER BY position, id",
                a["bundle_version_id"])
            d = dict(a)
            d["items"] = [{"item_type": i["item_type"], "item_data": _parse_jsonb(i["item_data"])} for i in items]
            out.append(d)
        return out


@app.post("/api/bundles", status_code=201)
async def create_bundle(payload: BundleIn, request: Request):
    user = get_user(request)
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_USECAT_MANAGE, pid)
        slug = _slugify(name)
        if await conn.fetchval("SELECT 1 FROM bundles WHERE slug=$1", slug):
            slug = f"{slug}-{secrets.token_hex(3)}"
        bid = await conn.fetchval(
            "INSERT INTO bundles (name, slug, kind, description, created_by) VALUES ($1,$2,$3,$4,$5) RETURNING id",
            name, slug, payload.kind, payload.description, user)
        await conn.execute(
            "INSERT INTO bundle_versions (bundle_id, version_no, status, created_by) VALUES ($1,1,'draft',$2)", bid, user)
        b = await conn.fetchrow("SELECT * FROM bundles WHERE id=$1", bid)
    await audit.record(pool, action="bundle.create", actor=user, object_type="bundle", object_id=str(bid), summary=f"created bundle {name}")
    async with pool.acquire() as conn:
        return await _bundle_public(conn, b)


@app.get("/api/bundles/{bid}")
async def get_bundle(bid: int, request: Request):
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        b = await conn.fetchrow("SELECT * FROM bundles WHERE id=$1", bid)
        if not b:
            raise HTTPException(404, "bundle not found")
        versions = await conn.fetch(
            "SELECT id, version_no, status, note, created_by, created_at, published_at FROM bundle_versions WHERE bundle_id=$1 ORDER BY version_no DESC", bid)
        latest = versions[0] if versions else None
        items = await conn.fetch(
            "SELECT id, item_type, item_data, source_id, position FROM bundle_items WHERE bundle_version_id=$1 ORDER BY position, id", latest["id"]) if latest else []
        attachments = await conn.fetch(
            "SELECT id, bundle_version_id, project_id, use_category, attached_by, attached_at FROM bundle_attachments WHERE bundle_id=$1", bid)
        out = await _bundle_public(conn, b)
        out["version_list"] = [dict(v) for v in versions]
        out["items"] = [{**dict(i), "item_data": _parse_jsonb(i["item_data"])} for i in items]
        out["attachment_list"] = [dict(a) for a in attachments]
        return out


@app.post("/api/bundles/{bid}/items", status_code=201)
async def add_bundle_item(bid: int, payload: BundleItemIn, request: Request):
    user = get_user(request)
    if payload.item_type not in _BUNDLE_ITEM_TYPES:
        raise HTTPException(400, f"invalid item_type: {payload.item_type}")
    if not payload.source_id and not payload.item_data:
        raise HTTPException(400, "either source_id (reference) or item_data (hand-authored) is required")
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_USECAT_MANAGE, pid)
        if not await conn.fetchval("SELECT 1 FROM bundles WHERE id=$1", bid):
            raise HTTPException(404, "bundle not found")
        vid = await _bundle_editable_version(conn, bid, user)
        data = payload.item_data or {}
        if payload.source_id and not data:
            data = await _snapshot_item(conn, payload.item_type, payload.source_id)
        iid = await conn.fetchval(
            "INSERT INTO bundle_items (bundle_version_id, item_type, item_data, source_id, position) VALUES ($1,$2,$3,$4,$5) RETURNING id",
            vid, payload.item_type, json.dumps(data), payload.source_id, payload.position)
        return {"ok": True, "id": iid, "bundle_version_id": vid}


@app.delete("/api/bundle-items/{iid}", status_code=204)
async def delete_bundle_item(iid: int, request: Request):
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_USECAT_MANAGE, pid)
        v = await conn.fetchrow(
            "SELECT bv.status FROM bundle_items bi JOIN bundle_versions bv ON bv.id=bi.bundle_version_id WHERE bi.id=$1", iid)
        if not v:
            raise HTTPException(404, "item not found")
        if v["status"] != "draft":
            raise HTTPException(409, "cannot edit a published version — adding an item creates a new draft")
        await conn.execute("DELETE FROM bundle_items WHERE id=$1", iid)


@app.post("/api/bundles/{bid}/publish")
async def publish_bundle(bid: int, request: Request):
    """Publish the draft version: snapshot each referenced item's current non-secret
    definition, freeze it, and make it the bundle's current version."""
    user = get_user(request)
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_USECAT_MANAGE, pid)
        v = await conn.fetchrow("SELECT id, status FROM bundle_versions WHERE bundle_id=$1 ORDER BY version_no DESC LIMIT 1", bid)
        if not v:
            raise HTTPException(404, "bundle has no version")
        if v["status"] == "published":
            raise HTTPException(409, "the latest version is already published")
        vid = v["id"]
        items = await conn.fetch("SELECT id, item_type, source_id FROM bundle_items WHERE bundle_version_id=$1", vid)
        if not items:
            raise HTTPException(400, "cannot publish an empty version")
        for it in items:
            if it["source_id"]:
                snap = await _snapshot_item(conn, it["item_type"], it["source_id"])
                if snap:
                    await conn.execute("UPDATE bundle_items SET item_data=$1 WHERE id=$2", json.dumps(snap), it["id"])
        await conn.execute("UPDATE bundle_versions SET status='published', published_at=now() WHERE id=$1", vid)
        await conn.execute("UPDATE bundles SET current_version_id=$1, updated_at=now() WHERE id=$2", vid, bid)
        b = await conn.fetchrow("SELECT * FROM bundles WHERE id=$1", bid)
        result = await _bundle_public(conn, b)
    await audit.record(pool, action="bundle.publish", actor=user, object_type="bundle", object_id=str(bid), detail={"version_id": vid}, summary=f"published bundle {bid}")
    return result


async def _materialize_attachment(conn, aid: int, version_id: int, pid, cat) -> int:
    """Phase 4d — (re)materialize an attachment's config items into real scoped registry
    rows so runs actually consume them via the normal scope resolvers. Idempotent: clears
    this attachment's prior rows, then inserts fresh copies of the pinned version's items at
    the attachment scope. Skips an item whose (scope, name) already exists (a manual or other
    row wins). Secrets are absent — snapshots never carry them. Returns count materialized."""
    await conn.execute("DELETE FROM mcp_server_configs WHERE bundle_attachment_id=$1", aid)
    await conn.execute("DELETE FROM model_configs WHERE bundle_attachment_id=$1", aid)
    items = await conn.fetch(
        "SELECT item_type, item_data FROM bundle_items WHERE bundle_version_id=$1 ORDER BY position, id", version_id)
    n = 0
    for it in items:
        d = _parse_jsonb(it["item_data"]) or {}
        name = (d.get("name") or "").strip()
        if not name:
            continue
        if it["item_type"] == "mcp_server":
            clash = await conn.fetchval(
                "SELECT 1 FROM mcp_server_configs WHERE COALESCE(project_id,0)=COALESCE($1::bigint,0) "
                "AND COALESCE(use_category,'')=COALESCE($2,'') AND lower(name)=lower($3)", pid, cat, name)
            if clash:
                continue
            await conn.execute(
                "INSERT INTO mcp_server_configs (name, sse_url, description, enabled, use_uc_assist, "
                "project_id, use_category, bundle_attachment_id, created_by) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                name, d.get("sse_url", ""), d.get("description", ""), bool(d.get("enabled", True)),
                bool(d.get("use_uc_assist", False)), pid, cat, aid, "system-bundle")
            n += 1
        elif it["item_type"] == "model_config":
            clash = await conn.fetchval(
                "SELECT 1 FROM model_configs WHERE COALESCE(project_id,0)=COALESCE($1::bigint,0) "
                "AND COALESCE(use_category,'')=COALESCE($2,'') AND lower(name)=lower($3)", pid, cat, name)
            if clash:
                continue
            await conn.execute(
                "INSERT INTO model_configs (name, provider, endpoint_url, model_id, capabilities, enabled, "
                "is_local, use_arch_review, use_uc_assist, project_id, use_category, bundle_attachment_id, created_by) "
                "VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11,$12,$13)",
                name, d.get("provider", "openai"), d.get("endpoint_url", ""), d.get("model_id", ""),
                json.dumps(d.get("capabilities") or []), bool(d.get("enabled", True)), bool(d.get("is_local", False)),
                bool(d.get("use_arch_review", True)), bool(d.get("use_uc_assist", False)), pid, cat, aid, "system-bundle")
            n += 1
        # output_template items are not materialized into a consumption registry yet (4e).
    return n


@app.post("/api/bundles/{bid}/attach", status_code=201)
async def attach_bundle(bid: int, payload: BundleAttachIn, request: Request):
    """Pin the bundle's current published version at a (project × use-category) scope.
    Attach-to-project → project.integrations; platform/use-category → usecat.manage."""
    user = get_user(request)
    async with pool.acquire() as conn:
        if payload.project_id is not None:
            await _require_priv_conn(conn, request, rbac.P_PROJECT_INTEGRATIONS, payload.project_id)
        else:
            pid = await _active_project_id(request, conn)
            await _require_priv_conn(conn, request, rbac.P_USECAT_MANAGE, pid)
        b = await conn.fetchrow("SELECT current_version_id FROM bundles WHERE id=$1", bid)
        if not b:
            raise HTTPException(404, "bundle not found")
        if not b["current_version_id"]:
            raise HTTPException(409, "bundle has no published version to attach")
        vid = b["current_version_id"]
        async with conn.transaction():
            row = await conn.fetchrow(
                "INSERT INTO bundle_attachments (bundle_id, bundle_version_id, project_id, use_category, attached_by) "
                "VALUES ($1,$2,$3,$4,$5) "
                "ON CONFLICT (bundle_id, COALESCE(project_id,0), COALESCE(use_category,'')) "
                "DO UPDATE SET bundle_version_id=EXCLUDED.bundle_version_id, attached_by=EXCLUDED.attached_by, attached_at=now() RETURNING id",
                bid, vid, payload.project_id, payload.use_category, user)
            materialized = await _materialize_attachment(conn, row["id"], vid, payload.project_id, payload.use_category)
    await audit.record(pool, action="bundle.attach", actor=user, object_type="bundle", object_id=str(bid), detail={"project_id": payload.project_id, "use_category": payload.use_category, "materialized": materialized}, summary=f"attached bundle {bid} ({materialized} item(s) live)")
    return {"ok": True, "id": row["id"], "bundle_version_id": vid, "materialized": materialized}


@app.delete("/api/bundle-attachments/{aid}", status_code=204)
async def detach_bundle(aid: int, request: Request):
    user = get_user(request)
    async with pool.acquire() as conn:
        a = await conn.fetchrow("SELECT bundle_id, project_id FROM bundle_attachments WHERE id=$1", aid)
        if not a:
            raise HTTPException(404, "attachment not found")
        if a["project_id"] is not None:
            await _require_priv_conn(conn, request, rbac.P_PROJECT_INTEGRATIONS, a["project_id"])
        else:
            pid = await _active_project_id(request, conn)
            await _require_priv_conn(conn, request, rbac.P_USECAT_MANAGE, pid)
        await conn.execute("DELETE FROM bundle_attachments WHERE id=$1", aid)
    await audit.record(pool, action="bundle.detach", actor=user, object_type="bundle", object_id=str(a["bundle_id"]), summary=f"detached attachment {aid}")


@app.delete("/api/bundles/{bid}", status_code=204)
async def delete_bundle(bid: int, request: Request):
    """Delete a bundle and ALL its versions/items/attachments (CASCADE). usecat.manage."""
    user = get_user(request)
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_USECAT_MANAGE, pid)
        if not await conn.fetchval("SELECT 1 FROM bundles WHERE id=$1", bid):
            raise HTTPException(404, "bundle not found")
        await conn.execute("DELETE FROM bundles WHERE id=$1", bid)
    await audit.record(pool, action="bundle.delete", actor=user, object_type="bundle", object_id=str(bid), summary=f"deleted bundle {bid}")


# ========================= REVIEW MODELS =========================


@app.get("/api/models")
async def list_review_models(request: Request):
    """List the active project's configured model endpoints; api_key is masked."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        cat = await _active_use_category(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        rows = await conn.fetch(
            """SELECT id, name, provider, endpoint_url, model_id,
                      CASE WHEN api_key != '' THEN '••••••••' ELSE '' END AS api_key,
                      enabled, is_local, use_arch_review, use_uc_assist,
                      capabilities, bundle_attachment_id,
                      created_by, created_at, updated_at
               FROM model_configs
               WHERE (project_id IS NULL OR project_id=$1) AND (use_category IS NULL OR use_category=$2)
               ORDER BY (project_id IS NULL), created_at""", pid, cat
        )
    out = []
    for r in rows:
        d = dict(r)
        d["capabilities"] = _parse_jsonb(d.get("capabilities"))
        d["from_bundle"] = bool(d.get("bundle_attachment_id"))  # #107 4d: read-only, bundle-owned
        out.append(d)
    return out


@app.post("/api/models", status_code=201)
async def create_review_model(payload: ModelConfigIn, request: Request):
    user = get_user(request)
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_MODELS, pid)
        try:
            row = await conn.fetchrow(
                """INSERT INTO model_configs
                     (name, provider, endpoint_url, model_id, api_key, enabled,
                      is_local, use_arch_review, use_uc_assist, capabilities, created_by, project_id)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                   RETURNING id, name, provider, endpoint_url, model_id, enabled,
                             is_local, use_arch_review, use_uc_assist, capabilities,
                             created_at""",
                payload.name, payload.provider, payload.endpoint_url,
                payload.model_id, payload.api_key, payload.enabled,
                payload.is_local, payload.use_arch_review, payload.use_uc_assist,
                json.dumps(payload.capabilities or {}), user, pid,
            )
        except Exception as e:
            if "unique" in str(e).lower():
                raise HTTPException(409, f"A model named '{payload.name}' already exists")
            raise HTTPException(500, str(e))
    d = dict(row)
    d["capabilities"] = _parse_jsonb(d.get("capabilities"))
    return d


async def _probe_model_endpoint(provider: str, endpoint_url: str, api_key: str) -> dict:
    """Connection-test + list models from an endpoint, mirroring the exact URL/auth
    convention the generation code uses (so a green probe means the model is callable):
    OpenAI-compatible → `GET {base}/v1/models` + `Authorization: Bearer`; Anthropic →
    `GET {base}/v1/models` + `x-api-key` + `anthropic-version`. Read-only; persists nothing."""
    base = (endpoint_url or "").rstrip("/")
    out = {"reachable": False, "url": None, "status_code": None,
           "models": [], "latency_ms": None, "error": None}
    if not base.startswith(("http://", "https://")):
        out["error"] = f"invalid endpoint: {endpoint_url!r} (must start with http:// or https://)"
        return out
    if base.endswith("/v1"):           # match the chat code — never produce /v1/v1
        base = base[:-3]
    url = f"{base}/v1/models"
    headers: dict[str, str] = {}
    if provider == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
        if api_key:
            headers["x-api-key"] = api_key
    elif api_key:                       # local vLLM has no key — omit the header entirely
        headers["Authorization"] = f"Bearer {api_key}"
    out["url"] = url
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as cx:
            resp = await cx.get(url, headers=headers)
        out["latency_ms"] = int((time.time() - start) * 1000)
        out["status_code"] = resp.status_code
        if 200 <= resp.status_code < 300:
            out["reachable"] = True
            try:
                data = resp.json()
                items = data.get("data") if isinstance(data, dict) else None
                if isinstance(items, list):
                    out["models"] = sorted(
                        m.get("id") for m in items if isinstance(m, dict) and m.get("id"))
            except Exception as e:
                out["error"] = f"connected, but could not parse the /v1/models response: {e}"
        elif resp.status_code in (401, 403):
            out["error"] = f"HTTP {resp.status_code} — authentication required or invalid (check the API key)"
        elif resp.status_code == 404:
            out["error"] = "HTTP 404 — no /v1/models at this endpoint (it may not be OpenAI-compatible)"
        else:
            out["error"] = f"HTTP {resp.status_code}"
    except httpx.TimeoutException:
        out["latency_ms"] = int((time.time() - start) * 1000)
        out["error"] = "timeout (8s) — endpoint unreachable from the API pod (check the URL / egress)"
    except httpx.RequestError as e:
        out["latency_ms"] = int((time.time() - start) * 1000)
        out["error"] = f"connection error: {e}"
    except Exception as e:
        out["error"] = f"probe failed: {e}"
    return out


@app.post("/api/models/probe")
async def probe_review_model(payload: ModelProbeIn, request: Request):
    """Connection-test an endpoint and return its available model IDs, so the Add Model
    dialog can offer them for selection instead of hand-typing. Nothing is persisted."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_MODELS, pid)
    return await _probe_model_endpoint(payload.provider, payload.endpoint_url, payload.api_key)


@app.put("/api/models/{mid}")
async def update_review_model(mid: int, payload: ModelConfigIn, request: Request):
    async with pool.acquire() as conn:
        cur = await conn.fetchrow("SELECT project_id, bundle_attachment_id FROM model_configs WHERE id=$1", mid)
        if cur is None:
            raise HTTPException(404, "Model config not found")
        if cur["bundle_attachment_id"]:
            raise HTTPException(409, "this model is provided by an attached bundle — edit the bundle instead")
        owner = cur["project_id"]
        await _require_priv_conn(conn, request, rbac.P_PROJECT_MODELS, owner)
        row = await conn.fetchrow(
            """UPDATE model_configs
               SET name=$1, provider=$2, endpoint_url=$3, model_id=$4,
                   api_key = CASE WHEN $5 != '' THEN $5 ELSE api_key END,
                   enabled=$6, is_local=$7, use_arch_review=$8, use_uc_assist=$9,
                   capabilities=$10::jsonb,
                   updated_at=now()
               WHERE id=$11 AND project_id=$12 AND bundle_attachment_id IS NULL RETURNING id""",
            payload.name, payload.provider, payload.endpoint_url,
            payload.model_id, payload.api_key, payload.enabled,
            payload.is_local, payload.use_arch_review, payload.use_uc_assist,
            json.dumps(payload.capabilities or {}), mid, owner,
        )
    if not row:
        raise HTTPException(404, "Model config not found")
    return {"ok": True}


@app.delete("/api/models/{mid}")
async def delete_review_model(mid: int, request: Request):
    async with pool.acquire() as conn:
        cur = await conn.fetchrow("SELECT project_id, bundle_attachment_id FROM model_configs WHERE id=$1", mid)
        if cur is None:
            return {"ok": True}
        if cur["bundle_attachment_id"]:
            raise HTTPException(409, "this model is provided by an attached bundle — detach the bundle instead")
        await _require_priv_conn(conn, request, rbac.P_PROJECT_MODELS, cur["project_id"])
        await conn.execute("DELETE FROM model_configs WHERE id=$1 AND bundle_attachment_id IS NULL", mid)
    return {"ok": True}


# ----- Per-(model, use) sampling profiles -----
# These let operators tune sampling params per-use without an engine
# rebuild. Engine resolves at run start as:
#   per-run CLI/UI override > use_profile row > mode default in code
# Engine drops any param the model's capabilities flag as unsupported,
# regardless of which layer set it. See migration 014.


@app.get("/api/models/{mid}/profiles")
async def list_model_use_profiles(mid: int, request: Request):
    async with pool.acquire() as conn:
        owner = await conn.fetchval("SELECT project_id FROM model_configs WHERE id=$1", mid)
        if owner is None:
            raise HTTPException(404, "model not found")
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, owner)
        rows = await conn.fetch(
            """SELECT id, model_config_id, use_key, params, notes,
                      updated_by, created_at, updated_at
               FROM model_use_profiles
               WHERE model_config_id=$1
               ORDER BY use_key""",
            mid,
        )
    out = []
    for r in rows:
        d = dict(r)
        d["params"] = _parse_jsonb(d.get("params"))
        out.append(d)
    return out


@app.get("/api/models/{mid}/profiles/{use_key}")
async def get_model_use_profile(mid: int, use_key: str, request: Request):
    if use_key not in _VALID_USE_KEYS:
        raise HTTPException(400, f"unknown use_key {use_key!r} — valid: {sorted(_VALID_USE_KEYS)}")
    async with pool.acquire() as conn:
        owner = await conn.fetchval("SELECT project_id FROM model_configs WHERE id=$1", mid)
        if owner is None:
            raise HTTPException(404, "model not found")
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, owner)
        row = await conn.fetchrow(
            """SELECT id, model_config_id, use_key, params, notes,
                      updated_by, created_at, updated_at
               FROM model_use_profiles
               WHERE model_config_id=$1 AND use_key=$2""",
            mid, use_key,
        )
    if not row:
        raise HTTPException(404, "profile not found")
    d = dict(row)
    d["params"] = _parse_jsonb(d.get("params"))
    return d


@app.put("/api/models/{mid}/profiles/{use_key}")
async def set_model_use_profile(mid: int, use_key: str, payload: ModelUseProfileIn, request: Request):
    if use_key not in _VALID_USE_KEYS:
        raise HTTPException(400, f"unknown use_key {use_key!r} — valid: {sorted(_VALID_USE_KEYS)}")
    user = get_user(request)
    async with pool.acquire() as conn:
        # Verify model exists + caller can manage its project. Disabled models can
        # still have profiles edited so operators can prep the swap before flipping.
        owner = await conn.fetchval("SELECT project_id FROM model_configs WHERE id=$1", mid)
        if owner is None:
            raise HTTPException(404, "model not found")
        await _require_priv_conn(conn, request, rbac.P_PROJECT_MODELS, owner)
        await conn.execute(
            """INSERT INTO model_use_profiles
                 (model_config_id, use_key, params, notes, updated_by, updated_at)
               VALUES ($1, $2, $3::jsonb, $4, $5, NOW())
               ON CONFLICT (model_config_id, use_key) DO UPDATE
                 SET params     = EXCLUDED.params,
                     notes      = EXCLUDED.notes,
                     updated_by = EXCLUDED.updated_by,
                     updated_at = NOW()""",
            mid, use_key, json.dumps(payload.params or {}),
            payload.notes or "", user,
        )
    return {"ok": True}


@app.delete("/api/models/{mid}/profiles/{use_key}")
async def delete_model_use_profile(mid: int, use_key: str, request: Request):
    async with pool.acquire() as conn:
        owner = await conn.fetchval("SELECT project_id FROM model_configs WHERE id=$1", mid)
        if owner is None:
            return {"ok": True}
        await _require_priv_conn(conn, request, rbac.P_PROJECT_MODELS, owner)
        await conn.execute(
            "DELETE FROM model_use_profiles WHERE model_config_id=$1 AND use_key=$2",
            mid, use_key,
        )
    return {"ok": True}


# ========================= MODEL DEFAULTS =========================

# Each entry is a distinct "use of a model" that has a project-scoped default
# selectable in Config. Per-view override pickers send an explicit model when
# the operator overrides; otherwise the endpoint resolves the default here.
#   arch-review  — Architectural Review
#   enhancement  — Enhancement Spec (falls back to arch-review when unset)
#   evaluation   — A/B evaluation runs
#   uc-authoring — UC authoring help: assist panel, wizard generate/refine,
#                  bulk-from-text extraction (one default, per-view overrides)
# The new-run engine default is NOT here — it lives in the inference source
# ConfigMap (Config → Pipeline Sources → Inference), which the pipeline reads.
_VALID_DEFAULT_KEYS = {"evaluation", "arch-review", "enhancement", "uc-authoring", "assessment-ingest"}


class ModelDefaultIn(BaseModel):
    model_config_id: Optional[int] = None


async def _model_default_row(conn, *default_keys, project_id: int) -> Optional[dict]:
    """Return the model_configs row (as dict) for the first set, enabled default
    among default_keys *within the given project*, or None. Lets an endpoint
    chain fallbacks, e.g. enhancement → arch-review. Both the default pointer and
    the model it resolves to are scoped to project_id (strict isolation)."""
    for key in default_keys:
        did = await conn.fetchval(
            "SELECT model_config_id FROM model_defaults WHERE key=$1 AND project_id=$2",
            key, project_id,
        )
        if did is not None:
            row = await conn.fetchrow(
                "SELECT * FROM model_configs WHERE id=$1 AND (project_id IS NULL OR project_id=$2) AND enabled",  # scope-aware (#107 2b): platform models too
                did, project_id,
            )
            if row:
                return dict(row)
    return None


# ── Cached Architectural Review / Enhancement Plan output ────────────────────
# Generations over a run's immutable analysis. Cached per (run_id, kind, scope,
# uc_uuid); staleness is decided by comparing source_ingested_at to the run's
# current MAX(analysis_runs.ingested_at) — a re-ingest of the same run_id makes
# the cache stale and the UI offers a refresh.

async def _store_output_cache(run_id: str, kind: str, scope: str,
                              uc_uuid: Optional[str], content: str,
                              model_label: str, user: str) -> None:
    """Write-through cache after a successful generation. Best-effort: a cache
    write must never break the stream, so callers wrap this in try/except."""
    if pool is None or not content.strip():
        log.info("output cache SKIP store: run=%s kind=%s reason=%s",
                 run_id, kind, "no-pool" if pool is None else "empty-content")
        return
    async with pool.acquire() as conn:
        ingested = await conn.fetchval(
            "SELECT MAX(ingested_at) FROM analysis_runs WHERE run_id=$1", run_id
        )
        log.info("output cache STORE: run=%s kind=%s scope=%s uc=%r len=%d ingested=%s",
                 run_id, kind, scope, uc_uuid or "", len(content), ingested)
        await conn.execute(
            """INSERT INTO analysis_output_cache
                 (run_id, kind, scope, uc_uuid, content, model_label,
                  source_ingested_at, created_by, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8, now())
               ON CONFLICT (run_id, kind, scope, uc_uuid) DO UPDATE
                 SET content            = EXCLUDED.content,
                     model_label        = EXCLUDED.model_label,
                     source_ingested_at = EXCLUDED.source_ingested_at,
                     created_by         = EXCLUDED.created_by,
                     created_at         = now()""",
            run_id, kind, scope, uc_uuid or "", content, model_label or "",
            ingested, user,
        )


@app.get("/api/analysis/output")
async def get_cached_output(
    request: Request,
    run_id: str = Query(..., min_length=1),
    kind: str = Query(..., pattern="^(review|enhancement)$"),
    scope: str = Query("run", pattern="^(run|uc|set)$"),
    uc_uuid: str = Query(""),
):
    """Return the cached generation for a run/scope/UC, or {cached: false}.

    `stale` is true when the run has been re-ingested since the cache was
    written (source_ingested_at older than the run's current MAX ingest)."""
    if pool is None:
        raise HTTPException(503, "pool not initialized")
    async with pool.acquire() as conn:
        # Sovereignty: cached review/enhancement output is post-ingestion, so the run is in
        # analysis_runs — enforce it belongs to the active project (was a cross-project IDOR).
        await _require_run_in_project(conn, request, run_id)
        row = await conn.fetchrow(
            """SELECT content, model_label, source_ingested_at, created_at, created_by
               FROM analysis_output_cache
               WHERE run_id=$1 AND kind=$2 AND scope=$3 AND uc_uuid=$4""",
            run_id, kind, scope, uc_uuid or "",
        )
        if not row:
            log.info("output cache MISS: run=%s kind=%s scope=%s uc=%r",
                     run_id, kind, scope, uc_uuid or "")
            return {"cached": False}
        current = await conn.fetchval(
            "SELECT MAX(ingested_at) FROM analysis_runs WHERE run_id=$1", run_id
        )
    stale = bool(current and row["source_ingested_at"] and current > row["source_ingested_at"])
    _c = row["content"] or ""
    log.info("output cache HIT: run=%s kind=%s scope=%s uc=%r len=%d stale=%s think_open=%d think_close=%d head=%r tail=%r",
             run_id, kind, scope, uc_uuid or "", len(_c), stale,
             _c.count("<think>"), _c.count("</think>"), _c[:160], _c[-160:])
    return {
        "cached": True,
        "content": row["content"],
        "model_label": row["model_label"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "created_by": row["created_by"],
        "stale": stale,
    }


# ── Resilient (tab-close-proof) generation ───────────────────────────────────
# The LLM consumption + cache write run in a background task that is NOT tied to
# the client's SSE connection. If the browser navigates away or closes the tab,
# the task keeps running and stores the result; the next view of that run loads
# it from cache. The SSE response merely observes a shared buffer, so cancelling
# it (client disconnect) never stops the underlying generation.
_active_gen: dict = {}   # key -> {buf: list[str], done: bool, error: str|None, task}


async def _run_generation_bg(state: dict, key, *, provider, endpoint_url, model_id,
                             api_key, system_prompt, user_prompt, run_id, kind,
                             scope, uc_uuid, model_label, user):
    from . import arch_review as _ar
    try:
        async for chunk in _ar.stream_review(
            provider=provider, endpoint_url=endpoint_url, model_id=model_id,
            api_key=api_key, system_prompt=system_prompt, user_prompt=user_prompt,
        ):
            state["buf"].append(chunk)
    except Exception as exc:
        log.exception("background generation error (%s)", kind)
        state["error"] = str(exc)
    if state["error"] is None:
        try:
            await _store_output_cache(run_id, kind, scope, uc_uuid,
                                      "".join(state["buf"]), model_label, user)
        except Exception:
            log.warning("resilient cache write failed", exc_info=True)
    state["done"] = True
    # Linger briefly so a still-connected client can drain the tail, then free
    # memory — the result lives in the cache from here on.
    try:
        await asyncio.sleep(45)
    finally:
        _active_gen.pop(key, None)


def _ensure_generation(key, **kwargs) -> dict:
    """Start a background generation for `key` unless one is already in flight
    (a second click/observer attaches to the running one rather than re-calling
    the model)."""
    existing = _active_gen.get(key)
    if existing is not None and not existing["done"]:
        return existing
    state = {"buf": [], "done": False, "error": None, "task": None}
    _active_gen[key] = state
    state["task"] = asyncio.create_task(_run_generation_bg(state, key, **kwargs))
    return state


async def _observe_generation(key):
    """SSE generator mirroring a background generation's buffer. Same wire
    protocol as before: data: {text}, data: {error}, data: [DONE]."""
    state = _active_gen.get(key)
    if state is None:
        yield "data: [DONE]\n\n"
        return
    idx = 0
    while True:
        buf = state["buf"]
        while idx < len(buf):
            yield f"data: {json.dumps({'text': buf[idx]})}\n\n"
            idx += 1
        if state["done"]:
            if state["error"]:
                yield f"data: {json.dumps({'error': state['error']})}\n\n"
            yield "data: [DONE]\n\n"
            return
        await asyncio.sleep(0.08)


@app.get("/api/model-defaults")
async def get_model_defaults(request: Request):
    """Return the active project's model defaults keyed by pipeline type."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        rows = await conn.fetch(
            "SELECT key, model_config_id FROM model_defaults WHERE project_id=$1", pid)
    return {r["key"]: r["model_config_id"] for r in rows}


@app.put("/api/model-defaults/{key}")
async def set_model_default(key: str, payload: ModelDefaultIn, request: Request):
    """Set or clear the active project's model default for a pipeline type."""
    if key not in _VALID_DEFAULT_KEYS:
        raise HTTPException(400, f"unknown default key: {key!r} — valid: {sorted(_VALID_DEFAULT_KEYS)}")
    user = get_user(request)
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_MODELS, pid)
        if payload.model_config_id is not None:
            exists = await conn.fetchval(
                "SELECT 1 FROM model_configs WHERE id=$1 AND project_id=$2 AND enabled",
                payload.model_config_id, pid,
            )
            if not exists:
                raise HTTPException(404, "model config not found or disabled in this project")
        await conn.execute(
            """INSERT INTO model_defaults (key, model_config_id, project_id, updated_by, updated_at)
               VALUES ($1, $2, $3, $4, NOW())
               ON CONFLICT (project_id, key) DO UPDATE
               SET model_config_id = EXCLUDED.model_config_id,
                   updated_by      = EXCLUDED.updated_by,
                   updated_at      = NOW()""",
            key, payload.model_config_id, pid, user,
        )
    return {"ok": True}


# ========================= ARCHITECTURAL REVIEW =========================


# ── Projects (tenancy foundation) + per-stage LLM context ────────────────────
async def _default_project_id(conn) -> Optional[int]:
    return await conn.fetchval("SELECT id FROM projects WHERE slug='default'")


async def _stage_context(conn, stage: str, project_id: Optional[int] = None) -> str:
    """Architect-set context/instructions for a stage, scoped to a project."""
    if project_id is None:
        project_id = await _default_project_id(conn)
    if project_id is None:
        return ""
    row = await conn.fetchval(
        "SELECT content FROM project_stage_context WHERE project_id=$1 AND stage=$2",
        project_id, stage,
    )
    return (row or "").strip()


def _inject_context(user_prompt: str, context: str, stage_label: str) -> str:
    """Append architect-set project/stage context to an LLM user prompt."""
    if not context:
        return user_prompt
    return (
        user_prompt
        + f"\n\n--- Project context & instructions for the {stage_label} stage "
          f"(set by the architect — honor these) ---\n{context}\n"
    )


_PROJECT_MEMBER_COUNT_SQL = (
    "(SELECT count(DISTINCT lower(ar.reviewer)) FROM rbac_account_roles ar "
    " JOIN rbac_roles ro ON ro.id=ar.role_id AND ro.scope='project' "
    " WHERE ar.project_id=p.id) AS member_count")


@app.get("/api/projects")
async def list_projects(request: Request, show_archived: bool = Query(False)):
    """Projects for the RBAC/admin views. PLATFORM admins see ALL projects (so
    they can assign anyone anywhere / move data); everyone else sees only the
    projects they're a member of. Membership/role come from RBAC."""
    user = get_user(request)
    platform = (not _multiuser()) or await _has_priv(user, rbac.P_PLATFORM_ADMIN)
    async with pool.acquire() as conn:
        myroles = await _user_project_roles(conn, user)          # direct project roles (for my_role display)
        accessible = await _accessible_project_ids(conn, user)   # direct + tenant + group (visibility)
        cols = ("p.id, p.slug, p.name, p.description, p.created_by, p.created_at, p.archived, "
                "p.is_exclusive, "
                + _PROJECT_MEMBER_COUNT_SQL)
        if platform:
            rows = await conn.fetch(
                f"SELECT {cols} FROM projects p WHERE ($1 OR NOT p.archived) "
                "ORDER BY (p.slug='default') DESC, p.name", show_archived)
        else:
            rows = await conn.fetch(
                f"SELECT {cols} FROM projects p WHERE p.id = ANY($1::bigint[]) AND ($2 OR NOT p.archived) "
                "ORDER BY (p.slug='default') DESC, p.name", list(accessible), show_archived)
    return {"projects": [{**dict(r), "created_at": r["created_at"].isoformat(),
                          "my_role": _legacy_proj_role(myroles.get(r["id"], [])),
                          "is_member": r["id"] in accessible} for r in rows]}


@app.get("/api/projects/mine")
async def my_projects(request: Request):
    """The top-bar switcher source: ONLY projects the caller is a member of
    (platform admins included — they add themselves to projects to access data).
    Also returns the caller's resolved default project."""
    user = get_user(request)
    async with pool.acquire() as conn:
        if not _multiuser():
            rows = await conn.fetch(
                "SELECT id, slug, name FROM projects WHERE NOT archived ORDER BY (slug='default') DESC, name")
        else:
            # Phase 1b-2: include projects reachable via a tenant/group role, not just direct
            # project bindings — so a tenant-viewer/admin sees the tenant's projects in the switcher.
            accessible = await _accessible_project_ids(conn, user)
            rows = await conn.fetch(
                "SELECT p.id, p.slug, p.name FROM projects p "
                "WHERE NOT p.archived AND p.id = ANY($1::bigint[]) "
                "ORDER BY (p.slug='default') DESC, p.name", list(accessible))
        member_ids = [r["id"] for r in rows]
        default_pid = await _resolve_default_project(conn, user, member_ids) if _multiuser() else None
    return {"projects": [dict(r) for r in rows], "default_project_id": default_pid}


# ========================= CUSTOMERS (first-class entity; customer-demand epic) =========================
# Customers are platform-level, orthogonal to projects (M:N). Phase-2a: entity CRUD +
# associations + demand rollup. Access here is single-axis (customer.view/edit on the
# customer; platform-admin superuser). The (customer × project) matrix enforcement on
# cell resources is a later slice. See docs/customer-demand-dedup-design.md.

class CustomerIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    is_exclusive: bool = False

class CustomerPatchIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_exclusive: Optional[bool] = None
    archived: Optional[bool] = None

class CustomerProjectIn(BaseModel):
    project_id: int

_CUSTOMER_DEMAND_SQL = (
    "(SELECT count(DISTINCT r.uc_uuid) FROM uc_customer_requests r WHERE r.customer_id=c.id) AS uc_count, "
    "(SELECT count(*) FROM uc_customer_requests r WHERE r.customer_id=c.id) AS request_count, "
    "(SELECT count(*) FROM customer_projects cp WHERE cp.customer_id=c.id) AS project_count")


async def _customer_visible_ids(conn, user: str):
    """None = sees all (platform-admin / single-user); else the customer ids the user
    holds any customer-scoped role on."""
    if (not _multiuser()) or await _has_priv(user, rbac.P_PLATFORM_ADMIN):
        return None
    rows = await conn.fetch(
        "SELECT DISTINCT ar.customer_id FROM rbac_account_roles ar "
        "JOIN rbac_roles ro ON ro.id=ar.role_id AND ro.scope='customer' "
        "WHERE lower(ar.reviewer)=lower($1) AND ar.customer_id IS NOT NULL", user)
    return [r["customer_id"] for r in rows]


@app.get("/api/customers")
async def list_customers(request: Request, show_archived: bool = Query(False)):
    """Customers visible to the caller (platform-admin = all; else the customers they
    hold a customer-scoped role on), with demand + association rollups."""
    user = get_user(request)
    async with pool.acquire() as conn:
        vis = await _customer_visible_ids(conn, user)
        cols = ("c.id, c.slug, c.name, c.description, c.is_exclusive, c.is_universal, "
                f"c.archived, c.created_at, {_CUSTOMER_DEMAND_SQL}")
        if vis is None:
            rows = await conn.fetch(
                f"SELECT {cols} FROM customers c WHERE ($1 OR NOT c.archived) "
                "ORDER BY c.is_universal DESC, c.name", show_archived)
        else:
            rows = await conn.fetch(
                f"SELECT {cols} FROM customers c WHERE c.id = ANY($1::bigint[]) AND ($2 OR NOT c.archived) "
                "ORDER BY c.is_universal DESC, c.name", vis, show_archived)
    return {"customers": [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]}


@app.post("/api/customers")
async def create_customer(payload: CustomerIn, request: Request):
    """Create a customer (Phase-2a: platform-admin; per-customer delegation is a later
    slice). An exclusive customer auto-grants the creator customer-edit (anti-lockout)."""
    user = await require_priv(request, rbac.P_PLATFORM_ADMIN)
    slug = _customer_slug(payload.name)
    if not slug:
        raise HTTPException(400, "name must contain at least one alphanumeric character")
    async with pool.acquire() as conn:
        if await conn.fetchval("SELECT 1 FROM customers WHERE slug=$1", slug):
            raise HTTPException(409, f"customer {slug!r} already exists")
        row = await conn.fetchrow(
            "INSERT INTO customers (slug, name, description, is_exclusive, created_by) "
            "VALUES ($1,$2,$3,$4,$5) RETURNING id",
            slug, payload.name.strip(), payload.description or "", payload.is_exclusive, user)
        if payload.is_exclusive:
            rid = await conn.fetchval("SELECT id FROM rbac_roles WHERE key='customer-edit'")
            if rid:
                await rbac.assign_role(conn, user.lower(), rid, None, user.lower(), customer_id=row["id"])
    return {"ok": True, "id": row["id"], "slug": slug}


@app.patch("/api/customers/{cid}")
async def patch_customer(cid: int, payload: CustomerPatchIn, request: Request):
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM customers WHERE id=$1", cid):
            raise HTTPException(404, "customer not found")
        await _require_customer_priv_conn(conn, request, rbac.P_CUSTOMER_EDIT, cid)
        user = get_user(request)
        sets, args = [], []
        for col, val in (("name", payload.name), ("description", payload.description),
                         ("is_exclusive", payload.is_exclusive), ("archived", payload.archived)):
            if val is not None:
                args.append(val); sets.append(f"{col}=${len(args)}")
        if not sets:
            return {"ok": True}
        args.append(cid)
        await conn.execute(f"UPDATE customers SET {', '.join(sets)} WHERE id=${len(args)}", *args)
        # Sealing a customer auto-grants the actor customer-edit (anti-lockout).
        if payload.is_exclusive:
            rid = await conn.fetchval("SELECT id FROM rbac_roles WHERE key='customer-edit'")
            if rid:
                await rbac.assign_role(conn, user.lower(), rid, None, user.lower(), customer_id=cid)
    return {"ok": True}


@app.delete("/api/customers/{cid}")
async def delete_customer(cid: int, request: Request):
    await require_priv(request, rbac.P_PLATFORM_ADMIN)
    async with pool.acquire() as conn:
        cust = await conn.fetchrow("SELECT is_universal FROM customers WHERE id=$1", cid)
        if not cust:
            raise HTTPException(404, "customer not found")
        if cust["is_universal"]:
            raise HTTPException(400, "the universal/internal customer cannot be deleted")
        n = await conn.fetchval("SELECT count(*) FROM uc_customer_requests WHERE customer_id=$1", cid)
        if n:
            raise HTTPException(409, f"customer has {n} demand request(s) — reassign or remove them first")
        await conn.execute("DELETE FROM customers WHERE id=$1", cid)
    return {"ok": True}


@app.get("/api/customers/{cid}/projects")
async def list_customer_projects(cid: int, request: Request):
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM customers WHERE id=$1", cid):
            raise HTTPException(404, "customer not found")
        await _require_customer_priv_conn(conn, request, rbac.P_CUSTOMER_VIEW, cid)
        rows = await conn.fetch(
            "SELECT p.id, p.slug, p.name, p.is_exclusive FROM customer_projects cp "
            "JOIN projects p ON p.id=cp.project_id WHERE cp.customer_id=$1 ORDER BY p.name", cid)
    return {"customer_id": cid, "projects": [dict(r) for r in rows]}


@app.post("/api/customers/{cid}/projects")
async def add_customer_project(cid: int, payload: CustomerProjectIn, request: Request):
    user = get_user(request)
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM customers WHERE id=$1", cid):
            raise HTTPException(404, "customer not found")
        await _require_customer_priv_conn(conn, request, rbac.P_CUSTOMER_EDIT, cid)
        if not await conn.fetchval("SELECT 1 FROM projects WHERE id=$1", payload.project_id):
            raise HTTPException(404, "project not found")
        await conn.execute(
            "INSERT INTO customer_projects (customer_id, project_id, created_by) "
            "VALUES ($1,$2,$3) ON CONFLICT DO NOTHING", cid, payload.project_id, user)
    return {"ok": True}


@app.delete("/api/customers/{cid}/projects/{pid}")
async def remove_customer_project(cid: int, pid: int, request: Request):
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM customers WHERE id=$1", cid):
            raise HTTPException(404, "customer not found")
        await _require_customer_priv_conn(conn, request, rbac.P_CUSTOMER_EDIT, cid)
        await conn.execute("DELETE FROM customer_projects WHERE customer_id=$1 AND project_id=$2", cid, pid)
    return {"ok": True}


@app.get("/api/customer-projects")
async def list_customer_project_pairs(request: Request):
    """All customer↔project association pairs the caller can see — feeds the
    (customer × project) association matrix grid (#130 2b-ii) without N+1. Scoped to the
    caller's visible customers (platform-admin = all)."""
    user = get_user(request)
    async with pool.acquire() as conn:
        vis = await _customer_visible_ids(conn, user)
        if vis is None:
            rows = await conn.fetch("SELECT customer_id, project_id FROM customer_projects")
        else:
            rows = await conn.fetch(
                "SELECT customer_id, project_id FROM customer_projects WHERE customer_id = ANY($1::bigint[])", vis)
    return {"pairs": [{"customer_id": r["customer_id"], "project_id": r["project_id"]} for r in rows]}


class CustomerMemberIn(BaseModel):
    reviewer: str
    role_id: Optional[int] = None
    role: Optional[str] = None   # customer role key: 'customer-viewer' | 'customer-edit'


@app.get("/api/customers/{cid}/members")
async def list_customer_members(cid: int, request: Request):
    """Accounts with a customer-scoped role on this customer (customer-viewer/edit)."""
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM customers WHERE id=$1", cid):
            raise HTTPException(404, "customer not found")
        await _require_customer_priv_conn(conn, request, rbac.P_CUSTOMER_VIEW, cid)
        rows = await conn.fetch(
            """SELECT lower(ar.reviewer) AS reviewer, u.email, u.display_name,
                      ar.role_id, ro.key AS role_key, ro.name AS role_name, ar.spans_all,
                      ar.granted_at AS added_at
               FROM rbac_account_roles ar
               JOIN rbac_roles ro ON ro.id=ar.role_id AND ro.scope='customer'
               LEFT JOIN users u ON lower(u.reviewer)=lower(ar.reviewer)
               WHERE ar.customer_id=$1 ORDER BY ro.name, ar.reviewer""", cid)
    return {"members": [{**dict(r), "added_at": r["added_at"].isoformat()} for r in rows]}


@app.post("/api/customers/{cid}/members")
async def add_customer_member(cid: int, payload: CustomerMemberIn, request: Request):
    """Grant a customer-scoped role (customer-viewer / customer-edit) to a user on this
    customer. Requires customer.edit on it (or platform-admin); escalation-guarded."""
    granter = get_user(request)
    reviewer = (payload.reviewer or "").strip().lower()
    if not reviewer:
        raise HTTPException(400, "reviewer required")
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM customers WHERE id=$1", cid):
            raise HTTPException(404, "customer not found")
        await _require_customer_priv_conn(conn, request, rbac.P_CUSTOMER_EDIT, cid)
        role_id = payload.role_id
        if role_id is None and payload.role:
            role_id = await conn.fetchval(
                "SELECT id FROM rbac_roles WHERE key=$1 AND scope='customer'", payload.role)
        if role_id is None or not await conn.fetchval(
                "SELECT 1 FROM rbac_roles WHERE id=$1 AND scope='customer'", role_id):
            raise HTTPException(400, "a valid customer role is required (customer-viewer / customer-edit)")
        # Escalation guard: only grant a role whose privileges you already hold on this customer.
        granter_privs = await rbac.privileges_for(conn, granter, None, cid)
        if rbac.P_PLATFORM_ADMIN not in granter_privs:
            role_privs = {x["privilege_key"] for x in await conn.fetch(
                "SELECT privilege_key FROM rbac_role_privileges WHERE role_id=$1", role_id)}
            esc = role_privs - granter_privs
            if esc:
                raise HTTPException(403, "you can only grant a role whose privileges you already "
                                         f"hold (this role adds: {', '.join(sorted(esc))})")
        await conn.execute(
            "INSERT INTO users (reviewer,email,role,approved,source,enabled) "
            "VALUES ($1,$1,'viewer',true,'manual',true) ON CONFLICT (reviewer) DO NOTHING", reviewer)
        await rbac.assign_role(conn, reviewer, role_id, None, granter, customer_id=cid)
    await _reload_approved()
    return {"ok": True}


@app.delete("/api/customers/{cid}/members/{reviewer}", status_code=204)
async def remove_customer_member(cid: int, reviewer: str, request: Request,
                                 role_id: Optional[int] = Query(None)):
    """Revoke a user's customer role(s) on this customer — a specific role_id, or all."""
    r = reviewer.strip().lower()
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM customers WHERE id=$1", cid):
            raise HTTPException(404, "customer not found")
        await _require_customer_priv_conn(conn, request, rbac.P_CUSTOMER_EDIT, cid)
        if role_id is not None:
            await conn.execute(
                "DELETE FROM rbac_account_roles WHERE customer_id=$1 AND lower(reviewer)=lower($2) AND role_id=$3",
                cid, r, role_id)
        else:
            await conn.execute(
                "DELETE FROM rbac_account_roles ar USING rbac_roles ro "
                "WHERE ar.role_id=ro.id AND ro.scope='customer' AND ar.customer_id=$1 "
                "AND lower(ar.reviewer)=lower($2)", cid, r)
    await _reload_approved()


class ProjectIn(BaseModel):
    slug: str
    name: str
    description: str = ""


def _norm_project_name(s: str) -> str:
    """Soft-normalize a project name for dedup: lowercase, punctuation/whitespace → single space."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _project_name_close(a: str, b: str) -> bool:
    """'Close in spelling' — normalized equality OR high fuzzy similarity (typo/variant catch)."""
    if not a or not b:
        return False
    if a == b:
        return True
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


@app.post("/api/projects")
async def create_project(payload: ProjectIn, request: Request):
    # #135: project.create is a baseline privilege (every authenticated user holds it).
    user = await require_priv(request, rbac.P_PROJECT_CREATE)
    name = (payload.name or payload.slug or "").strip()
    if not name:
        raise HTTPException(400, "project name is required")
    slug = (payload.slug or "").strip().lower() or _slugify(name)
    if not re.match(r"^[a-z0-9][a-z0-9-]*$", slug):
        raise HTTPException(400, "slug must be lowercase alphanumeric/dashes, starting alphanumeric")
    async with pool.acquire() as conn:
        # Soft dedup: compare names case-insensitively + fuzzily (close spelling), slug exactly.
        norm = _norm_project_name(name)
        for e in await conn.fetch("SELECT name, slug FROM projects"):
            if e["slug"] == slug or _project_name_close(norm, _norm_project_name(e["name"])):
                raise HTTPException(409, f"project already exists: {e['name']!r}")
        row = await conn.fetchrow(
            "INSERT INTO projects (slug, name, description, created_by) VALUES ($1,$2,$3,$4) RETURNING id",
            slug, name, payload.description or "", user,
        )
        # #135: the creator administers their own project (project-admin) — can manage members,
        # settings, and content. (A future two-step, externally-administered flow may narrow this.)
        rid = await conn.fetchval("SELECT id FROM rbac_roles WHERE key='project-admin'")
        if rid:
            await rbac.assign_role(conn, user.lower(), rid, row["id"], user.lower())
    return {"ok": True, "id": row["id"], "slug": slug}


class ProjectPatchIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    archived: Optional[bool] = None
    is_exclusive: Optional[bool] = None   # seal: explicit grant required (customer-demand epic)


@app.patch("/api/projects/{pid}")
async def patch_project(pid: int, payload: ProjectPatchIn, request: Request):
    await require_project_admin(request, pid)
    sets, args = [], []
    for col, val in (("name", payload.name), ("description", payload.description),
                     ("archived", payload.archived), ("is_exclusive", payload.is_exclusive)):
        if val is not None:
            args.append(val)
            sets.append(f"{col}=${len(args)}")
    if not sets:
        return {"ok": True}
    args.append(pid)
    async with pool.acquire() as conn:
        res = await conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id=${len(args)}", *args)
    if res.endswith("0"):
        raise HTTPException(404, "project not found")
    return {"ok": True}


@app.delete("/api/projects/{pid}")
async def delete_project(pid: int, request: Request):
    """Delete a project. Requires project.delete on it (or platform.admin). The
    'default' project is protected, and a project that still holds content (use
    cases / runs / sets) is refused — move or remove its data first."""
    user = get_user(request)
    async with pool.acquire() as conn:
        proj = await conn.fetchrow("SELECT slug FROM projects WHERE id=$1", pid)
        if not proj:
            raise HTTPException(404, "project not found")
        if proj["slug"] == "default":
            raise HTTPException(400, "the default project cannot be deleted")
        if _multiuser() and not await _has_priv(user, rbac.P_PLATFORM_ADMIN) \
                and not await _has_priv(user, rbac.P_PROJECT_DELETE, pid):
            raise HTTPException(403, "requires the project delete privilege")
        for tbl, label in (("managed_use_cases", "use cases"), ("analysis_runs", "runs"),
                           ("use_case_sets", "sets")):
            cnt = await conn.fetchval(f"SELECT count(*) FROM {tbl} WHERE project_id=$1", pid)
            if cnt:
                raise HTTPException(409, f"project still has {cnt} {label} — move or delete its data first")
        async with conn.transaction():
            for tbl in ("analysis_output_cache", "capability_catalog", "run_sessions",
                        "managed_use_cases", "analysis_runs", "use_case_sets",
                        "project_stage_context", "rbac_account_roles", "project_members"):
                await conn.execute(f"DELETE FROM {tbl} WHERE project_id=$1", pid)
            await conn.execute("UPDATE users SET default_project_id=NULL WHERE default_project_id=$1", pid)
            await conn.execute("DELETE FROM projects WHERE id=$1", pid)
    return {"ok": True}


# ── UC destination assignment (Phase 2: where a project/UC's use cases live) ──
class UcDestinationIn(BaseModel):
    repo_uuid: Optional[str] = None   # managed_repos.uuid; null = clear → global default
    path: str = ""
    branch: str = ""


async def _uc_dest_repo_summary(conn, repo_uuid) -> Optional[dict]:
    if not repo_uuid:
        return None
    repo = await _repos.get_repo(conn, str(repo_uuid))
    if not repo:
        return None
    return {"uuid": str(repo.get("uuid") or repo_uuid),
            "namespace": repo["namespace"],
            "display_name": repo.get("display_name") or repo["namespace"],
            "provider": (repo.get("metadata") or {}).get("provider", "external"),
            "roles": repo.get("roles") or []}


async def _validate_uc_store(conn, repo_uuid: str) -> None:
    repo = await _repos.get_repo(conn, repo_uuid)
    if not repo:
        raise HTTPException(404, "uc-store repo not found")
    if "uc-store" not in (repo.get("roles") or []):
        raise HTTPException(400, "selected repo is not a uc-store (tag it with the uc-store role first)")


@app.get("/api/projects/{pid}/uc-destination")
async def get_project_uc_destination(pid: int, request: Request):
    """The project's default UC git destination (repo + path + branch)."""
    get_user(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT uc_repo_uuid, uc_path, uc_branch FROM projects WHERE id=$1", pid)
        if not row:
            raise HTTPException(404, "project not found")
        repo = await _uc_dest_repo_summary(conn, row["uc_repo_uuid"])
    return {"repo_uuid": str(row["uc_repo_uuid"]) if row["uc_repo_uuid"] else None,
            "path": row["uc_path"], "branch": row["uc_branch"], "repo": repo}


@app.put("/api/projects/{pid}/uc-destination")
async def set_project_uc_destination(pid: int, payload: UcDestinationIn, request: Request):
    """Set the project's default UC destination. uc-admin (of this project) only."""
    await require_uc_admin(request, pid)
    async with pool.acquire() as conn:
        if payload.repo_uuid:
            await _validate_uc_store(conn, payload.repo_uuid)
        res = await conn.execute(
            "UPDATE projects SET uc_repo_uuid=$2::uuid, uc_path=$3, uc_branch=$4 WHERE id=$1",
            pid, payload.repo_uuid or None, (payload.path or "").strip().strip("/"),
            (payload.branch or "").strip())
    if res.endswith("0"):
        raise HTTPException(404, "project not found")
    return {"ok": True}


@app.put("/api/use-cases/{uuid}/uc-destination")
async def set_uc_destination(uuid: str, payload: UcDestinationIn, request: Request):
    """Per-UC destination override (wins over the project default). uc-admin only."""
    async with pool.acquire() as conn:
        uc = await conn.fetchrow(
            "SELECT project_id FROM managed_use_cases WHERE uuid=$1", uuid)
        if not uc:
            raise HTTPException(404, "use case not found")
    await require_uc_admin(request, uc["project_id"])
    async with pool.acquire() as conn:
        if payload.repo_uuid:
            await _validate_uc_store(conn, payload.repo_uuid)
        await conn.execute(
            "UPDATE managed_use_cases SET source_repo_uuid=$2::uuid, source_path=$3, "
            "source_ref=$4 WHERE uuid=$1",
            uuid, payload.repo_uuid or None, (payload.path or "").strip().strip("/"),
            (payload.branch or "").strip())
    return {"ok": True}


class MoveDataIn(BaseModel):
    target_project_id: int


@app.post("/api/projects/{pid}/move-data")
async def move_project_data(pid: int, payload: MoveDataIn, request: Request):
    """Reassign ALL project-scoped data from project `pid` into the target
    project (use cases, runs, sessions, sets, cached outputs, capability catalog).
    Platform admin only — it's a cross-project operation."""
    await require_role(request, "admin")
    tgt = payload.target_project_id
    if pid == tgt:
        raise HTTPException(400, "source and target are the same project")
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name FROM projects WHERE id = ANY($1::bigint[])", [pid, tgt])
        ids = {r["id"]: r["name"] for r in rows}
        if pid not in ids:
            raise HTTPException(404, "source project not found")
        if tgt not in ids:
            raise HTTPException(404, "target project not found")
        moved = {}
        async with conn.transaction():
            # capability_catalog is UNIQUE(project_id, cap_key) — drop source rows
            # that would collide with the target, then move the remainder.
            await conn.execute(
                "DELETE FROM capability_catalog WHERE project_id=$1 AND cap_key IN "
                "(SELECT cap_key FROM capability_catalog WHERE project_id=$2)", pid, tgt)
            for tbl in ("managed_use_cases", "analysis_runs", "run_sessions",
                        "use_case_sets", "analysis_output_cache", "capability_catalog"):
                res = await conn.execute(
                    f"UPDATE {tbl} SET project_id=$2 WHERE project_id=$1", pid, tgt)
                moved[tbl] = int(res.split()[-1])
    return {"ok": True, "moved": moved, "total": sum(moved.values()),
            "source": ids[pid], "target": ids[tgt]}


@app.get("/api/projects/{pid}/members")
async def list_project_members(pid: int, request: Request):
    """Members = accounts with a project-scoped RBAC role on this project. A user
    may appear once per role they hold here."""
    get_user(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT lower(ar.reviewer) AS reviewer, u.email, u.display_name,
                      ar.role_id, ro.key AS role_key, ro.name AS role_name,
                      ar.granted_at AS added_at
               FROM rbac_account_roles ar
               JOIN rbac_roles ro ON ro.id=ar.role_id AND ro.scope='project'
               LEFT JOIN users u ON lower(u.reviewer)=lower(ar.reviewer)
               WHERE ar.project_id=$1 ORDER BY ro.name, ar.reviewer""", pid)
    return {"members": [{**dict(r), "added_at": r["added_at"].isoformat()} for r in rows]}


# Legacy role names tolerated for back-compat with older clients.
_LEGACY_TO_RBAC = {"admin": "project-admin", "editor": "project-edit",
                   "viewer": "project-viewer", "uc-admin": "project-admin"}


class MemberIn(BaseModel):
    reviewer: str
    role_id: Optional[int] = None
    role: Optional[str] = None   # RBAC role key (or a legacy name)


@app.post("/api/projects/{pid}/members")
async def add_project_member(pid: int, payload: MemberIn, request: Request):
    """Grant a project-scoped RBAC role to a user on this project (RBAC — same
    model as the Accounts panel). Escalation-guarded."""
    granter = await require_project_admin(request, pid)
    reviewer = (payload.reviewer or "").strip().lower()
    if not reviewer:
        raise HTTPException(400, "reviewer required")
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM projects WHERE id=$1", pid):
            raise HTTPException(404, "project not found")
        role_id = payload.role_id
        if role_id is None and payload.role:
            key = _LEGACY_TO_RBAC.get(payload.role, payload.role)
            role_id = await conn.fetchval(
                "SELECT id FROM rbac_roles WHERE key=$1 AND scope='project'", key)
        if role_id is None:
            raise HTTPException(400, "a valid project role is required")
        if not await conn.fetchval(
                "SELECT 1 FROM rbac_roles WHERE id=$1 AND scope='project'", role_id):
            raise HTTPException(400, "not a project-scoped role")
        # Escalation guard: only grant a role whose privileges you already hold.
        granter_privs = await rbac.privileges_for(conn, granter, pid)
        if rbac.P_PLATFORM_ADMIN not in granter_privs:
            role_privs = {x["privilege_key"] for x in await conn.fetch(
                "SELECT privilege_key FROM rbac_role_privileges WHERE role_id=$1", role_id)}
            esc = role_privs - granter_privs
            if esc:
                raise HTTPException(403, "you can only grant a role whose privileges you already "
                                         f"hold (this role adds: {', '.join(sorted(esc))})")
        await conn.execute(
            "INSERT INTO users (reviewer,email,role,approved,source,enabled) "
            "VALUES ($1,$1,'viewer',true,'manual',true) ON CONFLICT (reviewer) DO NOTHING", reviewer)
        await rbac.assign_role(conn, reviewer, role_id, pid, granter)
        await _reconcile_admin(conn)
    await _reload_approved()
    return {"ok": True}


@app.delete("/api/projects/{pid}/members/{reviewer}", status_code=204)
async def remove_project_member(pid: int, reviewer: str, request: Request,
                                role_id: Optional[int] = Query(None)):
    """Revoke a user's project role(s) on this project — a specific role_id, or
    all their project roles here when role_id is omitted."""
    await require_project_admin(request, pid)
    r = reviewer.strip().lower()
    async with pool.acquire() as conn:
        if role_id is not None:
            await conn.execute(
                "DELETE FROM rbac_account_roles WHERE project_id=$1 AND lower(reviewer)=lower($2) "
                "AND role_id=$3", pid, r, role_id)
        else:
            await conn.execute(
                "DELETE FROM rbac_account_roles ar USING rbac_roles ro "
                "WHERE ar.role_id=ro.id AND ro.scope='project' AND ar.project_id=$1 "
                "AND lower(ar.reviewer)=lower($2)", pid, r)
        await _reconcile_admin(conn)
    await _reload_approved()


async def _user_project_roles(conn, user: str) -> dict:
    """{project_id: [project-role-keys]} the user holds DIRECTLY (RBAC project bindings)."""
    rows = await conn.fetch(
        "SELECT ar.project_id, ro.key FROM rbac_account_roles ar "
        "JOIN rbac_roles ro ON ro.id=ar.role_id AND ro.scope='project' "
        "WHERE lower(ar.reviewer)=lower($1) AND ar.project_id IS NOT NULL", user)
    out: dict = {}
    for r in rows:
        out.setdefault(r["project_id"], []).append(r["key"])
    return out


async def _accessible_project_ids(conn, user: str) -> set:
    """Project ids the user can reach (tenancy Phase 1b-2): a DIRECT project-role binding, a TENANT
    role on the project's tenant, OR either reached via a group. Set-wide mirror of _is_project_member
    — so tenant/group role holders see their projects in the switcher + admin list."""
    rows = await conn.fetch(
        """
        SELECT p.id FROM projects p
        WHERE EXISTS (
          SELECT 1 FROM (
            SELECT ar.role_id, ar.project_id, ar.tenant_id FROM rbac_account_roles ar
              WHERE lower(ar.reviewer) = lower($1)
            UNION ALL
            SELECT gr.role_id, g.project_id, g.tenant_id FROM rbac_group_members gm
              JOIN rbac_groups g       ON g.id = gm.group_id
              JOIN rbac_group_roles gr ON gr.group_id = g.id
              WHERE lower(gm.reviewer) = lower($1)
          ) b JOIN rbac_roles ro ON ro.id = b.role_id
          WHERE (ro.scope = 'project' AND b.project_id = p.id)
             OR (ro.scope = 'tenant'  AND b.tenant_id  = p.tenant_id)
        )
        """, user)
    return {r["id"] for r in rows}


_PROJ_ROLE_RANK = {"project-admin": 3, "project-edit": 2, "project-viewer": 1}
_PROJ_ROLE_LEGACY = {"project-admin": "admin", "project-edit": "editor", "project-viewer": "viewer"}


def _legacy_proj_role(keys: list) -> Optional[str]:
    if not keys:
        return None
    return _PROJ_ROLE_LEGACY.get(max(keys, key=lambda k: _PROJ_ROLE_RANK.get(k, 0)))


async def _is_project_member(conn, user: str, pid: int) -> bool:
    # Tenancy Phase 1: membership of a project = a project-scoped binding on it OR a tenant-scoped
    # binding on the project's tenant — from a DIRECT account binding OR via a group (Phase 1b).
    # So a tenant/project role holder (directly or through a group) is a member of the project.
    return bool(await conn.fetchval(
        "SELECT 1 FROM ( "
        "  SELECT ar.role_id, ar.project_id, ar.tenant_id FROM rbac_account_roles ar "
        "    WHERE lower(ar.reviewer)=lower($1) "
        "  UNION ALL "
        "  SELECT gr.role_id, g.project_id, g.tenant_id FROM rbac_group_members gm "
        "    JOIN rbac_groups g ON g.id=gm.group_id JOIN rbac_group_roles gr ON gr.group_id=g.id "
        "    WHERE lower(gm.reviewer)=lower($1) "
        ") b JOIN rbac_roles ro ON ro.id=b.role_id "
        "WHERE (ro.scope='project' AND b.project_id=$2) "
        "   OR (ro.scope='tenant'  AND b.tenant_id=(SELECT tenant_id FROM projects WHERE id=$2)) "
        "LIMIT 1",
        user, pid))


async def _resolve_default_project(conn, user: str, member_ids: list) -> Optional[int]:
    """The user's saved default project if it's still one they belong to; else
    auto-pick (and persist) the first project they're a member of; else None."""
    cur = await conn.fetchval(
        "SELECT default_project_id FROM users WHERE lower(reviewer)=lower($1) OR lower(email)=lower($1)",
        user)
    if cur and cur in member_ids:
        return cur
    if member_ids:
        await conn.execute(
            "UPDATE users SET default_project_id=$2 WHERE lower(reviewer)=lower($1)", user, member_ids[0])
        return member_ids[0]
    return None


# ── Scope & bundles (#107) Phase 2: scope resolution for config registries ──────
# Two orthogonal axes: project_id (NULL = platform) × use_category (NULL = any). An item
# applies to the active (project, use-category) context by UNION semantics. The active
# use-category is the X-DAV-UseCategory request hint (run-derived resolution lands later);
# absent → only platform + project items with NO category show (category-scoped items
# appear only when their category is active). See docs/scope-and-bundles-design.md.
async def _active_use_category(request: Request, conn=None) -> Optional[str]:
    hdr = (request.headers.get("X-DAV-UseCategory") or "").strip().lower()
    return hdr or None

def _scope_where(pid_param: str, cat_param: str) -> str:
    """WHERE predicate matching a scoped row against the active context (UNION semantics).
    NULL project_id = platform (all projects); NULL use_category = all categories."""
    return (f"(project_id IS NULL OR project_id = {pid_param}) "
            f"AND (use_category IS NULL OR use_category = {cat_param})")


async def _active_project_id(request: Request, conn) -> Optional[int]:
    """Resolve the caller's active project for DATA scoping: the X-DAV-Project
    header when set to a project the caller is a MEMBER of, else their default
    project. In single-user mode any non-archived project is honored."""
    hdr = request.headers.get("X-DAV-Project")
    if hdr and hdr.isdigit():
        pid = int(hdr)
        ok = await conn.fetchval("SELECT 1 FROM projects WHERE id=$1 AND NOT archived", pid)
        # The trusted service token (identity system:engine) is not a member of
        # any project, but it acts on behalf of the system (e.g. engine-/script-
        # triggered runs) and must be able to target a project via the header.
        # Without this bypass its runs resolved to project_id=NULL — invisible in
        # the project-scoped runs list (orphaned) AND the model_configs override
        # lookup (scoped by project_id) silently returned nothing. See the
        # no-orphan guard in trigger_run.
        if ok and (not _multiuser() or _service_token_ok(request)
                   or await _is_project_member(conn, get_user(request), pid)):
            return pid
    if not _multiuser():
        return await _default_project_id(conn)
    member_ids = list((await _user_project_roles(conn, get_user(request))).keys())
    return await _resolve_default_project(conn, get_user(request), member_ids)


class StageContextIn(BaseModel):
    content: str = ""
    project_id: Optional[int] = None
    section_overrides: Optional[dict] = None  # F8: {section_name: replacement_text}


async def _stage_customization(conn, stage: str, project_id: Optional[int]) -> dict:
    """A project's full customization for a stage: append content + section overrides."""
    if project_id is None:
        return {"content": "", "section_overrides": {}}
    row = await conn.fetchrow(
        "SELECT content, section_overrides, applied FROM project_stage_context WHERE project_id=$1 AND stage=$2",
        project_id, stage,
    )
    if not row:
        return {"content": "", "section_overrides": {}, "applied": False}
    so = row["section_overrides"]
    if isinstance(so, str):
        try: so = json.loads(so)
        except Exception: so = {}
    return {"content": (row["content"] or "").strip(), "section_overrides": so or {}, "applied": bool(row["applied"])}


@app.get("/api/stage-context/{stage}")
async def get_stage_context(stage: str, request: Request):
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        content = await _stage_context(conn, stage, pid)
    return {"stage": stage, "project_id": pid, "content": content}


@app.put("/api/stage-context/{stage}")
async def put_stage_context(stage: str, payload: StageContextIn, request: Request):
    user = get_user(request)
    async with pool.acquire() as conn:
        pid = payload.project_id if payload.project_id is not None else await _active_project_id(request, conn)
        if pid is None:
            raise HTTPException(404, "no project to attach stage context to")
        # F8: prompt.manage supersedes archreview.context (old grants alias to it in rbac).
        await _require_priv_conn(conn, request, rbac.P_PROMPT_MANAGE, pid)
        so = payload.section_overrides if payload.section_overrides is not None else None
        if so is None:
            await conn.execute(
                """INSERT INTO project_stage_context (project_id, stage, content, updated_by, updated_at)
                   VALUES ($1,$2,$3,$4, now())
                   ON CONFLICT (project_id, stage) DO UPDATE
                   SET content=EXCLUDED.content, updated_by=EXCLUDED.updated_by, updated_at=now()""",
                pid, stage, payload.content or "", user,
            )
        else:
            await conn.execute(
                """INSERT INTO project_stage_context (project_id, stage, content, section_overrides, updated_by, updated_at)
                   VALUES ($1,$2,$3,$4::jsonb,$5, now())
                   ON CONFLICT (project_id, stage) DO UPDATE
                   SET content=EXCLUDED.content, section_overrides=EXCLUDED.section_overrides,
                       updated_by=EXCLUDED.updated_by, updated_at=now()""",
                pid, stage, payload.content or "", json.dumps(so), user,
            )
    return {"ok": True, "stage": stage, "project_id": pid}


# ---------------------------------------------------------------------------
# Prompt management (F8) — per-project, per-stage prompt customization.
# Registry (prompts_registry.py) = stages + base sections; project customization =
# append content + section overrides (stored in project_stage_context). Console stages
# inject context at runtime today; the stage-2 engine prompt is stored-held (A/B first).
# See docs/prompt-management-design.md.
# ---------------------------------------------------------------------------
@app.get("/api/prompts/stages")
async def prompts_stages(request: Request):
    """The stage/section registry (read-only) for the editor. Members can read."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
    return {"stages": _prompts_registry.registry()}


@app.get("/api/prompts/project/{stage}")
async def prompts_project_get(stage: str, request: Request):
    """A project's customization for a stage + the assembled preview. Members can read."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        cust = await _stage_customization(conn, stage, pid)
    assembled = _prompts_registry.assemble(
        stage, content=cust["content"], section_overrides=cust["section_overrides"])
    meta = _prompts_registry.stage(stage)
    return {"stage": stage, "project_id": pid, "meta": meta,
            "content": cust["content"], "section_overrides": cust["section_overrides"],
            "applied": cust.get("applied", False), "assembled": assembled}


class Stage2AppliedIn(BaseModel):
    applied: bool


@app.put("/api/prompts/stage2/applied")
async def set_stage2_applied(payload: Stage2AppliedIn, request: Request):
    """#93 promotion go-live: flip the project's Evaluation (stage-2) prompt between stored-held and
    LIVE (injected into NORMAL runs). prompt.manage gated. Promote only after a winning A/B — this
    changes eval behavior for every subsequent run."""
    user = get_user(request)
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        if pid is None:
            raise HTTPException(404, "no active project")
        await _require_priv_conn(conn, request, rbac.P_PROMPT_MANAGE, pid)
        content = await conn.fetchval(
            "SELECT content FROM project_stage_context WHERE project_id=$1 AND stage='stage2-analysis'", pid)
        if payload.applied and not (content or "").strip():
            raise HTTPException(400, "set an Evaluation prompt before applying it to live runs")
        await conn.execute(
            """INSERT INTO project_stage_context (project_id, stage, content, applied, updated_by, updated_at)
               VALUES ($1, 'stage2-analysis', '', $2, $3, now())
               ON CONFLICT (project_id, stage) DO UPDATE SET applied=$2, updated_by=$3, updated_at=now()""",
            pid, payload.applied, user)
    return {"ok": True, "applied": payload.applied}


class PromptAssistIn(BaseModel):
    stage: str
    target: str = "append"          # 'append' | 'section:<name>'
    intent: str
    current: str = ""               # optional draft to refine


@app.post("/api/prompts/assist")
async def prompts_assist(payload: PromptAssistIn, request: Request):
    """AI-assisted prompt authoring (F8): describe the intent → a drafted/refined prompt
    text for the stage's additional context or a named section. Reuses the project's
    authoring model (uc-authoring → arch-review → evaluation fallback). Stateless — the
    human reviews/edits/saves; nothing is applied. Requires prompt.manage."""
    if not (payload.intent or "").strip():
        raise HTTPException(400, "intent is required")
    meta = _prompts_registry.stage(payload.stage)
    if not meta:
        raise HTTPException(404, "unknown stage")
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROMPT_MANAGE, pid)
        cfg = await _model_default_row(conn, "uc-authoring", "arch-review", "evaluation", project_id=pid)
    if not cfg:
        raise HTTPException(400, "no authoring/arch-review/evaluation model configured for this project")
    if payload.target.startswith("section:"):
        what = f"a replacement for the base prompt section '{payload.target.split(':', 1)[1]}'"
    else:
        what = "an additional-context block appended to the stage prompt (it must NOT restate the base prompt)"
    sections_txt = "\n\n".join(
        f"### {s['label']} ({s['name']})\n{s.get('base', '')}" for s in meta.get("sections", []))
    system = ("You are an expert prompt engineer helping an architect author the prompt for the "
              f"DAV pipeline stage '{meta['label']}'. DAV evaluates whether an architecture "
              "specification supports a use case. Write clear, concise, instruction-style prompt "
              "text. Output ONLY the prompt text — no preamble, no markdown code fences, no "
              "explanation, no surrounding quotes.")
    user = (f"Stage: {meta['label']} — {meta.get('description', '')}\n\n"
            f"Base prompt sections (for context; do not repeat verbatim):\n{sections_txt or '(none)'}\n\n"
            f"You are writing {what}.\n\n"
            f"Architect's intent:\n{payload.intent.strip()}\n\n"
            + (f"Current draft to refine (improve it, keep what works):\n{payload.current.strip()}\n\n"
               if payload.current.strip() else "")
            + "Return the improved prompt text only.")
    call_fn = _make_diagnosis_call_fn(cfg)
    try:
        suggestion = await call_fn(system, user)
    except Exception as e:
        raise HTTPException(502, f"assist model call failed: {e}")
    suggestion = (suggestion or "").strip()
    # Strip accidental code fences the model may add despite instructions.
    if suggestion.startswith("```"):
        suggestion = suggestion.split("\n", 1)[-1]
        if suggestion.rstrip().endswith("```"):
            suggestion = suggestion.rstrip()[:-3].rstrip()
    return {"stage": payload.stage, "target": payload.target,
            "suggestion": suggestion, "model": cfg.get("model_id")}


# ── Capability catalog (manual-curated, LLM-suggested) ───────────────────────
class CatalogIn(BaseModel):
    cap_key: str = ""
    name: str = ""
    definition: str = ""
    domain: str = ""
    spec_refs: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    status: str = "confirmed"
    project_id: Optional[int] = None
    # Capability method (#132): DDD subdomain + R4 disposition (≈ Gartner TIME) + drivers.
    # `subdomain` (not `classification`) — that column already exists for data sensitivity.
    subdomain: Optional[str] = None        # core | supporting | generic (DDD subdomain)
    disposition:    Optional[str] = None   # reuse | refurbish | replace | retire
    strategic_fit:  Optional[str] = None   # high | low
    tech_fitness:   Optional[str] = None   # aligned | constrained
    # m-iii: ownership — the owning DDD bounded context + the single strategic provider.
    bounded_context:    Optional[str] = None
    strategic_provider: Optional[str] = None


def _catalog_row(r) -> dict:
    d = dict(r)
    d["created_at"] = d["created_at"].isoformat()
    d["updated_at"] = d["updated_at"].isoformat()
    return d


async def _catalog_name_map(conn, project_id) -> dict:
    """cap_key -> display name from the catalog, for resolving raw ids in the
    Capability Map / Foundational views to a descriptive name."""
    if project_id is None:
        return {}
    rows = await conn.fetch(
        "SELECT cap_key, name FROM capability_catalog WHERE project_id=$1", project_id)
    return {r["cap_key"]: (r["name"] or r["cap_key"]) for r in rows}


async def _catalog_meta_map(conn, project_id) -> dict:
    """cap_key -> {subdomain, disposition} from the catalog (#132). Lets the
    Engineering roadmap / Cap-Map views carry the DDD subdomain + R4 disposition
    lens without re-deriving — the catalog stays the single source of truth."""
    if project_id is None:
        return {}
    rows = await conn.fetch(
        "SELECT cap_key, subdomain, disposition FROM capability_catalog WHERE project_id=$1", project_id)
    return {r["cap_key"]: {"subdomain": r["subdomain"], "disposition": r["disposition"]} for r in rows}


async def _capability_usage_map(conn, run_id) -> dict:
    """capability_id -> a representative usage sentence the analysis already
    produced (stored at ingest, shown on the Results page). Reused to give
    capabilities a readable gloss in the aggregate views — not re-derived."""
    if not run_id:
        return {}
    rows = await conn.fetch(
        "SELECT DISTINCT ON (capability_id) capability_id, usage FROM uc_capabilities "
        "WHERE run_id=$1 AND COALESCE(usage,'') <> '' ORDER BY capability_id, length(usage) DESC",
        run_id)
    return {r["capability_id"]: r["usage"] for r in rows}


@app.get("/api/catalog")
async def list_catalog(request: Request):
    """The active project's capability catalog (membership-scoped; the project
    comes from the validated X-DAV-Project, never a caller-supplied id)."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        rows = await conn.fetch(
            "SELECT * FROM capability_catalog WHERE project_id=$1 ORDER BY status, name, cap_key", pid
        )
    return {"project_id": pid, "capabilities": [_catalog_row(r) for r in rows]}


@app.get("/api/catalog/suggestions")
async def catalog_suggestions(request: Request, run_id: Optional[str] = Query(None)):
    """Candidate capabilities the LLM emitted in analyses (uc_capabilities) that
    aren't in the catalog yet — the architect confirms/merges these in. Scoped to
    the active project's runs (no cross-project capability enumeration)."""
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
        # Also pull a representative usage (the analysis's readable gloss, already
        # stored) so a suggestion has context without re-deriving anything.
        _usage = "(array_agg(usage ORDER BY length(usage) DESC) FILTER (WHERE COALESCE(usage,'') <> ''))[1] AS usage"
        # Constrain to runs in THIS project so capabilities don't leak across projects.
        # NB: the project-scope subquery binds the LAST param in each branch — keep the
        # placeholder numbers in sync with the args (the no-run_id branch has only $1,
        # so a hard-coded $2 there raised IndeterminateDatatypeError — the catalog 500).
        if run_id:
            rows = await conn.fetch(
                f"SELECT capability_id, COUNT(DISTINCT uc_uuid) AS n, {_usage} FROM uc_capabilities "
                "WHERE run_id=$1 AND run_id IN (SELECT run_id FROM analysis_runs WHERE project_id=$2) "
                "GROUP BY capability_id ORDER BY n DESC", run_id, pid)
        else:
            rows = await conn.fetch(
                f"SELECT capability_id, COUNT(DISTINCT uc_uuid) AS n, {_usage} FROM uc_capabilities "
                "WHERE run_id IN (SELECT run_id FROM analysis_runs WHERE project_id=$1) "
                "GROUP BY capability_id ORDER BY n DESC", pid)
        ex = {r["cap_key"] for r in await conn.fetch(
            "SELECT cap_key FROM capability_catalog WHERE project_id=$1", pid)}
    # Exclude already-cataloged caps by their NORMALIZED key (cap_key), not the raw
    # LLM string — otherwise case/spacing variants slip through as dup "suggestions".
    from .capability_catalog import _cap_key
    suggestions = [{"capability_id": r["capability_id"], "uc_count": int(r["n"]), "usage": r["usage"]}
                   for r in rows if r["capability_id"] and _cap_key(r["capability_id"]) not in ex]
    return {"project_id": pid, "suggestions": suggestions}


@app.post("/api/catalog")
async def add_catalog(payload: CatalogIn, request: Request):
    user = get_user(request)
    from .capability_catalog import _cap_key
    raw = (payload.cap_key or "").strip()
    # Store the NORMALIZED key so it matches observed entries + the suggestions
    # exclusion (which compares _cap_key(capability_id)); keep the raw text as the
    # display name when none was supplied. Without this, an added capability is
    # never recognized as already-cataloged and its suggestion never clears.
    key = _cap_key(raw)
    if not key:
        raise HTTPException(400, "cap_key required")
    async with pool.acquire() as conn:
        # Scope to the ACTIVE project (same as GET /api/catalog + suggestions) so the
        # capability lands where the user is looking — defaulting to the default project
        # made adds vanish from any non-default project's list.
        pid = payload.project_id if payload.project_id is not None else await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_CATALOG, pid)
        if await conn.fetchval("SELECT 1 FROM capability_catalog WHERE project_id=$1 AND cap_key=$2", pid, key):
            raise HTTPException(409, f"capability {key!r} already in catalog")
        row = await conn.fetchrow(
            """INSERT INTO capability_catalog
               (project_id, cap_key, name, definition, domain, spec_refs, depends_on, status,
                subdomain, disposition, strategic_fit, tech_fitness,
                bounded_context, strategic_provider, created_by, updated_by)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$15) RETURNING id""",
            pid, key, payload.name or raw, payload.definition, payload.domain,
            payload.spec_refs, payload.depends_on, payload.status,
            payload.subdomain, payload.disposition, payload.strategic_fit, payload.tech_fitness,
            payload.bounded_context, payload.strategic_provider, user)
    return {"ok": True, "id": row["id"], "cap_key": key}


@app.put("/api/catalog/{cap_id}")
async def update_catalog(cap_id: int, payload: CatalogIn, request: Request):
    user = get_user(request)
    async with pool.acquire() as conn:
        owner = await _gate_resource(conn, request, "capability_catalog", "id", cap_id,
                                     rbac.P_PROJECT_CATALOG, "capability not found")
        result = await conn.execute(
            """UPDATE capability_catalog
               SET name=$2, definition=$3, domain=$4, spec_refs=$5, depends_on=$6, status=$7,
                   subdomain=$10, disposition=$11, strategic_fit=$12, tech_fitness=$13,
                   bounded_context=$14, strategic_provider=$15,
                   updated_by=$8, updated_at=now()
               WHERE id=$1 AND project_id=$9""",
            cap_id, payload.name, payload.definition, payload.domain, payload.spec_refs,
            payload.depends_on, payload.status, user, owner,
            payload.subdomain, payload.disposition, payload.strategic_fit, payload.tech_fitness,
            payload.bounded_context, payload.strategic_provider)
    if result == "UPDATE 0":
        raise HTTPException(404, "capability not found")
    return {"ok": True, "id": cap_id}


@app.delete("/api/catalog/{cap_id}")
async def delete_catalog(cap_id: int, request: Request):
    get_user(request)
    async with pool.acquire() as conn:
        owner = await _gate_resource(conn, request, "capability_catalog", "id", cap_id,
                                     rbac.P_PROJECT_CATALOG, "capability not found")
        await conn.execute("DELETE FROM capability_catalog WHERE id=$1 AND project_id=$2", cap_id, owner)
    return {"ok": True, "id": cap_id}


class SuggestMetaIn(BaseModel):
    capability_id: str
    model_config_id: Optional[int] = None


@app.post("/api/catalog/suggest-meta")
async def catalog_suggest_meta(payload: SuggestMetaIn, request: Request):
    """Draft a readable name + description + domain for a raw capability id, using
    how it was used across analyses. The architect edits and confirms into the
    catalog. Isolated one-shot call to the Arch Review model (NOT the stage-2 prompt)."""
    from . import arch_review as _ar
    cid = (payload.capability_id or "").strip()
    if not cid:
        raise HTTPException(400, "capability_id required")
    async with pool.acquire() as conn:
        pid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_CATALOG, pid)
        if payload.model_config_id is not None:
            model_row = await conn.fetchrow(
                "SELECT * FROM model_configs WHERE id=$1 AND (project_id IS NULL OR project_id=$2) AND enabled",  # scope-aware (#107 2b): platform models too
                payload.model_config_id, pid)
        else:
            model_row = await _model_default_row(conn, "arch-review", project_id=pid)
            model_row = model_row if model_row else None
        if not model_row:
            raise HTTPException(400, "No model available; set a Default Arch Review model in Config")
        model = dict(model_row)
        ctx_rows = await conn.fetch(
            "SELECT usage, rationale FROM uc_capabilities WHERE capability_id=$1 LIMIT 8", cid)
    usages = "\n".join(f"- {(r['usage'] or '').strip()}" for r in ctx_rows if r['usage'])
    rationales = "\n".join(f"- {(r['rationale'] or '').strip()}" for r in ctx_rows if r['rationale'])
    system = (
        "You name and describe software capabilities for an architecture catalog. "
        "Given a raw capability id and how it was used across use cases, produce a concise, "
        "human-readable Name (Title Case), a one-sentence Description, and a short lowercase "
        "domain/category. Output ONLY a JSON object: "
        '{"name": "...", "description": "...", "domain": "..."} — no prose, no markdown.'
    )
    user = (
        f"Raw capability id: {cid}\n\n"
        + (f"Observed usages across UCs:\n{usages}\n\n" if usages else "")
        + (f"Rationales:\n{rationales}\n\n" if rationales else "")
        + "Return the JSON object."
    )
    text = ""
    try:
        async for chunk in _ar.stream_review(
            provider=model["provider"], endpoint_url=model["endpoint_url"],
            model_id=model["model_id"], api_key=model["api_key"],
            system_prompt=system, user_prompt=user,
        ):
            text += chunk
    except Exception as e:
        raise HTTPException(502, f"model call failed: {e}")
    clean = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    name = desc = dom = ""
    try:
        m = re.search(r"\{[\s\S]*\}", clean)
        obj = json.loads(m.group(0)) if m else {}
        name = str(obj.get("name") or "").strip()
        desc = str(obj.get("description") or "").strip()
        dom = str(obj.get("domain") or "").strip()
    except Exception:
        pass
    return {"capability_id": cid, "name": name, "description": desc, "domain": dom}


@app.post("/api/arch-review")
async def arch_review(payload: ArchReviewIn, request: Request):
    """Stream an architectural review from a configured model.

    Scope 'uc': reviews gaps for a single use case.
    Scope 'run': cross-cutting review across all UCs in a run.

    Returns text/event-stream with data: {"text": "..."} chunks,
    a final data: [DONE], or data: {"error": "..."} on failure.
    """
    reviewer = get_user(request)
    from . import arch_review as _ar

    async with pool.acquire() as conn:
        # Arch review is run-driven: authorize + scope models by the run's project.
        arpid = await conn.fetchval(
            "SELECT project_id FROM analysis_runs WHERE run_id=$1 AND project_id IS NOT NULL LIMIT 1",
            payload.run_id) if payload.run_id else None
        if arpid is None:
            arpid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_ARCHREVIEW_EXECUTE, arpid)
        if payload.model_config_id is not None:
            model_row = await conn.fetchrow(
                "SELECT * FROM model_configs WHERE id=$1 AND (project_id IS NULL OR project_id=$2) AND enabled",  # scope-aware (#107 2b): platform models too
                payload.model_config_id, arpid,
            )
            if not model_row:
                raise HTTPException(404, "Model config not found or disabled in this project")
            model_row = dict(model_row)
        elif payload.endpoint_url and payload.model_id:
            # Custom endpoint+model: inherit provider/api_key from a registered
            # row (in this project) at the same endpoint, falling back to openai/no-key.
            base = await conn.fetchrow(
                "SELECT provider, api_key FROM model_configs WHERE endpoint_url=$1 AND (project_id IS NULL OR project_id=$2) AND enabled ORDER BY (project_id IS NULL), id LIMIT 1",  # scope-aware (#107 2b): project key preferred, platform fallback
                payload.endpoint_url, arpid,
            )
            model_row = {
                "provider":     base["provider"] if base else "openai",
                "endpoint_url": payload.endpoint_url,
                "model_id":     payload.model_id,
                "api_key":      base["api_key"]  if base else "",
            }
        else:
            # Fall back to the project-scoped Arch Review default.
            model_row = await _model_default_row(conn, "arch-review", project_id=arpid)
            if model_row is None:
                raise HTTPException(
                    400,
                    "Provide a model, or set a Default Arch Review model in Config",
                )
            model_row = dict(model_row)

        if payload.scope == "set":
            # Set scope (roadmap): cross-cutting review over the latest eval per UC in the
            # masthead Scoping Set (the run picker is retired). Keyed by a synthetic
            # `set:<id>` run token so the cache/generation machinery is reused unchanged.
            analyses = await _set_latest_analyses(conn, arpid, payload.set_id)
            if not analyses:
                raise HTTPException(404, "No evaluated use cases in this scope — ingest the set first")
            user_prompt = _ar._build_run_prompt(await _set_label(conn, payload.set_id), analyses)
            system_prompt = _ar._RUN_SYSTEM

        elif payload.scope == "uc":
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
    # Inject the architect's project/stage context (DCM per-stage context).
    async with pool.acquire() as _c:
        _ctx = await _stage_context(_c, "arch_review")
    user_prompt = _inject_context(user_prompt, _ctx, "architectural review")

    _gen_run = _set_token(payload.set_id) if payload.scope == "set" else payload.run_id
    key = ("review", payload.scope, _gen_run, payload.uc_uuid or "")
    _ensure_generation(
        key,
        provider=model["provider"], endpoint_url=model["endpoint_url"],
        model_id=model["model_id"], api_key=model["api_key"],
        system_prompt=system_prompt, user_prompt=user_prompt,
        run_id=_gen_run, kind="review", scope=payload.scope,
        uc_uuid=payload.uc_uuid,
        model_label=model.get("name") or model.get("model_id") or "",
        user=reviewer,
    )
    return StreamingResponse(_observe_generation(key), media_type="text/event-stream")


@app.get("/api/arch-review/prompt")
async def get_arch_review_prompt(
    request: Request,
    scope: str = Query(..., pattern="^(uc|run|set)$"),
    run_id: str = Query(...),
    uc_uuid: Optional[str] = Query(None),
):
    """Return system + user prompts for an arch review without calling any model.

    Intended for copy-to-clipboard so users can paste into Claude Code or chat.
    Set scope carries the Scoping Set as a synthetic `set:<id>` run token.
    """
    from . import arch_review as _ar

    async with pool.acquire() as conn:
        _sid = _parse_set_token(run_id) if scope == "set" else None
        if scope == "set":
            pid = await _active_project_id(request, conn)
            analyses = await _set_latest_analyses(conn, pid, _sid)
            if not analyses:
                raise HTTPException(404, "No evaluated use cases in this scope — ingest the set first")
            user_prompt = _ar._build_run_prompt(await _set_label(conn, _sid), analyses)
            system_prompt = _ar._RUN_SYSTEM
        elif scope == "uc":
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

async def _enhancement_prompts(scope: str, run_id: str, uc_uuid: Optional[str], conn,
                               set_id: Optional[str] = None, project_id: Optional[int] = None):
    """Shared DB logic for both the streaming and prompt-export endpoints."""
    from . import arch_review as _ar
    if scope == "set":
        # Roadmap scope: enhancement plan over the latest eval per UC in the Scoping Set.
        analyses = await _set_latest_analyses(conn, project_id, set_id)
        if not analyses:
            raise HTTPException(404, "No evaluated use cases in this scope — ingest the set first")
        return (_ar._build_enhancement_run_prompt(await _set_label(conn, set_id), analyses),
                _ar._ENHANCEMENT_RUN_SYSTEM)
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
    reviewer = get_user(request)

    async with pool.acquire() as conn:
        # Run-driven: authorize + scope models by the run's project.
        enpid = await conn.fetchval(
            "SELECT project_id FROM analysis_runs WHERE run_id=$1 AND project_id IS NOT NULL LIMIT 1",
            payload.run_id) if payload.run_id else None
        if enpid is None:
            enpid = await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_ENH_EXECUTE, enpid)
        if payload.model_config_id is not None:
            model_row = await conn.fetchrow(
                "SELECT * FROM model_configs WHERE id=$1 AND (project_id IS NULL OR project_id=$2) AND enabled",  # scope-aware (#107 2b): platform models too
                payload.model_config_id, enpid,
            )
            if not model_row:
                raise HTTPException(404, "Model config not found or disabled in this project")
            model_row = dict(model_row)
        elif payload.endpoint_url and payload.model_id:
            base = await conn.fetchrow(
                "SELECT provider, api_key FROM model_configs WHERE endpoint_url=$1 AND (project_id IS NULL OR project_id=$2) AND enabled ORDER BY (project_id IS NULL), id LIMIT 1",  # scope-aware (#107 2b): project key preferred, platform fallback
                payload.endpoint_url, enpid,
            )
            model_row = {
                "provider":     base["provider"] if base else "openai",
                "endpoint_url": payload.endpoint_url,
                "model_id":     payload.model_id,
                "api_key":      base["api_key"]  if base else "",
            }
        else:
            # Fall back to the Enhancement default, then the Arch Review default
            # (enhancement is part of the same review track).
            model_row = await _model_default_row(conn, "enhancement", "arch-review", project_id=enpid)
            if model_row is None:
                raise HTTPException(
                    400,
                    "Provide a model, or set a Default Enhancement / Arch Review "
                    "model in Config",
                )
        if payload.scope != "set" and not payload.run_id:
            raise HTTPException(400, "run_id required")
        user_prompt, system_prompt = await _enhancement_prompts(
            payload.scope, payload.run_id, payload.uc_uuid, conn,
            set_id=payload.set_id, project_id=enpid,
        )
        # Inject the architect's project/stage context (F8: enhancement is now its own
        # stage, independent of review; existing arch_review content was migrated over).
        _ctx = await _stage_context(conn, "enhancement", enpid)
        user_prompt = _inject_context(user_prompt, _ctx, "enhancement planning")

    model = model_row

    _gen_run = _set_token(payload.set_id) if payload.scope == "set" else payload.run_id
    key = ("enhancement", payload.scope, _gen_run, payload.uc_uuid or "")
    _ensure_generation(
        key,
        provider=model["provider"], endpoint_url=model["endpoint_url"],
        model_id=model["model_id"], api_key=model["api_key"],
        system_prompt=system_prompt, user_prompt=user_prompt,
        run_id=_gen_run, kind="enhancement", scope=payload.scope,
        uc_uuid=payload.uc_uuid,
        model_label=model.get("name") or model.get("model_id") or "",
        user=reviewer,
    )
    return StreamingResponse(_observe_generation(key), media_type="text/event-stream")


class EnhancementApplyIn(BaseModel):
    """Take the streamed text from /api/enhancements and open one PR per
    affected spec repo. Patches auto-route by the namespace prefix on each
    ENHANCEMENT block's `target:` field — `dcm/components/foo.md` lands in
    the managed_repos row with namespace='dcm' (provided it has
    role=enhancement-target), `udlm/...` lands in udlm's repo, etc.

    `repo_overrides` lets you remap a namespace to a specific managed_repos
    row by uuid or namespace — useful when the spec is mirrored, or when
    you want all patches to land in a fork. Empty dict = pure auto-routing.
    """
    enhancement_text: str = Field(..., min_length=10)
    repo_overrides: dict[str, str] = Field(default_factory=dict)   # {namespace -> uuid_or_namespace}
    branch_name: Optional[str] = None                              # default: dav-enh/<random>; shared across PRs
    selected_ids: Optional[list[str]] = None   # #138: only submit these enhancement ids (None = all)
    # Scope context for the PR description
    scope: Optional[str] = None     # 'uc' | 'run'
    run_id: Optional[str] = None
    uc_uuid: Optional[str] = None
    uc_handle: Optional[str] = None
    pr_title: Optional[str] = None


def _finding_out(e) -> dict:
    """#138: one parsed ENHANCEMENT as a workbench-renderable finding."""
    return {
        "id": e.id, "gap_ids": e.gap_ids, "uc_handles": e.uc_handles,
        "target": e.target, "target_namespace": e.target_namespace, "target_path": e.target_path,
        "action": e.action, "section_title": e.section_title, "position": e.position,
        "rationale": e.rationale, "content": e.content, "acceptance": e.acceptance,
        "parse_errors": e.parse_errors,
    }


class EnhancementPreviewIn(BaseModel):
    enhancement_text: str = Field(..., min_length=10)
    run_id: Optional[str] = None
    repo_overrides: dict[str, str] = Field(default_factory=dict)


@app.post("/api/enhancements/preview")
async def preview_enhancements(payload: EnhancementPreviewIn, request: Request):
    """#138 workbench: parse + route the enhancement plan WITHOUT creating PRs. Returns findings
    grouped by their target repo (the PR groupings), plus unmatched namespaces (with reason) and
    targetless findings — so the UI can show/select/submit per repo or per finding. Read-only."""
    async with pool.acquire() as conn:
        pid = (await _run_project_id(conn, payload.run_id)) if payload.run_id \
            else await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_READ, pid)
    enhancements = _enh_apply.parse_enhancement_blocks(payload.enhancement_text)
    by_ns: dict[str, list] = {}
    no_target = []
    for e in enhancements:
        (by_ns.setdefault(e.target_namespace, []) if e.target else no_target).append(e)
    groups, unmatched = [], []
    async with pool.acquire() as conn:
        for ns, ns_enhs in by_ns.items():
            override = payload.repo_overrides.get(ns)
            repo = await _repos.get_repo(conn, override or ns)
            findings = [_finding_out(e) for e in ns_enhs]
            if (not repo or repo.get("project_id") != pid
                    or "enhancement-target" not in (repo.get("roles") or [])):
                unmatched.append({
                    "namespace": ns, "findings": findings,
                    "reason": (f"no enhancement-target repo for namespace {ns!r}"
                               if not repo else
                               f"repo {repo.get('namespace')!r} lacks role=enhancement-target"),
                })
            else:
                groups.append({
                    "namespace": ns,
                    "repo": {"uuid": repo["uuid"], "name": repo.get("display_name") or repo.get("namespace"),
                             "url": repo.get("repo_url"), "branch": repo.get("repo_branch") or "main"},
                    "findings": findings,
                })
    return {"groups": groups, "unmatched": unmatched,
            "no_target": [_finding_out(e) for e in no_target],
            "total": len(enhancements),
            "parse_errors": [e.id for e in enhancements if e.parse_errors]}


@app.post("/api/enhancements/apply")
async def apply_enhancements(payload: EnhancementApplyIn, request: Request):
    """Parse the LLM-emitted ENHANCEMENT blocks and open one PR per
    affected spec repo. Patches are routed by the namespace prefix on
    each enhancement's `target:` field.

    Failure modes surfaced in the response (not 500'd):
      * parse_errors per block
      * apply_warnings per file (replace_text NYI, anchor not found, ...)
      * unmatched_namespaces — targets whose namespace has no managed_repos
        row with role=enhancement-target (silently dropping would lose data)
    Per-file fetch / push failures DO raise 502.
    """
    user = get_user(request)
    async with pool.acquire() as conn:
        applypid = (await _run_project_id(conn, payload.run_id)) if payload.run_id \
            else await _active_project_id(request, conn)
        await _require_priv_conn(conn, request, rbac.P_PROJECT_ENH_PR, applypid)
    enhancements = _enh_apply.parse_enhancement_blocks(payload.enhancement_text)
    if not enhancements:
        raise HTTPException(400, "no ENHANCEMENT blocks found in input text")
    if payload.selected_ids is not None:   # #138: submit only the selected findings
        _want = set(payload.selected_ids)
        enhancements = [e for e in enhancements if e.id in _want]
        if not enhancements:
            raise HTTPException(400, "none of the selected enhancement ids were found")

    # Group enhancements by target namespace (e.g., dcm, udlm).
    by_namespace: dict[str, list] = {}
    for e in enhancements:
        if not e.target:
            continue
        by_namespace.setdefault(e.target_namespace, []).append(e)
    if not by_namespace:
        raise HTTPException(400, "all parsed enhancements were missing a target")

    import secrets as _sysrand
    branch_name = payload.branch_name or f"dav-enh/{_sysrand.token_hex(4)}"
    apply_warnings: list[str] = []
    unmatched_namespaces: list[dict] = []
    repo_results: list[dict] = []

    # Look up the run's recorded spec scope so we can flag namespace drift —
    # patches that target a namespace the run never grounded in get a warning,
    # not a silent drop (the patch still applies because the model may have
    # found a legitimate cross-namespace dependency, but the operator needs
    # to know it happened).
    run_scope: Optional[set[str]] = None
    if payload.run_id:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT spec_namespaces FROM run_sessions WHERE run_name = $1",
                payload.run_id,
            )
        if row and row["spec_namespaces"]:
            run_scope = set(row["spec_namespaces"])
        # If spec_namespaces is None/empty, the run was unfiltered (all
        # registered specs in scope) — don't warn on anything.

    async with pool.acquire() as conn:
        for ns, ns_enhs in by_namespace.items():
            if run_scope is not None and ns not in run_scope:
                apply_warnings.append(
                    f"CROSS-NAMESPACE DRIFT: enhancement(s) "
                    f"{', '.join(e.id for e in ns_enhs)} target namespace "
                    f"{ns!r} which was NOT in this run's spec scope "
                    f"({sorted(run_scope)}). The PR will still be opened, "
                    f"but verify the model didn't drift from your intended scope."
                )
            override = payload.repo_overrides.get(ns)
            repo = await _repos.get_repo(conn, override or ns)
            # Repo must belong to the run's project (no cross-project push) and
            # carry the enhancement-target role.
            if (not repo or repo.get("project_id") != applypid
                    or "enhancement-target" not in (repo.get("roles") or [])):
                unmatched_namespaces.append({
                    "namespace": ns,
                    "enhancements": [e.id for e in ns_enhs],
                    "reason": (
                        f"no managed_repos row with namespace={ns!r} and "
                        f"role=enhancement-target (override={override!r})"
                        if not repo else
                        f"repo {repo.get('namespace')!r} found but lacks role=enhancement-target"
                    ),
                })
                continue
            secrets = await _repos.get_repo_secrets(conn, repo["uuid"]) or {}
            token = secrets.get("github_pat")
            if not token:
                unmatched_namespaces.append({
                    "namespace": ns,
                    "enhancements": [e.id for e in ns_enhs],
                    "reason": f"repo {repo['namespace']!r} has no GitHub PAT configured",
                })
                continue
            if not corpus_push.is_github(repo["repo_url"]):
                unmatched_namespaces.append({
                    "namespace": ns,
                    "enhancements": [e.id for e in ns_enhs],
                    "reason": f"target {repo['repo_url']!r} is not a GitHub URL",
                })
                continue
            repo_result = await _apply_to_one_repo(
                repo=repo, token=token, enhancements=ns_enhs,
                branch_name=branch_name, all_enhancements=enhancements,
                payload=payload, user=user, warnings=apply_warnings,
            )
            repo_results.append(repo_result)

    return {
        "ok": True,
        "branch": branch_name,
        "shared_branch_across_prs": True,
        "repo_results": repo_results,
        "unmatched_namespaces": unmatched_namespaces,
        "apply_warnings": apply_warnings,
        "enhancements_total": len(enhancements),
        "enhancements_with_parse_errors": [e.id for e in enhancements if e.parse_errors],
    }


async def _apply_to_one_repo(
    *, repo: dict, token: str, enhancements: list,
    branch_name: str, all_enhancements: list,
    payload: EnhancementApplyIn, user: str,
    warnings: list[str],
) -> dict:
    """Open one PR against `repo` carrying the patches whose target
    namespace matches. Returns a per-repo result dict for the response."""
    owner, repo_name = corpus_push.parse_github_url(repo["repo_url"])
    base_branch = repo.get("repo_branch") or "main"
    root_path = _repos.resolve_root_path(repo, "enhancement-target") or ""

    # Group THIS namespace's enhancements by target file
    by_file: dict[str, list] = {}
    for e in enhancements:
        by_file.setdefault(e.target, []).append(e)

    files_changed: list[dict] = []
    for target_handle, file_enhs in by_file.items():
        rel_path = file_enhs[0].target_path
        # #186: the enhancement target becomes a real file commit that triggers the repo's
        # CI — an arbitrary-write → RCE path. Reject traversal / absolute paths; allowlist
        # doc extensions; deny CI-control files even when they carry an allowed extension.
        _norm = (rel_path or "").strip()
        _low = _norm.lower()
        _ci_deny = (".github/", ".gitlab-ci", ".gitea/", "jenkinsfile", "makefile", "dockerfile")
        if (not _norm or _norm.startswith("/") or "\\" in _norm
                or ".." in _norm.split("/")
                or not _low.endswith((".md", ".yaml", ".yml", ".txt", ".rst"))
                or any(tok in _low for tok in _ci_deny)):
            raise HTTPException(400, f"enhancement target path not allowed: {rel_path!r}")
        repo_path = f"{root_path}/{rel_path}".lstrip("/") if root_path else rel_path
        try:
            existing = await corpus_push.fetch_file_content(
                owner=owner, repo=repo_name, file_path=repo_path,
                ref=base_branch, token=token,
            )
        except Exception as e:
            raise HTTPException(502, f"fetch {owner}/{repo_name}:{repo_path} — {e}")

        current = existing or ""
        applied_ids: list[str] = []
        for enh in file_enhs:
            if enh.parse_errors:
                warnings.append(f"[{repo['namespace']}] {enh.id}: {'; '.join(enh.parse_errors)}")
                continue
            if existing is None and enh.action != "new_document":
                warnings.append(
                    f"[{repo['namespace']}] {enh.id}: target {target_handle} doesn't exist; "
                    f"applying as new_document (was {enh.action})"
                )
                enh.action = "new_document"
            new_content, err = _enh_apply.apply_enhancement(current, enh)
            if err:
                warnings.append(f"[{repo['namespace']}] {enh.id}: {err}")
                continue
            current = new_content
            applied_ids.append(enh.id)

        if applied_ids:
            files_changed.append({
                "target_handle": target_handle,
                "repo_path": repo_path,
                "applied": applied_ids,
                "new_content_preview": current[:400],
            })
            try:
                await corpus_push.push_uc_to_github(
                    owner=owner, repo=repo_name,
                    base_branch=base_branch,
                    file_path=repo_path,
                    file_content=current,
                    branch_name=branch_name,
                    commit_message=f"DAV enhancement patches for {target_handle}",
                    pr_title=payload.pr_title or _enh_apply_default_pr_title(payload, len(by_file)),
                    pr_body=_enh_apply_pr_body(payload, all_enhancements, warnings, user, repo["namespace"]),
                    author_name=user or "DAV",
                    author_email=f"{user or 'dav'}@dav-review.local",
                    token_override=token,
                )
            except Exception as e:
                raise HTTPException(502, f"push {owner}/{repo_name}:{repo_path} — {e}")

    # Resolve the PR URL after all files pushed
    pr_url: Optional[str] = None
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=10.0) as cx:
            r = await cx.get(
                f"https://api.github.com/repos/{owner}/{repo_name}/pulls",
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json"},
                params={"head": f"{owner}:{branch_name}", "state": "open"},
            )
        if r.status_code == 200 and r.json():
            pr_url = r.json()[0]["html_url"]
    except Exception:
        pass

    return {
        "namespace": repo["namespace"],
        "repo": f"{owner}/{repo_name}",
        "base_branch": base_branch,
        "pr_url": pr_url,
        "files_changed": files_changed,
    }


def _enh_apply_default_pr_title(payload: EnhancementApplyIn, file_count: int) -> str:
    if payload.scope == "uc" and payload.uc_handle:
        return f"DAV enhancement: address gaps in {payload.uc_handle}"
    if payload.scope == "run" and payload.run_id:
        return f"DAV enhancement: roadmap for run {payload.run_id}"
    return f"DAV enhancement: {file_count} spec patch(es)"


def _enh_apply_pr_body(
    payload: EnhancementApplyIn,
    enhancements: list,
    warnings: list[str],
    user: str,
) -> str:
    lines = ["This PR was generated by DAV's enhancement-apply pipeline.",
             "",
             "## Source"]
    if payload.run_id:
        lines.append(f"- Run: `{payload.run_id}`")
    if payload.uc_handle:
        lines.append(f"- UC: `{payload.uc_handle}`")
    if payload.uc_uuid:
        lines.append(f"- UC UUID: `{payload.uc_uuid}`")
    lines += ["", "## Enhancements applied"]
    for e in enhancements:
        gaps = f" — gaps: {', '.join(e.gap_ids)}" if e.gap_ids else ""
        lines.append(f"- **{e.id}**{gaps}  ·  `{e.target}`  ·  `{e.action}`")
        if e.acceptance:
            lines.append(f"  - acceptance: {e.acceptance}")
    if warnings:
        lines += ["", "## Warnings"]
        for w in warnings:
            lines.append(f"- {w}")
    lines += ["", f"Triggered by `{user}` via `POST /api/enhancements/apply`."]
    return "\n".join(lines)


@app.get("/api/enhancements/prompt")
async def get_enhancement_prompt(
    request: Request,
    scope: str = Query(..., pattern="^(uc|run|set)$"),
    run_id: str = Query(...),
    uc_uuid: Optional[str] = Query(None),
):
    """Return system + user prompts for enhancement planning without calling any model.
    Set scope carries the Scoping Set as a synthetic `set:<id>` run token."""
    async with pool.acquire() as conn:
        _sid = _parse_set_token(run_id) if scope == "set" else None
        _pid = await _active_project_id(request, conn) if scope == "set" else None
        user_prompt, system_prompt = await _enhancement_prompts(
            scope, run_id, uc_uuid, conn, set_id=_sid, project_id=_pid)
    return {"system_prompt": system_prompt, "user_prompt": user_prompt}


# ========================= CODE REPOSITORIES (deprecated post-ADR-006) =========================
#
# Per ADR-006 these endpoints are deprecated; code repositories live in
# the managed_repos registry with role='enhancement-target'. The endpoints
# return 410 Gone with a Location header pointing at the new path so any
# external caller gets an actionable error.


_CODE_REPOS_GONE_DETAIL = {
    "message": (
        "Endpoint deprecated per ADR-006. Code repositories are now part of the "
        "managed_repos registry with role='enhancement-target'."
    ),
    "use_instead": "/api/repos?role=enhancement-target",
    "see": "https://github.com/croadfeldt/dav/blob/main/adr/006-consolidate-code-repos-into-managed-repos.md",
}


@app.get("/api/code-repos")
async def list_code_repos_gone():
    raise HTTPException(410, detail=_CODE_REPOS_GONE_DETAIL)


@app.post("/api/code-repos", status_code=410)
async def create_code_repo_gone(payload: CodeRepoIn, request: Request):
    raise HTTPException(410, detail=_CODE_REPOS_GONE_DETAIL)


@app.put("/api/code-repos/{rid}")
async def update_code_repo_gone(rid: int, payload: CodeRepoIn, request: Request):
    raise HTTPException(410, detail=_CODE_REPOS_GONE_DETAIL)


@app.delete("/api/code-repos/{rid}", status_code=410)
async def delete_code_repo_gone(rid: int, request: Request):
    raise HTTPException(410, detail=_CODE_REPOS_GONE_DETAIL)


# ========================= PR / MR CREATION =========================


def _slugify_handle(handle: str) -> str:
    h = unicodedata.normalize("NFKD", handle)
    h = h.encode("ascii", "ignore").decode("ascii")
    h = re.sub(r"[^\w-]", "-", h).strip("-")
    h = re.sub(r"-+", "-", h)
    return h.lower() or "unknown"


async def _pr_gap_context(scope: str, run_id: str, uc_uuid: Optional[str], conn,
                          project_id: Optional[int] = None) -> dict:
    """Build PR metadata (title, branch, file_path, gap_context) from gap DB rows."""
    import base64 as _b64

    if scope == "set":
        # Roadmap scope: aggregate gaps over the latest eval per UC in the Scoping Set.
        sid = _parse_set_token(run_id) or "__all__"
        analyses = await _set_latest_analyses(conn, project_id, sid)
        label = await _set_label(conn, sid)
        slug = _slugify_handle(str(sid))
        total_gaps = sum(len(a.get("gaps") or []) for a in analyses)
        title = f"gap(set/{sid}): cross-cutting enhancements"
        branch = f"gap/set-{slug}"
        file_path = f"enhancements/set-{slug}.md"
        lines = [
            "## Context\n",
            f"**Scope:** {label}  ",
            f"**Use cases:** {len(analyses)}  ",
            f"**Total gaps:** {total_gaps}  ",
            "",
            "## Gaps addressed\n",
        ]
        for a in analyses:
            ag = a.get("gaps") or []
            if not ag:
                continue
            lines.append(f"\n### {a.get('uc_handle') or a.get('uc_uuid')} — {a.get('verdict') or 'unknown'}\n")
            for g in ag:
                lines.append(f"- **[{g.get('gap_id') or '?'}]** {g.get('title') or ''}")
        gap_context = "\n".join(lines)
        return {"title": title, "branch": branch, "file_path": file_path, "gap_context": gap_context}

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
    request: Request,
    scope: str = Query(..., pattern="^(uc|run|set)$"),
    run_id: str = Query(...),
    uc_uuid: Optional[str] = Query(None),
):
    """Return PR metadata (title, branch, file_path, gap_context) without touching any remote."""
    async with pool.acquire() as conn:
        _pid = await _active_project_id(request, conn) if scope == "set" else None
        return await _pr_gap_context(scope, run_id, uc_uuid, conn, project_id=_pid)


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
        # ADR-006: enhancement target is a managed_repos row with role=enhancement-target
        managed = await _repos.get_repo(conn, payload.repo_uuid)
        if not managed:
            raise HTTPException(404, f"Repo {payload.repo_uuid!r} not found")
        if "enhancement-target" not in (managed.get("roles") or []):
            raise HTTPException(
                400,
                f"Repo {managed['namespace']} does not have the "
                f"'enhancement-target' role. Add it via the Repos UI.",
            )
        secrets = await _repos.get_repo_secrets(conn, payload.repo_uuid)
        token = (secrets or {}).get("github_pat") or ""
        if not token:
            raise HTTPException(
                400,
                f"Repo {managed['namespace']} has no PAT configured. "
                f"Set one via the Repos UI (inline or shared credential).",
            )
        # Provider: from metadata override, else infer from URL
        provider = (managed.get("metadata") or {}).get("provider")
        if not provider:
            repo_url_lc = (managed["repo_url"] or "").lower()
            if "github.com" in repo_url_lc:
                provider = "github"
            elif "gitlab" in repo_url_lc:
                provider = "gitlab"
            else:
                raise HTTPException(
                    400,
                    f"Cannot infer provider for {managed['repo_url']!r}; "
                    f"set metadata.provider on the repo to 'github' or 'gitlab'.",
                )
        # Synthesise the repo_row shape the rest of the function expects
        repo_row = {
            "provider": provider,
            "repo_url": managed["repo_url"],
            "default_branch": managed["repo_branch"],
            "token": token,
            "enabled": True,  # post-ADR-006: presence of role IS the enabled flag
        }

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


# ── analyze vocabulary aliases (operating-model DR §4) ────────────────────────
# The DR renames the run-of-an-analysis verb from "ingestion"/"run" to "analyze":
# a run IS an analysis. The HTTP surface historically uses /api/runs… ; rather
# than rename 17 handlers (and break every existing client at once), we register
# a hidden twin of each /api/runs… route at /api/analyses… that dispatches to the
# SAME endpoint (same guards, same response). New clients/UI can move to the
# analyze vocabulary; /api/runs stays the compatibility alias until retired.
# include_in_schema=False keeps the OpenAPI surface single-spelled (/api/runs).
def _register_analyze_aliases(application: FastAPI) -> None:
    from fastapi.routing import APIRoute
    _OLD_PREFIX = "/api/runs"
    _NEW_PREFIX = "/api/analyses"
    # Snapshot first: we mutate application.routes while iterating.
    existing = [r for r in application.routes
                if isinstance(r, APIRoute) and r.path.startswith(_OLD_PREFIX)]
    aliased = 0
    for route in existing:
        new_path = _NEW_PREFIX + route.path[len(_OLD_PREFIX):]
        methods = sorted((route.methods or set())
                         & {"GET", "POST", "PUT", "PATCH", "DELETE"})
        if not methods:
            continue
        application.add_api_route(
            new_path,
            route.endpoint,
            methods=methods,
            name=f"{route.name}__analyses_alias",
            dependencies=list(route.dependencies or []),
            include_in_schema=False,
        )
        aliased += 1
    log.info("registered %d /api/analyses alias route(s) for /api/runs", aliased)


_register_analyze_aliases(app)
