"""Self-test pipeline trigger.

Creates and lists Tekton PipelineRuns against the DCM self-test Pipeline.
Uses in-cluster Kubernetes authentication via the API's ServiceAccount,
which must be bound to the `dav-review-runs-trigger` Role.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

log = logging.getLogger("dav-review-api.validations")

# --- Configuration (from env, set by the Deployment template) ---
NAMESPACE = os.environ.get("DAV_NAMESPACE", "dav")
PIPELINE_NAME = os.environ.get("DAV_PIPELINE_NAME", "dav")
# Default branch to checkout when no override is supplied
DEFAULT_BRANCH = os.environ.get("DAV_DEFAULT_BRANCH", "main")
# Whether this feature is wired up at all
ENABLED = os.environ.get("DAV_TRIGGER_ENABLED", "true").lower() == "true"

_TEKTON_GROUP = "tekton.dev"
_TEKTON_VERSION = "v1"
_PIPELINERUN_PLURAL = "pipelineruns"

_custom_api: Optional[client.CustomObjectsApi] = None


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


def _api() -> client.CustomObjectsApi:
    global _custom_api
    if _custom_api is None:
        _load_kube_config()
        _custom_api = client.CustomObjectsApi()
    return _custom_api


def is_available() -> bool:
    """Quick check if the feature is enabled and reachable."""
    if not ENABLED:
        return False
    try:
        _api()
        return True
    except Exception:
        return False


def _mk_pipelinerun(
    branch: str,
    commit_sha: Optional[str],
    inference_endpoint: Optional[str],
    test_count: Optional[str],
    triggered_by: str,
) -> dict:
    """Build a PipelineRun object targeting the DCM self-test Pipeline."""
    suffix = str(int(time.time()))[-6:]
    name = f"{PIPELINE_NAME}-console-{suffix}"

    params = [{"name": "git-branch", "value": branch}]
    if commit_sha:
        params.append({"name": "commit-sha", "value": commit_sha})
    if inference_endpoint:
        params.append({"name": "inference-endpoint", "value": inference_endpoint})
    if test_count:
        params.append({"name": "test-count", "value": test_count})

    return {
        "apiVersion": f"{_TEKTON_GROUP}/{_TEKTON_VERSION}",
        "kind": "PipelineRun",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": {
                "app.kubernetes.io/part-of": "dav",
                "app.kubernetes.io/component": "pipeline-run",
                "triggered-by": "review-console",
            },
            "annotations": {
                "dav-review/triggered-by-user": triggered_by,
                "dav-review/trigger-source": "review-console-ui",
            },
        },
        "spec": {
            "pipelineRef": {"name": PIPELINE_NAME},
            "params": params,
            "timeouts": {"pipeline": "2h"},
        },
    }


def trigger_run(
    triggered_by: str,
    branch: Optional[str] = None,
    commit_sha: Optional[str] = None,
    inference_endpoint: Optional[str] = None,
    test_count: Optional[str] = None,
) -> dict:
    """Create a PipelineRun. Returns the created object's status summary."""
    if not ENABLED:
        raise RuntimeError("self-test trigger disabled")

    body = _mk_pipelinerun(
        branch=branch or DEFAULT_BRANCH,
        commit_sha=commit_sha,
        inference_endpoint=inference_endpoint,
        test_count=test_count,
        triggered_by=triggered_by,
    )

    try:
        resp = _api().create_namespaced_custom_object(
            group=_TEKTON_GROUP,
            version=_TEKTON_VERSION,
            namespace=NAMESPACE,
            plural=_PIPELINERUN_PLURAL,
            body=body,
        )
    except ApiException as e:
        log.error("Failed to create PipelineRun: %s", e)
        raise

    meta = resp.get("metadata", {})
    return {
        "name": meta.get("name"),
        "namespace": meta.get("namespace"),
        "uid": meta.get("uid"),
        "created_at": meta.get("creationTimestamp"),
        "triggered_by": triggered_by,
        "branch": branch or DEFAULT_BRANCH,
        "commit_sha": commit_sha,
    }


def list_recent(limit: int = 20) -> list[dict]:
    """List recent PipelineRuns for the self-test Pipeline."""
    if not ENABLED:
        return []

    try:
        resp = _api().list_namespaced_custom_object(
            group=_TEKTON_GROUP,
            version=_TEKTON_VERSION,
            namespace=NAMESPACE,
            plural=_PIPELINERUN_PLURAL,
            label_selector=f"tekton.dev/pipeline={PIPELINE_NAME}",
        )
    except ApiException as e:
        # Tekton may label PipelineRuns differently than we expect, or the
        # label might not exist yet on console-triggered runs. Fall back to
        # listing everything in the namespace and filtering client-side.
        log.warning("Labeled list failed (%s); falling back to full list", e)
        resp = _api().list_namespaced_custom_object(
            group=_TEKTON_GROUP,
            version=_TEKTON_VERSION,
            namespace=NAMESPACE,
            plural=_PIPELINERUN_PLURAL,
        )

    runs = []
    for item in resp.get("items", []):
        meta = item.get("metadata", {})
        spec = item.get("spec", {})
        status = item.get("status", {})
        pipeline_ref = spec.get("pipelineRef", {}).get("name")
        if pipeline_ref != PIPELINE_NAME:
            continue

        conditions = status.get("conditions", [])
        succeeded = next(
            (c for c in conditions if c.get("type") == "Succeeded"), {}
        )
        phase = _phase_from_condition(succeeded)

        runs.append({
            "name": meta.get("name"),
            "created_at": meta.get("creationTimestamp"),
            "started_at": status.get("startTime"),
            "completed_at": status.get("completionTime"),
            "phase": phase,
            "status_reason": succeeded.get("reason"),
            "status_message": succeeded.get("message"),
            "triggered_by": meta.get("annotations", {}).get(
                "dav-review/triggered-by-user"
            ),
            "trigger_source": meta.get("annotations", {}).get(
                "dav-review/trigger-source", "external"
            ),
            "params": {
                p["name"]: p.get("value") for p in spec.get("params", [])
            },
        })

    runs.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return runs[:limit]


def _phase_from_condition(cond: dict) -> str:
    """Translate Tekton Succeeded condition into a display phase."""
    status = cond.get("status")
    reason = cond.get("reason", "")
    if status == "True":
        return "Succeeded"
    if status == "False":
        # Map Tekton's Failure reasons to friendlier labels
        if reason in ("Cancelled", "PipelineRunCancelled"):
            return "Cancelled"
        if reason in ("PipelineRunTimeout", "TaskRunTimeout"):
            return "TimedOut"
        return "Failed"
    if status == "Unknown":
        if reason in ("Running", "Started", "Pending"):
            return reason
        return "Running"
    return "Unknown"
