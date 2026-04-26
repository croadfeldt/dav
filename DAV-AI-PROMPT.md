# Rebuilding DAV

Audience: an LLM (or developer) tasked with confidently modifying or rebuilding the DAV framework. Where `AI-ONBOARDING.md` covers daily operations, this document covers **why DAV is shaped the way it is**. If you're going to touch the codebase non-trivially — add a runtime mode, change the schema, build a new consumer, port the framework to a different inference backend — read this first.

This is a design narrative, not a reference. The reference is in `specs/`. This document tells you which decisions were deliberate (don't undo them without good reason), which are incidental (free to change), and which compromises were made when (so you know what to revisit).

---

## 1. What DAV is and isn't

DAV is **DCM Architecture Validation** — a framework for running an LLM agent against an architectural specification to validate that the spec supports a defined set of use cases. The output is a structured Analysis YAML per use case: which components are required, which capabilities are invoked, what gaps the spec has, and a verdict.

The framing matters: DAV validates **specifications**, not **implementations**. It runs over Markdown architecture docs, ADRs, capability lists — content that exists before code does. The agent reads the docs via MCP tool calls and produces an analysis grounded in what the spec actually says.

Things DAV is **not**:

- A unit test runner. There is no code under test. The "test" is whether the spec describes how the use case is supported.
- A documentation linter. DAV checks substantive architectural support, not formatting or completeness in the prose sense.
- A deterministic system. LLM outputs vary; DAV's job is to make that variance **predictable and bounded**, not zero. This shapes every runtime decision.
- A general-purpose evaluation framework. DAV is opinionated: schema is fixed, agent prompt is fixed, output format is fixed. Consumers configure their content and vocabulary, not the runtime.

The first consumer is **DCM** (Red Hat FlightPath sovereign-cloud framework), and the framework's design is informed by working with DCM as the primary use case. ADR-001 establishes DAV as consumer-agnostic; ADR-002 covers the future where DAV becomes a first-class DCM capability (process provider, deferred 6-12 months).

---

## 2. The predictable-correctness framing

This is the single most important framing in the entire framework. **Read this twice.**

LLM-based analysis cannot be made deterministic. Even at temperature 0, dual-GPU tensor parallelism produces argmax flips at borderline tokens. Caching produces different outputs depending on what's been seen before. Two runs of the "same" prompt against the "same" model can disagree.

An early DAV design treated this variance as a problem to eliminate (rule-based simulator as a deterministic cross-check, low temperatures everywhere, KV cache disabled by default). The current framing is different: **predictable correctness, not strict determinism, is the property we want.** The output should be:

- **Stable enough to compare across runs** for regression detection.
- **Bounded enough to vote on** when several samples disagree.
- **Variance-aware** — explicit about where the model is confident vs. guessing.

This led to the three runtime modes. Each makes a different trade-off:

- **verification** (default): N samples (default 3) at low temperature (0.2), ensemble-merged. The merger does a majority vote per field; ties resolve conservatively (more severe verdict wins, confidence caps at medium); seeds are derived from the UC uuid for stable comparison across runs against unchanged corpus. This is the CI/regression default.
- **reproduce**: N=1 (forced), greedy decoding (temp 0.0), seed from UC uuid (or `--seed` override). Closest to deterministic — same UC produces byte-identical output (modulo timestamp) on rerun. Use for audit exemplars, debug, bisection.
- **explore**: N samples (default 10) at high temperature (0.7), no merge. Outputs all per-sample analyses plus a `variance.yaml` report. Use for UC authoring (seeing how the model interprets your wording) and adversarial poke-testing.

The merger logic in `engine/src/dav/core/ensemble.py` is load-bearing. It must:

- Aggregate same-shape lists (components_required, gaps_identified) by majority membership; ties go to "include with reduced confidence".
- Resolve verdict ties **toward the more severe verdict** (`not_supported` beats `partially_supported` beats `supported`).
- Cap `overall_confidence` at `medium` when the verdict was tied (a tied verdict cannot be high-confidence).
- Populate `sample_annotations` with the per-sample seeds and the consensus split (3/3, 2/3, etc.) so a reviewer can see where samples agreed/disagreed.

Don't change those rules without thinking through the consequences for regression comparison.

---

## 3. Schema v1.0 — what's load-bearing

The schema is in `engine/src/dav/core/use_case_schema.py`. It's the authoritative shape. The specs at `specs/05-use-case-schema.md` and `specs/07-analysis-output-schema.md` describe the shape in prose.

### Use case shape

Required identity:

- **`uuid`** must start with `uc-`. Don't ship a UC without `uc-` prefix; the analyzer keys on it for run-id and seed derivation.
- **`handle`** must be `<category>/<descriptor>`. The category groups related UCs; the descriptor is human-readable. Both are used in run summaries and output filenames.

Required structure:

- **`scenario.actor`** has `persona` + `profile`. Persona is free-form (e.g. `librarian`). Profile must be in the consumer profile's `profiles[]` list.
- **`scenario.intent`** is **one sentence**. Don't split into multiple sentences; the analyzer treats it as the operative description of what success looks like.
- **`scenario.success_criteria`** is a list of independently testable conditions. Each criterion should be observable. Vague criteria produce vague analyses.
- **`scenario.dimensions`** has six fixed fields, each with values constrained by the consumer profile. The dimensions classify the UC for analysis routing and reporting.
- **`scenario.profile`** repeats `actor.profile`. This is intentional: the profile drives multiple things (vocab, future per-profile splits) and we want it explicitly captured at the scenario level.
- **`scenario.expected_domain_interactions`** is optional but improves analysis quality significantly. Each `{domain, interaction}` pair points the agent at where evidence should exist.

The **5-label severity** scheme (`critical / major / moderate / minor / advisory`) is in `Severity` enum. Three-label severity was tried earlier and rejected — it didn't have enough resolution to distinguish "this is a real architectural problem" from "this is a documentation gap". Don't go back to fewer labels.

The **descriptor form** for confidence is also load-bearing: a `ConfidenceDescriptor` carries both a `label` (high/medium/low) and an optional `band` (lower/upper bounds). The merger uses the band to express "samples disagreed but mostly agreed" without losing the label simplicity for casual reading. Don't drop the band.

### Analysis shape

The Analysis is what the agent produces. The shape is fixed and the agent prompt enforces it via guided-JSON decoding (or strict prompting on endpoints that don't support guided decoding).

`AnalysisMetadata` carries the run provenance:
- `model` (string) — the model name.
- `engine_version` / `engine_commit` — from `dav.core.version`. These graceful-degrade if git is unavailable; populate them via `version.engine_version_string()` not `git describe`.
- `consumer_version` — read from the consumer's `dav-version.yaml` (key: `consumer_version`). Empty if the consumer hasn't shipped one. The analyzer doesn't fail on missing consumer_version.
- `stage2_run_id` — uuid generated per analyzer call (NOT per corpus-runner call; the corpus runner has its own run-id).
- `wall_time_seconds` — single-sample wall time; in verification-merged outputs this is the merged wall time (sum or max — see ensemble.py).
- `sample_seeds`, `sample_count`, `mode` — populated only on verification-merged outputs (and explore variance reports).

`AnalysisSummary` is verdict + confidence + notes. Verdict is a string from `Verdict` enum. Confidence is a `ConfidenceDescriptor`.

`sample_annotations` only appears in verification-merged outputs. It records per-sample `verdict + confidence + seed` tuples plus the consensus split. Consumers reviewing a verification result can see whether samples agreed or whether the merger had to break a tie.

Lists like `components_required[]` are aggregated by the merger across samples. Each list item has its own structure (see schema for shape). The merger's aggregation logic preserves uniqueness by `(name, kind)` tuples in most cases; check `ensemble.py` for the exact rules.

### What's NOT in the schema (deliberately)

- **No assertion runner integration.** `AssertionResult` exists in the schema as a forward placeholder, but no executor exists. Assertions are post-MVP — they'd let UCs run code against the consumer's content tree to verify documented invariants. Open design questions: how is the code shipped, how is it sandboxed, how does it integrate with verification/reproduce/explore. Don't add an assertion runner without designing those answers first.

- **No live tool result caching.** The MCP client fetches docs fresh every tool call. Caching could improve throughput but breaks reproducibility (cache state affects outputs). Defer.

- **No streaming output.** The agent waits for the full LLM response before parsing JSON. Streaming would speed up UI response but complicates guided-JSON validation. Defer.

---

## 4. Architecture rationale — what's load-bearing

This section explains *why* DAV's pieces are shaped the way they are. The framework converged on these decisions through real use; understanding the rationale matters when you're tempted to relax one of them.

### Engine as a normal Python package

The engine lives at `engine/src/dav/` as a standard `pip install -e .` package, not embedded inside the Ansible role. CLI entry points are all `python -m dav.<x>`. Tests live alongside the code at `engine/src/dav/tests/`.

**Don't undo:** mixing deployment artifacts with the framework conflates two concerns and breaks Python tooling. Keep the package layout standard so consumers, contributors, and CI can use ordinary Python workflows.

### Profile-validated schema (v1.0)

UCs and analyses have versioned shapes:

- 5-label severity (`critical / major / moderate / minor / advisory`)
- 3-label confidence with optional band metadata (`ConfidenceDescriptor`)
- `sample_annotations` for verification-mode merge provenance
- `AssertionResult` placeholder for future assertion runner integration
- Dataclasses with `to_dict()` / `from_dict()` round-trips
- A `validate(profile)` method that checks dimension values against the active consumer profile

3-label severity was tried and rejected — not enough resolution to distinguish a real architectural problem from a documentation gap. The descriptor confidence form (label + band) lets the merger express "samples disagreed but mostly agreed" without forcing a label-only collapse.

**Don't undo:** the validate-against-profile pattern is how DAV stays opinionated without being rigid. Adding a new dimension or vocabulary value is a profile change, not a code change.

### Three runtime modes

The runtime has three modes. Verification is the default:

| Mode | Default samples | Default temp | Output | When to use |
|------|-----------------|--------------|--------|-------------|
| `verification` | 3 | 0.2 | One merged Analysis | CI, regression, the cross-run-comparable default |
| `reproduce` | 1 (forced) | 0.0 | One Analysis | Audit exemplar, debug, bisection — byte-identical on rerun |
| `explore` | 10 | 0.7 | Per-sample analyses + variance.yaml | UC authoring, adversarial testing |

The merger lives in `engine/src/dav/core/ensemble.py` and the variance report builder is `engine/src/dav/core/explore.py`.

The shift from "deterministic" to "predictable-correctness" framing is what makes verification the default — it's the only mode safe to compare across runs because it absorbs sample-to-sample variance into the merge step.

**Don't undo:** the three modes are how DAV manages LLM variance. Adding a fourth mode is fine if the trade-off is genuinely different from the existing three; collapsing them is not.

### Consumer profile externalization

`engine/src/dav/core/consumer_profile.py` contains a `ConsumerProfile` dataclass and a `load_profile()` function with three loading paths (file, MCP, built-in DCM reference fallback). Schema validators take an optional profile parameter. Prompts are templated via `build_stage2_system_prompt(profile)`.

Module-level `__getattr__` shims in `use_case_schema.py` and `prompts.py` build profile-bound globals lazily so call sites that import a global like `STAGE2_SYSTEM_PROMPT` get the DCM-bound version unless `set_default_profile()` was called first. Always set the profile before importing such globals when running with a non-DCM consumer.

Ansible variables prefix everything with `dav_*` (operator config) or `consumer_*` (per-consumer config). `consumer_spec_repo_url`, `consumer_corpus_repo_url`, etc. — never `dcm_*`. Consumer-agnostic naming is the contract.

`AnalysisMetadata` carries `engine_version`, `engine_commit`, and `consumer_version`. The first two come from `dav.core.version` (graceful-degrades if git is unavailable). The third comes from the consumer's `dav-version.yaml` file (empty if missing; framework doesn't fail on absence).

**Don't undo:** consumer externalization is what makes DAV a framework rather than scaffolding. New consumers plug in via profile + content tree, not by forking the engine.

### Engine-side corpus iteration

`engine/src/dav/stages/run_corpus.py` iterates a UC corpus in a single Python process. Per-UC failure isolation via try/except. Continue-on-error by default; `--halt-on-error` to change. Output layout:

```
<output-dir>/<run-id>/
├── analyses/<uc-uuid>.yaml         per-UC analysis
├── failures/<uc-uuid>.error.txt    per-UC error (if failed)
└── run-summary.yaml                unified summary
```

Tekton's `matrix` feature was an alternative — one TaskRun per UC, parallel execution. Rejected because: matrix has a 256-element ceiling that DCM-scale corpora can hit; per-UC pod overhead dominates over per-UC analysis time at small corpus sizes; cross-UC state (run-summary aggregation) needs an extra task to combine matrix outputs.

Engine-side iteration is simpler: one container, one Python process, native Python loop over the corpus, write outputs to a single directory.

**Don't undo:** the corpus runner is the default entry point for full-corpus runs. Single-UC `stage2_analyze` still exists for ad-hoc work, but production runs go through `run_corpus`.

### Tekton pipeline shape

The `dav-stage2` pipeline does three things:

1. Clone consumer's spec repo into `<workspace>/spec/`
2. Clone consumer's corpus repo into `<workspace>/corpus/`
3. Run `dav-run-corpus` against `<workspace>/corpus/use-cases/`

Both clones happen in parallel (no inter-step dependency). The shared workspace + subdirectory layout keeps the pipeline simple — one PVC, no inter-task data shuffling, native filesystem paths.

Two reusable tasks back the pipeline:

- `dav-git-sync.yaml.j2` — parameterized clone with a `target-subdir` param so the same task handles both spec and corpus
- `dav-run-corpus.yaml.j2` — wraps `python -m dav.stages.run_corpus` with the right args plumbed in

**Don't undo:** the parallel-clone + single-task-run shape is intentional. Splitting analysis into per-UC TaskRuns reintroduces the matrix problems above.

### Deployment topology — engine and inference separated

DAV's deployment splits into two independently-deployable concerns:

- **Engine** — the orchestration layer. The DAV Python package, MCP server, review console, Tekton pipeline (or quadlet equivalent on podman). Reads/writes from a shared workspace, talks to an inference endpoint over HTTP.
- **Inference** — the compute layer. llama.cpp, vLLM, hosted OpenAI API, anything that exposes an OpenAI-compatible `/v1` endpoint.

These are intentionally decoupled. Operators choose deploy targets per layer:

- Engine on OpenShift, inference on bare-metal podman+GPU host (current deploy)
- Engine on OpenShift, inference on a hosted API (cloud-based)
- Engine on podman, inference on the same host (single-machine demo)
- Engine on podman, inference on a remote vLLM (split deployment)

**Today's state:** the Ansible role conflates the two — it deploys engine resources AND a `vllm-tier3` fallback inference deployment. This is the "everything in one cluster" path.

**Future direction (locked, not yet implemented):**

- **OpenShift target:** an Operator with two CRDs — `DavEngine` and `DavInference`. Each can be applied independently. ADR-003 candidate captures this.
- **Podman target:** Ansible-managed quadlets, two role sets — `dav-engine-quadlets` and `dav-inference-quadlets`.

**Don't undo:** if you're working on the deploy layer, preserve the engine/inference split. The engine consumes inference as a URL; the engine deploy and the inference deploy are not coupled. Reintroducing coupling reverses an explicit architectural decision.

### Built-in DCM reference profile

The framework ships a DCM profile in code (`get_dcm_reference_profile()`) so DAV is usable with no external configuration. This is a deliberate exception to "no consumer-specific knowledge in the framework" — the DCM profile is the canonical worked example, kept current alongside the framework, and provides a fallback so smoke tests and exemplar UCs work without setup.

Other consumers ship their own profile YAML files. The framework does not have a built-in reference profile for any consumer other than DCM.

**Don't undo:** if you're tempted to also bundle a "minimal-consumer" or "BookCatalog" profile in code, don't. Those live as YAML examples in `examples/`. The DCM reference profile is special because DAV was built from DCM and DCM is the testing target.

### Synthetic exemplar UCs

`examples/exemplar-ucs/` contains two UCs in a fictional `BookCatalog` domain — one happy path, one gap discovery. They validate clean against the DCM reference profile (the profile's vocabulary is general enough to cover them). They're deliberately non-DCM so they serve as portable "what a v1.0 UC looks like" references for new consumers.

**Don't undo:** keep these synthetic. If you ship DCM-specific UCs as exemplars, they stop being useful as references for other consumers.

---

## 5. Consumer profile externalization (deeper dive)

`core/consumer_profile.py` is the most subtle module in the framework. Reading the code is necessary; this section explains the design intent.

A `ConsumerProfile` declares:

- `consumer_id` (string identifier, e.g. `dcm`)
- `framework_name` (display name)
- Per-dimension allowed-value lists (`lifecycle_phases`, `resource_complexities`, etc.)
- `profiles[]` (the `profile` values UCs may use)
- `personas[]` (optional; informational, not enforced)
- `provider_types[]` (vocabulary for analysis output)
- `policy_modes[]` (vocabulary for analysis output)
- Capability/component category lists (used in prompt templating)

Loading order (in `load_profile()`):

1. If `path` is given, load from that YAML file.
2. Else if `mcp_url` is given, attempt to fetch via MCP (`get_consumer_profile` tool). **Currently the MCP server doesn't implement this tool.** It falls through.
3. Else fall back to the built-in DCM reference profile (`get_dcm_reference_profile()`).

The MCP fallback path was wired in ε.1 anticipating that consumers would ship their profile via the same MCP server that serves their docs. The server-side handler is still pending. Don't remove the MCP loading code; finish the server side instead.

The DCM reference profile is the canonical example. It lives in code (not YAML) so the framework remains usable without any external files. Other consumers ship YAML; DCM is special because DAV was built from DCM.

Module-level `__getattr__` shims in `use_case_schema.py` and `ai/prompts.py` preserve the old API where pre-ε.1 callers expected globals like `ANALYSIS_JSON_SCHEMA` or `STAGE2_SYSTEM_PROMPT`. These are now built dynamically from the active profile via `build_analysis_json_schema(profile)` and `build_stage2_system_prompt(profile)`. The shims call `get_default_profile()` (which returns DCM if nothing's been set). Setting a default via `set_default_profile()` is required when running with a non-DCM consumer.

**Subtle gotcha:** if you import `STAGE2_SYSTEM_PROMPT` before calling `set_default_profile()`, you get the DCM-bound prompt. Always set the profile first.

---

## 6. The agent and the prompt

`engine/src/dav/ai/agent.py` contains `Stage2Agent`, the tool-use loop. The shape is:

1. Build the system prompt from `build_stage2_system_prompt(profile)`.
2. Build the user prompt (UC + scenario formatted as the agent's task description).
3. Loop:
   a. Send messages to the inference endpoint.
   b. If the response includes tool calls, execute them via the MCP client and append results.
   c. If the response is a final assistant turn, parse it as JSON (the Analysis), validate, return.
   d. Stop after `max_tool_calls` (default 30) iterations.

The agent uses **guided JSON decoding** when the endpoint supports it (vLLM `guided_json` parameter). On endpoints without guided decoding, the prompt strictly demands JSON output and parsing falls back to best-effort extraction. The schema for guided decoding is built dynamically from the active profile (the vocab values become enums in the JSON schema).

The MCP client (`ai/mcp_tools.py`) is a fastmcp wrapper. It exposes spec content tools to the agent: `list_documents`, `get_document_section`, `search_capabilities`, etc. These tools are how the agent grounds its analysis in the actual spec content. The agent CAN'T fabricate evidence — if it claims a component is required, it should have called a tool that returned the relevant doc section.

The prompt itself (`ai/prompts.py`) is the single most important piece of natural-language code in the framework. It:

- Establishes the agent's role (architectural reviewer, not a generic assistant)
- Defines the analysis task in terms of the v1.0 Analysis shape
- Provides the controlled vocabularies as explicit lists (the agent must use these values)
- Defines the verdict criteria (when to call something `supported` vs. `partially_supported` vs. `not_supported`)
- Sets confidence calibration guidance (don't be overconfident on indirect evidence)
- Includes few-shot examples of well-formed analyses

When changing the prompt, **bump `STAGE2_PROMPT_VERSION`** (in prompts.py). This is captured in AnalysisMetadata and is what regression comparison uses to know whether two analyses are comparable. Two analyses produced by different prompt versions are not directly comparable — the prompt change might be the source of any verdict differences.

`--no-enable-thinking` is the recommended default for Qwen3-family models. Thinking mode burns tokens on internal reasoning that we don't capture or use; for stage 2 it's pure overhead.

---

## 7. Repository structure rationale

The repo has three logical sections:

### `engine/` — the framework

Pure Python package. No deployment concerns, no Tekton, no Ansible. Tests live alongside the code (`engine/src/dav/tests/`). Editable-install via `pip install -e engine/`. The package is `dav`, not `engine`; the `src/` layout means imports are `from dav.X import Y`, which is the public API.

CLI entry points are all `python -m dav.<x>`. Don't add `setup.py` console_scripts — the `python -m` form makes the CLI discoverable from the package without installation paths leaking into the namespace.

### `ansible/` — the deployment

OpenShift role for deploying DAV. Renders Tekton tasks and pipelines from Jinja templates, applies them via `kubernetes.core.k8s`. The role assumes OpenShift 4.18+ (uses TaskRun template + serviceAccount fields that need recent Tekton).

Templates split:
- `templates/tekton-tasks/*.j2` — reusable tasks, globbed and applied by `engine.yaml` task
- `templates/pipeline-stage2.yaml.j2` — the pipeline (explicit application by `tekton.yaml` task)
- `templates/<other>.yaml.j2` — deployments, services, routes for the engine, MCP server, review console

The Ansible variable namespace prefixes everything with `dav_` (operator-controlled config) or `consumer_` (per-consumer config). Don't reintroduce `dcm_*` variables — they were renamed in ε.2 deliberately.

### `specs/` and `adr/` and `docs/`

`specs/` — versioned, authoritative specifications. The numbered ones (`05-use-case-schema.md`, `07-analysis-output-schema.md`) are the source of truth for shapes; the dataclasses in code must match them.

`adr/` — architecture decision records. Currently two: ADR-001 (consumer-agnostic framework) and ADR-002 (DAV-as-DCM-capability future direction). Add new ADRs when locking new architectural decisions; don't edit existing ones (they're historical).

`docs/` — design docs, system spec, project context. Living documents, freer to update. `PROJECT_CONTEXT.md` is the project-level onboarding; `DAV-System-Design-Spec.md` is the deep architecture.

---

## 8. Testing philosophy

166 tests across 7 suites. The suites cover:

- `test_schema_v1` — UseCase/Analysis schema invariants, validation, round-trip
- `test_consumer_profile` — profile loading (file/MCP/fallback), validation against profiles
- `test_ensemble` — verification merger logic (vote, ties, confidence cap, sample_annotations)
- `test_explore` — variance report builder
- `test_stage2_orchestration` — single-UC CLI orchestration (mocked inference + MCP)
- `test_version` — engine_version_string, graceful degradation
- `test_run_corpus` — corpus runner (gathering, run-id derivation, failure isolation)

Tests use unittest-style assertions but a custom no-fixture runner (`if __name__ == "__main__"` blocks). Each test file is runnable standalone via `python -m dav.tests.<name>`. There's no pytest dependency.

**Test invariants, not implementation details.** Tests should fail when the public behavior changes. They should NOT fail when refactoring internals. If a test breaks during a refactor, ask whether the test was checking too much before fixing it.

**Mocking philosophy:** mock external boundaries (inference endpoint, MCP server, filesystem in some cases) with `unittest.mock`. Don't mock internal modules; if module A calls module B and you're mocking B, your test is probably integration-level and should be promoted.

---

## 9. Things that are deliberately compromised

These are decisions that aren't great but are intentional:

- **The simulator is deliberately absent.** An earlier DAV design included a rule-based simulator that served as a deterministic cross-check on the LLM analyzer. The predictable-correctness framing made the cross-check redundant, and the simulator's rules were DCM-specific (couldn't survive consumer externalization). If you find yourself wanting deterministic verification of architectural rules independent of the LLM, that's a new design with new constraints — not a resurrection of the old simulator.

- **No streaming.** The agent waits for the full LLM response. Streaming would improve perceived latency but complicates JSON parsing. Defer until a UI need surfaces.

- **No assertion runner.** `AssertionResult` is a forward placeholder. Until consumers ship spec-bound assertion code, no executor.

- **Single-tenant Ansible deployment.** The role assumes one DAV instance per OpenShift cluster. Multi-tenancy is a future concern.

- **MCP server doesn't implement `get_consumer_profile`.** ε.1 wired the engine for it; the server-side handler is still pending. The fallback to the built-in DCM profile keeps things working in the meantime.

- **Engine version metadata uses `git describe` and degrades gracefully.** When DAV runs in a container without `.git`, version strings are `<unknown>`. Acceptable; consumers don't rely on engine_version for correctness.

- **Per-UC analyzer state is a singleton.** The MCP client and inference client are constructed once per UC and reused across samples. Concurrent samples within a UC run serially by default (`--sample-concurrency 1`); the concurrency knob exists but hasn't been stress-tested.

- **No retry on transient inference failures.** A timeout fails the UC (continue-on-error default at the corpus level lets others proceed). Retries with exponential backoff are a reasonable future addition; absence is intentional for now (don't paper over real endpoint problems).

---

## 10. How to extend DAV

The order of difficulty (and decreasing change-radius):

### Trivial (no schema change, no prompt change)

- Add a CLI flag — modify `stages/stage2_analyze.py` and/or `run_corpus.py` argparse, thread through to `AgentConfig` if relevant.
- Add a new test — drop a `test_*.py` in `engine/src/dav/tests/`, follow the existing pattern.
- Add a new ADR — drop a Markdown file in `adr/`, follow the format.

### Small (touches schema or profile)

- Add a new dimension value — update the consumer profile YAML (or the DCM reference profile). UCs using the new value validate; analyzer output handles it via the dynamically-built JSON schema.
- Add a new Analysis output field — update the dataclass, the to_dict/from_dict, the schema spec doc, the prompt's expected output format, the merger if it's list-shaped. Bump the prompt version.

### Medium (touches the agent)

- Add a new MCP tool — implement on the MCP server, add the client wrapper in `ai/mcp_tools.py`, list it in the prompt's tools section.
- Change the agent's tool-use behavior — modify `agent.py` carefully. The loop is small but every change affects analyzer outputs. Run all 8 test suites + a manual UC run before committing.

### Large (touches the runtime model)

- Add a new runtime mode — extend the mode dispatch in `stages/stage2_analyze.py`, add mode defaults in run_corpus, decide what merger or output behavior the new mode needs. Document the trade-off vs. existing modes.
- Add the assertion runner — design first (sandbox model, code shipping format, mode integration). The `AssertionResult` placeholder gives you the output shape; the executor and the prompt updates are open work.

### Largest (rebuild the framework)

- Port to a different inference backend — the OpenAI-compatible `/v1/chat/completions` API is the contract. Different backends with the same API (vLLM, llama.cpp server, hosted services) work with no code changes. A genuinely different backend (Anthropic API, Bedrock, etc.) would require an `InferenceClient` rewrite plus prompt validation against the new model's quirks.
- Build a new consumer — author a profile YAML, build a corpus of v1.0 UCs, set up a spec repo and corpus repo, point Ansible variables at them, run the playbook. The framework doesn't care about your domain.
- Rebuild DAV from scratch — read this document, then `AI-ONBOARDING.md`, then the specs, then `ai/prompts.py`, in that order. This document encodes the architectural rationale; the code gives you the shape.

---

## 11. Locked decisions (don't undo without strong reason)

For convenience, here's the consolidated list of decisions that are deliberately locked. Each emerged from working with a real consumer (DCM) over time and learning what was load-bearing vs. incidental.

- **Predictable correctness, not strict determinism.** Three modes (verification/reproduce/explore) are how we manage variance.
- **Ensemble merger conservative on ties.** More severe verdict wins; confidence caps at medium when tied.
- **Seeds derived from UC uuid by default.** Stable run-to-run comparison without operator intervention.
- **5-label severity, 3-label confidence with band descriptor.** Don't reduce labels; don't drop the band.
- **Uuid `uc-` prefix is mandatory.** Pre-v1.0 `tc-` prefixes get migrated.
- **Handle is `<category>/<descriptor>`.** Don't mix slashes for nested categories.
- **`generated_by` is structured (mode + source), not a bare string.** Migrated from pre-v1.0.
- **Consumer profile is external (file or MCP), with built-in DCM fallback.** Don't hardcode DCM-specific assumptions in the engine.
- **Dimensions are profile-validated.** Adding a value is a profile change, not a code change.
- **Ansible variables are `consumer_*` not `dcm_*`.** Consumer-agnostic naming.
- **AnalysisMetadata carries engine_version + engine_commit + consumer_version.** Provenance is non-negotiable.
- **Engine-side corpus iteration over Tekton matrix.** Avoids the 256-UC ceiling and per-UC overhead.
- **Continue-on-error by default for corpus runs.** `--halt-on-error` to change.
- **Forward-only migration.** Pre-v1.0 data isn't preserved within v1.0 files; recover from `.backup` siblings or git.
- **Migration tool default is in-place with `.backup` siblings.** Trust git as the safety net.
- **Multi-profile UCs split into N v1.0 UCs at migration time.** One UC per profile.
- **Synthetic exemplar UCs (BookCatalog) shipped with the framework.** Deliberately non-DCM.
- **No legacy stage 1+3+4 architecture.** A rule-based simulator, AI test generation step, and report aggregator/generator are not part of DAV. If you see references to these in older context or external docs, they describe a design that was tried and abandoned in favor of the current single-stage analyzer with three runtime modes.

---

## 12. What's not yet built

Future work, in roughly priority order:

- **MCP server `get_consumer_profile` handler.** Engine side wired in ε.1; server side pending.
- **Assertion runner.** Schema placeholder exists; executor + prompt + mode integration are open design questions.
- **Vocabulary refinement based on real usage.** ε.1 froze DCM's vocab; some dimensions may turn out to be vestigial after months of usage.
- **DAV-as-DCM-capability (ADR-002).** Make DAV a process provider in DCM itself, opt-in. Deferred 6-12 months.
- **Multi-consumer concurrent runs.** Single-tenant deployment today.
- **Streaming agent output.** Latency improvement; complicates JSON parsing.
- **Inference retry / backoff.** Currently a transient failure fails the UC.

If you're picking up DAV development without prior context, your first non-trivial task is probably one of these. Read the relevant section above, check the corresponding code, and propose a design before implementing.

---

## 13. Final thought

DAV is opinionated. The opinions came from running the framework against a real consumer (DCM) over months and learning what was load-bearing vs. incidental. Most of this document is a record of those opinions.

When you find yourself wanting to relax an opinion, you may be right. Check the corresponding ADR or section above first; if the rationale doesn't apply anymore, document why and propose the change. If it does, the opinion is doing its job — the friction you're feeling is the framework refusing to be misused.

Don't rebuild from scratch lightly. The shape converged through real use; rebuilding without that context will recover most of it through painful iteration. Read first, modify second, rebuild only if you have a fundamentally different goal.
