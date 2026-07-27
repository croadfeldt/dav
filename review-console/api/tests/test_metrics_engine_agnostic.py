"""Metric queries must not assume vLLM is the only inference engine.

DAV serves two engines. vLLM publishes `vllm:*`; llama.cpp publishes `llamacpp:*`
under entirely different names (`tokens_predicted_total`, not
`generation_tokens_total`). Every token/throughput/queue query was written
against `vllm:` only, so the moment a llama.cpp model was serving — the 235B and
gpt-oss-120b both are — those panels went blank and finished runs finalized with
zero tokens recorded, permanently.

The GPU tiles kept working throughout, which is what made this confusing to
diagnose: those come from the AMD exporter and are engine-independent. "Some
stats present, others blank" reads like a UI bug rather than a query one.

`or` is the right operator here: the two sides carry different `__name__`
labels, so no series on the right is shadowed by one on the left, and a fleet
running both engines sums both rather than silently reporting whichever it saw
first. It also degrades correctly to a single engine.

Note the fleet ALSO normalizes llamacpp→vllm names via ServiceMonitor
metricRelabelings. These queries deliberately do not depend on that: a
deployment that forgets the relabeling block (gpt-oss-120b did) should degrade
to correct numbers, not to a blank panel.
"""
import re

import pytest

from app import metrics as metrics_mod
from app.metrics import _SNAPSHOT_QUERIES, _TIMESERIES_QUERIES

# Panels that depend on a metric llama.cpp genuinely does not publish. Kept
# vllm-only on purpose — a blank panel beats a number derived from a different
# quantity.
# vllm_kv_pct is the timeseries twin of vllm_kv_cache_pct — llama.cpp exposes no
# KV-cache utilisation gauge at all, so both stay vLLM-only.
_VLLM_ONLY = {"vllm_kv_cache_pct", "vllm_kv_pct",
              "vllm_time_to_first_token", "vllm_ttft_p95"}

_ENGINE_KEYS = [k for k in _SNAPSHOT_QUERIES
                if k.startswith("vllm_") and k not in _VLLM_ONLY]


@pytest.mark.parametrize("key", _ENGINE_KEYS)
def test_snapshot_engine_queries_cover_llamacpp(key):
    q = _SNAPSHOT_QUERIES[key]
    assert "llamacpp:" in q, f"{key} is vLLM-only; blank under llama.cpp: {q}"


@pytest.mark.parametrize("key", sorted(_VLLM_ONLY & set(_SNAPSHOT_QUERIES)))
def test_metrics_llamacpp_does_not_publish_stay_vllm_only(key):
    """Guard against someone 'fixing' these by mapping an unrelated series in."""
    assert "llamacpp:" not in _SNAPSHOT_QUERIES[key]


def test_timeseries_engine_queries_cover_llamacpp():
    """These drive the run-window sparklines — flat-empty for every llama.cpp run."""
    missing = [k for k, q in _TIMESERIES_QUERIES
               if k.startswith("vllm_") and k not in _VLLM_ONLY and "llamacpp:" not in q]
    assert not missing, f"vLLM-only timeseries: {missing}"


def test_gpu_queries_stay_engine_independent():
    """GPU metrics come from the AMD exporter, not the server. If one ever grows
    an engine-specific selector, the tiles start lying about which card is busy."""
    for key, q in _SNAPSHOT_QUERIES.items():
        if key.startswith("gpu_"):
            assert "vllm:" not in q and "llamacpp:" not in q, f"{key} bound to an engine: {q}"


def test_engine_presence_probes_exist():
    """So the UI can distinguish 'idle' from 'this backend does not publish that'."""
    for k in ("engine_vllm_up", "engine_llamacpp_up"):
        assert k in _SNAPSHOT_QUERIES


def test_engine_probes_reach_the_response():
    """snapshot() assembles its payload from an EXPLICIT key list, so adding a
    query to _SNAPSHOT_QUERIES is not enough to surface it — the original version
    of this test only checked the query dict, and the two probes ran on every
    snapshot (a Prometheus round-trip each) while being silently discarded.

    Asserting the response shape is the whole point: a probe nobody can read is
    worse than no probe, because it costs work and looks present in the code."""
    from unittest.mock import patch

    async def fake_query(promql, timeout=5.0):
        # Absent series -> empty vector, which is how Prometheus answers
        # `count(x) > 0` when x is not scraped at all.
        present = "llamacpp:" in promql
        return {"status": "success",
                "data": {"result": [{"metric": {}, "value": [0, "1"]}] if present else []}}

    import asyncio
    # snapshot() short-circuits outside the cluster (no SA token); stub the gate
    # so this exercises the payload assembly, which is what regressed.
    with patch.object(metrics_mod, "is_available", return_value=True), \
         patch.object(metrics_mod, "query", side_effect=fake_query):
        snap = asyncio.run(metrics_mod.snapshot())

    assert "engine" in snap, "engine block missing from snapshot payload"
    assert snap["engine"]["llamacpp"] is True
    assert snap["engine"]["vllm"] is False, "absent series must read False, not None"
    assert snap["engine"]["any"] is True


def test_or_operands_use_distinct_metric_names():
    """`a or b` drops right-hand series whose label set matches the left. Identical
    __name__ on both sides would silently discard one engine's data."""
    for key, q in _SNAPSHOT_QUERIES.items():
        # engine_*_up probe a single engine on purpose — one operand is correct.
        if "llamacpp:" not in q or key.startswith("engine_"):
            continue
        names = set(re.findall(r"(?:vllm|llamacpp):[a-z_]+", q))
        assert len(names) >= 2, f"{key}: expected distinct operands, got {names}"
