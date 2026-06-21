"""Projection: managed_repos registry → source ConfigMaps.

Per ADR-003, the managed_repos table is source-of-truth; the
dav-source-spec ConfigMap is downstream cache. This module owns the
write-side hook: whenever rows touching role=spec change, regenerate
the ConfigMap's `sources` field from the registry and trigger a
rollout-restart of dav-docs-mcp so its init container re-clones.

Idempotent: if the projected sources YAML matches what's already in the
ConfigMap, the function is a no-op (no patch, no rollout). This makes it
safe to call from the seed path and from any CRUD hook without worrying
about wasteful rollouts.

Corpus projection (role=corpus → dav-source-corpus) is not yet wired
because the corpus side is currently single-source-only and consumed by
the Tekton pipeline (which re-clones every run, so no Deployment rollout
is needed). When corpus becomes multi-source, the project_corpus_sources
sibling lands here.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import asyncpg
import yaml as _yaml
from kubernetes.client.rest import ApiException

from . import repos as _repos
from . import sources as _sources

log = logging.getLogger("dav-review-api.projector")


async def _mcp_source_project_id(conn: asyncpg.Connection) -> "int | None":
    """The single project whose repos feed the shared dav-docs-mcp / source ConfigMaps
    (tenancy Phase 0). Until per-project MCP serving lands (Phase 3) there is ONE shared
    MCP, so we project exactly one 'source' project's repos into it — preventing other
    projects' newly-registered repos from polluting the shared ConfigMap that existing
    runs depend on. Resolution: env DAV_MCP_SOURCE_PROJECT_SLUG (default 'dcm') → its id;
    else the project that already owns spec/corpus repos (the current source). None only
    if neither resolves (then projection falls back to all repos = legacy behavior)."""
    slug = os.environ.get("DAV_MCP_SOURCE_PROJECT_SLUG", "dcm")
    pid = await conn.fetchval("SELECT id FROM projects WHERE slug=$1", slug)
    if pid is not None:
        return pid
    return await conn.fetchval(
        "SELECT project_id FROM managed_repos "
        "WHERE ('spec' = ANY(roles) OR 'corpus' = ANY(roles)) AND project_id IS NOT NULL "
        "ORDER BY project_id LIMIT 1"
    )

SPEC_CONFIGMAP = "dav-source-spec"
MCP_DEPLOYMENT = "dav-docs-mcp"
CORPUS_CONFIGMAP = "dav-source-corpus"
# No deployment to roll for corpus: it's consumed by Tekton PipelineRuns,
# each of which clones from the ConfigMap fresh at run start.
ANNOTATION_PREFIX = _sources.ANNOTATION_PREFIX


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _render_sources_yaml(rows: list[dict], role: str) -> str:
    """Render the registry rows into the ConfigMap sources YAML.

    `role` is used by ADR-007's per-role path resolution: each source's
    `root_path` in the rendered YAML is `resolve_root_path(repo, role)`
    so the same managed_repos row can serve different subdirs to spec vs
    corpus.

    Returns empty string if no rows — caller decides whether to project
    an empty sources list.
    """
    if not rows:
        return ""
    items = []
    for r in rows:
        items.append({
            "namespace": r["namespace"],
            "repo_url": r["repo_url"],
            "repo_branch": r["repo_branch"],
            "root_path": _repos.resolve_root_path(r, role),
        })
    return _yaml.safe_dump(items, default_flow_style=False, sort_keys=False)


async def project_spec_sources(
    conn: asyncpg.Connection,
    *,
    applied_by: str = "system:projector",
) -> dict:
    """Regenerate dav-source-spec.data.sources from the registry.

    If the projected YAML differs from what the ConfigMap already has,
    patch the ConfigMap and rollout-restart dav-docs-mcp. If they match,
    do nothing.

    Returns a dict describing what happened. Never raises on a k8s read
    miss or a no-op; logs and returns a status. Raises on a write failure
    so the caller (API endpoint) can surface a 5xx.
    """
    now = _now_iso()
    # Phase 0: project only the shared MCP's source project's spec repos, so a repo
    # registered for a different project can't pollute the one shared ConfigMap.
    _src_pid = await _mcp_source_project_id(conn)
    rows = await _repos.list_repos(conn, role="spec", project_id=_src_pid)
    new_sources = _render_sources_yaml(rows, role="spec")

    # Read the current ConfigMap to detect drift.
    try:
        cm = _sources._core().read_namespaced_config_map(
            name=SPEC_CONFIGMAP, namespace=_sources.NAMESPACE,
        )
    except ApiException as e:
        log.warning(
            "projector: cannot read %s (status=%s); skipping projection",
            SPEC_CONFIGMAP, e.status,
        )
        return {
            "status": "skipped",
            "reason": f"configmap read failed: {e.status}",
            "source_count": len(rows),
        }

    existing_data = cm.data or {}
    old_sources = existing_data.get("sources", "") or ""
    had_legacy_keys = bool(
        existing_data.get("repo_url") or existing_data.get("repo_branch")
    )

    # Treat YAML-equivalent content as unchanged. Comparing the raw strings
    # works because _render_sources_yaml emits a stable representation.
    sources_equal = new_sources.strip() == old_sources.strip()

    # Decide whether we need to patch:
    # - If sources differ, yes.
    # - If sources are equal but legacy keys are still around, yes (we want
    #   to clean them up and stamp the annotation to multi-source mode).
    # - Otherwise, no.
    needs_patch = (not sources_equal) or had_legacy_keys

    if not needs_patch:
        log.info(
            "projector: %s already current (%d source(s)); no patch needed",
            SPEC_CONFIGMAP, len(rows),
        )
        return {
            "status": "unchanged",
            "source_count": len(rows),
            "configmap": SPEC_CONFIGMAP,
        }

    if not rows:
        # Refusing to project an empty sources list — the MCP init container
        # would fail (no /config/sources entries to clone). Operator must
        # ensure at least one role=spec row exists.
        log.warning(
            "projector: refusing to write empty sources list to %s "
            "(no role=spec rows in managed_repos); preserving current ConfigMap",
            SPEC_CONFIGMAP,
        )
        return {
            "status": "refused",
            "reason": "no role=spec rows; MCP would have nothing to serve",
            "source_count": 0,
        }

    # Build the data patch. Setting a key to None deletes it — explicitly
    # clear the legacy single-source keys when transitioning to multi-source.
    data_patch: dict = {"sources": new_sources}
    if had_legacy_keys:
        data_patch["repo_url"] = None
        data_patch["repo_branch"] = None

    cm_patch = {
        "metadata": {
            "annotations": {
                f"{ANNOTATION_PREFIX}/managed-by": "runtime",
                f"{ANNOTATION_PREFIX}/last-applied-by": applied_by,
                f"{ANNOTATION_PREFIX}/last-applied-at": now,
                f"{ANNOTATION_PREFIX}/source-mode": "multi",
                f"{ANNOTATION_PREFIX}/source-count": str(len(rows)),
            },
        },
        "data": data_patch,
    }
    _sources._core().patch_namespaced_config_map(
        name=SPEC_CONFIGMAP, namespace=_sources.NAMESPACE, body=cm_patch,
    )
    log.info(
        "projector: patched %s with %d source(s) [%s]",
        SPEC_CONFIGMAP, len(rows),
        ", ".join(r["namespace"] for r in rows),
    )

    # Rollout-restart dav-docs-mcp so the init container re-clones with the
    # new sources list. Standard kubectl-rollout-restart pattern: bump a
    # pod-template annotation, which forces the Deployment controller to
    # create a new ReplicaSet.
    rolled = False
    try:
        dep_patch = {
            "metadata": {
                "annotations": {
                    f"{ANNOTATION_PREFIX}/source-mode": "multi",
                    f"{ANNOTATION_PREFIX}/source-count": str(len(rows)),
                    f"{ANNOTATION_PREFIX}/last-applied-by": applied_by,
                    f"{ANNOTATION_PREFIX}/last-applied-at": now,
                },
            },
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": now,
                        },
                    },
                },
            },
        }
        _sources._apps().patch_namespaced_deployment(
            name=MCP_DEPLOYMENT, namespace=_sources.NAMESPACE, body=dep_patch,
        )
        rolled = True
        log.info("projector: rollout-restarted %s", MCP_DEPLOYMENT)
    except ApiException as e:
        # ConfigMap update succeeded; rollout failed. The MCP will pick up
        # the new sources on the NEXT restart (manual or otherwise). Surface
        # this as a warning, not a hard error — the durable state (DB +
        # ConfigMap) is consistent.
        log.warning(
            "projector: rollout-restart of %s failed (status=%s); "
            "ConfigMap updated but MCP still serves old sources until next restart",
            MCP_DEPLOYMENT, e.status,
        )

    return {
        "status": "projected",
        "source_count": len(rows),
        "sources": [r["namespace"] for r in rows],
        "rolled_out": rolled,
        "configmap": SPEC_CONFIGMAP,
        "deployment": MCP_DEPLOYMENT,
        "applied_at": now,
    }


def repo_touches_spec(repo: dict | None) -> bool:
    """True if the repo carried role=spec at any point (before or after a
    change). Caller passes BOTH the pre- and post-change dicts and the
    projector runs if either side has the role.
    """
    if not repo:
        return False
    return "spec" in (repo.get("roles") or [])


def repo_touches_corpus(repo: dict | None) -> bool:
    """True if the repo carried role=corpus at any point (before or after
    a change). Same usage pattern as repo_touches_spec."""
    if not repo:
        return False
    return "corpus" in (repo.get("roles") or [])


async def project_corpus_sources(
    conn: asyncpg.Connection,
    *,
    applied_by: str = "system:projector",
) -> dict:
    """Sibling of project_spec_sources for the corpus side (ADR-007).

    Regenerates dav-source-corpus.data.sources from all managed_repos
    rows with role=corpus. Per-row root_path is resolved via
    resolve_root_path(repo, 'corpus') so the metadata.role_paths.corpus
    override wins over the row's default root_path.

    Unlike the spec projector, no Deployment is rolled — the corpus is
    consumed by Tekton PipelineRuns that clone fresh at run start. Each
    new run picks up the current ConfigMap automatically.
    """
    now = _now_iso()
    # Phase 0: scope to the shared MCP's source project (see project_spec_sources).
    _src_pid = await _mcp_source_project_id(conn)
    rows = await _repos.list_repos(conn, role="corpus", project_id=_src_pid)
    new_sources = _render_sources_yaml(rows, role="corpus")

    try:
        cm = _sources._core().read_namespaced_config_map(
            name=CORPUS_CONFIGMAP, namespace=_sources.NAMESPACE,
        )
    except ApiException as e:
        log.warning(
            "projector: cannot read %s (status=%s); skipping corpus projection",
            CORPUS_CONFIGMAP, e.status,
        )
        return {
            "status": "skipped",
            "reason": f"configmap read failed: {e.status}",
            "source_count": len(rows),
        }

    existing_data = cm.data or {}
    old_sources = existing_data.get("sources", "") or ""
    had_legacy_keys = bool(
        existing_data.get("repo_url") or existing_data.get("repo_branch")
    )
    sources_equal = new_sources.strip() == old_sources.strip()
    needs_patch = (not sources_equal) or had_legacy_keys

    if not needs_patch:
        log.info(
            "projector: %s already current (%d source(s)); no patch needed",
            CORPUS_CONFIGMAP, len(rows),
        )
        return {
            "status": "unchanged",
            "source_count": len(rows),
            "configmap": CORPUS_CONFIGMAP,
        }

    if not rows:
        # Refuse to write an empty list — Tekton runs would have nothing to
        # clone. Operator should keep at least one role=corpus row.
        log.warning(
            "projector: refusing to write empty sources list to %s "
            "(no role=corpus rows in managed_repos); preserving current ConfigMap",
            CORPUS_CONFIGMAP,
        )
        return {
            "status": "refused",
            "reason": "no role=corpus rows; Tekton runs would have no corpus",
            "source_count": 0,
        }

    data_patch: dict = {"sources": new_sources}
    if had_legacy_keys:
        data_patch["repo_url"] = None
        data_patch["repo_branch"] = None

    cm_patch = {
        "metadata": {
            "annotations": {
                f"{ANNOTATION_PREFIX}/managed-by": "runtime",
                f"{ANNOTATION_PREFIX}/last-applied-by": applied_by,
                f"{ANNOTATION_PREFIX}/last-applied-at": now,
                f"{ANNOTATION_PREFIX}/source-mode": "multi",
                f"{ANNOTATION_PREFIX}/source-count": str(len(rows)),
            },
        },
        "data": data_patch,
    }
    _sources._core().patch_namespaced_config_map(
        name=CORPUS_CONFIGMAP, namespace=_sources.NAMESPACE, body=cm_patch,
    )
    log.info(
        "projector: patched %s with %d source(s) [%s]",
        CORPUS_CONFIGMAP, len(rows),
        ", ".join(r["namespace"] for r in rows),
    )

    return {
        "status": "projected",
        "source_count": len(rows),
        "sources": [r["namespace"] for r in rows],
        "configmap": CORPUS_CONFIGMAP,
        "applied_at": now,
        # Per ADR-007: no rollout — Tekton reads ConfigMap fresh per run
        "rolled_out": False,
    }
