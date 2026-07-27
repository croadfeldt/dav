# DAV ADR-012: Scope is resolved before launch — the corpus index

**Status:** Accepted (P1 shipped dav#89/t009; trigger resolution + preflight surface staged)
**Date:** 2026-07-27
**Related:** ADR-010 (index validates against the published vocabulary);
`docs/scope-first-class-plan.md` (all five decision points Chris-ruled);
`docs/run-source-resolution-design.md` (the sibling epic retiring the ConfigMap plane —
Chris-ruled: the DB is the source of truth for operational state, for the MCP as well).

## Context

DAV never knew what a run would analyze until the engine had started: scope was discovered at
stage 2, quarantine in a YAML on the results PVC, and UI denominators fell back to ingested
counts (a half-done run read "4/4", a finished 6-UC run read "3/3"). The sharpest incident:
"corpus: 8 files" followed by "running 1 UC(s)" — seven UCs silently gone, no file named, no
reason given.

## Decision

One row per UC per namespace in `corpus_index`, populated by the same sweep that maintains the
files cache, **dimension-validated at index time** against the published vocabulary,
**SHA-stamped**. Rulings baked in: sync-refresh with a visible staleness marker (never a
blocking re-index at trigger); predicted quarantine warns and only 0-of-N blocks; scope is
snapshotted at trigger and reconciled at sync (drift between them is provenance signal);
`valid=NULL` means unvalidated, never passing.

## Consequences

- Quarantine became a *prediction* shown before spend, not a post-mortem: on its first day the
  index caught a UC using a retired vocabulary value before any run hit it, and enumerated the
  intent-fulfillment family for that family's first baseline trigger.
- Honest-accounting rules are load-bearing and tested: non-UC files counted rather than
  dropped; absent expectations scored as missed — both are the anti-vacuous-pass discipline.

## Alternatives considered

Re-clone-and-scan at trigger (adds clone latency to every launch; ruled against);
engine-reported scope only (keeps discovery post-launch, which is the problem).
