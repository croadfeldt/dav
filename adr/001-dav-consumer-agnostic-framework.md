# ADR-001 — DAV is a Consumer-Agnostic Framework

**Status:** Accepted
**Date:** 2026-04-25
**Author:** Chris Roadfeldt

---

## 1. Context

DAV (DCM Architecture Validation) validates whether an architectural specification supports a defined set of use cases. The framework runs an LLM agent over a consumer's spec content (architecture docs, capability inventories) and produces a structured `Analysis` for each use case: which components are required, which capabilities are invoked, what gaps the spec has, and a verdict.

The framework's first consumer is DCM (Red Hat FlightPath sovereign-cloud framework). The framework was designed to be useful beyond DCM — any project with text-based architectural specs and a use case corpus is a candidate consumer.

The question this ADR settles: **does DAV know about its consumers, or does it operate on consumer-supplied data without consumer-specific knowledge?**

The decision is non-trivial because DCM-specific assumptions are tempting:

- DCM has a vocabulary (`provider_types`, `policy_modes`, `governance_contexts`, etc.). Hardcoding these as enums in the schema would simplify the prompt and the validators.
- DCM's spec docs have known section headings. Tools that index those headings could be specialized to DCM's structure.
- DCM has a known set of personas (`developer`, `operator`, `architect`, ...). Hardcoding the persona enum simplifies actor validation.

Each of these is a small productivity gain in the short term that becomes a fork point as soon as a second consumer arrives. The decision below picks the long-term path.

## 2. Decision

**DAV operates on consumer-supplied data and does not encode consumer-specific knowledge in framework code.**

Concretely:

1. **Consumer Profile externalization.** A consumer ships a `consumer-profile.yaml` declaring its controlled vocabularies (allowed values for each schema dimension), provider types, policy modes, framework name, and other consumer-specific values. The framework validates use cases and analyses against this profile rather than hardcoded enums.

2. **Consumer content is fetched, not bundled.** The framework's MCP server clones the consumer's spec repo and serves doc tools to the agent. The framework's pipeline clones the consumer's corpus repo (use cases + version manifest). No consumer content lives in the framework repository.

3. **Prompts are templated.** The stage 2 system prompt is built at runtime from the active consumer profile so framework name, vocabulary, and provider categories come from the consumer, not from prompt strings. The prompt template itself is framework-owned and version-controlled.

4. **Schema validation is profile-bound.** `UseCase.validate(profile)` and the dynamically-built `ANALYSIS_JSON_SCHEMA` enforce profile-declared values, not framework-hardcoded ones. Adding a new vocabulary value is a profile change in the consumer's repo, not a framework code change.

5. **The framework ships a built-in DCM reference profile** as the default fallback. This keeps the framework usable out of the box without external configuration files. Other consumers ship their own profiles; the DCM profile is the example, not the contract.

## 3. Consumer Contract

A DAV consumer provides:

| Artifact | Where | Required |
|----------|-------|----------|
| `consumer-profile.yaml` | corpus repo or served via MCP | yes |
| Spec content (Markdown docs) | spec repo, served via MCP | yes |
| `use-cases/*.yaml` | corpus repo | yes |
| `dav-version.yaml` | corpus repo | yes (provenance) |

Consumers are otherwise free to organize their repos as they see fit. The framework's only assumption is that these artifacts exist and conform to the published schemas.

## 4. Consequences

**Positive:**

- DAV remains usable for any architectural spec project without forking the framework.
- The DCM/DAV boundary is explicit: DCM ships profile + content; DAV ships engine + pipeline + prompt template.
- Vocabulary changes ship with the consumer's release cadence, not the framework's.
- A second consumer plugs in by writing a profile and pointing the pipeline at their repos.

**Negative:**

- Two repos are required for a working DAV deployment (DAV + at least one consumer). This is more setup than a self-contained tool would need.
- Profile validation must run before the prompt is built, adding a small cold-start cost.
- The framework cannot make domain-specific reasoning improvements that would require consumer knowledge (e.g. "this UC's `lifecycle_phase` value typically implies these capabilities"). Such improvements would need to live in the consumer's profile or be deferred to per-consumer extensions.

**Neutral:**

- The built-in DCM reference profile is part of the framework codebase. This is a deliberate exception to "no consumer-specific knowledge" — DCM's profile is the worked example, kept current alongside the framework, and allows the framework to function with no external configuration. Other consumers' profiles live in their own repos.

## 5. Alternatives Considered

**A. Multi-consumer code in DAV.** Branch on `consumer_id` throughout the engine. Rejected: every new consumer is a code change to DAV, and the branches accumulate.

**B. DCM-only DAV.** Hardcode DCM's vocabulary; defer multi-consumer support until needed. Rejected: rebuilding the framework to be consumer-agnostic later would be more expensive than designing for it now.

**C. DAV as a library inside DCM.** Skip the standalone framework framing entirely; DAV becomes a DCM module. Rejected: scopes DAV's value to DCM users only, and the architectural validation work is genuinely separable from DCM's domain. (See ADR-002 for a future direction where DAV gains an additional integration mode as a DCM-managed capability without becoming a DCM-internal module.)

## 6. Implementation Notes

- The `ConsumerProfile` dataclass (`engine/src/dav/core/consumer_profile.py`) is the runtime representation. Loaders accept a file path, an MCP URL, or fall back to the built-in DCM reference profile.
- `set_default_profile()` / `get_default_profile()` provide the module-level singleton used by code paths that don't accept a profile parameter explicitly. CLI entry points set the default early in startup.
- The engine's CLI accepts `--consumer-profile <path>` to override the default. The Tekton pipeline plumbs this through as a pipeline parameter.
- The MCP server is generic over consumer content. The same MCP image serves DCM's spec or any other consumer's spec depending on which repo it's pointed at.
