# DAV Specification 02 — Stage Model

**Status:** Stub (not yet authored)
**Audience:** Consumer building a stage configuration; DAV maintainer implementing new stages
**Depends on:** `05-use-case-schema.md`, `07-analysis-output-schema.md`

## Purpose

DAV's analysis pipeline is organized into stages. Each stage has a declared role, an input contract, and an output contract. This spec defines the stage model generally and catalogs the stages DAV ships with.

Topics this spec will cover when authored:

- The stage concept: a named, contract-bound unit of the analysis pipeline
- Stage declaration: a consumer's `stage-config/stages.yaml` declares which stages run, with what parameters, and in what order
- The Stage Configuration Schema: YAML shape for stage declarations
- Current stages:
  - **Stage 0 — Assertions.** Consumer-supplied Python assertion checks. Pre-LLM, deterministic. Hard-gate-capable.
  - **Stage 1 — Seed.** LLM-assisted expansion of a skeletal use case into a full use case with success criteria, domain context, and expected artifacts. Used during UC authoring.
  - **Stage 2 — Analyze.** The core architectural analysis. LLM agent reads the spec corpus via MCP tools, produces a structured Analysis. This is the stage most consumers think of as "DAV."
- Stage I/O contracts: how stages consume inputs and emit outputs
- Stage composition: how outputs from one stage become inputs to the next
- Error handling: what happens when a stage fails
- Future stages: pointers to anticipated additions (recommendation generation, gap prioritization, multi-turn refinement)

This spec is foundational. It should be authored after the I/O schemas (`05` and `07`) are stable, since stages are defined in terms of those I/O contracts.
