# DAV ADR-003: The must-reject family — the corpus asserts refusal, not just expression

**Status:** Proposed
**Date:** 2026-07-25
**Related — the complete picture, each cited once.** The failure space it completes (ADR-001 —
what the model wrongly accepts, extended from schemas to governance), the policy architecture
it validates (udlm ADR-041 — policy is an information firewall over classified reference
edges), and the corpus convention it inverts (the six positive families, dual-homed in udlm
`use-cases/` and the analysis hammer sets).

## Context

The entire use-case corpus is positive: every scenario succeeds when the intent is expressed
and realized. But the model's most differentiating claims are about what must *not* happen — a
cross-tenant reference without authorization, an export across a sovereignty boundary, a secret
literal where a credential reference belongs, a projection that leaks a policy-masked field.
Nothing in the corpus asserts any of that. The sovereignty and policy spine — the novel part —
has no validation at all: a system that silently accepted every one of those intents would pass
the whole existing corpus.

## Decision

A seventh use-case family, `must-reject`, with inverted success semantics: the scenario
succeeds if and only if the system **refuses** the intent, and the refusal contract holds —
typed (a named policy/validation error, not a generic failure), actionable (names what to
change), non-leaking (the refusal reveals nothing the requester's scope excludes), and
auditable (the refusal itself produces an audit record). Six seed cases cover the known
rejection surfaces: cross-tenant reference, sovereignty-boundary export, inline credential
value, undeclared-output binding at request time, unauthorized provider realization, and
policy-scoped projection leak.

The family uses the existing use-case schema unchanged — inversion is expressed in the success
criteria and tags, not new schema surface — and is dual-homed like every family. Analysis
stages treat a must-reject case as failed when the intent would be accepted or when the refusal
contract is violated, including the quiet failure mode: acceptance with the offending part
silently dropped.

## Consequences

- The policy firewall, tenancy boundaries, and credential discipline get regression coverage
  for the first time; a change that relaxes them now fails a corpus run instead of shipping.
- The refusal contract becomes testable surface: refusals that are untyped, leaky, or
  unaudited are findings even when the rejection itself was correct.
- Growth rule: every new policy or boundary mechanism lands with at least one must-reject case,
  the same way every new type lands with corpus coverage (rule 36k extended to the negative
  space).
- The family will surface unimplemented enforcement (cases the current stack cannot yet
  refuse); those findings are the enforcement roadmap, ranked by the same gap analysis as
  everything else.
