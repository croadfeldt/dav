# DAV self-improvement: vision & design direction

**Status:** Vision / direction (not yet built). Authored 2026-05-30 after the
OSAC 0/15 → 15/15 stabilization, which was this loop run *manually*.
**Goal (operator's words):** *"the system to be able to do this work and to
design / modify the prompts in the system automatically in order to get better
results. Self-healing / self-improvement, with the ability to be guided and to
guide."*

---

## 1. The insight: the stabilization session was the loop, by hand

Over 2026-05-28..30 the OSAC failure chain (see
`experiments/2026-05-29-osac-stabilization-and-r9700-model-eval.md`) was fixed
by repeating one loop seven times:

```
run eval → read failure signature → root-cause → change a prompt/config/code knob
         → re-run → compare to baseline → keep if better, revert if worse
```

Every step has a machine-detectable analogue. The "self-improving system" is
the automation of *this* loop, with the human moving from doing-each-step to
setting-goals-and-approving-changes. Crucially, several of those seven fixes
were **not** prompt edits — they were parser/config/throughput problems — so
self-improvement is a search over *prompts + config + (proposed) code*, not
prompt-rewriting alone. The system must first **classify what kind of problem
it's looking at**.

## 2. Failure signatures are already structured and detectable

The loop is feasible because DAV failures announce themselves:

| Signature (in run log / summary) | Class | Auto-fixable lever |
|---|---|---|
| `Section '<X>' NOT FOUND` cascade | retrieval / prompt | prompt nudge, MCP resolver, new tool |
| `invalid severity label '<v>'` | schema vocab | alias map (config/code) |
| `504 Gateway Time-out` at high token count | throughput × timeout | `max-tokens` ↓ or route timeout ↑ |
| `forcing final emit` frequently | context ceiling | `max-model-len` ↑ or model swap |
| budget-hit / fishing (N misses/UC) | agent behavior | prompt, tool design |
| parse failure on final analysis | output format | prompt, `max-tokens`, guided schema |

DAV already records the inputs needed to learn from these: `run_sessions` +
`effective_sampling` (what config produced what), the analysis DB (structured
results), `STAGE2_PROMPT_VERSION` (prompt provenance), the per-(model,use)
profile system (a tunable config surface), and the OSAC/Barclays eval sets
(ground-truth-ish targets).

## 3. Loop architecture (phased)

**Phase 0 — Observability (mostly exists).** Persist every run's config +
failure-signature histogram. A run already emits enough; add a structured
"failure taxonomy" field to the summary so signatures are queryable, not
grep-derived.

**Phase 1 — Diagnose & propose (assisted).** An agent reads the failure
histogram for a run and proposes a *typed* change: `{kind: prompt|profile|
route|tool|code, target, diff, rationale, predicted_effect}`. It does NOT
apply — it files a proposal for review. This alone turns "Claude spent a day
root-causing" into "the system hands the operator a ranked list of candidate
fixes with evidence."

**Phase 2 — Candidate eval (auto, gated).** For prompt/profile changes (low
blast radius), spin a *candidate* config — new `STAGE2_PROMPT_VERSION` or a
shadow profile — run it against a held-out eval set, and compare to the current
baseline. Promote only on a real improvement with no regression; auto-revert
otherwise. Code changes stay human-gated (PRs), reusing the existing
enhancement → PR machinery.

**Phase 3 — Continual + guided.** Run the loop on a schedule and on every
corpus/spec change (the MCP-refresh cron is the hook). Surface a dashboard of
"what I tried, what moved the needle, what I'm uncertain about." The operator
steers: approve/reject, set objectives (quality > speed, or vice-versa), inject
domain knowledge, pin invariants.

### Implementation status

**Phase 0 + Phase 1 — SHIPPED 2026-05-30** (commit `7828547`):

- `review-console/api/app/failure_taxonomy.py` — classifies a run's
  `failures/*.error.txt` into typed signatures (`route_504`,
  `output_truncation`, `severity_reject`, `budget_exhausted`/fishing,
  `context_overflow`, `tool_parse_error`, …). The pattern table encodes the
  OSAC 2026-05-29/30 failure chain.
- `review-console/api/app/diagnose.py` — rules layer turns signatures into
  ranked typed proposals and **re-derives the exact fixes made by hand this
  session** (verified by `test_self_improvement.py`); optional LLM second
  opinion with the §5 guardrails baked into its system prompt. Proposals are
  filed, never applied.
- `migrate_015` — `run_diagnoses` (taxonomy snapshot, survives workspace
  cleanup) + `improvement_proposals` (the review queue).
- Endpoints: `POST/GET /api/diagnose/{run_id}`,
  `GET /api/improvement-proposals?status=&kind=&run_id=`,
  `POST /api/improvement-proposals/{id}/review` (accept/reject — review only).
- Observability: `diagnose_llm` logs on degrade; `llm_attempted` vs `used_llm`
  distinguishes "no model" from "no contribution" (this caught a real bug on
  first run — an empty `Bearer` header to the local vLLM).

**Review-queue UI — SHIPPED** (commit `f92c799`): a top-level **"Improve" tab**
(🩺) with a two-pane review queue — status-filtered proposal list + a
"diagnose a run" picker (LLM toggle) on the left; proposal detail
(kind/target/confidence/source, proposed change, rationale, predicted effect)
+ Accept/Reject (review-only, two-click) on the right. A run-drawer "🩺 Diagnose"
button runs the diagnoser in context. The `/api/diagnose/{id}` endpoint accepts
either a workspace run_id or a Tekton run name (resolves via timestamp
correlation), so both entry points work.

**Phase 2 — A/B candidate experiments — SHIPPED** (commit `c323ce0`): the
"always measure, never assume" guardrail as code. An experiment runs a baseline
+ candidate over the same eval set (candidate differs by one config delta —
today, `max_tokens` via a per-run PipelineRun param, so it's **fully isolated:
no profile or deploy-var mutation, production + spamllm untouched**), and
`experiment_eval.gate()` decides promote / revert / inconclusive. The gate
**refuses to promote a change that introduces a new high-severity failure
class** (the v1.9 lesson, enforced) and treats ties as inconclusive — validated
against this session's real runs (0/15→15/15 promotes; regressions and
new-failure-modes revert). Surfaced as a Proposals|Experiments toggle in the
Improve tab + a "Run A/B" launcher on max_tokens proposals + an
A/B-scorecard/verdict/Promote detail. `POST/GET /api/experiments`,
`POST /api/experiments/{id}/promote`. Promotion of a max_tokens change is
**human-gated** (its production home is the `dav_stage2_max_tokens` deploy var —
the Tekton task arg wins over any profile): the A/B PROOF is automated, the
apply is instructed. `migrate_016` adds the `experiments` table + a
`change_spec` column bridging Phase 1 proposals to applyable deltas.

**Not yet built:** sampling-param experiments (temp/top_k/… via a shadow
`model_use_profile` — the runtime-applyable, auto-promotable case, vs max_tokens
which is redeploy-gated); auto-promote-on-win policy (today promotion is always
operator-confirmed); Phase 3 scheduling/continual operation; richer evidence
display (per-signature exemplars) in the proposal detail.

## 4. Guide ⇄ be-guided (the duality the operator asked for)

- **Be guided:** human sets the objective function (e.g. "maximize gap-recall
  without raising hallucinated components"), approves promotions, marks some
  knobs off-limits, and can always override. The eval set encodes intent.
- **Guide:** the system explains *why* a change helped, ranks proposals by
  expected value + confidence, flags when a failure class needs a human
  (code/infra, not prompt), and reports honestly when it's stuck — rather than
  thrashing.

## 5. Guardrails — the hard lessons that must be built in

These come straight from this session and are non-negotiable for any auto-tuner:

1. **Always A/B against a held-out baseline; auto-revert regressions.** The
   v1.8 → v1.9 prompt "hardening" made fishing *worse on every model* and broke
   the baseline. A naive optimizer that only sees "I added a stop-fishing rule"
   would have shipped it. The loop must measure, not assume.
2. **Don't out-word agentic guardrails.** Terse beats a lecture; the model
   fixates on whatever the prompt emphasizes. An auto-tuner that keeps *adding*
   prompt text is moving the wrong direction — it should be able to *remove*.
3. **Diagnose the class before editing prompts.** Most of the seven fixes were
   NOT prompt problems. An auto-tuner that treats everything as a prompt edit
   will mangle the prompt while the real bug (a 30 s route timeout, a strict
   resolver, a vocab mismatch) persists.
4. **Guard against eval over-fit.** Use multiple diverse eval sets; a change
   that passes OSAC must not silently regress Barclays.
5. **Bounded autonomy.** Prompt/profile = auto-with-revert; code/infra =
   human-gated PR. Never let the loop touch consumer config or production
   defaults without approval; always end on a known-good baseline.

## 6. Why DAV is unusually well-positioned

DAV is a system whose *job* is structured analysis with machine-checkable
output (verdicts, gaps, spec_refs, severities) against a versioned corpus, run
through a reproducible Tekton pipeline with per-run config provenance. That is
close to the ideal substrate for a self-improvement loop: the objective is
measurable, the config surface is already parameterized, the history is
recorded, and the prompt is versioned. The missing pieces are the *diagnose →
propose → candidate-eval → promote/revert* glue and the operator-in-the-loop
control surface — not the foundations.

---

*Direction doc — refine as the loop is built. The stabilization experiment is
the worked example of one full turn of this loop.*
