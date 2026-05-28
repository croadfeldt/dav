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
# Workspace binding for PipelineRun — the Pipeline declares a workspace
# named DAV_PIPELINE_WORKSPACE which must be bound to a PVC at run creation.
WORKSPACE_NAME = os.environ.get("DAV_PIPELINE_WORKSPACE", "shared-data")
WORKSPACE_PVC  = os.environ.get("DAV_PIPELINE_WORKSPACE_PVC", "dav-workspace")

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
    # Extended params for full run management
    mode: Optional[str] = None,
    sample_count: Optional[int] = None,
    corpus_subpath: Optional[str] = None,
    corpus_repo_url: Optional[str] = None,
    corpus_repo_branch: Optional[str] = None,
    spec_repo_url: Optional[str] = None,
    spec_repo_branch: Optional[str] = None,
    inference_model: Optional[str] = None,
    halt_on_error: bool = False,
    uc_handles: Optional[list[str]] = None,
    uc_uuids: Optional[list[str]] = None,
    managed_uc_uuids: Optional[list[str]] = None,
    corpus_namespaces: Optional[list[str]] = None,
    spec_namespaces: Optional[list[str]] = None,
) -> dict:
    """Build a PipelineRun object targeting the DAV Pipeline."""
    suffix = str(int(time.time()))[-6:]
    name = f"{PIPELINE_NAME}-console-{suffix}"

    params = [{"name": "git-branch", "value": branch}]
    if commit_sha:
        params.append({"name": "commit-sha", "value": commit_sha})
    if inference_endpoint:
        params.append({"name": "inference-endpoint", "value": inference_endpoint})
    if test_count:
        params.append({"name": "test-count", "value": test_count})
    if mode:
        params.append({"name": "mode", "value": mode})
    if sample_count is not None:
        params.append({"name": "sample-count", "value": str(sample_count)})
    if corpus_subpath:
        params.append({"name": "corpus-uc-subpath", "value": corpus_subpath})
    if corpus_repo_url:
        params.append({"name": "consumer-corpus-repo-url", "value": corpus_repo_url})
    if corpus_repo_branch:
        params.append({"name": "consumer-corpus-repo-branch", "value": corpus_repo_branch})
    if spec_repo_url:
        params.append({"name": "consumer-spec-repo-url", "value": spec_repo_url})
    if spec_repo_branch:
        params.append({"name": "consumer-spec-repo-branch", "value": spec_repo_branch})
    if inference_model:
        params.append({"name": "inference-model", "value": inference_model})
    if halt_on_error:
        params.append({"name": "halt-on-error", "value": "true"})
    if uc_handles:
        params.append({"name": "uc-handles", "value": ",".join(uc_handles)})
    if uc_uuids:
        params.append({"name": "uc-uuids", "value": ",".join(uc_uuids)})
    if managed_uc_uuids:
        params.append({"name": "managed-uc-uuids", "value": ",".join(managed_uc_uuids)})
        # The engine needs to know where to fetch the YAML from. Default to
        # the in-cluster Service for the API; can be overridden via env var.
        console_url = os.environ.get(
            "DAV_CONSOLE_INTERNAL_URL",
            f"http://dav-review-api.{NAMESPACE}.svc.cluster.local:8000",
        )
        params.append({"name": "console-api-url", "value": console_url})
    if corpus_namespaces:
        params.append({"name": "corpus-namespaces", "value": ",".join(corpus_namespaces)})
    if spec_namespaces:
        params.append({"name": "spec-namespaces", "value": ",".join(spec_namespaces)})

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
            "workspaces": [
                {
                    "name": WORKSPACE_NAME,
                    "persistentVolumeClaim": {"claimName": WORKSPACE_PVC},
                }
            ],
            "timeouts": {"pipeline": "2h"},
        },
    }


def trigger_run(
    triggered_by: str,
    branch: Optional[str] = None,
    commit_sha: Optional[str] = None,
    inference_endpoint: Optional[str] = None,
    test_count: Optional[str] = None,
    mode: Optional[str] = None,
    sample_count: Optional[int] = None,
    corpus_subpath: Optional[str] = None,
    corpus_repo_url: Optional[str] = None,
    corpus_repo_branch: Optional[str] = None,
    spec_repo_url: Optional[str] = None,
    spec_repo_branch: Optional[str] = None,
    inference_model: Optional[str] = None,
    halt_on_error: bool = False,
    uc_handles: Optional[list[str]] = None,
    uc_uuids: Optional[list[str]] = None,
    managed_uc_uuids: Optional[list[str]] = None,
    corpus_namespaces: Optional[list[str]] = None,
    spec_namespaces: Optional[list[str]] = None,
) -> dict:
    """Create a PipelineRun. Returns the created object's status summary."""
    if not ENABLED:
        raise RuntimeError("pipeline trigger disabled")

    body = _mk_pipelinerun(
        branch=branch or DEFAULT_BRANCH,
        commit_sha=commit_sha,
        inference_endpoint=inference_endpoint,
        test_count=test_count,
        triggered_by=triggered_by,
        mode=mode,
        sample_count=sample_count,
        corpus_subpath=corpus_subpath,
        corpus_repo_url=corpus_repo_url,
        corpus_repo_branch=corpus_repo_branch,
        spec_repo_url=spec_repo_url,
        spec_repo_branch=spec_repo_branch,
        inference_model=inference_model,
        halt_on_error=halt_on_error,
        uc_handles=uc_handles,
        uc_uuids=uc_uuids,
        managed_uc_uuids=managed_uc_uuids,
        corpus_namespaces=corpus_namespaces,
        spec_namespaces=spec_namespaces,
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
        "mode": mode or "verification",
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


def get_task_logs(run_name: str, step: str, tail: int = 200) -> dict:
    """Return the tail of pod logs for a specific TaskRun within a PipelineRun.

    Resolves `step` (the logical pipelineTask name, e.g. 'run-corpus') to the
    actual TaskRun child of the named PipelineRun, then fetches the trailing
    `tail` lines of its pod's `step-<step>` container logs. Tekton names the
    runtime container `step-<step-name>` per task step.
    """
    # Load child TaskRuns from the PipelineRun
    try:
        pr = _api().get_namespaced_custom_object(
            group=_TEKTON_GROUP, version=_TEKTON_VERSION,
            namespace=NAMESPACE, plural=_PIPELINERUN_PLURAL, name=run_name,
        )
    except ApiException as e:
        if e.status == 404:
            raise KeyError(f"pipelinerun {run_name!r} not found")
        raise
    status = pr.get("status", {}) or {}
    child_refs = status.get("childReferences", []) or []
    target_tr = None
    for c in child_refs:
        if c.get("kind") != "TaskRun":
            continue
        # Each TaskRun has the pipelineTask label; resolve by GET so we can match
        tr_name = c.get("name")
        try:
            tr = _api().get_namespaced_custom_object(
                group=_TEKTON_GROUP, version=_TEKTON_VERSION,
                namespace=NAMESPACE, plural="taskruns", name=tr_name,
            )
        except ApiException:
            continue
        tr_step = (tr.get("metadata", {}).get("labels") or {}).get("tekton.dev/pipelineTask")
        if tr_step == step:
            target_tr = tr
            break
    if target_tr is None:
        raise KeyError(f"no TaskRun for step {step!r} under {run_name!r}")

    pod_name = (target_tr.get("status") or {}).get("podName")
    if not pod_name:
        return {"run": run_name, "step": step, "pod": None,
                "container": None, "logs": "(no pod yet)", "lines": 0}

    # Tekton names step containers `step-<step-name>`. Try that first, fall
    # back to "any container" if the naming convention has drifted.
    core = client.CoreV1Api()
    container_candidates = [f"step-{step}", None]  # None = let the API pick
    last_err = None
    for container in container_candidates:
        try:
            log_data = core.read_namespaced_pod_log(
                name=pod_name, namespace=NAMESPACE,
                container=container, tail_lines=tail,
            )
            return {
                "run": run_name, "step": step, "pod": pod_name,
                "container": container or "(default)",
                "logs": log_data,
                "lines": log_data.count("\n") + (1 if log_data and not log_data.endswith("\n") else 0),
            }
        except ApiException as e:
            last_err = e
            continue
    raise RuntimeError(f"could not read pod logs: {last_err}")


def get_run_detail(name: str) -> dict:
    """Fetch a single PipelineRun and its child TaskRun statuses.

    Used by the run-detail UI: returns the pipeline-level phase + a list of
    TaskRuns with their step/container status so the UI can render a task
    ladder (current step, durations, conditions).
    """
    if not ENABLED:
        raise RuntimeError("pipeline trigger disabled")
    try:
        pr = _api().get_namespaced_custom_object(
            group=_TEKTON_GROUP,
            version=_TEKTON_VERSION,
            namespace=NAMESPACE,
            plural=_PIPELINERUN_PLURAL,
            name=name,
        )
    except ApiException as e:
        if e.status == 404:
            raise KeyError(name)
        raise

    meta = pr.get("metadata", {}) or {}
    spec = pr.get("spec", {}) or {}
    status = pr.get("status", {}) or {}
    conditions = status.get("conditions", []) or []
    succeeded = next((c for c in conditions if c.get("type") == "Succeeded"), {})

    # Walk childReferences (Tekton v1+) and fetch each TaskRun's status.
    # Fall back to status.taskRuns (older API) if childReferences absent.
    child_refs = status.get("childReferences", []) or []
    task_names = [c.get("name") for c in child_refs
                  if c.get("kind") == "TaskRun" and c.get("name")]
    if not task_names:
        # Older shape: status.taskRuns is a map { name: { ... } }
        task_names = list((status.get("taskRuns") or {}).keys())

    tasks: list[dict] = []
    for tn in task_names:
        try:
            tr = _api().get_namespaced_custom_object(
                group=_TEKTON_GROUP, version=_TEKTON_VERSION,
                namespace=NAMESPACE, plural="taskruns", name=tn,
            )
        except ApiException:
            continue
        tr_meta = tr.get("metadata", {}) or {}
        tr_spec = tr.get("spec", {}) or {}
        tr_status = tr.get("status", {}) or {}
        tr_conds = tr_status.get("conditions", []) or []
        tr_succ = next((c for c in tr_conds if c.get("type") == "Succeeded"), {})
        # pipelineTask is the logical step name (e.g. "sync-corpus"); fall
        # back to labels if absent.
        step_name = (tr_meta.get("labels") or {}).get("tekton.dev/pipelineTask")
        if not step_name:
            step_name = tr_spec.get("taskRef", {}).get("name") or tn
        tasks.append({
            "name": tn,
            "step": step_name,
            "phase": _phase_from_condition(tr_succ),
            "reason": tr_succ.get("reason"),
            "message": tr_succ.get("message"),
            "started_at": tr_status.get("startTime"),
            "completed_at": tr_status.get("completionTime"),
            "pod_name": tr_status.get("podName"),
        })
    # Sort by start time so the ladder reads in execution order
    tasks.sort(key=lambda t: t.get("started_at") or "")

    return {
        "name": meta.get("name"),
        "uid": meta.get("uid"),
        "phase": _phase_from_condition(succeeded),
        "status_reason": succeeded.get("reason"),
        "status_message": succeeded.get("message"),
        "created_at": meta.get("creationTimestamp"),
        "started_at": status.get("startTime"),
        "completed_at": status.get("completionTime"),
        "triggered_by": (meta.get("annotations") or {}).get("dav-review/triggered-by-user"),
        "params": {p["name"]: p.get("value") for p in spec.get("params", [])},
        "workspaces": [
            {"name": w.get("name"), "pvc": (w.get("persistentVolumeClaim") or {}).get("claimName")}
            for w in spec.get("workspaces", [])
        ],
        "tasks": tasks,
    }


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
