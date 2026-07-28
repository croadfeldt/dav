## 🔬 PROPOSED 2026-07-27 — validation fixture suite (ground truth at last)
**Why:** DAV is validated against the live UDLM/DCM specs, so every measurement is a
moving target with no correct answer to check against — the only questions we can ask
are self-referential ("does run A match run B?"), which a consistently-wrong system
passes perfectly. One night's work produced four separate invalidations: schema
enforcement inert (#74), n=1 verdicts disagreeing 4-of-6, n=3 collapsing to
all-`partially_supported` via union bias (#80), and the spec moving mid-session.
- Proposal: a small **frozen, tag-pinned** repo — synthetic spec + ~12 UCs + `expected/`
  ground truth per UC, including **`must_not_report`** (the precision half; without it,
  the ensemble's gap inflation reads as thoroughness).
- Measures what we cannot today: **precision · recall · verdict accuracy · invariance
  under N · determinism** — all against a known answer rather than another suspect run.
- **No engine change**: runs already accept corpus/spec repo URL + branch + commit_sha.
- The fixture must itself be validated: reintroduce each of the four bugs and confirm
  it FAILS. One that passes under all four supplies false assurance.
- Design: **`docs/validation-fixture-suite-design.md`**. Not built. Chris's call, 07-27.


