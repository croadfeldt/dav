# The readiness brief — supported? why not? what gotchas?

_Design note, 2026-07-28. Chris: "We need an automated way to validate the data model and
architecture to support the target use cases and if not, why not and what gotchas are we
likely to encounter if we try." And the objective refinement: best answer first, time
second, within reason. This maps that ask onto the machinery the calibration campaign
built, names what ships now, and what each open dependency adds._

## The three questions and their sources

| question | source channel | state |
|---|---|---|
| **Supported?** | the stabilized verdict (quorum ensemble + the default stack; criteria-derived for refuse UCs once adjudicated) | live |
| **Why not?** | quorum-backed gaps (catalog id, severity, consensus, rationale) + criteria answered `false` WITH the citation that should have covered them | live (gaps); criteria pending ADR-003 adjudication (entry #15) |
| **Gotchas if we try?** | assembled from what is true but not verdict-moving: sub-quorum (advisory) concerns, criteria answered `unknown` ("the spec does not say"), untagged findings (taxonomy-gap candidates) | live via `GET /api/runs/{run_id}/readiness` |

The design principle: **every answer is evidence, not a label.** A "why not" names the
catalog capability and cites; a gotcha carries its consensus so the reader knows it is a
one-sample concern, not a confirmed defect. This is the convergence property — a reviewer
argues with one cited item, not with an opinion.

## What ships in this increment

`GET /api/runs/{run_id}/readiness` — per-UC brief + run rollup, pure projection over
already-captured data (no new model output, no engine change). Consumers: the UI (a
Readiness panel is the natural next UI increment), the ops MCP (agents can ask "is UC X
buildable and what will bite me"), and the model session's handoffs.

## What each open thread adds when it lands

- **ADR-003 adjudication** → criteria vectors join "why not" (cited per-element false) and
  "gotchas" (unknowns) for refuse UCs; the strict-vs-graduated ruling decides the verdict
  mapping.
- **Per-lens union (task #25)** → gotchas gain the stakeholder dimension ("the auditor
  lens flags X") — a gotcha for the persona who will actually hit it.
- **Scheduled real-corpus runs** (the nightly-battery pattern pointed at udlm/dcm) →
  "automated" becomes continuous: the readiness rollup becomes a trend, and a model/spec
  change that flips a UC's readiness surfaces the next morning with its why.

## Non-goals here

No new judgment machinery — the brief only reorganizes measured channels. Anything that
would require the model to answer new questions goes through the ladder (battery-gated)
first.
