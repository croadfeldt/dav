# Validation fixtures

A frozen synthetic corpus with **ground truth**, so DAV can be measured for
accuracy — precision and recall against deliberately seeded holes — not only for
self-consistency. Design: `../docs/validation-fixture-suite-design.md` (dav#82).

**Start with `MANIFEST.md`.** It carries the seeded claims and the reasoning behind
each. Everything else here is mechanical; the manifest is the judgement.

```
spec/      synthetic platform ("Halyard"), with holes we put there on purpose
corpus/    8 use cases — 5 must-reject, 3 must-realize
expected/  ground truth per UC: expected_gaps (recall) + must_not_report (precision)
score.py   scores a run; --gate exits non-zero unless perfect
```

Run it by pointing a DAV run at this directory as both corpus and spec source,
**pinned by tag or SHA — never a branch.** A fixture that drifts silently stops
being ground truth, which is the failure it exists to end.

    score.py --run-id <RID> [--gate]

This validates the *instrument*. It says nothing about whether UDLM or DCM are
good specs, and a fixture pass must never be read that way.
