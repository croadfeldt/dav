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
from datetime import datetime, timezone

import asyncpg
import yaml as _yaml
from kubernetes.client.rest import ApiException

from . import repos as _repos
from . import sources as _sources

log = logging.getLogger("dav-review-api.projector")

SPEC_CONFIGMAP = "dav-source-spec"
MCP_DEPLOYMENT = "dav-docs-mcp"
ANNOTATION_PREFIX = _sources.ANNOTATION_PREFIX


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _render_sources_yaml(rows: list[dict]) -> str:
    """Render the registry's role=spec rows into the ConfigMap sources YAML.

    Returns empty string if no rows — caller decides whether to project
    an empty sources list (which would make the MCP unable to serve).
    """
    if not rows:
        return ""
    # Match the shape the init container parses + the sources-spec-configmap
    # template renders. Stable key order so identical content always produces
    # identical YAML (idempotency check below relies on this).
    items = []
    for r in rows:
        items.append({
            "namespace": r["namespace"],
            "repo_url": r["repo_url"],
            "repo_branch": r["repo_branch"],
            "root_path": r.get("root_path") or "",
        })
    # default_flow_style=False for block style; sort_keys=False to preserve
    # the field order above (matches what humans expect to read).
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
    rows = await _repos.list_repos(conn, role="spec")
    new_sources = _render_sources_yaml(rows)

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
