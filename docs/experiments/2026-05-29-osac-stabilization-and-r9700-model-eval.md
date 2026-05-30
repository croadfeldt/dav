# OSAC stabilization + R9700 model/config evaluation (dual R9700, gfx1201)

**Dates:** 2026-05-28 → 2026-05-30
**Hardware:** 2× AMD Radeon AI PRO R9700 (RDNA4 / gfx1201, Navi 44), 32 GB VRAM each = 64 GB total; 128 GB system RAM; single node `ocp-worker03`
**vLLM image:** `docker.io/kyuz0/vllm-therock-gfx1201@sha256:63c3abe13e08e11f379265d20e46e70a5aee4f9b3c9bce09bbfe410c964d3916` (community "TheRock" ROCm build for gfx1201)
**vLLM version:** `0.21.1rc1.dev147+ga10d69116.d20260520`
**Outcome:** OSAC stage-2 went **0/15 → 15/15** (run `dav-stage2-console-103283`). Qwen3-32B-FP8 confirmed the stable production default; Qwen2.5-72B-AWQ evaluated as a larger candidate.

> This document is the posterity record for the multi-day stabilization +
> model-evaluation effort. §2 (Model & serving configuration reference) and
> §3–4 (the config math) are the **future playbook** — read those first when
> bringing up a new model on this stack.

---

## 1. Executive summary

- **DAV stage-2 OSAC was 0/15 and is now 15/15.** Seven distinct bugs, peeled off one at a time across two passes — each fix exposed the next, shallower one. None were model-quality problems; all were resolution / parsing / config / throughput issues. See §5.
- **The binding constraint on this hardware is `throughput × route-timeout`, not VRAM.** At ~20–26 tok/s and a 600 s gateway timeout, the honest single-generation ceiling is ~10–13 K output tokens. `max-tokens=16384` over-provisioned *into a range that can't physically complete in time*, which is exactly where runaway generations died with 504s. See §3.
- **AWQ 4-bit works on gfx1201** via the Triton-W4 path (`VLLM_USE_TRITON_AWQ=1`) — directly enabling 70B-class models in 64 GB VRAM at full GPU speed (no RAM offload). See §2.4.
- **Bigger model ⇒ less context on fixed VRAM.** Qwen3-32B-FP8 (32 GB weights) leaves room for an **86 K** context; Qwen2.5-72B-AWQ (~40 GB weights) leaves KV for only **~54 K tokens**. See §4.
- **Durable infrastructure** (persistent kernel cache, `r9700-llm` abstraction route, per-(model,use) sampling profiles, MCP corpus refresh) survives every model swap. See §7.

---

## 2. Model & serving configuration reference  ← future playbook

Every model tried on this stack, with the full serving config and the verdict. Copy the closest row when bringing up a new model.

### 2.1 Qwen3-32B-FP8 — **STABLE PRODUCTION DEFAULT**

| | |
|---|---|
| Repo | `Qwen/Qwen3-32B-FP8` |
| Quant | FP8 (W8A8 block 128) — native, no offload |
| Weights | ~32 GB (≈16 GB/GPU at TP=2) |
| Parser | `--tool-call-parser hermes` |
| Context | `--max-model-len 86016` via YaRN (`--hf-overrides` rope_scaling factor 4.0, original 32768); native is 32 K |
| KV | bf16 (NOT fp8 — see note) → ~86 K usable context |
| Throughput | ~26 tok/s generation, prefix-cache hit ~89 % |
| Key args | `--tensor-parallel-size 2 --dtype auto --max-num-seqs 32 --gpu-memory-utilization 0.90 --enable-prefix-caching --enable-auto-tool-choice` |
| Verdict | **The reliable default.** 15/15 OSAC. Big context window is its key advantage for DAV stage-2. |

- **Do NOT use `--kv-cache-dtype fp8`** to chase 128 K: A/B tested 2026-05-28, cost 5–15 confidence points on 3/6 UCs and doubled wall time. The kyuz0 FP8 checkpoint ships no calibrated q_scale/prob_scale, so fp8 attention runs uncalibrated. Detail: `docs/experiments/ab-fp8kv-128k-vs-bf16kv-86k.md`.
- `--max-num-seqs 32` (down from the vLLM default 256): at 86 K context the sampler warmup for 256 dummy requests OOMs during init. 32 is plenty for serial DAV.

### 2.2 Qwen3.6-27B-FP8 (+ inline MTP) — **BLOCKED (upstream bug)**

| | |
|---|---|
| Repo | Qwen3.6-27B-FP8 (hybrid Gated DeltaNet, inline MTP) |
| Why wanted | MTP for higher tok/s; vision variant available |
| Result | **0/15 across 9+ overnight runs.** |
| Root cause | **vLLM bug [#43713](https://github.com/vllm-project/vllm/issues/43713)** (fix PR #43714): the `qwen3_xml` tool parser concatenates multi-function `<function=…>` arguments into one string; on the next turn vLLM's own `_postprocess_messages` does `json.loads()` on it → `Extra data: line 1 column N` crash. The `hermes` parser silently *discards* Qwen3.6's XML tool calls (inner-JSON decode fails). Both paths → 0/15. |
| Verdict | **Do not attempt until #43714 merges and kyuz0 rebuilds**, or build a locally-patched image. The MTP throughput win does not justify the integration cost under a must-be-stable constraint. |

### 2.3 Qwen3-Coder-30B-A3B-Instruct-FP8 — **single-call OK, agentic loop NO**

| | |
|---|---|
| Repo | `Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8` (MoE, 30.5 B total / 3.3 B active) |
| Parser | `--tool-call-parser qwen3_coder` (hermes swallows its calls; this is the correct lineage parser) |
| Behavior | Reaches the agent loop, then **"fishes for section titles"** — ~15 invented-title `get_document_section` calls per UC, burning the budget. v1.9 prompt hardening made it WORSE (226 → 447 misses), not better. |
| Single-call workloads | Arch-review (7.8 KB) and enhancement (19.3 KB, proper patch blocks) work fine. |
| Verdict | **Not for stage-2 agentic loops** on this corpus; fine for single-shot arch-review/enhancement. |

### 2.4 Qwen2.5-72B-Instruct-AWQ — **larger-model evaluation candidate**

| | |
|---|---|
| Repo | `Qwen/Qwen2.5-72B-Instruct-AWQ` (dense 72 B, 4-bit AWQ INT4) |
| Weights | ~38.7 GB total (~19.4 GB/GPU at TP=2), 11 shards |
| **Quant path** | `--quantization awq` + **`VLLM_USE_TRITON_AWQ=1`** (env). AWQ-INT4 **works on gfx1201 via the Triton-W4 dequant+GEMM path**. Marlin (`awq_marlin`/`gptq_marlin`) is CUDA-only — auto-fallback to Triton/HIP on ROCm. The kyuz0 image already ships tested AWQ-INT4 models (attested up to 35 B; 72 B was un-attested before this run and now confirmed working). |
| Parser | `--tool-call-parser hermes` (Hermes `<tool_call>` template baked into Qwen2.5's `tokenizer_config.json`; emits clean valid-JSON tool calls — verified in the live DAV agent loop). |
| Context | `--max-model-len 32768` (native; NO YaRN). KV cache = **8.37 GiB → 54,832 tokens → 1.67× concurrency at 32 K**. Could push to ~49 K (1×) but 32 K is clean and covers OSAC. |
| Boot | first-boot AWQ autotune + torch.compile ≈ 8 min; weight load ~230 s |
| Throughput | ~12–18 tok/s (≈half the 32 B) — **route timeout raised to 900 s** on this ISVC to absorb it (see §3) |
| Key args | `--tensor-parallel-size 2 --quantization awq --dtype auto --max-model-len 32768 --gpu-memory-utilization 0.92 --enable-prefix-caching --enable-auto-tool-choice --tool-call-parser hermes`; env `VLLM_USE_TRITON_AWQ=1` |
| NOT needed | the FP8 W8A8 block-kernel ConfigMaps (those are FP8-specific; AWQ uses INT4 kernels) |
| Verdict | **Not worth it for DAV on this hardware** (run `109569`, 5/6). ~5–7× slower than the 32 B, ⅓ the context, 1 truncation failure, only mixed/comparable quality. Kept as a stopped, preserved option. Full analysis in §9. |

### 2.5 Config knobs that matter on this stack (apply to any model)

- **`--served-model-name {{.Name}} r9700-llm`** — multiple aliases, **single-space form, NO `=`** (argparse `nargs="+"` rejects the `=` form). The second alias `r9700-llm` is the stable abstraction (see §7).
- **Kernel-cache env** (persistent autotune): `TRITON_CACHE_DIR`, `VLLM_CACHE_ROOT`, `TORCHINDUCTOR_CACHE_DIR`, `AITER_JIT_DIR`, all under `/var/cache/llm/{{.Name}}/…` on the RWX CephFS `r9700-kernel-cache` PVC. Subdir-per-`{{.Name}}` so model caches don't collide. First boot still autotunes (~8–15 min); same-model restarts replay.
- **ROCm/RCCL env** (carried on every R9700 runtime): `LD_LIBRARY_PATH` (so RCCL finds `libamd_smi`), `LD_PRELOAD=…/libtcmalloc_minimal.so.4`, `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`, `NCCL_PROTO=simple`, `PYTORCH_TUNABLEOP_ENABLED=0`. `iommu=pt` on the node (MachineConfig) is required for RCCL GPU-to-GPU DMA — without it `ncclCommInitRank` hangs.
- **Route timeout** propagates from the **ISVC** annotation `haproxy.router.openshift.io/timeout` → the auto-managed Route (via odh-model-controller) and **survives reconciliation**. Annotating the Route directly does NOT stick (KServe reconciles it back to ~30 s). See §3.
- **GPU-tier label** `gpu-tier: r9700` on the ISVC → the `r9700-llm` Service/Route follow whichever R9700 model is up.

### 2.6 Known gfx1201 gotchas (for image updates)

- **TP=2 RCCL deadlock is version-specific.** Both GPUs pin at 100 % with zero tokens at RCCL 2.27.7 / vLLM 0.19.x / ROCm 7.2.1 (vLLM #40980, ROCm #5480); 2.27.3 + older nightlies work. The current pinned image is on the good side (it serves TP=2 fine). **Pin the digest; smoke-test TP=2 after any image bump.**
- **FP8 silent FP32 fallback** (vLLM #28649): gfx1201 was missing from AITER's arch map → FP8 silently dequantizes to FP32 (~18–22 tok/s instead of ~35–40). The pinned image patches aiter to map gfx1201 → MI350X.
- AWQ Marlin kernels are CUDA-only; ROCm GPTQ-MoE on non-SiLU activations has no Marlin fallback (vLLM #34118) → **prefer dense + AWQ over MoE + GPTQ** on this stack.

---

## 3. The throughput × timeout principle (the load-bearing config lesson)

A single inference call must satisfy:

```
max_tokens / throughput(tok/s)  <  route_timeout(s)
```

On this stack: ~20–26 tok/s × 600 s ⇒ **honest ceiling ≈ 10,800–13,200 output tokens.**

- A legitimate verbose DAV analysis tops out ~9,000 tokens (the documented UC #6 greenfield case) = ~450 s at 20 tok/s — **already under 600 s**.
- The original `max-tokens=16384` (bumped 2026-05-28 "for verbose analyses with margin") over-provisioned into the 13–16 K range that *cannot complete before the 600 s gateway gives up*. Only degenerate over-generations reach that range — so 16384's "margin" was illusory and was precisely where the 504s lived.
- **Fix:** `max-tokens` is now the deployment variable `dav_stage2_max_tokens` (default **10240**) — covers UC #6 with margin, keeps every generation under the route timeout at ~20 tok/s. **The Tekton task `--max-tokens` arg WINS over any `model_use_profile`** (`run_corpus.py:803`), so the task default is authoritative; a profile row would NOT have taken effect.
- **Corollary for slower models:** the 72B at ~15 tok/s needs `10240 / 15 ≈ 683 s`, so its ISVC route timeout is **900 s**. On a faster GPU, raise `max-tokens` and the route timeout in lock-step.

---

## 4. Context vs KV — the bigger-model tradeoff on fixed VRAM

KV-cache room is whatever VRAM is left after weights:

| Model | Weights | KV budget | Usable context |
|---|---|---|---|
| Qwen3-32B-FP8 | ~32 GB | large | **86 K** (with YaRN) |
| Qwen2.5-72B-AWQ | ~38.7 GB | 8.37 GiB | **54,832 tokens** (~32–49 K) |

**The 72 B is "smarter per token" but has roughly half to a third of the context room** on the same 64 GB. For DAV stage-2 — which deliberately runs an 86 K window so the cross-turn dedup marker has room to land and deep UCs (20–25 tool calls) don't hit the ceiling — this is a real, quantified downside of the larger model on this hardware, independent of any quality difference.

---

## 5. The OSAC failure chain — 0/15 → 15/15 (seven bugs, two passes)

Run progression: **`088639` 0/15 → `092466` 11/15 → [4-UC isolation runs `098818`, `101108`] → `103283` 15/15.**

**Pass 1 (2026-05-29):**

1. **MCP handle resolution too strict** — `_resolve_handle` accepted only the full handle (`dcm/architecture/DCM-Capabilities-Matrix.md`) or an exact relpath. The model calls `search_docs` (gets the full handle), then naturally shortens it to `dcm/DCM-Capabilities-Matrix.md` for follow-up `get_document_section` calls. Every shortcut missed → "section not found" → fishing cascade → context bloat → 504. **Fix:** namespace+tail fallback resolving the shortcut to the unique doc whose path ends with the trailing segment. Commit `9804875`.
2. **Capability-ID-as-section-title misuse** — the model passed matrix row IDs (`OBS-002`, …) as `section_title` to `get_document_section`; they're table rows, not headers, so every call missed. **Fix:** new `get_capability(capability_id)` MCP tool indexes markdown-table rows matching `[A-Z]{2,5}-\d{3}` (396 rows) and returns `{section, table_header, row}`; stage-2 prompt 1.8 → **1.10** points the model at it. Commit `1df7ae5`. *(v1.9 was skipped — it was a regression, see §6.)*
3. **HAProxy route timeout 30 s** — the KServe-auto-generated `qwen3-32b` Route inherited the cluster default ~30 s; long stage-2 turns blew it. **Fix:** ISVC annotation `haproxy.router.openshift.io/timeout: 600s` (propagates + survives reconciliation). Commit `llm-serving d40a1bb`.
4. **`severity: 'medium'` schema rejection** — model emitted `medium` (from the confidence axis) where severity's 41-60 label is `moderate`; a full 358 s analysis crashed on `invalid severity label`. **Fix:** `_SEVERITY_ALIASES`. Commit `b6a6546`.

**Pass 2 (2026-05-30) — got `092466`'s 11/15 to converge:**

5. **Oversized `get_document_section` dump** — the 4 residual failures all fetched the **Foundational Capabilities Matrix as one section: 87,686 chars / ~22 K tokens / 331 rows**, bloating context into a ~15 K-token runaway generation that blew even the 600 s timeout. **Fix:** cap sections over `_MAX_SECTION_CHARS` (32000) to a 6 K head + drill-down guidance (capability rows → `get_capability`, else subsection list). Corpus-agnostic, size-triggered. Verified: 87,686 → ~8,192 chars (92 % smaller). Commit `a0dee22`.
6. **`max-tokens` too high for the stack** — see §3. Commit `0f6346d`.
7. **`severity: 'low'`/`'high'` rejection** — once 504s were gone, a complete 270 s analysis died on `invalid severity label 'low'`. Pass-1's fix #4 aliased only `medium`, calling low/high "too ambiguous" — reality refuted that: the model emits the **whole** low/medium/high scale. **Fix:** `low→minor, medium→moderate, high→major` (middle of the 5-level range, ordering preserved; advisory/critical reserved for explicit use); aliased dict labels with confidence-style scores use the canonical default score; unknown labels still raise. Commit `10fe660`.

**Final validation `103283`:** 15/15, 0 section misses, 0 severity rejects, 0 real gateway 504s, 15 high-quality analyses (proper spec_refs, structured components/data/capabilities/gaps).

---

## 6. Engine / parser robustness lessons

- **Don't over-harden agentic-guardrail prompts.** Bumping STAGE2_PROMPT_VERSION 1.8 → 1.9 to add a "stop fishing" contract made fishing *worse* on every model (Coder 226 → 447 misses; even broke the 32 B baseline) — the language is self-fulfilling, the model focuses on whatever the instructions spend the most words on. Reverted (`3d36c3c`); v1.10 adds *one* bullet, not a lecture.
- **Map the whole alternate vocabulary at once.** Aliasing only `medium` (and declaring low/high "too ambiguous") just deferred the failure to the next run. When the model borrows a 3-bucket scale, alias all three. Coercing a *known* vocabulary is fine; arbitrary garbage (`catastrophic`) still raises.
- **Cap tool outputs by size, corpus-agnostically.** A single huge tool result (the 22 K-token matrix) is a context bomb that triggers runaway generation. Cap by size + redirect, don't special-case documents.
- **Log artifacts ≠ behavior.** "matrix-cap hits: 0" was a grep artifact (the tool *result* isn't echoed to the run log); the cap had fired — proven by context only growing ~3.6 K tokens after the fetch instead of ~22 K. Verify mechanism, not just log strings.

---

## 7. Durable infrastructure (survives every model swap)

- **Persistent kernel cache** — RWX CephFS PVC `r9700-kernel-cache` at `/var/cache/llm/{{.Name}}/…`. First boot autotunes; same-model restarts replay (Triton + torch.compile AOT). Validated populated (62 MB Triton / 1017 files + 106 MB vLLM on the 32 B). `llm-serving c6e910d`.
- **`r9700-llm` stable abstraction route** — Service + DMZ Route select on `gpu-tier=r9700`; every R9700 ISVC publishes `r9700-llm` as a second `--served-model-name`. Consumers (spamllm) pin `https://r9700.llm.ocp.roadfeldt.com/v1` + model `r9700-llm` once and never reconfigure across model swaps. `llm-serving 73fdc0b`.
- **Per-(model, use) sampling profiles** — migration 014 + API + engine resolution + run-summary `effective_sampling`. Tune per (model × use_key) via `PUT /api/models/{id}/profiles/{use_key}`. **Caveat:** the Tekton task's explicit CLI args (e.g. `--max-tokens`) override the profile (`run_corpus.py:803`).
- **MCP corpus refresh** — hourly CronJob `dav-docs-mcp-refresh` (narrow RBAC) + Config-page "Refresh now" button (`POST /api/mcp/refresh-now`). Closes the silent-staleness gap. `eaa9c73`.
- **Corpus-agnostic engine** — `_GENERIC_REFERENCE_PROFILE` / `fall_back_to_generic` (no DCM-specific constants); consumers load their own ConsumerProfile. `94fe3d0`.

---

## 8. Run ledger

| Run | Model / config | UCs | Result | Taught |
|---|---|---|---|---|
| `088639` | Qwen3-32B-FP8, v1.8, max-tok 16384, route 30 s | 15 | **0/15** | section fishing + 30 s 504s |
| `092466` | + MCP shortcut + get_capability + v1.10 + route 600 s | 15 | **11/15** | 4× matrix-dump 504s remain |
| `098818` | + section cap (`a0dee22`) | 4 | 3/4 | matrix cap recovers 3; 1 over-gen 504 |
| `101108` | + max-tokens 10240 (`0f6346d`) | 4 | 3/4 | 0 504s; 1 `severity 'low'` reject |
| `103283` | + full severity scale (`10fe660`) | 15 | **15/15** | clean — chain complete |
| `109569` | **Qwen2.5-72B-AWQ**, max-len 32768, route 900 s | 6 | **5/6** | larger model ~5–7× slower, ⅓ context, mixed quality, 1 truncation fail → not worth it; 32 B kept |

---

## 9. The 72 B evaluation

**Question:** does a larger model (Qwen2.5-72B-AWQ) produce materially better DAV gap analysis than Qwen3-32B-FP8, and is it worth the costs (≈half throughput, ~32–49 K context vs 86 K)?

**Method:** same 6 OSAC UCs spanning the 32 B's difficulty range (by baseline tool-count: 7–17), same engine, same sampling, same `max-tokens 10240`. Baseline = the 32 B's `103283` analyses (snapshotted). Compared on components_required / data_model_touched / capabilities_invoked / gaps counts **and** qualitative depth/grounding of the richest UC.

**De-risked along the way:** AWQ-72B boots and serves on gfx1201; hermes tool-calling produces valid JSON tool calls in the live DAV agent loop; no RAM offload (fits 64 GB VRAM at TP=2).

**Results (run `109569`, 5/6 — one JSON-truncation failure):**

| UC | 32B (comp/data/cap/gaps · tools · wall) | 72B (comp/data/cap/gaps · tools · wall) |
|---|---|---|
| 1a2c9d8e | 2/2/2/1 · 7t · 40 s | 1/1/1/1 · 2t · 198 s |
| 2b3c4d5e | 4/3/5/2 · 11t · 72 s | 4/3/4/2 · 10t · 487 s |
| 3c4d5e6f | **8/5/8/2 · 17t · 101 s** | 4/3/4/1 · 8t · 470 s |
| 45a1f7e9 | 1/2/1/1 · 9t · 34 s | **6/2/6/2 · 8t · 543 s** |
| 7c8d9e0f | 3/2/3/2 · 10t · 65 s | 2/2/2/1 · 8t · 289 s |
| 1a2b3c4d | 3/3/8/2 · 17t · 79 s | **FAILED** — JSON parse (unbalanced braces) at 928 s |

**What the numbers + the analyses say:**

1. **Throughput: 32 B wins decisively — ~5–7× faster** (34–101 s/UC vs 198–543 s/UC). A full 15-UC OSAC run is ~75 min on the 32 B vs an extrapolated ~3 h on the 72 B.
2. **Exploration breadth: 32 B explores more.** Fewer tool calls on the 72 B (2–10 vs 7–17) → **43 distinct spec IDs cited across the 5 shared UCs vs the 72 B's 22.** The slow 72 B commits to an answer earlier.
3. **Quality: genuinely mixed, no decisive 72 B edge.** On the hard UC `3c4d5e6f` the 32 B was richer *and* better-grounded — 8 components tied to spec IDs (`RSE-001…004`, `OBS-001/002`, `CMP-001`, `SUB-001`) vs the 72 B's 4 *generic* labels ("Resource/Service Request", "DCM Tenant"). But on `45a1f7e9` the 32 B was lazy (1 generic `cost_analysis`) while the 72 B dug deeper — 6 grounded IDs (`OBS-002/005`, `REQ-001/004/007/008`). So each model is better on different UCs; neither dominates on quality.
4. **Reliability: 72 B failed 1/6 on output truncation** — `max-tokens=10240` was too small for its more verbose final emit on the capability-heavy `1a2b3c4d`, cutting the JSON mid-object (unbalanced braces). The 32 B did that UC cleanly. (Raising the 72 B's `max-tokens` fights its 900 s route timeout — `10240/15 ≈ 683 s` already.)
5. **Context: 32 B's 86 K vs the 72 B's 32 K** — the larger window is part of *why* the 32 B explores more before committing.

**Verdict: keep Qwen3-32B-FP8 as the default. The 72 B does not justify its costs** (~5–7× slower, ~⅓ the context, one reliability failure) given only mixed/comparable quality. The 72 B was stopped and the 32 B restored as the stable default; the 72 B manifests + downloaded weights are preserved (ISVC stopped, not deleted) for future revisit.

**The most useful insight — and it points at the self-improvement work:** DAV analysis quality here is gated more by **exploration depth + consistency** than by raw model size. The 32 B's *inconsistency* (rich on `3c4d5e6f`, lazy on `45a1f7e9`) is the real opportunity — a self-improving loop that detects shallow analyses (low tool-call count / few spec refs) and nudges for more grounding would likely beat swapping to a bigger, slower model. See `docs/dav-self-improvement-vision.md`.

---

## 10. Operational runbook

**Swap the active R9700 model** (only one fits — both need all 2 GPUs at TP=2):

```bash
# Stop the current model (KServe scale-to-zero via minReplicas:0 does NOT work —
# RawDeployment HPA needs min>=1; the stop annotation is the working lever):
oc -n llm-serving annotate isvc <current> serving.kserve.io/stop=true --overwrite
# Bring up the other (apply ServingRuntime + ISVC, or un-stop):
oc -n llm-serving annotate isvc <other> serving.kserve.io/stop=false --overwrite
```

Both carry `gpu-tier=r9700`, so `r9700-llm` follows whichever is up — consumers don't reconfigure. **End state must be: qwen3-32b running, others stopped**, unless a challenger proves clearly better AND stable.

**Point a DAV run at a specific endpoint:** `POST /api/runs` accepts `inference_endpoint` + `inference_model` overrides directly (bypasses the configured default). Use the model's own external Route (its ISVC carries the right `haproxy.router.openshift.io/timeout`). Cross-namespace in-cluster service calls (dav → llm-serving) are blocked at the network layer — use the external Route.

**Where things live:** manifests in `/Users/chris/git/llm-serving` (`02-storage/pvcs.yaml`, `03-download-jobs/`, `04-serving-runtimes/`, `05-inference-services/`; local-only repo). DAV engine/console in `/Users/chris/git/dav` (remote `origin`). Ansible vault pass: `/Users/chris/git/dav/.vault_pass` (set `ANSIBLE_VAULT_PASSWORD_FILE`). Tekton tasks applied by `engine.yaml` (tag `engine`), not `tekton.yaml`.

---

## 11. Standing principles (distilled)

1. **Budget output to `throughput × timeout`, not to VRAM.** The slow GPU, not the memory, is the ceiling for a latency-bounded agentic workload.
2. **Bigger model ⇒ less context on fixed VRAM.** Decide which you need more of.
3. **AWQ (Triton-W4) is the 4-bit path on gfx1201; FP8 for native; avoid Marlin/MoE-GPTQ.** RAM offload only for spot-checking a model too big to fit — it re-introduces the timeout problem for real workloads.
4. **Pin the image digest; smoke-test TP=2 + tool-calling after any bump.** The RCCL deadlock and parser behavior are version-specific.
5. **Coerce known model vocabularies; don't hard-fail a complete analysis on one synonym.** And map the *whole* alternate scale at once.
6. **Don't out-word agentic guardrails** — terse beats a lecture; the model fixates on whatever you emphasize.
7. **One stable default, challengers behind the abstraction route.** Always end stable.
