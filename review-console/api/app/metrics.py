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
    # KV-cache utilization. vLLM renamed this gauge `gpu_cache_usage_perc` →
    # `kv_cache_usage_perc`; PromQL `or` prefers the new name and falls back to
    # the old so we read whichever the running vLLM exposes (otherwise the panel
    # shows a constant 0% — the metric simply wasn't there under the old name).
    "vllm_kv_cache_pct":        "avg(vllm:kv_cache_usage_perc or vllm:gpu_cache_usage_perc) * 100",
    "vllm_gen_tps":             "sum(rate(vllm:generation_tokens_total[1m]))",
    "vllm_prompt_tps":          "sum(rate(vllm:prompt_tokens_total[1m]))",
    # Cumulative counters since vLLM process start. Useful as both an
    # absolute total and (via UI-side baseline subtraction) a "tokens
    # this session" running stat.
    "vllm_gen_tokens_total":    "sum(vllm:generation_tokens_total)",
    "vllm_prompt_tokens_total": "sum(vllm:prompt_tokens_total)",
    "vllm_time_to_first_token": "histogram_quantile(0.95, sum by(le)(rate(vllm:time_to_first_token_seconds_bucket[5m])))",
}


def _scalarize(result: list) -> Optional[float]:
    """Extract a single scalar from a Prometheus vector result (first row).

    Returns None for missing rows or non-finite values. Prometheus returns
    "NaN" / "+Inf" / "-Inf" as strings in the JSON response (e.g. when a
    histogram_quantile has no samples), and FastAPI's default JSON encoder
    rejects those — must coerce to None before they hit the wire.
    """
    if not result:
        return None
    try:
        v = float(result[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    # math.isfinite would do the same; spell it out to avoid an import here
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


async def range_aggregates(started_at_iso: str, completed_at_iso: str) -> dict:
    """Compute time-averaged GPU + token counters across a finished run's window.

    Used by the run-detail finalizer to compute session-totals like
    energy (J), avg/peak power, and total tokens generated/prompted.

    `started_at` / `completed_at` are ISO 8601 strings (Kubernetes status).
    Issues `query_range` against thanos-querier; falls back to instant
    `query` with appropriate `avg_over_time` / `max_over_time` / `increase`
    windows because thanos-querier handles those uniformly.
    """
    from datetime import datetime
    try:
        start = datetime.fromisoformat(started_at_iso.replace("Z", "+00:00")).timestamp()
        end   = datetime.fromisoformat(completed_at_iso.replace("Z", "+00:00")).timestamp()
    except Exception as e:
        return {"available": False, "reason": f"bad timestamps: {e}"}
    if end <= start:
        return {"available": False, "reason": "non-positive run window"}
    window = max(int(end - start), 1)
    range_str = f"{window}s"

    # offset() pins the range to the actual run window even if we're querying
    # after the fact. "now() - end_time" gives the offset.
    now = time.time()
    offset = max(int(now - end), 0)
    off_clause = f" offset {offset}s" if offset > 0 else ""

    qs = {
        "gpu_avg_power":   f"avg_over_time(gpu_average_package_power[{range_str}]{off_clause})",
        "gpu_peak_power":  f"max_over_time(gpu_average_package_power[{range_str}]{off_clause})",
        "gpu_avg_gfx":     f"avg_over_time(gpu_gfx_activity[{range_str}]{off_clause})",
        # increase() returns total counter delta over the window
        "prompt_tokens":   f"sum(increase(vllm:prompt_tokens_total[{range_str}]{off_clause}))",
        "gen_tokens":      f"sum(increase(vllm:generation_tokens_total[{range_str}]{off_clause}))",
    }
    out: dict = {"available": True, "window_seconds": window, "queries": qs}
    results = await asyncio.gather(*[query(q) for q in qs.values()])
    by_name = dict(zip(qs.keys(), results))

    # gpu_*: sum across GPU rows (each node has 2)
    def _agg_sum(res):
        if (res or {}).get("status") != "success":
            return None
        total = 0.0; count = 0
        for r in (res.get("data") or {}).get("result", []) or []:
            try:
                v = float(r["value"][1])
                if v == v and v not in (float("inf"), float("-inf")):
                    total += v; count += 1
            except (KeyError, IndexError, TypeError, ValueError):
                pass
        return total if count else None
    def _agg_avg(res):
        if (res or {}).get("status") != "success":
            return None
        vs = []
        for r in (res.get("data") or {}).get("result", []) or []:
            try:
                v = float(r["value"][1])
                if v == v and v not in (float("inf"), float("-inf")):
                    vs.append(v)
            except (KeyError, IndexError, TypeError, ValueError):
                pass
        return sum(vs)/len(vs) if vs else None
    def _agg_max(res):
        if (res or {}).get("status") != "success":
            return None
        vs = []
        for r in (res.get("data") or {}).get("result", []) or []:
            try:
                v = float(r["value"][1])
                if v == v and v not in (float("inf"), float("-inf")):
                    vs.append(v)
            except (KeyError, IndexError, TypeError, ValueError):
                pass
        return max(vs) if vs else None

    gpu_avg_p_total = _agg_sum(by_name["gpu_avg_power"])   # sum across GPUs = total node draw
    gpu_peak_p_total = _agg_sum(by_name["gpu_peak_power"]) # peak summed across GPUs (worst case)
    out["gpu_avg_power_watts"]  = gpu_avg_p_total
    out["gpu_peak_power_watts"] = gpu_peak_p_total
    out["gpu_energy_joules"] = (gpu_avg_p_total * window) if gpu_avg_p_total is not None else None
    out["gpu_avg_gfx_activity"] = _agg_avg(by_name["gpu_avg_gfx"])
    pt = _agg_sum(by_name["prompt_tokens"])
    gt = _agg_sum(by_name["gen_tokens"])
    out["total_prompt_tokens"] = int(pt) if pt is not None else None
    out["total_gen_tokens"]    = int(gt) if gt is not None else None
    return out


async def query_range(promql: str, start: float, end: float, step: int,
                      timeout: float = 10.0) -> dict:
    """Run a PromQL range query against thanos-querier.

    Returns the standard Prometheus envelope with resultType=matrix.
    """
    token = _read_token()
    if not token:
        return {"status": "error", "errorType": "no_token",
                "error": "SA token not available"}
    url = f"{THANOS_URL.rstrip('/')}/api/v1/query_range"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"query": promql, "start": start, "end": end, "step": step}
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=_verify_ctx()) as cx:
            resp = await cx.get(url, headers=headers, params=params)
        if resp.status_code == 401:
            _token_cache["value"] = None
            token = _read_token()
            if token:
                async with httpx.AsyncClient(timeout=timeout, verify=_verify_ctx()) as cx:
                    resp = await cx.get(url, headers={"Authorization": f"Bearer {token}"},
                                        params=params)
        if resp.status_code != 200:
            return {"status": "error", "errorType": f"http_{resp.status_code}",
                    "error": resp.text[:300]}
        return resp.json()
    except httpx.TimeoutException:
        return {"status": "error", "errorType": "timeout",
                "error": f"thanos-querier range query timed out after {timeout}s"}
    except httpx.RequestError as e:
        return {"status": "error", "errorType": "connection",
                "error": f"{type(e).__name__}: {e}"}


def _matrix_to_series(res: dict) -> list[list[list]]:
    """Extract [[ts, val], ...] series list from a matrix query result.

    Returns one list per label-set (GPU), or an empty list on error.
    Filters out non-finite values.
    """
    if (res or {}).get("status") != "success":
        return []
    rows = (res.get("data") or {}).get("result") or []
    out = []
    for row in rows:
        pts = []
        for ts, v in (row.get("values") or []):
            try:
                fv = float(v)
                if fv == fv and fv not in (float("inf"), float("-inf")):
                    pts.append([ts, round(fv, 3)])
            except (ValueError, TypeError):
                pass
        if pts:
            out.append({"metric": row.get("metric") or {}, "values": pts})
    return out


# Queries to include in the timeseries response.
# Each entry is (key, promql). GPU per-series queries return one series per GPU.
_TIMESERIES_QUERIES: list[tuple[str, str]] = [
    ("gpu_power_watts",   "gpu_average_package_power"),
    ("gpu_gfx_activity",  "gpu_gfx_activity"),
    # Per-GPU VRAM % and edge temperature, for the per-stat sparklines in the
    # GPU tiles (labels align by gpu_id so the division is per-GPU).
    ("gpu_vram_pct",      "100 * gpu_used_vram / gpu_total_vram"),
    ("gpu_temp",          "gpu_edge_temperature"),
    ("vllm_gen_tps",      "sum(rate(vllm:generation_tokens_total[1m]))"),
    ("vllm_prompt_tps",   "sum(rate(vllm:prompt_tokens_total[1m]))"),
    ("vllm_running",      "sum(vllm:num_requests_running)"),
    ("vllm_waiting",      "sum(vllm:num_requests_waiting)"),
    ("vllm_kv_pct",       "avg(vllm:kv_cache_usage_perc or vllm:gpu_cache_usage_perc) * 100"),
    ("vllm_ttft_p95",     "histogram_quantile(0.95, sum by(le)(rate(vllm:time_to_first_token_seconds_bucket[5m])))"),
]


async def timeseries(started_at_iso: str, completed_at_iso: Optional[str] = None) -> dict:
    """Fetch time-series data for sparkline rendering in the run-detail drawer.

    Returns per-metric arrays of {metric: {labels}, values: [[ts, val], ...]}
    for the run window between started_at and completed_at (or now if still
    running). Step is auto-chosen based on window duration.
    """
    import math
    try:
        start_ts = (
            __import__("datetime").datetime
            .fromisoformat(started_at_iso.replace("Z", "+00:00"))
            .timestamp()
        )
        end_ts = (
            __import__("datetime").datetime
            .fromisoformat(completed_at_iso.replace("Z", "+00:00"))
            .timestamp()
            if completed_at_iso
            else time.time()
        )
    except Exception as e:
        return {"available": False, "reason": f"bad timestamps: {e}"}
    if end_ts <= start_ts:
        end_ts = start_ts + 60  # at least 60s window
    window = end_ts - start_ts
    # Choose step so we get ~60 data points — good for a sparkline
    step = max(10, int(math.ceil(window / 60)))
    results = await asyncio.gather(
        *[query_range(promql, start_ts, end_ts, step) for _, promql in _TIMESERIES_QUERIES]
    )
    out = {"available": True, "start": start_ts, "end": end_ts, "step": step}
    for (key, _), res in zip(_TIMESERIES_QUERIES, results):
        out[key] = _matrix_to_series(res)
    return out


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
                v = float(row["value"][1])
                if v == v and v not in (float("inf"), float("-inf")):
                    slot[name] = v
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
        "gen_tokens_total":     _scalarize((raw["vllm_gen_tokens_total"].get("data") or {}).get("result", [])),
        "prompt_tokens_total":  _scalarize((raw["vllm_prompt_tokens_total"].get("data") or {}).get("result", [])),
        "ttft_p95_seconds":     _scalarize((raw["vllm_time_to_first_token"].get("data") or {}).get("result", [])),
    }

    return {
        "available": True,
        "as_of": time.time(),
        "gpus": gpus,
        "vllm": vllm,
        "errors": errors,
    }
