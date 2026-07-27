# Reference baselines: frontier and human — validating the validator

_Design note, 2026-07-27, Chris: "we need to validate the output from all of this, compared to
a frontier / human — how does the analysis and output compare?" The fixture answers "how do the
local models score against seeded ground truth"; this adds the two references above it: the
frontier ceiling and the human gold standard. Costed and sequenced; two enabling asks at the
end are Chris's to grant._

## Why each tier exists

- **Fixture scorer** (built): objective, but only as good as the seeded claims — and the seeder
  was wrong once already (the wholeness ruling).
- **Frontier baseline**: separates *model capability* from *harness quality*. If frontier scores
  ~1.0 on the fixture where the 32B gets 0.5, the gap is the local model; if frontier ALSO
  misses `FIX-AUDIT-001`, the prompt or the seeding is the problem — the fixture is then
  measuring the wrong thing and we find out cheaply.
- **Human**: the only tier that can rule the ground truth itself. The evidence this matters is
  one day old: Chris overruled a seeded claim that BOTH the fixture author and the analyzer
  independently got wrong (transaction semantics). Two models sharing a bias is exactly the
  failure only a human catches.

## Tier F1 — frontier on the fixtures (cheap, clean, no engine change)

The Halyard spec is ~7 short docs: **the whole spec fits in one context**, so the engine's
unfinished Anthropic tool loop is irrelevant here. A small runner script: per UC, one API call
with the full spec + UC inline, response constrained to the analysis shape, written to
gaps-json, scored by the SAME `score.py`. Same three numbers as every other row in the table.

- Contamination rule: a **fresh API call** is clean; the interactive session that AUTHORED the
  ground truth is not a valid analyst for it, however convenient. (For future blind extensions,
  the model session can seed holes the fixture author never sees — the claim-test channel
  already works this way in spirit.)
- Cost: 10 UCs × (spec ~8k + output ~2k) tokens ≈ low single-digit dollars per pass at current
  frontier pricing. n=3 for the invariance row triples it. Rounding error next to GPU-hours.

## Tier F2 — frontier as judge on REAL corpus output (where no ground truth exists)

Fixtures have ground truth; UDLM/DCM runs do not — and "validate the output from all of this"
must cover the outputs that matter. Per sampled finding: one API call with the finding + the
spec sections it cites, returning agree / disagree / cannot-determine + a one-line reason.
Report agreement rate per model per family. Sampling, not exhaustive: ~30 findings per report
cycle keeps cost trivial and turnaround same-day. Disagreements feed Tier H.

## Tier H — human as adjudicator, not parallel analyst

A full parallel human review does not scale and is not the comparison that matters. The human
rules on exactly three things:

1. **Contested ground truth** — already happening (the manifest review); formalized as: any
   fixture claim a frontier pass disputes goes to Chris with both arguments.
2. **F2 disagreements** — where frontier says the analyzer is wrong about the real corpus.
3. **A calibration sample** — a handful of F2 *agreements* audited per cycle, so "frontier
   agrees" is itself validated rather than trusted (the judge needs judging once in a while).

Output: one table — local model vs frontier vs human agreement, per family — which is the
direct answer to "how does the analysis compare."

## Sequencing and asks

After the claim battery (the local-model numbers should exist before the ceiling is measured,
or there is nothing to compare). Then F1 → F2 on the next real report → H cadence with each
report cycle.

**Ask 1 (the standing blocker):** an Anthropic API key + a spend ceiling. The throughput
campaign's Claude A/B has been blocked on exactly this; F1/F2 reuse the same grant.
**Ask 2:** the human-time budget — the F2 audit sample size is the dial (recommend starting at
5 agreements + all disagreements per cycle; minutes, not hours).
