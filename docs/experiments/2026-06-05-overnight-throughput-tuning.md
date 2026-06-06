# Overnight throughput tuning — running ledger (2026-06-05 → 06-06)

**Goal:** cut DAV stage-2 per-UC wall time while holding gap quality.
**Quality is priority 1; speed a close 2nd** (no config is left deployed unless
quality is validated — else revert to last known-good).

**Fixed harness:** 15 managed `test/standard/*` UCs, verification, **1 sample/UC**,
AITER decode backend. Compared against ROCM_ATTN baselines (b929352/2508edb,
24–26 gaps / 1.60–1.73 per UC) and the AITER baseline `702758`.

**Quality gate per run:** 15/15 success; total gaps within ~24–28; gaps/UC ~1.6–1.9;
per-UC within ±1 of baseline; semantic spot-check on diverged UCs shows valid
(not hallucinated) gaps. Watch for context-pressure degradation.

---

## Baseline — `702758` (AITER, MCP cap 8000)
- 15/15, **1h50m**. Gaps 28 / 1.87 per UC. Quality validated ≈ ROCM_ATTN.
- **Agent-loop tax:** mean ~18.5 turns/UC; **3/15 hit the 30-turn cap**
  (`9e0f1a2b` 26×, `8a3c1b0d` 17×, `45a1f7e9` 12×) crawling one large doc
  section-by-section via `get_document_section`. Root cause of the slow tail.

## Exp 1 — `712152` (MCP `get_document` cap 8000→90000)
- 15/15, 1h38m. **No real effect:** the agent goes straight to
  `get_document_section` (never calls `get_document`), so raising that cap
  changed nothing. Cap-hitters still 29 turns. Wall-time delta = AITER variance.
- **Lesson:** the crawl is via `get_document_section`, so the fix must live there.

## Exp 2 — whole-doc short-circuit in `get_document_section` (cap 90000)  `718951`
- **Speed: spectacular.** Crawl collapsed — cap-hitters `9e0f1a2b`/`8a3c1b0d`/
  `45a1f7e9` went **29 → 7-8 turns**; mean ~18.5 → ~7. `max_result=87686` confirms
  the matrix returned whole in one call.
- **Quality: FAILED — 8/15.** 7 UCs hit context overflow: injecting the whole
  ~22K-token matrix pushes the prompt to ~70K input + the 16K output reservation
  = **86,017 > 86,016** (over by 1 token). The engine's post-overflow retry can't
  recover because the bulk is one un-droppable tool result.
- **Key insight:** the per-section crawl was *implicit context management* —
  streaming the doc in small, evictable pieces (sliding window). A single
  whole-doc result defeats that. **Reverted MCP to cap 8000 (safe) immediately.**

## Exp 3 — forward WINDOW in `get_document_section` (cap 14000)  `723339`  ✅ KEPT
- Change: for a large doc, return the requested section + following sections up
  to ~14000 chars (~3.5K tok) with a resume pointer — NOT the whole doc. Each
  window stays evictable (sliding-window dynamics of the validated baseline).
- **Speed: WIN.** Mean turns **18.5 → 11.9 (-36%)**; the 3 original cap-hitters
  collapsed (`9e0f1a2b` 29→8, `8a3c1b0d` 29→10, `45a1f7e9` 29→13). Wall-time
  **1h50m → 1h24m (-24%)**.
- **Quality: HOLDS.** 15/15, **0 overflow**. Gaps 29 / 1.93 per UC (baseline
  28/1.87; ROCM band 24-26/1.60-1.73) — in-band. Per-UC within ±1 on 13/15;
  `custom-service` 1→5 (that UC ranged 1-5 across all prior runs — stochastic);
  semantic spot-check on the lone new cap-hitter (`bare-metal`, 29 turns) shows
  valid, coherent gaps — not degraded.
- One regression to revisit: `5c6d7e8f` (bare-metal) rose 21→29 turns (windows it
  reads 18×). Aggregate still far better; tune later (window size or its grounding).
- **DECISION: keep.** New validated-good = AITER + windowed doc reads (cap 14000,
  `mcp/dav-docs-mcp/server.py` default; also live env `DAV_MCP_MAX_DOC_CHARS=14000`).

---

## Net result (vs the original ROCM_ATTN + 8000-char crawl)
- Decode backend AITER: **1.93× faster** tok/s (Exp on `702758`, quality-validated).
- Doc windowing: **-36% agent turns, -24% wall-clock**, quality held (Exp `723339`).
- Both quality-gated (15/15, gaps in the ROCM_ATTN noise envelope, semantic checks).
- Full-corpus runs that used to time out now finish well inside budget.

## Exp 4 — TunableOp (`PYTORCH_TUNABLEOP_ENABLED=1`, persistent tune file)  ❌ REVERTED
- Decode micro-benchmark @ 12.8K ctx: steady-state **26.05 tok/s** (rock-steady
  26.02) vs AITER baseline **25.74** (noisy 23.86-27.35) → **+1.2%, within noise**.
  Identical output hash (numerically neutral). Variance much tighter, but throughput
  unchanged.
- **Negative result (useful):** decode here is **memory-bandwidth-bound**, so GEMM
  autotuning can't move it — the throughput lever was already maxed by AITER.
- **Reverted** to the maintainer's tested-stable config (TunableOp off); not worth
  the first-request tuning latency + config deviation for ~1%.

## Exp 6 — UC-level concurrency (`--uc-concurrency`)  [BUILT, A/B PENDING]
- Engine: ThreadPool over UCs in `run_corpus` (mirrors `run_samples`); UCs are
  independent (per-UC factories for MCP/inference clients; per-UC seeds derive
  from the UC uuid → order-independent, directly comparable to serial). Results/
  progress mutated only from the main thread via `as_completed`; halt-on-error
  cancels not-yet-started UCs (in-flight finish). Effective in-flight requests
  = uc_concurrency × sample_concurrency (vLLM has 32 slots).
- Plumbed end-to-end: engine arg → Tekton task `uc-concurrency` param →
  pipeline pass-through → `RunTriggerIn.uc_concurrency` → `trigger_run`.
- Why it should win: decode is memory-bandwidth-bound and BATCHED decode reads
  the weights once per step for the whole batch — concurrent UC streams scale
  aggregate tok/s nearly free. Projection: 32-UC production run ~2.3h serial →
  ~50min at concurrency 3.
- **RESULT: ✅ PASS — VALIDATED.** Run `765649` (ucc=3): 15/15, 0 failed,
  **wall 31m51s vs 1h09m memo-serial avg = 2.2× faster**. Gaps 27 / 1.80 per UC
  / 3 major — squarely in the 22–30 band. Single-variable A/B (memo on in both
  arms). Production projection confirmed: 32-UC × 3-sample full validation
  ≈ 1.5–2h (was: impossible — timeouts).

### Cumulative optimization arc (15-UC eval set, quality-gated at every step)
*timed out* → 1h50m (AITER 1.93× decode) → 1h24m (doc windowing −36% turns)
→ 1h05m (retrieval memo −34% turns) → **32min (uc-concurrency 3, 2.2×)**.

## Recommended next (need a model restart / supervision)
- **n-gram / prompt-lookahead speculative**: model-agnostic; might help decode if
  output repeats context. Lower expected value now that decode is confirmed
  bandwidth-bound, but the only remaining decode lever. Restarts the stable model.
- **Agent-loop, further**: fix `5c6d7e8f` (rose to 29 turns under windowing —
  reads 18 windows); investigate its grounding. Tune window 14k→16-18k (watch
  overflow). These are engine/MCP-side, no model restart.
- Reduce the ~48K base context (system prompt + spec grounding) — would directly
  raise the ceiling for everything and is the deepest lever; quality-sensitive,
  best done with you.

---

## MORNING SUMMARY (2026-06-06)
**Two validated wins shipped, both quality-gated; one negative result; system left
clean and stable.**

| Lever | Result | Status |
|---|---|---|
| AITER decode backend | **1.93× tok/s** (13.3→25.7), quality ≈ ROCM_ATTN | **shipped** (llm-serving, pushed gitlab) |
| Doc windowing (MCP) | **-36% turns, -24% wall**; 15/15, gaps in-band | **shipped** (dav, pushed github) |
| TunableOp | +1% (noise); decode is bandwidth-bound | reverted |
| MTP | not viable — Qwen3-32B has no draft heads | n/a |

**Combined:** a 15-UC verification run went from repeatedly timing out → **1h24m,
15/15**. Decode ~2× faster; agent loop ~36% shorter.

**Also fixed/shipped tonight:** engine→service-token runs no longer orphan (API
guard, deployed+verified); 3 orphaned runs backfilled; UI run-name width + turn
grouping + per-turn "UC N of M / iter N" (deployed). All committed & pushed
(dav `feat/dcm-uc-prioritization` 298ab8d; llm-serving gitlab).

**System state:** AITER on, MCP windowing (cap 14000, code default, no one-off env),
TunableOp off. Validated-good.

---

## Session 2 (2026-06-06 daytime) — turn optimization + frontier-model prep

### Exp 5 — retrieval memo (agent.py)  `752348`  [RUNNING]
- Change: pin an always-current "retrieval ledger" at the message TAIL each turn
  (gated `DAV_RETRIEVAL_MEMO`, default on) listing what's already been fetched, so
  the model stops re-requesting docs it already has. DAV never evicts tool results
  (confirmed: overflow handler only trims output tokens, never drops messages), so
  everything is still in context — the model just loses track. Prevention to
  complement the reactive cross-turn dedup.
- Baseline = Exp3 `723339` (memo off): mean 11.9 turns, 1h24m, 29 gaps.
- **RESULT: ✅ PASS — KEPT.** 15/15, 0 failed.
  - **Turns: 11.9 → 7.9 mean (−34%)**; worst UC 29 → 11 (the Exp3 cap-hitter
    `5c6d7e8f` dropped to 8). **Wall: 1h24m → 1h05m (−23%).**
  - **0 cross-turn dedup blocks across the whole run** (prevention worked —
    baseline runs needed multiple reactive blocks).
  - Quality: gaps **29 / 1.93 per UC — identical aggregate to baseline**; severity
    2 major / 22 moderate (envelope 2–8 major); per-UC swings within the
    established stochasticity (custom-service ranges 1–5 across all runs);
    semantic spot-check valid (DNS gaps consolidated into one cleaner statement).
- **Exp5b confirmation `756549`:** efficiency STABLE — 15/15, mean 8.0 turns
  (run 1: 7.9), 1h13m, 1 dup block (caught by the backstop, layered design
  working). Gaps **22 / 1.47 per UC** — below the historical band (24–29) but
  structurally sound: every UC ≥1 gap (8×1, 7×2), 4 majors, no misses.
- **Exp5c tie-break `761248`: ✅ 30 gaps / 2.00 per UC, 9 major — the highest of
  ANY run.** 15/15, 1h10m, 1 dup block. Decision rule (≥24) met decisively.
- **FINAL VERDICT: memo CONFIRMED KEEP.** Three-run memo dataset: gaps 29/22/30
  (mean 27, non-memo band 24–29 — run 2's 22 was tail variance as hypothesized);
  turns 7.9/8.0 mean; walls 1h05m/1h13m/1h10m (avg 1h09m vs 1h24m serial
  baseline). Quality envelope fully held across all three.
- Disable path remains `DAV_RETRIEVAL_MEMO=0` (env, no rebuild) — not needed.

### Cumulative (vs original ROCM_ATTN + 8k crawl)
AITER ~2× decode → windowing −36% turns → memo −34% more turns. 15-UC eval:
*timed out* → 1h50m → 1h24m → **1h05m**, quality held at every step.

### Adapter validated vs the Claude API reference (`62d747e`)
- Fixed: `temperature` 400s on Opus 4.7+/4.8 (now omitted there); Anthropic
  `usage.input_tokens` is the uncached remainder — prompt_tokens now sums
  input+cache_creation+cache_read so the agent's context budgeting stays right.
- Confirmed: headers (`anthropic-version: 2023-06-01`, caching GA = no beta),
  cache_control placement (system + tool_result; ≤4 breakpoints), tool format.
- Model IDs: `claude-opus-4-8`, `claude-sonnet-4-6`. **Pricing correction:**
  Opus 4.8 = $5/$25 per M (NOT $15/$75) → 15-UC ≈ $18 uncached / ~$7.5 cached —
  only ~1.6× Sonnet. Recommendation: A/B both, lean Opus for DCM/UDLM.
- Follow-ups: adaptive thinking needs thinking-block round-trip (v1 omits
  `thinking`); set `DAV_MODEL_CONTEXT_LIMIT` high for Claude runs (1M context).

### Frontier-model adapter — built, committed `b979113`, NEEDS live key
- `client.py`: native Anthropic Messages-API path with prompt caching
  (cache_control on the static system prefix + a rolling breakpoint on the latest
  tool_result → re-sent context bills ~0.1×). Format conversion unit-tested.
- `run_corpus.py`: api_key from `--inference-api-key-env` (e.g. CLAUDE_API_KEY) or
  `DAV_INFERENCE_API_KEY`. External OpenAI-compatible models work via the existing
  path + this key.
- **Remaining to run Sonnet/Opus (needs the key):** (1) the Secret + vars.local var
  (user doing); (2) wire the secret→env in the run-corpus task template + pass
  `--inference-api-key-env`; (3) a `model_config` row → `https://api.anthropic.com/v1`,
  model `claude-sonnet-4-x` / `claude-opus-4-x`; (4) validation run — confirm cache
  hits (usage.cache_read_input_tokens), final-JSON reliability (no guided_json on
  Anthropic), and gap quality vs Qwen3-32B on the 15-UC harness; A/B both Sonnet+Opus.

## Next candidates (if needed)
- Tune `DAV_MCP_MAX_DOC_CHARS` down (e.g. 50k) if context pressure hurts quality
  (still fixes cost-models; matrix would need a smarter escalation).
- Section-crawl escalation with a K-call threshold (only escalate after the agent
  proves it needs most of a doc) — more surgical than always-whole.
- TunableOp (`PYTORCH_TUNABLEOP_ENABLED=1`, persistent filename) — quality-neutral
  decode lever, needs a model restart.
- **n-gram / prompt-lookahead speculative decoding** — model-agnostic speculative
  (no draft heads), DAV output repeats spec/doc terms so lookahead may hit.
  vLLM serving-config change → model restart; sequence AFTER the doc-cap work.

## MTP — not viable on the current model (checked 2026-06-06)
`Qwen3-32B` FP8 is dense (`Qwen3ForCausalLM`, model_type `qwen3`):
`num_nextn_predict_layers: None`, no nextn/mtp keys, no MTP/draft weight shards.
MTP needs model-shipped draft layers (Qwen3.6-27B / DeepSeek-V3 style). The prior
MTP attempt was on Qwen3.6-27B → 0/15 (upstream vLLM bug #43713 + that model's own
quality issues). So MTP on the stable default is off the table without a model
swap; n-gram speculative (above) is the model-agnostic alternative.
