# A/B test: ROCM_AITER_UNIFIED_ATTN vs ROCM_ATTN decode backend (Qwen3-32B on dual R9700)

**Date:** 2026-06-05
**Hardware:** 2× AMD Radeon AI Pro R9700 (gfx1201 / RDNA4), tensor-parallel-size 2
**vLLM image:** `kyuz0/vllm-therock-gfx1201@sha256:63c3abe1…` (v0.21.1rc1.dev147)
**Test set:** 15 managed `test/standard/*` UCs, verification mode, **1 sample/UC** (matches ROCM_ATTN baselines exactly)
**AITER run:** `dav-stage2-console-702758` (`2026-06-05T23-39-43Z-35641d1`)
**ROCM_ATTN baselines:** `b929352`, `2508edb` (2026-06-01), `5934217` (2026-05-31)
**Decision:** **Promote ROCM_AITER_UNIFIED_ATTN as the Qwen3-32B default.**

---

## Motivation

DAV stage-2 is **decode-bound**: ~18 agent-loop turns/UC × hundreds of decoded
tokens/turn at 12–25K-token context (86K YaRN window). Per-UC wall time is
dominated by single-stream decode throughput, and full-corpus runs were hitting
the pipeline timeout.

vLLM auto-selects `ROCM_ATTN` on gfx1201 — the fp8 `TURBOQUANT` attention path
is rejected for `AttentionType.DECODER`, so it falls back to `ROCM_ATTN`:

```
[rocm.py:555] Found incompatible backend(s) [TURBOQUANT] with AttentionType.DECODER.
  Overriding with ROCM_ATTN out of potential backends:
  ['ROCM_ATTN', 'ROCM_AITER_UNIFIED_ATTN', 'TRITON_ATTN'].
```

The kyuz0 toolbox ships `ROCM_AITER_UNIFIED_ATTN` (Triton-based aiter unified
attention) specifically to fix long-context decode on the R9700 — DAV's regime.

### Gotcha: it must be set via the CLI flag, not an env var

`VLLM_ATTENTION_BACKEND` is **not recognized** by this build
(`WARNING [envs.py:1900] Unknown vLLM environment variable detected`) and is
silently ignored. The supported mechanism is the `--attention-backend
ROCM_AITER_UNIFIED_ATTN` CLI flag, which bypasses the fp8 auto-override:

```
[rocm.py:510] Using ROCM_AITER_UNIFIED_ATTN backend (selected via --attention-backend).
[rocm_aiter_unified_attn.py:132] Using aiter unified attention for RocmAiterUnifiedAttentionImpl
```

(The backend also sets the KV-cache block size to 64.)

---

## Speed result (decode micro-benchmark)

Isolated decode tok/s at ~12.8K-token context by running the same prompt at
`max_tokens=1` then `max_tokens=512` and subtracting prefill (greedy, seed=0).

| Backend | Mean decode tok/s | Range | Full-gen wall (512 tok) | Output hash |
|---|---|---|---|---|
| `ROCM_ATTN` (baseline) | **13.34** | 10.99–15.58 | 33–47 s | `197c19cb…` |
| `ROCM_AITER_UNIFIED_ATTN` | **25.74** | 23.86–27.35 | ~20 s | `0f8ce15d…` |

**≈1.93× faster decode**, and far more stable (tight 23.9–27.4 band vs the
baseline's jittery 11–16). Each backend is internally deterministic (identical
hash across its 3 iters); the hash differs *between* backends because different
attention kernels take different numeric paths — so this is NOT a bit-identical
free win and required a quality A/B (below).

---

## Quality result (full 15-UC run)

DAV gap output is **stochastic**: the two ROCM_ATTN baselines (same backend,
same 15 UCs) agree on only **9 of 26 gaps** by exact (gap_id, severity) — ~35%.
gap_ids are positional (`GAP-001/002` per UC) and titles are freeform LLM text,
so exact-match is the wrong metric. Quality is judged **distributionally**
against the ROCM_ATTN envelope, plus a semantic spot-check.

| Run | Success | Gaps | major / moderate / other | Gaps/UC |
|---|---|---|---|---|
| **AITER (702758)** | **15/15** | 28 | 4 / 21 / 3 | 1.87 |
| ROCM base A (b929352) | 15/15 | 26 | 8 / 15 / 3 | 1.73 |
| ROCM base B (2508edb) | 15/15 | 26 | 2 / 17 / 7 | 1.73 |
| ROCM base C (5934217) | 14/15 | 24 | 5 / 13 / 6 | 1.60 |

- **Per-UC:** AITER is within **±1 gap of baseline on every UC** (5 UCs +1,
  3 UCs −1, 7 identical) — inside the run-to-run noise floor.
- **Severity:** AITER's `major` (4) is mid-band (baselines 2–8); the count skews
  slightly toward `moderate` (21 vs 13–17), i.e. more decisive than `advisory`.
- **Semantic spot-check** (diverged UCs): AITER's extra/changed gaps are
  coherent and on-topic, generally *more* specific — e.g. `vm-metric` upgraded a
  vague `[advisory] Missing VM metrics` into two concrete `[moderate]`
  architectural gaps; `dns-metric` elevated the core gap to `[major]`. The one
  place it found fewer (`custom-service`, dropped "Event Delivery") is a single
  moderate gap within noise. No hallucinated/noise gaps observed.

**Wall time:** 15 UCs in **1h50m (~7.3 min/UC)** — comfortably under the timeout
that killed earlier ROCM_ATTN full-corpus runs.

---

## Decision

**Promote `ROCM_AITER_UNIFIED_ATTN`.** Unlike the fp8-KV experiment (which cost
5–15 confidence points for more context), this nearly doubles decode throughput
with **no quality regression** — arguably a marginal improvement in gap
decisiveness/specificity. Set via `--attention-backend ROCM_AITER_UNIFIED_ATTN`
in `llm-serving/04-serving-runtimes/vllm-rocm-qwen3-32b.yaml`. Revert to
`ROCM_ATTN` only if a future image regresses aiter unified-attn quality.

## Reproduce

- Speed: `/tmp/bench_vllm.py 20000 512 3` against the model route (prefill-
  subtracted decode tok/s).
- Quality: trigger 15 `test/standard/*` UCs via `managed_uc_uuids`, verification,
  `sample_count=1`; compare the distributional table + semantic spot-check above.
