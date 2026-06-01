# Grounding nudge (#45b) — A/B result

**Date:** 2026-06-01
**Status:** inconclusive — capability shipped, kept OFF by default, not promoted.

## Change under test
`DAV_GROUNDING_NUDGE` (Tekton `grounding-nudge` param) — a terse, OFF-by-default
addendum to the stage-2 system prompt that pushes the analyst to:
cite ≥1 consulted `spec_ref` for every component / data entity / capability /
policy-mode claim; favor fewer spec-anchored findings over many generic ones;
and drop a claim to `confidence: low` rather than assert it unanchored.

Built per the **v1.9 guardrail**: terse, do not out-word the model. The v1.9
"stop fishing" lecture regressed quality by over-instructing — this nudge is
deliberately short, and the A/B is the check that it doesn't repeat that.

## Hypothesis
The nudge raises grounding density (↑ `mean_distinct_spec_refs` and/or
↓ `shallow_fraction` from the #45a detector) at equal-or-better `success_rate`,
with no new high-severity failure class.

## Method
- Eval set: OSAC (`set_id=5`), 15 UCs, verification mode, **`sample_count=1`**.
- Model: Qwen3-32B (stable default), two-pass stage-2, MCP grounding vs the DCM spec.
- Arms:
  - candidate (nudge **ON**): `dav-stage2-console-284978`, run `2026-06-01T03-36-56Z-2508edb` (Succeeded, 91 min)
  - baseline (nudge **OFF**): `dav-stage2-grndbase-285345`, run `2026-06-01T05-09-02Z-b929352` (Succeeded, ~110 min)
- Scored via the production path: `results.get_run_summary` / `get_failures` /
  `get_run_exploration` / `get_run_shallowness` → `experiment_eval.score_run` → `gate`.
- Arms run **serially** (see "Harness bug" below).

## Result

| metric | baseline (OFF) | candidate (ON) | Δ (cand − base) |
|---|---|---|---|
| success_rate | 100% (15/15) | 100% (15/15) | +0.00% |
| new high-sev failure class | — | — | none |
| mean_distinct_spec_refs | 5.33 | 5.0 | **−0.33** |
| shallow_fraction | 0.133 (2/15 thin) | 0.067 (1/15 thin) | **−0.066** |
| distinct_gaps (exploration) | 26 | 26 | 0 |

**Gate verdict: `inconclusive`** — "no meaningful change (success_rate 100% →
100%, Δ=+0.00%); promote only on a real improvement." Guardrail clean: no new
high-severity failure class.

## Reading
- **Safe, not harmful.** No success regression, no new failure class — the key
  v1.9-guardrail pass. A terse nudge behaves very differently from the v1.9
  over-instruction that made things worse.
- **No demonstrable lift.** The two grounding signals split and both are tiny:
  `shallow_fraction` improved by exactly one UC (2→1 thin) while
  `mean_distinct_spec_refs` dipped ~0.33 ref/UC (~5 refs across the whole
  corpus). At N=1 sample × 15 UCs these are within single-run noise — one UC
  flipping thin↔not-thin *is* the entire shallow delta. `consistency` is `null`
  (undefined at N=1), which is itself the tell: there is no within-arm variance
  estimate to separate signal from noise.
- **`mean_distinct_spec_refs` is an ambiguous target for this nudge** — it pushes
  refs both up ("anchor every claim") and down ("favor fewer findings").
  `shallow_fraction` is the cleaner signal and it moved the right way, but not
  past the noise floor.

## Decision
- **Keep the nudge OFF by default (its shipped state). Do not promote.** The
  capability is in place and per-run toggleable (`DAV_GROUNDING_NUDGE` env /
  `grounding-nudge` Tekton param) for future testing.
- A real verdict needs **multi-sample arms** — N≥3 ensemble per UC (verification
  mode), or several independent runs per arm — to clear the noise floor and
  produce a `consistency` estimate. Deferred as a deliberate ~90-min-per-run
  follow-up; not chased now while clearing the plate for DCM/UDLM.

## Harness bug found (logged as #52)
The A/B harness launches both arms ~1.3 s apart sharing the `dav-workspace` PVC;
their `cleanup-workspace` + `sync-corpus` tasks race on
`/workspace/source/corpus/...` → `FileExistsError` (experiment #9's first
baseline `284977` died this way). RWX PVC, so it's a concurrent-write race, not
a mount conflict. **Affects all experiments**, not just grounding_nudge. Worked
around here by running the arms serially. Proper fix: per-run source isolation
(per-PipelineRun source subdir) or serialized arms.
