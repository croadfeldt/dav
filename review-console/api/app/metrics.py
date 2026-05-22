"""Cluster Prometheus query proxy.

Queries thanos-querier (openshift-monitoring) using the API pod's
ServiceAccount bearer token. Returns curated GPU + vLLM metrics for the
run-detail UI.

Auth model:
  - The dav-review-api SA needs ClusterRoleBinding to cluster-monitoring-view.
  - The API pod mounts a ConfigMap (with service.beta.openshift.io/inject-cabundle)
    so thanos-querier's service-CA-signed TLS cert verifies.

Falls back to non-functional state if either piece is missing (no token
file, no CA bundle, or query 403) so the API stays healthy when monitoring
isn't yet wired up.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx

log = logging.getLogger("dav-review-api.metrics")

THANOS_URL = os.environ.get(
    "DAV_METRICS_THANOS_URL",
    "https://thanos-querier.openshift-monitoring.svc:9091",
)
SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
# Default location for the injected service-ca bundle ConfigMap mount.
# Override via DAV_METRICS_CA_PATH if mounted elsewhere.
CA_BUNDLE_PATH = os.environ.get(
    "DAV_METRICS_CA_PATH",
    "/var/run/configmaps/service-ca/service-ca.crt",
)

# Cached token (kubelet rotates SA tokens; re-read on 401)
_token_cache: dict[str, Any] = {"value": None, "loaded_at": 0.0}
_TOKEN_TTL = 600.0  # re-read at most every 10 min, or on 401


def _read_token() -> Optional[str]:
    now = time.time()
    if _token_cache["value"] and (now - _token_cache["loaded_at"]) < _TOKEN_TTL:
        return _token_cache["value"]
    try:
        v = Path(SA_TOKEN_PATH).read_text().strip()
        _token_cache["value"] = v
        _token_cache["loaded_at"] = now
        return v
    except Exception as e:
        log.warning("SA token unreadable at %s: %s", SA_TOKEN_PATH, e)
        return None


def _verify_ctx() -> Any:
    """Return a value suitable for httpx 'verify' arg."""
    if Path(CA_BUNDLE_PATH).is_file():
        return CA_BUNDLE_PATH
    # If the service-ca bundle isn't mounted (e.g. local dev), don't verify.
    # Production must mount the CM — log a warning so it's noticed.
    log.warning(
        "CA bundle missing at %s — TLS verification disabled. "
        "Mount the service-ca ConfigMap in production.",
        CA_BUNDLE_PATH,
    )
    return False


def is_available() -> bool:
    """Cheap probe — has the SA token and a verify context."""
    return Path(SA_TOKEN_PATH).is_file()


async def query(promql: str, timeout: float = 5.0) -> dict:
    """Run a single PromQL instant query against thanos-querier.

    Returns the standard Prometheus envelope:
      {"status": "success", "data": {"resultType": "...", "result": [...]}}
    On error returns {"status": "error", "errorType": "...", "error": "..."}.
    """
    token = _read_token()
    if not token:
        return {"status": "error", "errorType": "no_token",
                "error": "SA token not available"}
    url = f"{THANOS_URL.rstrip('/')}/api/v1/query"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=_verify_ctx()) as cx:
            resp = await cx.get(url, headers=headers, params={"query": promql})
        if resp.status_code == 401:
            # Token likely rotated; force re-read and one retry
            _token_cache["value"] = None
            token = _read_token()
            if token:
                async with httpx.AsyncClient(timeout=timeout, verify=_verify_ctx()) as cx:
                    resp = await cx.get(url, headers={"Authorization": f"Bearer {token}"},
                                        params={"query": promql})
        if resp.status_code != 200:
            return {"status": "error", "errorType": f"http_{resp.status_code}",
                    "error": resp.text[:300]}
        return resp.json()
    except httpx.TimeoutException:
        return {"status": "error", "errorType": "timeout",
                "error": f"thanos-querier did not respond within {timeout}s"}
    except httpx.RequestError as e:
        return {"status": "error", "errorType": "connection",
                "error": f"{type(e).__name__}: {e}"}


# Curated snapshot queries for the run-detail UI.
# Per-GPU rows: one entry in `result` per GPU label-set.
# vLLM aggregates: single scalar per query.
_SNAPSHOT_QUERIES: dict[str, str] = {
    # AMD GPU activity / memory / power / thermal
    "gpu_gfx_activity":         "gpu_gfx_activity",
    "gpu_umc_activity":         "gpu_umc_activity",
    "gpu_used_vram_mb":         "gpu_used_vram",
    "gpu_total_vram_mb":        "gpu_total_vram",
    "gpu_power_watts":          "gpu_average_package_power",
    "gpu_edge_temp_c":          "gpu_edge_temperature",
    "gpu_junction_temp_c":      "gpu_junction_temperature",
    "gpu_memory_temp_c":        "gpu_memory_temperature",
    # vLLM aggregates (sum across replicas / GPUs)
    "vllm_running_requests":    "sum(vllm:num_requests_running)",
    "vllm_waiting_requests":    "sum(vllm:num_requests_waiting)",
    "vllm_kv_cache_pct":        "avg(vllm:gpu_cache_usage_perc) * 100",
    "vllm_gen_tps":             "sum(rate(vllm:generation_tokens_total[1m]))",
    "vllm_prompt_tps":          "sum(rate(vllm:prompt_tokens_total[1m]))",
    "vllm_time_to_first_token": "histogram_quantile(0.95, sum by(le)(rate(vllm:time_to_first_token_seconds_bucket[5m])))",
}


def _scalarize(result: list) -> Optional[float]:
    """Extract a single scalar from a Prometheus vector result (first row)."""
    if not result:
        return None
    try:
        return float(result[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


async def snapshot() -> dict:
    """Run all curated queries in parallel; return structured snapshot.

    Per-GPU metrics keep their label set so the UI can render per-GPU tiles.
    vLLM aggregates are returned as scalars.
    """
    if not is_available():
        return {"available": False, "reason": "SA token missing — running outside cluster?"}

    async def _one(name: str, promql: str) -> tuple[str, dict]:
        res = await query(promql)
        return name, res

    items = await asyncio.gather(*[_one(n, q) for n, q in _SNAPSHOT_QUERIES.items()])
    raw = dict(items)

    # Structure the output:
    #   gpu: list of per-GPU dicts (one row per gpu_id label)
    #   vllm: dict of scalar values
    #   errors: per-query errors so UI can render placeholders
    errors: dict[str, str] = {}
    for name, res in raw.items():
        if res.get("status") != "success":
            errors[name] = f"{res.get('errorType', 'error')}: {res.get('error', '')}"

    # Group AMD GPU metrics by GPU label set
    gpu_metric_names = [n for n in raw if n.startswith("gpu_")]
    by_gpu_key: dict[tuple, dict] = {}
    for name in gpu_metric_names:
        results = (raw[name].get("data") or {}).get("result") or []
        for row in results:
            metric = row.get("metric") or {}
            # Use gpu_id + serial_number as the per-GPU key; fall back to a
            # tuple of all labels if those aren't present (varies by exporter version)
            key = (
                metric.get("gpu_id"),
                metric.get("serial_number") or metric.get("gpu_partition_id"),
            )
            slot = by_gpu_key.setdefault(key, {
                "gpu_id": metric.get("gpu_id"),
                "serial_number": metric.get("serial_number"),
                "node": metric.get("node") or metric.get("exported_node") or metric.get("hostname"),
                "model": metric.get("card_model") or metric.get("model") or metric.get("device_id"),
            })
            try:
                slot[name] = float(row["value"][1])
            except (KeyError, IndexError, TypeError, ValueError):
                pass

    gpus = list(by_gpu_key.values())
    # Compute derived: used_vram_pct
    for g in gpus:
        used = g.get("gpu_used_vram_mb"); total = g.get("gpu_total_vram_mb")
        if used is not None and total and total > 0:
            g["used_vram_pct"] = round(used / total * 100, 1)

    vllm = {
        "running_requests":     _scalarize((raw["vllm_running_requests"].get("data") or {}).get("result", [])),
        "waiting_requests":     _scalarize((raw["vllm_waiting_requests"].get("data") or {}).get("result", [])),
        "kv_cache_pct":         _scalarize((raw["vllm_kv_cache_pct"].get("data") or {}).get("result", [])),
        "generation_tps":       _scalarize((raw["vllm_gen_tps"].get("data") or {}).get("result", [])),
        "prompt_tps":           _scalarize((raw["vllm_prompt_tps"].get("data") or {}).get("result", [])),
        "ttft_p95_seconds":     _scalarize((raw["vllm_time_to_first_token"].get("data") or {}).get("result", [])),
    }

    return {
        "available": True,
        "as_of": time.time(),
        "gpus": gpus,
        "vllm": vllm,
        "errors": errors,
    }
