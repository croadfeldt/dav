"""Sourcing switcher — spec & corpus.

Reads and mutates the two sourcing ConfigMaps (dav-source-spec,
dav-source-corpus) and triggers rollouts of the affected Deployments
(dav-docs-mcp, dav-review-api) when the UI switches refs.

Uses in-cluster Kubernetes authentication via the API's ServiceAccount,
which must be bound to the `dav-review-sourcing` Role (RBAC is in
roles/dav/templates/review-console-sourcing-rbac.yaml.j2).

The design keeps the ConfigMap as the single source of truth. The
Deployment's init-container reads repo_url + repo_branch from the
ConfigMap via env vars at pod start, so switching refs is:

  1. Patch ConfigMap data {repo_url, repo_branch}
  2. Mirror new values onto the Deployment annotations
  3. Trigger a rolling restart by annotating pod template with a
     restart timestamp (standard kubectl rollout-restart mechanism)

Branch enumeration uses the GitHub public API (unauthenticated, 60
req/hour per cluster egress IP). Small in-memory cache avoids burning
the budget on repeat UI loads.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx
from kubernetes import client, config
from kubernetes.client.rest import ApiException

log = logging.getLogger("dav-review-api.sources")

# --- Configuration ---
NAMESPACE = os.environ.get("DAV_NAMESPACE", "dav")

# ConfigMap + Deployment names are fixed by the Ansible role; encoded
# here as module constants rather than env vars because they are part
# of the contract between Ansible and this code, not operator-tunable.
SOURCES = {
    "spec": {
        "configmap": "dav-source-spec",
        "deployment": "dav-docs-mcp",
        "data_keys": ("repo_url", "repo_branch"),
    },
    "corpus": {
        "configmap": "dav-source-corpus",
        "deployment": "dav-review-api",
        "data_keys": ("repo_url", "repo_branch"),
    },
    "inference": {
        # Read by the pipeline trigger at PipelineRun creation time, not
        # any Deployment. No pod restart needed when this changes — the
        # next run picks up the new values.
        "configmap": "dav-source-inference",
        "deployment": None,
        "data_keys": ("endpoint", "model"),
    },
}

# Annotation key prefix — must match what Ansible stamps in the templates
ANNOTATION_PREFIX = "dav.dev"

# Branch cache — 5 min TTL per repo URL
_BRANCH_CACHE: dict[str, tuple[float, list[str]]] = {}
_BRANCH_CACHE_TTL_SECONDS = 300

_core_api: Optional[client.CoreV1Api] = None
_apps_api: Optional[client.AppsV1Api] = None


def _load_kube_config() -> None:
    """Load in-cluster config (pod) or fall back to kubeconfig (local dev)."""
    try:
        config.load_incluster_config()
        log.info("Loaded in-cluster Kubernetes config")
    except config.ConfigException:
        try:
            config.load_kube_config()
            log.info("Loaded kubeconfig (dev mode)")
        except Exception as e:
            log.warning("No Kubernetes config available: %s", e)
            raise


def _core() -> client.CoreV1Api:
    global _core_api
    if _core_api is None:
        _load_kube_config()
        _core_api = client.CoreV1Api()
    return _core_api


def _apps() -> client.AppsV1Api:
    global _apps_api
    if _apps_api is None:
        _load_kube_config()
        _apps_api = client.AppsV1Api()
    return _apps_api


def is_available() -> bool:
    """Quick check if sourcing is wired up and reachable."""
    try:
        _core().read_namespaced_config_map(
            name=SOURCES["spec"]["configmap"], namespace=NAMESPACE
        )
        return True
    except Exception as e:
        log.warning("sources not available: %s", e)
        return False


# ------------------------- Read -------------------------


def _cm_to_source_state(cm, data_keys: tuple) -> dict:
    """Extract a uniform state dict from a ConfigMap object.

    `data_keys` names the fields this source kind stores in ConfigMap.data —
    e.g. ('repo_url','repo_branch') for repo sources, ('endpoint','model')
    for inference. Returned as both flat keys (back-compat) and under 'data'.
    """
    data = cm.data or {}
    meta = cm.metadata
    ann = meta.annotations or {}
    out = {
        "data": {k: data.get(k) for k in data_keys},
        "managed_by": ann.get(f"{ANNOTATION_PREFIX}/managed-by"),
        "last_applied_by": ann.get(f"{ANNOTATION_PREFIX}/last-applied-by"),
        "last_applied_at": ann.get(f"{ANNOTATION_PREFIX}/last-applied-at"),
        "ansible_initial_url": ann.get(
            f"{ANNOTATION_PREFIX}/ansible-managed-initial-url"
        ),
        "ansible_initial_branch": ann.get(
            f"{ANNOTATION_PREFIX}/ansible-managed-initial-branch"
        ),
        "configmap_resource_version": meta.resource_version,
    }
    # Promote each data field to the top level for back-compat with the
    # existing UI / spec/corpus consumers that read repo_url, repo_branch.
    for k in data_keys:
        out[k] = data.get(k)
    return out


def _deploy_to_rollout_state(dep) -> dict:
    """Extract rollout status from a Deployment object."""
    status = dep.status or client.V1DeploymentStatus()
    spec = dep.spec or client.V1DeploymentSpec()
    desired = spec.replicas or 1
    ready = status.ready_replicas or 0
    updated = status.updated_replicas or 0
    available = status.available_replicas or 0
    observed = status.observed_generation or 0
    generation = (dep.metadata.generation or 0) if dep.metadata else 0

    # "rolled_out" means the controller has seen the latest spec AND all
    # replicas are ready/updated/available at the new spec.
    rolled_out = (
        observed >= generation
        and updated == desired
        and ready == desired
        and available == desired
    )

    return {
        "deployment": dep.metadata.name,
        "generation": generation,
        "observed_generation": observed,
        "replicas_desired": desired,
        "replicas_ready": ready,
        "replicas_updated": updated,
        "replicas_available": available,
        "rolled_out": rolled_out,
        "status": "rolled_out" if rolled_out else "applying",
    }


def get_source_state(kind: str) -> dict:
    """Return the combined ConfigMap + Deployment state for a source kind."""
    if kind not in SOURCES:
        raise ValueError(f"unknown source kind: {kind}")
    cfg = SOURCES[kind]

    try:
        cm = _core().read_namespaced_config_map(
            name=cfg["configmap"], namespace=NAMESPACE
        )
    except ApiException as e:
        log.error("ConfigMap %s not found: %s", cfg["configmap"], e)
        raise

    dep = None
    if cfg.get("deployment"):
        try:
            dep = _apps().read_namespaced_deployment(
                name=cfg["deployment"], namespace=NAMESPACE
            )
        except ApiException as e:
            log.warning(
                "Deployment %s not found: %s (sourcing state may be stale)",
                cfg["deployment"],
                e,
            )
            dep = None

    state = {
        "kind": kind,
        "configmap": cfg["configmap"],
        "deployment": cfg.get("deployment"),
        **_cm_to_source_state(cm, cfg["data_keys"]),
    }
    if dep is not None:
        state["rollout"] = _deploy_to_rollout_state(dep)
        # Also surface the Deployment's mirror annotations so UI can
        # detect mismatch between ConfigMap and Deployment (would
        # indicate the annotation-mirror step failed or is in-flight)
        dep_ann = (dep.metadata.annotations or {}) if dep.metadata else {}
        state["deployment_annotations"] = {
            "source_repo_url": dep_ann.get(
                f"{ANNOTATION_PREFIX}/source-repo-url"
            ),
            "source_repo_branch": dep_ann.get(
                f"{ANNOTATION_PREFIX}/source-repo-branch"
            ),
            "last_applied_at": dep_ann.get(
                f"{ANNOTATION_PREFIX}/last-applied-at"
            ),
        }
    return state


def get_all_sources_state() -> dict:
    """Return state for all known source kinds."""
    return {kind: get_source_state(kind) for kind in SOURCES}


# ------------------------- Branch enumeration -------------------------


def _parse_github_repo(repo_url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub URL. Raises on non-GitHub URLs."""
    parsed = urlparse(repo_url)
    host = parsed.netloc.lower()
    if host not in ("github.com", "www.github.com"):
        raise ValueError(f"branch enumeration only supports GitHub URLs (got {host})")
    path = parsed.path.lstrip("/").rstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    parts = path.split("/")
    if len(parts) < 2:
        raise ValueError(f"cannot parse owner/repo from URL {repo_url}")
    return parts[0], parts[1]


def list_branches(repo_url: str) -> list[str]:
    """List branches for a GitHub repo (public, unauthenticated).

    5-minute in-memory cache per repo_url to conserve the 60 req/hour
    unauthenticated rate limit.
    """
    now = time.time()
    cached = _BRANCH_CACHE.get(repo_url)
    if cached is not None:
        cached_at, branches = cached
        if now - cached_at < _BRANCH_CACHE_TTL_SECONDS:
            return branches

    owner, repo = _parse_github_repo(repo_url)
    api = f"https://api.github.com/repos/{owner}/{repo}/branches?per_page=100"
    branches: list[str] = []
    try:
        with httpx.Client(timeout=10.0) as cx:
            # GitHub paginates; follow Link headers if present. For most
            # repos under 100 branches, one call is enough.
            next_url: Optional[str] = api
            while next_url:
                resp = cx.get(
                    next_url,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                resp.raise_for_status()
                for b in resp.json():
                    name = b.get("name")
                    if name:
                        branches.append(name)
                # Parse Link header for pagination
                link = resp.headers.get("Link", "")
                next_url = None
                for part in link.split(","):
                    if 'rel="next"' in part:
                        next_url = part[part.find("<") + 1 : part.find(">")]
                        break
    except httpx.HTTPError as e:
        log.warning("GitHub branch listing failed for %s: %s", repo_url, e)
        # Return whatever we got (possibly empty) rather than erroring the UI
        return branches

    _BRANCH_CACHE[repo_url] = (now, branches)
    return branches


def clear_branch_cache(repo_url: Optional[str] = None) -> None:
    """Expose cache clear for admin / test. If repo_url is None, clear all."""
    if repo_url is None:
        _BRANCH_CACHE.clear()
    else:
        _BRANCH_CACHE.pop(repo_url, None)


# ------------------------- Write -------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _validate_repo_input(repo_url: str, repo_branch: str) -> None:
    if not repo_url or not repo_url.startswith(("http://", "https://", "git@")):
        raise ValueError(f"invalid repo_url: {repo_url!r}")
    if not repo_branch or any(c.isspace() for c in repo_branch):
        raise ValueError(f"invalid repo_branch: {repo_branch!r}")
    if len(repo_url) > 512 or len(repo_branch) > 256:
        raise ValueError("repo_url or repo_branch too long")


def _validate_inference_input(endpoint: str, model: str) -> None:
    if not endpoint or not endpoint.startswith(("http://", "https://")):
        raise ValueError(f"invalid inference endpoint: {endpoint!r}")
    if not model or any(c.isspace() for c in model):
        raise ValueError(f"invalid inference model: {model!r}")
    if len(endpoint) > 512 or len(model) > 256:
        raise ValueError("endpoint or model too long")


def apply_source(
    kind: str,
    applied_by: str,
    repo_url: Optional[str] = None,
    repo_branch: Optional[str] = None,
    endpoint: Optional[str] = None,
    model: Optional[str] = None,
) -> dict:
    """Apply new values to the given source kind.

    For spec/corpus: requires repo_url + repo_branch; triggers Deployment rollout.
    For inference:   requires endpoint + model; no Deployment to roll (pipeline
                     picks up the values at PipelineRun creation time).

    Returns the resulting state dict (same shape as get_source_state).
    """
    if kind not in SOURCES:
        raise ValueError(f"unknown source kind: {kind}")
    cfg = SOURCES[kind]
    now = _now_iso()

    # Build the data patch + validate per source kind.
    if kind == "inference":
        _validate_inference_input(endpoint or "", model or "")
        data_patch = {"endpoint": endpoint, "model": model}
        mirror_annotations = {
            f"{ANNOTATION_PREFIX}/source-endpoint": endpoint,
            f"{ANNOTATION_PREFIX}/source-model": model,
        }
    else:
        _validate_repo_input(repo_url or "", repo_branch or "")
        data_patch = {"repo_url": repo_url, "repo_branch": repo_branch}
        mirror_annotations = {
            f"{ANNOTATION_PREFIX}/source-repo-url": repo_url,
            f"{ANNOTATION_PREFIX}/source-repo-branch": repo_branch,
        }

    # Step 1: patch the ConfigMap
    cm_patch_body = {
        "metadata": {
            "annotations": {
                f"{ANNOTATION_PREFIX}/managed-by": "runtime",
                f"{ANNOTATION_PREFIX}/last-applied-by": applied_by,
                f"{ANNOTATION_PREFIX}/last-applied-at": now,
            },
        },
        "data": data_patch,
    }
    try:
        _core().patch_namespaced_config_map(
            name=cfg["configmap"],
            namespace=NAMESPACE,
            body=cm_patch_body,
        )
        log.info("Patched ConfigMap %s by=%s", cfg["configmap"], applied_by)
    except ApiException as e:
        log.error("ConfigMap patch failed for %s: %s", cfg["configmap"], e)
        raise

    # Step 2: patch Deployment (skip when source kind has no Deployment).
    if cfg.get("deployment"):
        dep_patch_body = {
            "metadata": {
                "annotations": {
                    **mirror_annotations,
                    f"{ANNOTATION_PREFIX}/last-applied-at": now,
                    f"{ANNOTATION_PREFIX}/last-applied-by": applied_by,
                },
            },
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            # Standard kubectl-rollout-restart pattern:
                            # changing the pod-template annotation forces a
                            # new ReplicaSet, which triggers a rolling restart.
                            f"{ANNOTATION_PREFIX}/restartedAt": now,
                        },
                    },
                },
            },
        }
        try:
            _apps().patch_namespaced_deployment(
                name=cfg["deployment"],
                namespace=NAMESPACE,
                body=dep_patch_body,
            )
            log.info("Patched Deployment %s: triggered rollout", cfg["deployment"])
        except ApiException as e:
            log.error("Deployment patch failed for %s: %s", cfg["deployment"], e)
            raise

    return get_source_state(kind)
