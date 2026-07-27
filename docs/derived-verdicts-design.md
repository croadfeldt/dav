# Derived verdicts — stop asking the model to judge

_Proposal, 2026-07-27. Stage 2 asks the model for a verdict. This proposes it emit
per-criterion evidence instead, and the engine derive the verdict from that evidence
in code. Settles two things at once: why verdicts are not reproducible run-to-run,
and whether a cheaper model can serve stage 2. Builds on ADR-003 (must-reject family —
a refusal is only correct if it is typed, actionable, non-leaking, auditable and whole),
`derive_verdict` (downgrade-only rules, already shipped — and, as of the ensemble
finding below, the place the bias lives), the catalog-anchored
`capability_id` on gaps (Wave 1 — stable cross-run gap identity), and dav#63
(success semantics: a must-reject UC succeeds only when the system refuses).
Not built. Written to be tested against the six must-reject UCs._

## The evidence this rests on

Measured 2026-07-27, all post-dav#74 (before that, `extra_body.guided_json` never
reached any server, so every earlier result was taken through a harness that
enforced nothing — see below).

At `sample_count: 1` — see the next section for why that qualifier matters:

| model | completes | verdicts (n=1) | wall, 6 UCs |
|---|---|---|---|
| Qwen3-32B FP8 | 6/6 | **6× `partially_supported`** — every UC, every time | 376 s |
| gpt-oss-120b | 6/6 | mixes `supported` / `partially_supported` | 947 s |
| Qwen3-235B | 4/6 | mixes | ~25–40 min *per UC* |

The 32B looks unusable from that table. Its gaps say otherwise:

| UC | gaps the 32B found |
|---|---|
| cross-tenant-reference | Cross-Tenant Authorization Policy Mechanism · Audit Record Structure |
| inline-credential-literal | Non-persistence mechanism · Resubmission validation |
| masked-projection-write | Enforcement of masked projection writes · Audit records for same |
| provider-capability-mismatch | Refusal Contract for Capability Mismatch · Audit Record |
| sovereignty-egress | Information-firewall enforcement on error path · Audit record structure |
| undeclared-output-binding | Output Binding Refusal Message · Output Binding Validation Mechanism |

These are specific to each use case and substantively right. **The 32B retrieves,
reads and analyses correctly. It just will not convert "I found two gaps" into
anything other than `partially_supported`.** The failure is isolated to the
judgment step.

Two more observations that shape the design:

**The 32B's own output decomposes along the refusal contract.** Every UC produced
one *mechanism* gap and one *audit* gap — unprompted. That is ADR-003's
`typed`/`whole` and `auditable` elements surfacing by themselves. The
decomposition this note proposes is already latent in what the model emits.

**Model-emitted verdicts are not reproducible.** Two gpt-oss runs, identical
weights, sampler and prompts — differing only in whether three expert layers ran
on CPU or GPU — disagreed on **4 of 6 verdicts**. Layer placement cannot change
model quality, so that is sampling variance at `sample_count: 1`, a
configuration that keeps `verification`'s temperature 0.2 while disabling the
ensemble that exists to absorb it. Verification's contract is N≥3 and I was not
honouring it. But the deeper point stands: **the verdict is the least stable
thing the model emits, and it is the thing we build on.**

## The ensemble makes verdicts monotonically worse (found 2026-07-27)

This was found while validating the above and it changes the proposal, so it goes
before it.

Running gpt-oss on the same six UCs at `sample_count: 3` instead of 1:

| run | verdicts | gaps | UCs with ≥1 gap |
|---|---|---|---|
| gpt-oss n=1 | 5 `supported` + 1 partial | 4 | 2 of 6 |
| **gpt-oss n=3** | **6 × `partially_supported`** | **20** | **6 of 6** |

That is the 32B's exact signature — the thing I had been calling "the 32B hedges,
gpt-oss discriminates". The mechanism is in `ensemble.py`:

- `_consolidate_gaps` merges gaps by **union** — a gap found by *any* one sample
  enters the merged set (line 493).
- `gap_consensus` is computed there and recorded on the merged Analysis (line 516).
- `derive_verdict(merged_verdict, gaps_merged)` then consumes the **unfiltered
  union** (line 525). A 1-of-3 gap weighs exactly as much as a 3-of-3 gap.
- `gap_consensus` is **not persisted** — `uc_analyses` carries only `sample_count`,
  so nothing downstream can tell the two apart either.

Because P(at least one sample finds a gap) rises with N, and the derivation is
downgrade-only, **the verdict weakens monotonically as sample count grows**. Push
N high enough and everything is `partially_supported` regardless of the spec or
the model. The ensemble exists to raise confidence; as built it accumulates
low-agreement noise as findings.

Two consequences:

**The n=1 model comparison was measuring the wrong thing.** Whether the models
are distinguishable *at all* once the ensemble is in play is a separate question
from which model is better, and the second cannot be answered before the first.

**Consensus filtering is load-bearing in this proposal, not a refinement.** If
per-criterion answers merge by union the way gaps do, derived verdicts inherit
the same decay and this design buys nothing. That is the amendment below.

## The proposal

Stage 2 pass 2 stops emitting `verdict`. It emits, per criterion:

```yaml
criteria:
  - id: typed            # ADR-003 refusal contract
    satisfied: true|false|unknown
    spec_ref: "udlm/docs/adr/ADR-041#policy-as-information-firewall"
    capability_id: CMP-014
    note: "one line, why this ref settles it"
```

Rules that make it work:

- **`satisfied: true` requires a `spec_ref`.** No citation, no yes. This is the
  main defence against relocating the hedge into the sub-answers.
- **`unknown` is a first-class answer**, distinct from `false`. "The spec does not
  say" and "the spec says something inadequate" are different findings and drive
  different roadmap actions.
- **The engine derives the verdict**, extending `derive_verdict` from a
  post-hoc downgrade pass into the primary mechanism. For a must-reject UC:
  all five `true` → `supported`; any `false` → `not_supported`; otherwise
  `partially_supported`.
- **The ensemble votes per criterion, not per verdict** — and votes *by
  quorum*, not by union. A criterion's merged answer is the majority across
  samples; a tie resolves to `unknown`, never to `false`.
- **Only quorum-backed evidence reaches the derivation.** A gap or a `false`
  criterion enters `derive_verdict` only when at least ⌈N/2⌉ samples agree on it.
  Sub-quorum findings are **kept and labelled `candidate`** — they stay visible
  for roadmap and catalog work, they just do not silently move a verdict. This is
  the direct fix for the monotonic decay above.
- **Persist the consensus.** `gap_consensus` is computed and thrown away today.
  It needs a column so a 1-of-3 finding is distinguishable from a 3-of-3 one in
  the UI, the roadmap, and any later comparison.

## What this buys, in the order it matters

**1. Consistency.** A verdict computed in code from structured evidence is
deterministic given the evidence. Today the least reproducible output is the one
everything downstream keys on. This is the same requirement as "same source and
use case should give the same gaps", addressed at the root rather than by
averaging more samples.

**2. A cheaper model becomes viable.** "Does the spec define a typed refusal for
this case — yes or no, cite it" is a far easier question than "is this spec
sufficient". The 32B already demonstrates it can find the underlying evidence.
At 376 s vs 947 s it is 2.5× faster, and it is the model we can run alongside
other work.

**3. Auditability.** Every verdict traces to five cited answers instead of a
model asserting a label. That is also what makes a verdict *arguable* by a
reviewer — they can disagree with one criterion rather than the whole call.

## How this could fail

**The hedge relocates.** If the 32B answers `unknown` to everything, nothing is
gained. Mitigation is the mandatory citation and the binary shape, but this is
the risk that decides the proposal, and it is why the validation below is
per-criterion rather than per-verdict.

**False confidence.** Deriving a crisp verdict from five soft answers looks
rigorous while resting on the same uncertainty. `unknown` existing as a distinct
value is the guard — a verdict derived from three `unknown`s must not present as
equivalent to one derived from five citations. The derived verdict should carry
the criterion vector, not replace it.

**Cost.** Five focused calls per UC instead of one broad judgment may erase the
32B's speed advantage. Unknown until measured; it is a reason to measure, not a
reason to assume.

**Coverage.** ADR-003's contract covers must-reject UCs. Must-realize UCs need an
analogous criterion set and this note does not propose one — doing so by analogy
without evidence is how the original holistic-verdict design went wrong.

## Validation

The six must-reject UCs, which now have trustworthy baselines. Success is not
"the 32B agrees with gpt-oss" — it is:

1. **Verdict invariance under sample count.** The derived verdict at n=1, n=3 and
   n=5 must agree for the same UC and spec. This is the decisive test — it is
   exactly what today's design fails, and no other result matters if this one
   does not hold.
2. **Per-criterion agreement** between the 32B and gpt-oss ≥ the current
   per-verdict agreement. If the models agree on evidence but not on holistic
   verdicts, the decomposition is doing its job.
3. **`reproduce` mode gives byte-identical criteria across two passes.** Greedy,
   seed from UC uuid, prompt cache off.
4. **`unknown` rate under 40%** for the 32B. Above that, the hedge relocated and
   this proposal fails on its own terms.
5. **Wall clock within 2× of the current 32B run** (376 s). Beyond that the cost
   argument collapses and gpt-oss single-model is the better answer.

Fail any of 1–4 and the honest conclusion is that gpt-oss-120b does the judging
and the 32B stays a pass-1 explorer — which is what dav#73 (per-stage routing)
already supports.

## Note on the baselines

Every model comparison taken before dav#74 is void: `extra_body` is an OpenAI
*client-library* concept, so a hand-built request body carried the JSON schema
somewhere no server reads it. Both vLLM and llama.cpp returned unconstrained
prose and the "re-emit once with guided schema" recovery path re-asked with no
constraint either. The numbers in this note are all post-fix, and the n=1
verdicts among them are directional only.
