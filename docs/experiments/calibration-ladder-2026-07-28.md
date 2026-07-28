# The calibration ladder — measured experiment record, 2026-07-28

_The gap-closing experiment programme (task #29): every quality intervention runs the
ground-truth fixture battery (12 UCs, 9 seeded gaps, 5 controls) and lands one row in the
Calibration tab before any default changes. This file is the durable record of what was
tried, what each row showed, and what was decided. Reference ceiling (F1, frontier blind
pack): **P 1.000 · R 0.900 · V 0.917**. Baseline (32B, n=3): **P 0.154 · R 0.600 · V 0.667**.
Battery noise floor: ±2 verdicts between identical-config runs at n=1 battery._

## Rows and verdicts

| rung | config | P | R | V | verdict |
|---|---|---|---|---|---|
| E1 | tagging post-pass | 0.161 | 0.500 | 0.583 | mechanism works (untagged 30→15), scores flat — placement binds, not tagging. Merged #105, default off |
| E2 | broken-chain prompt anchor | — | — | — | **falsified twice**: first undelivered (pipeline dropped `stage2-context` → #108 + param contract test), then delivered (536ch logged) with 0/6 chain samples emitting `not_supported` and hedging worsened. Reverted |
| E6 | derived verdicts (criteria) | — | — | — | mechanism proven: chain UC `not_supported` at both n, unknown-rate 7% (kill-bar was 40%). Merged #109 flag-off — **blocked on the ADR-003 strict-vs-graduated adjudication** (entry #15); the derivation is stricter than the shared verdict vocabulary |
| E3 | advisory split (sub-quorum → advisory) | 1.000 | 0.300 | 0.583 | **precision axis closed** — primary pool is all true positives; every sub-quorum finding kept, labeled, visible. Merged #112, default on |
| E1×E3 | tagger feeds quorum | **1.000** | **0.400** | **0.667** | **current operating point** — composition works: consistent tags merge same-UC fragments to quorum |
| E1×E3 n=5 | more draws | 0.600 | 0.300 | 0.750 | **rejected**: consistent noise reaches 3-of-5 quorum; recall doesn't recover; +67% wall-clock |
| E4 | criterion anchor (#113, open) | pending | | | prediction: R → ~0.6 with P holding — scattered finds converge on the UC whose criteria they block |

## The two structural findings behind the rows

**Placement scatter is the recall frontier.** One seeded hole sat at 1/3 consensus on four
DIFFERENT UCs — the same real concern, salient from many UCs, reported stochastically at
each. No identity mechanism (tags), no sampling mechanism (n=5) can quorum a finding that
changes address every draw. E4 anchors reporting to each UC's `success_criteria` — the one
deterministic, author-declared scope every UC carries.

**Sub-quorum retrieval hedges dominated real-corpus noise.** The F2 frontier adjudication
(30 sampled real-corpus findings, full blind spec pack) rejected **30/30**; 24 were
1/3-consensus "not in retrieved sections" hedges the spec settles directly
(`~/dav-f2-adjudication-2026-07-28.md`). E3 is the reporting-layer completion of #80's
quorum design: those hedges are advisory, not primary.

## Standing decisions this record encodes

- A change is an improvement only if its row moves toward the reference without giving
  back precision; one variable per battery run; identical-config deltas ≤ the noise floor
  are not signal.
- Failed runs never score (compute 400s on 0-successful); partial ingests never score
  (409 until converged); every experimental row is labeled, invalid rows marked
  `source='invalid'`.
- `FIX-WHOLE-001` retired from the offered catalog (ruled-not-required; its presence
  manufactured the exact fp the model session pre-registered as a regression signal).
