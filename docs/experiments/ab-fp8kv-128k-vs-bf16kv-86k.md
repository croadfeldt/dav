# A/B test: fp8-KV @ 128K vs bf16-KV @ 86K (Qwen3-32B on dual R9700)

**Date:** 2026-05-28
**Test set:** Barclays 2026-05-20 (6 managed UCs, 3 samples each = 18 sample-evaluations)
**Engine commit:** `bfb62ff` (cross-turn dedup + prompt nudge + summary record, STAGE2_PROMPT_VERSION 1.6)
**vLLM image:** `kyuz0/vllm-therock-gfx1201@sha256:63c3abe1…`
**Decision:** **Revert to bf16-KV @ 86K. Keep the dedup engine fix.**

---

## Motivation

A previous live run (`dav-stage2-console-929164`) surfaced a cross-turn
tool-call repetition pattern: Qwen3 lost track of which `search_docs` calls
it had already issued and re-emitted the same query on turns 7/9/11 as
context grew past ~20K tokens. The fix landed in two parts:

1. **Engine-side** (load-bearing): `Stage2Agent._call_history` short-circuits
   cross-turn duplicates with a `⛔ DUPLICATE-CROSS-TURN` marker pointing
   at the original `tool_call_id` (commit `bfb62ff`).
2. **vLLM-side** (belt-and-suspenders): bump max-model-len from native 32K
   so the dedup marker has room to land inside the conversation instead
   of bumping into the context ceiling.

For #2, two viable configurations on the 2× R9700 hardware budget:

| Option | max-model-len | KV dtype | KV cache headroom |
|---|---|---|---|
| Baseline | 86,016 | bf16 | 88,768 tokens (1.03× max-seq) |
| Experimental | 131,072 | fp8 | 177,552 tokens (1.35× max-seq) |

The bf16-86K option was chosen as the safe deploy first; this A/B
evaluated whether fp8-128K was worth the quality risk.

---

## Methodology

1. **Phase 1 — Baseline.** `dav-stage2-console-933026`
   - vLLM config: `--max-model-len=86016 --max-num-seqs=32` (bf16 KV)
   - Trigger Barclays Set 3 via `POST /api/runs` (verification, 3 samples)
   - Same dedup engine `bfb62ff`
2. **Phase 0 — fp8-KV smoke.** Restart vLLM with `--kv-cache-dtype=fp8`
   added, max-model-len unchanged. Confirm the gfx1201 ROCm build accepts
   the flag and boots cleanly. **Result: passed.** vLLM emitted a warning:
   `"Using uncalibrated q_scale 1.0 and/or prob_scale 1.0 with fp8
   attention. This may cause accuracy issues."`
3. **Phase 2 — Experimental.** Bump max-model-len to 131072, same
   `--kv-cache-dtype=fp8`. Same Barclays Set 3 run, same engine, same
   seeds (`dav-stage2-console-964910`).
4. **Compare** per-UC and aggregate metrics across the two runs.

---

## Results

### Aggregate

| Metric | Phase 1 (86K bf16) | Phase 2 (128K fp8) | Δ |
|---|---|---|---|
| UCs successful | 5 / 6 | **6 / 6** | +1 capability win |
| Total samples | 15 | 18 | +3 |
| Total tool calls | 95 | 85 | −10 |
| Cross-turn duplicates blocked | 1 | 1 | unchanged |
| Section-title misses (anti-fishing) | 11 | 10 | −1 |
| Runner wall time | 1281 s (21 min) | **2654 s (44 min)** | **+107 % slower** |
| Mean per-UC wall time | 197 s | 442 s | **+124 %** |

### Per-UC

| UC handle | Phase 1 wall (s) | Phase 2 wall (s) | Components p1 | Components p2 | Mean confidence p1 | Mean confidence p2 |
|---|---|---|---|---|---|---|
| governance/standard/self-service-fsi | 237 | 382 | 10 | 4 | 85 | 85 |
| automation/dev/ansible-api | 222 | 579 | 8 | 7 | 80.6 | **65.0** |
| governance/prod/finops | 204 | 522 | 10 | 6 | 85 | **73.3** |
| security/standard/identity | 188 | 318 | 8 | 4 | 85 | 85 |
| orchestration/prod/workflow | 134 | 558 | 3 | 10 | 85 | 81.5 |
| infrastructure/prod/greenfield | **failed** | 294 | 0 (JSON trunc.) | 6 | — | 73.3 |

Gap counts were 0 across all UCs in both runs (model concluded
architecture meets requirements every time).

---

## Interpretation

### What fp8-KV @ 128K bought us

- **One real recovery.** UC #6 (`infrastructure/prod/greenfield`) failed
  in Phase 1 from `AgentError: could not parse final analysis as JSON:
  unbalanced braces` — the model emitted a partial valid JSON object,
  then ran out of `--max-tokens=6144` mid-output. Phase 2 succeeded.
  This is NOT a context-pressure fix; it's a `max-tokens` ceiling issue
  that happened to mask itself differently in Phase 2 (which generated
  a shorter analysis for this UC).
- **Slightly fewer tool calls in aggregate.** 95 → 85. Hard to attribute
  cleanly: could be context-recall benefit, could be exploration drift
  from a different per-token sampling distribution at fp8.

### What it cost us

- **Wall time doubled.** 21 → 44 min for the same 6 UCs. The fp8-KV
  quant/dequant overhead per attention layer is real, and longer
  contexts let the model take more turns before being forced to commit.
- **Confidence dropped 5–15 points on 3 of 6 UCs.** That tracks
  directly with vLLM's emitted warning: uncalibrated q_scale/prob_scale
  factors at 1.0 reduce the fidelity of the attention computation. This
  is the well-documented fp8-KV quality cost; the kyuz0 image's FP8
  Qwen3-32B checkpoint doesn't ship pre-computed scaling factors.
- **Cross-turn duplicates blocked: unchanged at 1.** Both runs hit the
  same single cross-turn duplicate (UC #3). The longer context window
  didn't change the model's recall behavior at the scale this corpus
  exercises (per-turn context usage stayed under 30K tokens for every
  UC in both runs).

### Why the 128K window was overkill

Looking at the actual per-turn tokens-in-context from the run-corpus
logs, even the deepest exploration only reached ~30K context use.
86K is already 2.7× the model's natural exploration depth on this corpus.
The dedup engine (`bfb62ff`) is the real fix; extra context past 86K
delivered no measurable recall improvement because the bottleneck was
the model's *planning* memory, not the *context-window* memory.

---

## Decision

**Revert to `--max-model-len=86016` with bf16 KV (no `--kv-cache-dtype`
flag).** The engine-side dedup is the load-bearing fix and is now live
on commit `bfb62ff`. The 128K + fp8-KV experiment delivered one
narrow capability win that's better addressed by tuning `--max-tokens`,
at the cost of ~10% confidence and 2× wall time. Not worth it.

### Follow-up actions

1. **Bump `--max-tokens` for the run-corpus Tekton task** ✅ **Done +
   validated 2026-05-28** (commit `e888c62`). 6144 → 8192 was
   insufficient (UC #6's final response is 33,907 chars / ~9000 tokens
   for a 44-component analysis). 8192 → 16384 was the working value.
   Validation run `dav-stage2-console-970095` on the same Barclays
   corpus: **6/6 success, +22% wall time concentrated in UC #6, zero
   quality regression** (matched Phase 1 confidence scores on all
   UCs that succeeded both runs).
2. **Defer fp8-KV until calibrated scaling factors are available.**
   Either (a) ship a pre-calibrated Qwen3-32B checkpoint with q_scale
   / prob_scale baked in, or (b) wait for the kyuz0 image to add
   on-the-fly calibration. The uncalibrated warning is not a hypothetical
   — it cost measurable per-UC confidence here.
3. **Sleeper option: re-test fp8-KV at 86K** (no context bump). Same
   capacity as baseline, halved KV memory, no rope-scaling overhead.
   Cheaper to evaluate (one phase instead of two) and would isolate
   the quality impact of fp8-KV from the impact of YaRN-extended
   context. Worth running on a quieter day.

---

## Reproducibility

| Item | Phase 1 (baseline) | Phase 2 (experimental) |
|---|---|---|
| PipelineRun | `dav-stage2-console-933026` | `dav-stage2-console-964910` |
| Workspace run_id | `2026-05-28T01-51-00Z-cd8c658` | `2026-05-28T10-42-18Z-6fad95a` |
| Start | 2026-05-28T01:51:00Z | 2026-05-28T10:42:18Z |
| End | 2026-05-28T02:12:21Z | 2026-05-28T11:26:32Z |
| Engine | dav-engine sha256:e6ca1347… (build 38) | same |
| vLLM args | `--max-model-len=86016 --max-num-seqs=32` | same + `--max-model-len=131072 --kv-cache-dtype=fp8` |
| Seeds | seed/UC: 928757360, 1922143993, 557149923, 1762012150, 2078669563, 157370292 | same |

Raw turns files and analysis YAMLs are in the workspace under
`results/<run_id>/` for both runs and remain available until
explicitly cleaned up.
