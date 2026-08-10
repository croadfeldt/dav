# persona-focus — what to review

**Contract:** this is the record of what I built, deployed, and verified overnight on
`feat/persona-focus` while you were asleep, and the three things I need a decision on.
Nothing is merged to `main`. The console at https://10.0.90.22:8843 **is running this
branch right now** — that was the point, so you can look at it rather than read about it.

Rollback, if you want the old console back before reading any further:

```
git checkout function-focus
# restage /tmp/dav-review-build from review-console/, then rebuild both images
oc start-build dav-review-api --from-dir=/tmp/dav-review-build/api -n dav --wait
oc start-build dav-review-ui  --from-dir=/tmp/dav-review-build/ui  -n dav --wait
```

Diff: `git diff function-focus..feat/persona-focus`

---

## 1. Enablement — the affirmative half of the analysis

**What was wrong.** The console reads as a defect tracker. `uc_gaps` has 1,678 rows and
four modules project it. `uc_capabilities` has **3,209 rows** — more affirmative data than
gap data — and exactly one module touched it. So the two personas who come to learn what
the platform *does*, Customer and Stakeholder, landed on screens that only listed what it
does not. The persona code admitted it: `// + Outcomes when built`.

**What I built.** `GET /api/analysis/enablement` — the same project-scoped, auth-guarded,
latest-analysis-per-UC projection the gaps endpoint uses, read from the other side. Plus a
view under **Roadmaps** with two modes: by use case (what is enabled, and what carries it)
and by capability (what each capability holds up).

Live numbers, DCM project, right now:

| | use cases | capabilities | supported | partial | claims |
|---|---|---|---|---|---|
| DCM (20) | 26 | 124 | 11 | 11 | 186 |
| Fixtures (760) | 12 | 48 | 3 | 8 | 51 |

Load-bearing capabilities sort first, because *"`udlm/FIX-DEPS-001` carries 6 use cases"*
is the sentence a stakeholder needs and no existing view says it. The tail is a long list
of capabilities carrying exactly one.

**One thing that fell out of it, worth your eye:** `udlm/FIX-NONLEAK-001` is claimed by 4
use cases and fully supports **zero** of them. That reads to me like a capability doing
less than the corpus assumes, but you know the corpus.

**Placement decision I made:** it lands under Roadmaps rather than as a new domain. That is
the one domain both Customer and Stakeholder already reach, so the view is visible to the
personas it was written for without widening anyone's lens. Easy to move if you disagree.

## 2. Build provenance — every console image has been lying

Found while verifying my own deploy, which is the only reason it surfaced.

The console images carry `org.opencontainers.image.revision=unknown`, and the in-app build
stamp renders the literal string `unknown` — defeating the exact purpose its own comment
claims ("makes a stale browser cache obvious at a glance"). This has been true for **every
console build**, not just mine.

**Cause:** `oc start-build --build-arg` is accepted without error and **silently dropped**
for Binary builds. The flag parses, the build starts, the resulting Build carries no
buildArgs at all. Verified on builds 418/419/420 (all ansible-driven, all empty) against
421 with the args set on the BuildConfig instead — real sha in both the image label and the
served page.

`engine.yaml` already dodges this a different way (it bakes a `BUILD_COMMIT` file into the
context), which is why engine provenance works and the console's never did.

**Fixed at the source**, per your standing rule: the ansible role now patches `buildArgs`
onto the BuildConfig immediately before the build, and the two Containerfile comments that
asserted the `--build-arg` path was live are corrected. The deployed images now carry
`142f78a` in the label *and* the served page.

This is the same family as the rest of this week — a signal that agreed with itself instead
of with reality.

## 3. The engine test I owed you

`b03e355` (the tool-call argument repair that ended the ten-night nightly-battery loss) went
out without a test. `engine/src/dav/tests/test_tool_arg_repair.py` now covers it, built
around the exact malformed string from the 08-08 run logs. 10 cases, all passing. The
invariant it pins is the one the incident actually turned on: whatever goes back on the wire
must survive the server re-parsing it.

---

## Verification

| check | result |
|---|---|
| `node build.mjs --check` | OK — `index.html` matches `src/` |
| `./lint.sh` | PASS |
| `node e2e.mjs` | **127 PASS / 0 FAIL** (124 before; +3 for enablement) |
| `check_routes.py` | OK — 296 routes, no shadowing |
| `check_migrations.py` | OK |
| `pytest test_tool_arg_repair.py` | 10 passed |
| deployed digests vs ImageStream `latest` | MATCH, both images |
| served page stamp | `142f78a…` = branch tip |
| `/api/analysis/enablement` live | 200, real data |
| regression: `/api/analysis/gaps`, `/api/projects`, `/api/runs` | 200 |

Verified after the rollout completed, not at t+0.

## Where I was wrong, and what it cost

Three corrections are already folded into `docs/persona-focus-plan.md`, but they belong here
too, because the pattern matters more than the items:

1. I planned to "split the 18,042-line `index.html`". It is a **generated artifact** —
   `src/` is the real source and `build.mjs --check` already guards drift. I was measuring
   the output.
2. I planned to "retire the run selector". Already retired; scope is already UC/Set. What I
   took for a selector is a read-only status chip.
3. I claimed there was no engine test suite. There is, at `engine/src/dav/tests/`.

All three came from reading greps instead of the source. The cost was only planning time —
but had I acted on (1) I would have hand-edited a generated file.

## Needs your ruling

1. **Outcome/Initiative object** — the consumer lenses stay thin without it, and it is a
   data-model change. I did not touch it.
2. **Stakeholder value projection** — depends on (1).
3. **Enablement placement** — under Roadmaps, or its own domain?

## Loose end I did not touch

`review-console/ui/node_modules` is a **tracked symlink** pointing at
`/tmp/dav-metrics/review-console/ui/node_modules`. Running the UI test harness replaces it
with a real directory, so `git status` shows it deleted. I kept it out of every commit. A
tracked symlink into `/tmp` is going to bite someone; not mine to change tonight.
