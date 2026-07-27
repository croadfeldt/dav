# A fixture corpus with ground truth — measuring DAV, not the spec

_Proposal, 2026-07-27. DAV is validated today against the live UDLM/DCM specs. That
makes every measurement a moving target with no correct answer to check against, so
the only questions we can ask are self-referential ones ("does run A match run B?").
This proposes a small frozen corpus with **deliberately seeded gaps**, so DAV can be
measured for accuracy — precision and recall against known-planted holes — not only
for consistency. Needs no engine change: runs already accept `corpus_repo_url` /
`spec_repo_url` + branch + `commit_sha`, so a pinned fixture repo drops straight in.
Prompted by Chris, 2026-07-27, after a night in which every measurement was
invalidated at least once. Not built._

## Why — what it cost us not to have this

One night's work, and the measurements failed in four distinct ways. None of them
were caught by the thing being measured; all were caught by accident.

| what happened | why the fixture fixes it |
|---|---|
| `extra_body.guided_json` never reached any server, so **every model comparison ever taken** was through a harness enforcing nothing (dav#74) | a fixture with known answers fails loudly the moment enforcement breaks — prose where JSON is required is an immediate red, not a silent pass |
| n=1 verdicts disagreed on **4 of 6 UCs** between identical-config runs | ground truth turns "they disagree" into "A is right, B is wrong" |
| n=3 verdicts collapsed to all-`partially_supported` on **both** models — union-merge bias (dav#80) | invariance under N is checkable against a fixed expected answer, not against another equally-suspect run |
| the spec moved mid-session, invalidating comparisons | a pinned SHA does not move |

The through-line: **without ground truth, every metric is self-referential.** "Run A
matches run B" is satisfied perfectly by a system that is consistently wrong — and
for several hours tonight, that is exactly what we had.

## What it is

A small repo, pinned by tag, containing a **synthetic** platform spec and a UC set.
Synthetic is the point: a real spec's holes are unknown, so nothing can be scored.
A synthetic one has the holes we put there.

```
fixtures/
  spec/                     # ~10-15 short docs, a miniature platform
    refusal-contract.md     #   deliberately COMPLETE for 2 of 5 elements
    tenancy.md              #   deliberately SILENT on cross-tenant audit
    ...
  corpus/
    must-reject/            # each UC ships its expected outcome
    must-realize/
  expected/
    <uc-handle>.yaml        # ground truth, see below
  MANIFEST.md               # what each seeded gap is, and why
```

Ground truth per UC:

```yaml
uc: must-reject/cross-tenant-reference-refused
expected_verdict: partially_supported
expected_gaps:                 # MUST be found — recall
  - capability_id: FIX-AUDIT-001
    why: "tenancy.md defines the boundary but never an audit record for refusals"
must_not_report:               # MUST NOT be found — precision
  - capability_id: FIX-TYPED-001
    why: "refusal-contract.md §2 defines the typed error completely; flagging it
           is a false positive, not conservatism"
```

`must_not_report` is the half that is easy to leave out and the half that matters
most right now. Tonight's ensemble bias produced *more* gaps as sample count rose;
with no measure of false positives that reads as thoroughness. It was noise.

## What it measures that we cannot measure today

**Recall** — of the gaps we planted, how many were found.
**Precision** — of the gaps reported, how many were planted. The direct check on
union-merge inflation and on `unmapped` free-text churn.
**Verdict accuracy** — against a known-correct verdict, not another run.
**Invariance** — verdict at n=1, n=3, n=5 must equal the expected verdict, which is
the acceptance test `derived-verdicts-design.md` names as decisive and which
currently has nothing to compare against.
**Determinism** — two `reproduce` passes must be identical *and* correct. Tonight's
run produced 12 gaps and 18 gaps with **zero** in common, with `temperature=0.0,
top_k=1, cache_prompt=False` confirmed applied. Under a fixture the same failure
also tells us which of the two passes was wrong.

## Scope — deliberately small

Ten to fifteen short spec docs and ~12 UCs. Not a model of UDLM; a test harness.

The temptation is to make it representative. It should instead be **fast and
diagnostic**: every UC targets one named behaviour, and a failure points at a
mechanism rather than requiring a bisect. A suite that takes an hour will not be
run before a change; one that takes five minutes will.

Seeded holes should cover the refusal contract element by element (ADR-003:
typed · actionable · non-leaking · auditable · whole), because that is the
decomposition `derived-verdicts-design.md` proposes to derive verdicts from, and it
needs a case per element to be testable at all.

## How DAV consumes it

No engine change. A run already takes `corpus_repo_url`, `spec_repo_url`, their
branches and `commit_sha`; point them at the fixture repo pinned to a tag. The
scoring step is new but small: compare ingested gaps against `expected/` by
`capability_id` and report precision/recall.

Pin by **tag, never branch**. A fixture that drifts is a fixture that silently stops
being ground truth — the same failure this proposal exists to end.

## What it does not do

**It does not validate UDLM or DCM.** Fixture results say nothing about whether the
real spec is good. Keeping that boundary explicit matters: the moment someone reads
a fixture pass as "the platform is fine", the suite has become misleading.

**It does not replace runs against the real corpus.** It tells you the instrument is
calibrated. You still have to point the instrument at the thing you care about.

**Seeded gaps embed our judgement.** If we plant a hole that is not actually a hole,
DAV is scored against our mistake. `MANIFEST.md` carries the reasoning for every
seeded gap so that judgement is reviewable rather than buried in a YAML field.

## Validation of the fixture itself

The suite is only trustworthy if a **known-broken** configuration fails it. Before
relying on it, confirm it catches each of tonight's four bugs when they are
reintroduced:

1. revert dav#74 (schema inert) → precision and verdict accuracy must collapse
2. revert dav#80 (union bias) → precision must fall as N rises
3. run at n=1 → invariance must flag the instability
4. `uc_concurrency: 2` in reproduce mode → determinism must fail

A fixture that passes under all four is not measuring anything, and building it
would have been worse than not building it — it would have supplied false assurance
in exactly the place we most needed real assurance.
