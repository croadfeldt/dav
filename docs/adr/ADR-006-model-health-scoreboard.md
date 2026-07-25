# DAV ADR-006: Model-health scoreboard — validation is a trend, not an event

**Status:** Proposed
**Date:** 2026-07-25
**Related — the complete picture, each cited once.** The hammers it aggregates (ADR-001's six,
plus the probes and round-trips of ADRs 002–005), the projection it mirrors (the
architecture-side roadmap — gaps ranked and tracked; the model side had no equivalent), and the
generated-artifact pattern it copies (udlm's type catalog — regenerate, `--check`, fail if
stale).

## Context

Every hammer produces findings; none of them accumulate. A fuzz run, a corpus campaign, a sweep
report — each is an anecdote about one moment, and "is the model getting stronger?" has no
answer better than a feeling. The 1.0 milestone makes that unacceptable: readiness needs a
threshold, and a threshold needs a measurement that persists across refs.

## Decision

A generated scoreboard in the registry itself — `registry/MODEL-HEALTH.md` plus a JSON twin —
computed deterministically from the working tree and kept current by a `--check` CI gate, the
catalog pattern. Deterministic metrics compute on every commit: discrimination density
(mutations rejected / attempted, from the fuzz harness), strictness coverage, output adequacy
(zero- and one-output types), context coverage, relationships coverage, use-case-family
coverage per type, consumer coverage (from the udlm ADR-044 manifests). Campaign-owned metrics
— expressibility coverage, interpretability success/divergence, round-trip fidelity, external-
corpus coverage, portability surface — hold named null slots from day one and fill as their
campaigns run, so the scoreboard's shape is complete before its data is.

The scoreboard is an instrument, not a gate: no metric fails CI by itself (staleness of the
generated file does). Thresholds live where decisions live — the 1.0 scope lock cites the
scoreboard; the scoreboard does not enforce the lock.

The scoreboard is also attestation evidence. After the full gate suite passes, CI emits the
scoreboard plus the registry ref as an uncommitted, signable artifact (`model_health.py
--attest`, uploaded per run) — evidence by construction, because the emission step is
unreachable on a failed suite. The attestation pipeline consumes it as the model-validation
subject's input; signing and anchoring are that pipeline's acts, not the scoreboard's.

## Consequences

- "Is the model getting stronger?" becomes a diff between two refs of one file, readable by an
  engineer in a minute.
- Regressions surface as trend breaks even when every hard gate stays green — the class of
  decay (thinning outputs, stagnating coverage) that gates cannot express.
- 1.0 readiness becomes a stated threshold over named metrics, decided once, checked
  mechanically thereafter.
- Discipline cost: every new hammer must report into the scoreboard or its findings evaporate
  again; that reporting hook is part of each hammer's definition of done.
