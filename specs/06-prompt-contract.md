# DAV Specification 06 — Prompt Contract

**Status:** Stub (not yet authored)
**Audience:** Consumer writing domain context for Stage 2 prompts
**Depends on:** `02-stage-model.md`, `05-use-case-schema.md`

## Purpose

DAV's Stage 2 uses a general-purpose prompt template that consumers fill with domain-specific context. This spec defines the template's slots, the content consumers must supply for each, and the rules governing prompt content.

Topics this spec will cover when authored:

- The Stage 2 prompt template structure: generic framing by DAV, consumer slots for domain context
- Required slots:
  - `consumer_name` — what to call the consumer architecture
  - `consumer_overview` — one-paragraph description
  - `domain_terminology` — key terms and their meanings
  - `doc_corpus_layout` — how spec documents are organized
- Optional slots:
  - `tenant_model` — if the architecture is multi-tenant, describe the model
  - `provider_types` — if the architecture has a provider model, describe types
  - `verdict_bucket_semantics` — consumer-specific interpretation of supported/partially_supported/not_supported
  - `out_of_scope` — topics DAV should decline to reason about
- Content consumers write: in `consumer-repo/dav/prompts/` as markdown files, one per slot
- Forbidden content:
  - Prescribing specific verdicts ("DAV should find that X is supported")
  - Framing analysis as adversarial or friendly toward the architecture
  - Including PII, credentials, or sensitive data
  - Using jailbreak-style instructions (framework ignores and rejects these)
- Length guidance: aim for each slot to be brief; the full populated prompt should fit comfortably in 2000 tokens of system message
- How DAV renders the final prompt from template + slot content
- Version pinning: consumers declare which prompt template version they target

This spec should be authored after `05-use-case-schema.md` is stable because prompts depend on UC structure.
