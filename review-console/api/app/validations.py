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

# Run time budgeting. The pipeline timeout ("time allowed") defaults to the
# estimate (uc_count × per-UC) PLUS a failsafe buffer — a safety net to catch a
# genuinely hung run, NOT a tight budget that kills working ones. It's editable
# mid-run via POST /api/runs/{name}/timeout (console-enforced; Tekton spec is the immutable 24h cap). Per-UC estimate + buffer are env-tunable
# and refine over time from observed run history (see main.py est_per_uc).
EST_SEC_PER_UC = int(os.environ.get("DAV_EST_SEC_PER_UC", "1800"))       # 30 min (until history)
FAILSAFE_BUFFER_SEC = int(os.environ.get("DAV_FAILSAFE_BUFFER_SEC", "7200"))  # +2 h
DEFAULT_TIMEOUT_SEC = int(os.environ.get("DAV_DEFAULT_TIMEOUT_SEC", "43200"))  # 12 h (corpus/unknown count)
_TIMEOUT_FLOOR_SEC = 3600
_TIMEOUT_CAP_SEC = 24 * 3600


def failsafe_timeout_sec(uc_count: Optional[int]) -> int:
    """Default 'time allowed' = ETA (uc_count × per-UC) + failsafe buffer,
    clamped. Full-corpus / unknown count → a generous fixed default."""
    if not uc_count or uc_count <= 0:
        return min(_TIMEOUT_CAP_SEC, DEFAULT_TIMEOUT_SEC)
    t = uc_count * EST_SEC_PER_UC + FAILSAFE_BUFFER_SEC
    return max(_TIMEOUT_FLOOR_SEC, min(_TIMEOUT_CAP_SEC, t))


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
    # Reload the in-cluster config on EVERY call, deliberately: bound SA
    # tokens rotate (~1h) and the kubernetes client snapshots the token at
    # config-load — a pod older than one rotation gets 401 Unauthorized on
    # every trigger while a fresh read of the same mounted file works
    # (measured 2026-07-28: curl with the token → 200, cached client → 401,
    # which broke POST /api/runs with a 500). Config load is a file read —
    # trivial next to creating a PipelineRun.
    global _custom_api
    _load_kube_config()
    _custom_api = client.CustomObjectsApi()
    return _custom_api


_authn_api: Optional[client.AuthenticationV1Api] = None


def _authn() -> client.AuthenticationV1Api:
    # Same token-rotation hazard as _api(); reload per call.
    global _authn_api
    _load_kube_config()
    _authn_api = client.AuthenticationV1Api()
    return _authn_api


def review_service_token(token: str, audience: str, trusted_sas: set) -> bool:
    """Validate a Kubernetes ServiceAccount projected token via the TokenReview
    API — the cloud-native way to authenticate an in-cluster caller (the engine
    fetching managed UCs) with NO shared static secret.

    Returns True iff the token cryptographically authenticates, is scoped to
    `audience` (projected tokens are audience-bound, so a token minted for some
    other service can't be replayed here), AND its ServiceAccount username is in
    `trusted_sas`. Requires the API's SA to hold `system:auth-delegator`.
    """
    if not token:
        return False
    # Returns True (valid) / False (definitively rejected) / None (TRANSIENT —
    # the TokenReview call itself errored). Callers must NOT cache a None: a
    # valid token must not be locked out for a transient apiserver blip (the
    # engine reuses one token for ~23 managed-UC fetches at run start, so a
    # cached negative would silently drop several UCs).
    try:
        body = client.V1TokenReview(
            spec=client.V1TokenReviewSpec(token=token, audiences=[audience])
        )
        resp = _authn().create_token_review(body)
    except ApiException as e:
        log.warning("TokenReview API error (transient): %s", getattr(e, "reason", e))
        return None
    except Exception as e:
        log.warning("TokenReview error (transient): %s", e)
        return None
    st = getattr(resp, "status", None)
    if not st or not getattr(st, "authenticated", False):
        return False
    auds = set(getattr(st, "audiences", None) or [])
    if audience not in auds:
        log.warning("TokenReview: audience %r not granted (got %s)", audience, auds)
        return False
    user = (getattr(st.user, "username", "") if getattr(st, "user", None) else "") or ""
    if user not in trusted_sas:
        log.warning("TokenReview: authenticated but untrusted SA %r", user)
        return False
    return True


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
    sample_concurrency: Optional[int] = None,
    uc_concurrency: Optional[int] = None,
    corpus_subpath: Optional[str] = None,
    corpus_repo_url: Optional[str] = None,
    corpus_repo_branch: Optional[str] = None,
    spec_repo_url: Optional[str] = None,
    spec_repo_branch: Optional[str] = None,
    inference_model: Optional[str] = None,
    pass1_inference_endpoint: Optional[str] = None,
    pass1_inference_model: Optional[str] = None,
    halt_on_error: bool = False,
    uc_handles: Optional[list[str]] = None,
    uc_uuids: Optional[list[str]] = None,
    managed_uc_uuids: Optional[list[str]] = None,
    corpus_namespaces: Optional[list[str]] = None,
    spec_namespaces: Optional[list[str]] = None,
    use_key: Optional[str] = None,
    capabilities_json: Optional[str] = None,
    use_profile_json: Optional[str] = None,
    known_capability_ids: Optional[list[str]] = None,
    capability_catalog_b64: Optional[str] = None,
    tag_untagged_gaps: Optional[bool] = None,
    derived_verdicts: Optional[bool] = None,
    criterion_anchor: Optional[bool] = None,
    multi_lens: Optional[bool] = None,
    stage2_two_pass: Optional[str] = None,
    max_tokens: Optional[int] = None,
    grounding_nudge: Optional[str] = None,
    enable_thinking: Optional[str] = None,
    request_timeout_seconds: Optional[int] = None,
    stage2_context: Optional[str] = None,
    uc_count: Optional[int] = None,
    time_allowed_seconds: Optional[int] = None,
) -> dict:
    """Build a PipelineRun object targeting the DAV Pipeline."""
    suffix = str(int(time.time()))[-6:]
    name = f"{PIPELINE_NAME}-console-{suffix}"

    params = [{"name": "git-branch", "value": branch}]
    # commit_sha / test_count are accepted for legacy self-test callers but NOT
    # forwarded: the stage-2 Pipeline never declared them, and Tekton REJECTS a
    # PipelineRun carrying undeclared params — appending them turned a legacy
    # call into a hard admission failure (found by the trigger→pipeline param
    # contract test, alongside the silently-dropped stage2-context).
    if inference_endpoint:
        params.append({"name": "inference-endpoint", "value": inference_endpoint})
    if mode:
        params.append({"name": "mode", "value": mode})
    if sample_count is not None:
        params.append({"name": "sample-count", "value": str(sample_count)})
    if sample_concurrency is not None:
        params.append({"name": "sample-concurrency", "value": str(sample_concurrency)})
    if uc_concurrency is not None:
        params.append({"name": "uc-concurrency", "value": str(uc_concurrency)})
    if max_tokens is not None:
        # Per-run output budget override (the Tekton task's max-tokens param,
        # default = dav_stage2_max_tokens). Used by the self-improvement loop's
        # A/B experiments to test a candidate max_tokens in isolation — no
        # profile or deploy-var mutation, so production + spamllm are untouched.
        params.append({"name": "max-tokens", "value": str(max_tokens)})
    if request_timeout_seconds is not None:
        # Per-request inference HTTP timeout. Param name must stay exactly
        # `request-timeout-seconds` — the engine PR adds the task-side param.
        params.append({"name": "request-timeout-seconds",
                       "value": str(request_timeout_seconds)})
    if corpus_subpath:
        params.append({"name": "corpus-uc-subpath", "value": corpus_subpath})
    # corpus_repo_url / corpus_repo_branch are accepted (and stored on the
    # session as provenance) but NOT forwarded: corpus sync is registry-driven
    # (dav-git-sync-multi-corpus reads /config/sources by namespace), so the
    # per-run repo override has been silently inert since that migration — the
    # pipeline declared the params and nothing consumed them. Real per-run
    # source pinning (SHA) is the run-source-resolution epic's job.
    if spec_repo_url:
        params.append({"name": "consumer-spec-repo-url", "value": spec_repo_url})
    if spec_repo_branch:
        params.append({"name": "consumer-spec-repo-branch", "value": spec_repo_branch})
    if inference_model:
        params.append({"name": "inference-model", "value": inference_model})
    # Per-stage routing is all-or-nothing: sending only one half would leave the
    # engine to error out mid-run, so refuse to build the PipelineRun at all.
    if pass1_inference_endpoint or pass1_inference_model:
        if not (pass1_inference_endpoint and pass1_inference_model):
            raise ValueError(
                "per-stage routing needs both pass1_inference_endpoint and "
                "pass1_inference_model, or neither")
        params.append({"name": "pass1-inference-endpoint", "value": pass1_inference_endpoint})
        params.append({"name": "pass1-inference-model", "value": pass1_inference_model})
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
    # Per-(model, use) override system (DAV migration 014). The API
    # resolves capabilities + use_profile from DB before calling
    # trigger_run and passes them through as opaque JSON strings.
    if use_key:
        params.append({"name": "use-key", "value": use_key})
    if capabilities_json:
        params.append({"name": "capabilities-json", "value": capabilities_json})
    if use_profile_json:
        params.append({"name": "use-profile-json", "value": use_profile_json})
    if known_capability_ids:
        # ADR-009 gap identity: the active project's catalog capability ids, so the
        # engine enum-constrains gaps[].capability_id for stable cross-run identity.
        params.append({"name": "known-capability-ids",
                       "value": ",".join(known_capability_ids)})
    if capability_catalog_b64:
        # E1 gap tagging: id -> name map for the untagged-gap classifier prompt.
        params.append({"name": "capability-catalog-b64",
                       "value": capability_catalog_b64})
    if tag_untagged_gaps:
        params.append({"name": "tag-untagged-gaps", "value": "true"})
    if derived_verdicts:
        params.append({"name": "derived-verdicts", "value": "true"})
    if criterion_anchor:
        params.append({"name": "criterion-anchor", "value": "true"})
    if multi_lens:
        params.append({"name": "multi-lens", "value": "true"})
    if stage2_two_pass is not None:
        params.append({"name": "stage2-two-pass", "value": stage2_two_pass})
    if grounding_nudge is not None:
        params.append({"name": "grounding-nudge", "value": grounding_nudge})
    if enable_thinking is not None:
        params.append({"name": "enable-thinking", "value": enable_thinking})
    if stage2_context:
        params.append({"name": "stage2-context", "value": stage2_context})

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
            # The Tekton spec timeout is the IMMUTABLE ultimate failsafe (the webhook
            # rejects spec updates once a run starts), so it is always the cap; the
            # user/ETA "time allowed" is CONSOLE-ENFORCED (run_sessions
            # trigger_payload.effective_timeout_seconds, watchdog in the finalizer
            # loop) and stays editable mid-run via POST /api/runs/{name}/timeout.
            "timeouts": {"pipeline": "%ds" % _TIMEOUT_CAP_SEC},
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
    sample_concurrency: Optional[int] = None,
    uc_concurrency: Optional[int] = None,
    corpus_subpath: Optional[str] = None,
    corpus_repo_url: Optional[str] = None,
    corpus_repo_branch: Optional[str] = None,
    spec_repo_url: Optional[str] = None,
    spec_repo_branch: Optional[str] = None,
    inference_model: Optional[str] = None,
    pass1_inference_endpoint: Optional[str] = None,
    pass1_inference_model: Optional[str] = None,
    halt_on_error: bool = False,
    uc_handles: Optional[list[str]] = None,
    uc_uuids: Optional[list[str]] = None,
    managed_uc_uuids: Optional[list[str]] = None,
    corpus_namespaces: Optional[list[str]] = None,
    spec_namespaces: Optional[list[str]] = None,
    use_key: Optional[str] = None,
    capabilities_json: Optional[str] = None,
    use_profile_json: Optional[str] = None,
    known_capability_ids: Optional[list[str]] = None,
    capability_catalog_b64: Optional[str] = None,
    tag_untagged_gaps: Optional[bool] = None,
    derived_verdicts: Optional[bool] = None,
    criterion_anchor: Optional[bool] = None,
    multi_lens: Optional[bool] = None,
    stage2_two_pass: Optional[str] = None,
    max_tokens: Optional[int] = None,
    grounding_nudge: Optional[str] = None,
    enable_thinking: Optional[str] = None,
    request_timeout_seconds: Optional[int] = None,
    stage2_context: Optional[str] = None,
    uc_count: Optional[int] = None,
    time_allowed_seconds: Optional[int] = None,
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
        sample_concurrency=sample_concurrency,
        uc_concurrency=uc_concurrency,
        corpus_subpath=corpus_subpath,
        corpus_repo_url=corpus_repo_url,
        corpus_repo_branch=corpus_repo_branch,
        spec_repo_url=spec_repo_url,
        spec_repo_branch=spec_repo_branch,
        inference_model=inference_model,
        pass1_inference_endpoint=pass1_inference_endpoint,
        pass1_inference_model=pass1_inference_model,
        halt_on_error=halt_on_error,
        uc_handles=uc_handles,
        uc_uuids=uc_uuids,
        managed_uc_uuids=managed_uc_uuids,
        corpus_namespaces=corpus_namespaces,
        spec_namespaces=spec_namespaces,
        use_key=use_key,
        capabilities_json=capabilities_json,
        use_profile_json=use_profile_json,
        known_capability_ids=known_capability_ids,
        capability_catalog_b64=capability_catalog_b64,
        tag_untagged_gaps=tag_untagged_gaps,
        derived_verdicts=derived_verdicts,
        criterion_anchor=criterion_anchor,
        multi_lens=multi_lens,
        stage2_two_pass=stage2_two_pass,
        max_tokens=max_tokens,
        grounding_nudge=grounding_nudge,
        enable_thinking=enable_thinking,
        request_timeout_seconds=request_timeout_seconds,
        stage2_context=stage2_context,
        uc_count=uc_count,
        time_allowed_seconds=time_allowed_seconds,
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


def delete_run(name: str) -> bool:
    """Delete a PipelineRun by name. Returns True if deleted, False if not found.

    Used by the run-management "complete delete" — removes the Tekton object so
    the run is fully gone (alongside its DB rows + workspace files)."""
    if not is_available():
        return False
    try:
        _api().delete_namespaced_custom_object(
            group=_TEKTON_GROUP, version=_TEKTON_VERSION, namespace=NAMESPACE,
            plural=_PIPELINERUN_PLURAL, name=name,
        )
        return True
    except ApiException as e:
        if e.status == 404:
            return False
        log.error("Failed to delete PipelineRun %s: %s", name, e)
        raise


def cancel_run(name: str) -> bool:
    """Gracefully stop an in-flight PipelineRun by setting spec.status=Cancelled.

    Tekton stops the running TaskRuns + their pods and still runs the pipeline's
    `finally` tasks (so the per-run workspace source dir is GC'd). The object is
    kept (shows as Cancelled) — use delete_run to remove it entirely. Returns
    True if patched, False if not found / already gone."""
    if not is_available():
        return False
    try:
        _api().patch_namespaced_custom_object(
            group=_TEKTON_GROUP, version=_TEKTON_VERSION, namespace=NAMESPACE,
            plural=_PIPELINERUN_PLURAL, name=name,
            body={"spec": {"status": "Cancelled"}},
        )
        return True
    except ApiException as e:
        if e.status == 404:
            return False
        log.error("Failed to cancel PipelineRun %s: %s", name, e)
        raise


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
        # Tekton spec timeout (the immutable 24h failsafe). The API layer overrides
        # this with the console-enforced effective "time allowed" from run_sessions
        # before serving the UI — do not read this field as the user-facing value.
        "timeout_seconds": _parse_go_duration_sec((spec.get("timeouts") or {}).get("pipeline")),
        "tasks": tasks,
    }


def _parse_go_duration_sec(s) -> Optional[int]:
    """Parse a Tekton/Go duration ('26400s', '2h0m0s', '2h') to seconds."""
    if not s:
        return None
    import re
    parts = re.findall(r"(\d+)(h|m|s)", str(s))
    if not parts:
        try:
            return int(float(s))
        except (TypeError, ValueError):
            return None
    mult = {"h": 3600, "m": 60, "s": 1}
    return sum(int(v) * mult[u] for v, u in parts)


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
