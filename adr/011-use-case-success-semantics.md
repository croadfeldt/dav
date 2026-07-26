# ADR-011 — Use-Case Success Semantics (realize vs refuse)

**Status:** Accepted
**Date:** 2026-07-26
**Author:** Chris Roadfeldt + Claude

---

## 1. Context

Every stage-2 analysis answers one question, and until now the prompt asked it one
way: *"analyze whether DCM supports this use case."* That phrasing carries a hidden
assumption — that a use case succeeds when the system **carries the intent out**.

For a large and growing part of the corpus that assumption is exactly backwards.
Whole families succeed **only if the system refuses**:

- `must-reject/*` (6 UCs) — cross-tenant references, sovereignty egress, inline
  credential literals, undeclared output bindings, provider capability mismatch,
  masked-projection writes.
- `class-versioning/*-refused` (7 UCs) — underdeclared breaking bumps, intra-registry
  version pins, provenance mismatch, misplaced plane classification.
- Author-tagged cases in `change-control/` and `vocabulary-intake/` (8 UCs) — e.g.
  *stale-knowledge-fails-closed*, *near-match-never-silently-bound*.

**21 of 932 UCs**, and the count is growing as the hammer campaigns extend.

For these the scored surface is not "can it be done" but the **quality of the
refusal**. Their `success_criteria` already say so — a typical one requires the
refusal be *typed* (machine-matchable, distinct from not-found), *actionable* (names
the remediation path), *non-leaking* (discloses nothing about the protected resource
beyond existence-as-forbidden), *auditable* (a refusal record with both identities and
the deciding policy), and *whole* (the entire intent refused, never silently repaired
into a partial acceptance the consumer never asked for).

The engine modeled none of this. `UseCase` had no notion of it and the prompt never
mentioned it. The failure mode is not that the model errors — it is that the model is
asked the wrong question and answers it well: an architecture that **correctly
refuses** invites being reported as *missing a capability*, and the gap list fills
with false positives that are really the system working as designed. An engine that
scores "cannot realize" as a gap gets the whole family inverted.

This was raised as the one engine requirement not to skip in the UDLM session's
handoff (2026-07-25 §3), which reached the same conclusion from the other side after
hand-running the campaign.

## 2. Decision

**Success semantics are a first-class property of a use case**, not an assumption
baked into the prompt.

`UseCase.success_semantics` is `realize` (default) or `refuse`:

- **`realize`** — the system succeeds by carrying the intent out. Every UC before
  this ADR, and the default for every UC after it.
- **`refuse`** — the system succeeds *only* by refusing, and the refusal contract is
  the scored surface.

**Explicit wins; naming infers.** An author may set the field directly. When absent it
is inferred from corpus convention — the `must-reject/` handle prefix, the `-refused`
suffix, or the `must-reject` / `refusal-contract` tags. This is deliberately narrow:
unambiguous naming conventions, never heuristics over prose. It means today's corpus
is interpreted correctly with no upstream reauthor, while `success_semantics: refuse`
remains the declared, preferred form going forward.

For refusal cases the stage-2 prompt emits an inverted-contract block that states the
success condition, enumerates the contract properties, **redefines the verdict
vocabulary**, and forbids the inversion outright:

| verdict | meaning for a refusal case |
|---|---|
| `supported` | the architecture specifies a refusal meeting the success criteria |
| `partially_supported` | it refuses, but the contract is incomplete — untyped, unaudited, leaks detail, or permits partial acceptance |
| `not_supported` | the architecture would **allow** the intent, or is silent on refusing it |

and: *gaps must describe what is missing from the refusal contract; do not report the
system's inability to carry out the intent as a gap — that inability is the correct
behavior this use case is testing for.*

Ordinary UCs are untouched: no block is emitted and their prompts are byte-identical.

## 3. Alternatives considered

**Leave it to the model.** The success criteria do describe the refusal, so a strong
model can infer the intent. Rejected: it makes correctness depend on model strength
for a whole corpus family, and the closing question actively pulls the other way. The
observed A/B behavior — where free-text gap labels churned ±5 between identical runs —
is not a foundation to leave an inversion resting on.

**Require the field in the UC schema.** Cleanest in principle. Rejected as the
*first* step: it strands 21 existing UCs until an upstream reauthor lands, and corpus
schema is owned by another session. Inference-with-explicit-override gets correct
behavior immediately and converges on the declared form.

**A separate refusal analysis mode.** A distinct pipeline for refusal UCs. Rejected as
premature: the analysis is the same work with an inverted success condition, and a
second mode would fork prompts, verdict handling, and comparison for no gain. If
refusal-enforcement tracing becomes its own capability (the UDLM session's hand-run
enforcement reports suggest it might), it can be a mode then.

**Infer from `dimensions.failure_mode`.** These UCs carry `failure_mode:
policy_violation`. Rejected: `policy_violation` also appears on UCs that *should*
realize after a policy check, so it over-matches. Handle and tag conventions are
author-controlled and unambiguous.

## 4. Consequences

- The 21 refusal UCs are scored on the right axis. They had never been analyzed, so
  no existing result changes — the fix landed before the first run that would have
  been wrong.
- Verified end-to-end: `must-reject/cross-tenant-reference-refused` returned
  `supported` (confidence 85, 0 gaps) with reasoning about typed errors, non-leaking
  semantics, and auditability, after fetching the refusal-contract sections of ADR-003
  and DCM ADR-027.
- Detection is author-driven. Every one of the 21 is either named by convention or
  carries an author-set tag; no UC is inverted by accident. New refusal families
  inherit the behavior by following the naming, or by declaring the field.
- Roadmap-relevant: refusal contracts are a natural target for the deterministic layer
  — "is the error typed", "does an audit record exist" are structural checks that do
  not need an LLM once the model expresses them.
- Open: `partially_supported` for a refusal case is judged qualitatively today. When
  verdict derivation (ADR-010) grows rules, "refuses but the contract is incomplete"
  is a strong candidate for a deterministic one.
