# DAV ADR-007: Structured output is enforced at the wire, and wire shapes are verified

**Status:** Accepted (shipped dav#74; guard tests merged)
**Date:** 2026-07-27
**Related:** ADR-003 (the must-reject family whose verdicts this made trustworthy);
`docs/validation-fixture-suite-design.md` (the instrument that would have caught this in hours
instead of months).

## Context

Every schema-constrained request the engine ever sent carried its JSON schema in
`extra_body.guided_json`. `extra_body` is an OpenAI *client-library* concept — the SDK lifts its
contents to the top level; a hand-built request body sends it as an unknown key that every
server silently drops. Structured output was therefore inert on every backend for the life of
the code, including the parse-failure recovery path, which "re-emitted once with guided schema"
— unconstrained. The requests succeeded, the responses were well-formed; only the constraint was
missing, so nothing ever errored. Every model comparison taken before the fix was measured
through a harness enforcing nothing.

## Decision

1. Schemas travel as **`response_format`/`json_schema`** — the OpenAI-standard form, verified
   live against both served backends (vLLM and llama.cpp) before adoption.
2. **Wire-shape verification is the norm for any "we set X on the backend" claim**: assert what
   went on the wire (or what came back), never the client-side intent. The guard tests assert
   the request body, and were mutation-verified — restoring `extra_body` fails them.

## Consequences

- The 235B's 51-minute schema-invalid run, the "no '{' found" failures, and the unrecoverable
  retry loop are all explained and gone; completion went 1/6 → 6/6 on the same model same day.
- Every earlier model baseline was voided and had to be re-taken — the cost of a claim that was
  never checked at the artifact. That cost is the argument for rule 2.

## Alternatives considered

Top-level `guided_json` (vLLM-only — re-creates a backend fork); per-backend adapters (more
surface, no benefit while `response_format` is honored by both).
