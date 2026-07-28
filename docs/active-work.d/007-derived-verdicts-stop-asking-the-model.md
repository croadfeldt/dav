## 🔬 PROPOSED 2026-07-27 — derived verdicts: stop asking the model to judge
**Why:** the verdict is the least reproducible thing stage 2 emits, and everything
downstream keys on it. Two gpt-oss runs with identical weights/sampler/prompts —
differing only in whether 3 expert layers ran on CPU or GPU — disagreed on **4 of 6
verdicts**. Meanwhile Qwen3-32B returns `partially_supported` for every must-reject UC
while producing *substantively correct, UC-specific gaps*: it retrieves and analyses
fine, it just won't commit to a judgment.
- Proposal: pass 2 emits **per-criterion evidence** (ADR-003's typed/actionable/
  non-leaking/auditable/whole), each with a **mandatory `spec_ref` for `satisfied: true`**;
  the engine **derives** the verdict via `derive_verdict`, and the ensemble votes
  per criterion rather than per verdict.
- Buys, in order: **consistency** (code-derived = deterministic given evidence),
  **a cheaper model becomes viable** (32B is 2.5× faster and already finds the evidence),
  **auditability** (a reviewer can disagree with one criterion, not the whole call).
- **Amended 2026-07-27 — the ensemble is the bigger problem.** gpt-oss at n=3 returned
  **6× `partially_supported`** (20 gaps, all 6 UCs) where n=1 gave 5 `supported` + 1
  partial (4 gaps, 2 UCs). `_consolidate_gaps` merges by **union**, `derive_verdict`
  is downgrade-only and consumes the **unfiltered** union, and `gap_consensus` is
  computed but neither used for filtering nor persisted. So **verdicts weaken
  monotonically as N grows** — a 1-of-3 gap counts as much as a 3-of-3 one. That is
  what collapsed the model distinction, not the models.
- Design therefore adds **quorum merging** (⌈N/2⌉) with sub-quorum findings kept as
  `candidate` (visible, non-verdict-affecting) and a column to persist consensus.
- Design: **`docs/derived-verdicts-design.md`**. Not built. Decisive acceptance test
  is **verdict invariance under sample count** (n=1 = n=3 = n=5) — exactly what
  today's design fails. Fail conditions explicit (`unknown` rate > 40% = the hedge
  relocated and the proposal fails on its own terms).


