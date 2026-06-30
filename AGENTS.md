# AGENTS.md — DAV

> Cross-agent context file ([agents.md](https://agents.md) standard). `CLAUDE.md` is a symlink to this
> file so Claude Code reads the same source of truth. This is the operational "how to use / work in DAV"
> guide; for **why** DAV is shaped this way (design rationale, reconstruction), read `DAV-AI-PROMPT.md`,
> then the `specs/` and the ADRs.

## What DAV does

**DAV — DCM Architecture Validation.** A framework for validating that an **architectural specification
supports a defined set of use cases**. It runs an LLM agent over a consumer's spec content (architecture
docs, capability inventories) and produces a structured **Analysis** YAML per use case: components
required, data-model entities touched, capabilities invoked, gaps, and a verdict
(`supported` / `partially_supported` / `not_supported`).

DAV is **not** a test runner for code — it's a test runner for *specifications*, the documents that
describe an architecture before code exists. The first consumer is **DCM** (the Red Hat FlightPath
sovereign-cloud framework); other consumers plug in by shipping a `consumer-profile.yaml` and pointing
DAV at their spec + corpus repos. **Apache-2.0.**

## Operating model — read this first (canonical)

The ground-up operating model is **[`docs/operating-model-decision-record.md`](docs/operating-model-decision-record.md)**
(ratified 2026-06-30). **Build to it.** Every change must respect:

- **Purpose / North Star (§0):** DAV is the **instrument to prove + mature DCM/UDLM** with evidence.
  **A-now** (instrument; project-scoped; simple) / **B-later** (general product; isolation-by-deployment).
  *Decision test:* does it help prove/mature DCM/UDLM **now**? If it only serves B, defer it.
- **One shape, two subjects (§2):** *gap analysis + current status + roadmap*. **Primary** = architecture
  validation (UCs × DCM/UDLM spec). **Secondary** = assessments (material × maturity framework → Maturity
  Wall + roadmap). **One pipeline — do not fork.**
- **Vocabulary (§3a):** **Ingest** = pull UCs in (+validate). **Analyze** = the gap-analysis run (formerly
  "run" / `/api/runs`). **Harvest** = internal results-load. (Rename in flight; keep `/api/runs` alias.)
- **Validation (§6):** one validator = the **engine's real loader** (no parallel "mirror"); **quarantine**
  invalid UCs at ingest with a reason; **tolerant loader** (unknown keys warn, never crash); background
  sweep → UC-health. **Single source of truth for schema + enums** — never redeclare enums in API/UI;
  derive from `engine/src/dav/core/consumer_profile.py`.
- **Isolation (§5):** **project scoping is the seam** (single schema + `project_id`). **No
  schema-per-tenant** (being collapsed). Real isolation later = a separate deployment.
- **Corpus vs spec (§6a):** corpus = the UCs (**mandatory curated subpath**); spec = the architecture.
  Architectures own an **approved validation corpus** (continuous validation + gating). UCs carry a
  **`purpose`** (architecture-validation / feedback / candidate / exploratory).
- **Capability = one shared spine (§6d, proposed):** one capability catalog for both missions; a maturity
  framework = capability-set + rubric overlay. **Don't add parallel capability stores.**
- **Scope = labels + selectors (§6c, proposed):** tag entities; compose scope with positive/negative
  (K8s-style) selectors; **tags select, project + RBAC authorize.**
- **Roadmap is a projection (§6b):** capability roadmap **and** **UC-enablement** roadmap (the UC is the
  **bridge to engineering**); applies to both missions.

## Build / run / test

- **Python ≥ 3.11** (`engine/pyproject.toml`). The package is `dav` (src layout under `engine/`).
- **Install (editable) before running `python -m dav.…`:** `cd engine && pip install -e .`
  (deps in `engine/requirements.txt`).
- **Tests:** standalone unittest-style, **no pytest dependency** — each suite has an `if __name__ ==
  "__main__"` block and runs via `python -m dav.tests.<name>` (from `engine/src/`, or after the editable
  install). The 8 suites live in `engine/src/dav/tests/` (test_consumer_profile, test_ensemble,
  test_explore, test_grounding_nudge, test_run_corpus, test_schema_v1, test_stage2_orchestration,
  test_version). To run all at once you may use pytest (`pip install pytest && python -m pytest`) — it
  discovers them — but pytest is **not** required and is not in `requirements.txt`.
- **No linter / formatter / type-checker is configured** (no ruff/mypy/black/flake8/tox). Don't invent
  one — match surrounding style.

## Repo layout

```
dav/
├── adr/                      Architecture decision records (001–008 + README index) — locked decisions
├── ansible/                  OpenShift deployment role (Tekton pipeline) — roles/dav/, inventory/, playbook.yaml
├── docs/                     Design docs, system spec, operator-runbook.md, agent-integration.md
├── engine/                   The Python framework (pip install -e .)
│   ├── pyproject.toml, requirements.txt
│   └── src/dav/
│       ├── ai/               agent.py (Stage2Agent tool-use loop), client.py (OpenAI-compat HTTP),
│       │                     mcp_tools.py (fastmcp wrapper), prompts.py (build_stage2_system_prompt;
│       │                     bump STAGE2_PROMPT_VERSION on prompt changes)
│       ├── core/             consumer_profile.py, corpus.py, ensemble.py (merge_analyses),
│       │                     explore.py (build_variance_report), use_case_schema.py (UseCase/Analysis
│       │                     dataclasses + validators), version.py
│       ├── evaluator/        compare.py (semantic comparator for Analysis YAMLs)
│       ├── stages/           stage2_analyze.py (single-UC CLI), run_corpus.py (corpus CLI)
│       ├── scripts/          compare_analyses.py, smoke_test_stage2.py
│       ├── schemas/          schema assets
│       └── tests/
├── mcp/                      The MCP server that clones a consumer's spec repo and serves docs to the agent
├── review-console/          Web UI/ops frontend: trigger runs, browse results, UC lifecycle management
├── use-cases/               UC content
├── examples/                dcm-reference-profile.yaml, exemplar-ucs/ (happy-path + gap-discovery), minimal-consumer/
├── specs/                   Versioned specifications (05-use-case-schema.md, 07-analysis-output-schema.md, …)
├── scripts/                 Top-level scripts
├── README.md, AGENTS.md (this), CLAUDE.md→AGENTS.md, DAV-AI-PROMPT.md (design narrative)
```

## Core concepts

### Consumer
The thing being validated (DCM is one; another could be a synthetic `minimal-consumer`). A consumer
ships two git repos — a **spec repo** (architecture Markdown; DAV's MCP server clones it and serves docs
to the agent via tool calls) and a **corpus repo** (use cases + a `dav-version.yaml` declaring
`consumer_version`) — plus a **consumer profile** (`consumer-profile.yaml`) listing its controlled
vocabularies (`lifecycle_phase`, `provider_types`, `profiles`, …). The DCM reference profile is built in
for backward compatibility; other consumers ship their own.

### Use case (v1.0 schema)
A YAML file describing one architectural test scenario. The authoritative schema is
`engine/src/dav/core/use_case_schema.py` (+ `specs/05-use-case-schema.md`); validate with
`UseCase.from_dict(...).validate(profile)`. Shape:

```yaml
uuid: uc-<slug>                  # MUST start with "uc-"
handle: <category>/<descriptor>  # e.g. "registration/happy-path"
scenario:
  description: "Free-form prose describing the scenario."
  actor: { persona: <role>, profile: <profile> }   # profile MUST be in consumer profile's profiles[]
  intent: "What should be accomplished."
  success_criteria: [ ... ]      # list of independently testable conditions
  dimensions:                    # values MUST be in the consumer profile vocab
    lifecycle_phase: <value>
    resource_complexity: <value>
    policy_complexity: <value>
    provider_landscape: <value>
    governance_context: <value>
    failure_mode: <value>        # happy_path for happy paths; a failure value for gap-discovery
  profile: <profile>             # repeated; future split point
  expected_domain_interactions:  # optional, improves analysis quality
    - { domain: <area>, interaction: "What happens here" }
generated_by:
  mode: regression               # regression | pr-targeted | authoring   (NOTE hyphen: pr-targeted)
  source: human-authored         # corpus | llm-unguided | llm-guided | human-authored
tags: []
```

See `examples/exemplar-ucs/` for two reference UCs (happy path + gap discovery).

### Analysis (v1.0 schema)
DAV's output, one per UC (`specs/07-analysis-output-schema.md`; dataclass in `use_case_schema.py`):
`use_case_uuid`, `analysis_metadata` (model, timestamp, engine_version/commit, consumer_version,
stage2_run_id, wall_time_seconds, sample_seeds/count, mode), `components_required`,
`data_model_touched`, `capabilities_invoked`, `provider_types_involved`, `policy_modes_required`,
`gaps_identified`, `summary` (verdict + overall_confidence{label,band} + notes), and (verification mode
only) `sample_annotations`. **Verdict** = supported | partially_supported | not_supported. **Severity**
= critical / major / moderate / minor / advisory (5). **Confidence** = high / medium / low (3, with
optional band for ensemble merging).

### Three runtime modes
Stage 2 runs in one of three modes; default `verification`:

| Mode | Samples | Temp | cache_prompt | Output | When |
|------|---------|------|--------------|--------|------|
| `verification` | 3 | 0.2 | true | one merged Analysis YAML | CI, regression — the cross-run-comparable default |
| `reproduce` | 1 (forced) | 0.0 | false | one Analysis YAML | audit exemplar, debug — byte-identical on rerun |
| `explore` | 10 | 0.7 | true | per-sample YAMLs + variance.yaml | UC authoring, adversarial poke-testing |

Framing is **predictable correctness**, not strict determinism. Verification merges N samples by
majority vote (ties conservative, confidence capped at medium). Reproduce is the closest to
deterministic. Explore is intentionally noisy to surface variance. (Defaults live in
`stages/stage2_analyze.py`.)

## How to run

**Single UC:**
```bash
python -m dav.stages.stage2_analyze \
    --use-case path/to/uc.yaml --output path/to/analysis.yaml \
    --inference-endpoint http://your-vllm:8000/v1 --inference-model qwen \
    --mcp-url http://dav-docs-mcp.dav.svc:8080 \
    --consumer-content-path /path/to/consumer/repo \
    --no-enable-thinking --max-tool-calls 30
```
Add `--mode reproduce` for deterministic single-sample; `--consumer-profile path/to/profile.yaml` to
override the DCM default. (The inference **model name** is `qwen` — llama.cpp accepts any name; the
backing GGUF is `Qwen3-32B-Q8_0`.)

**Whole corpus:**
```bash
python -m dav.stages.run_corpus \
    --corpus-path path/to/use-cases/ --output-dir path/to/runs/ \
    --inference-endpoint http://your-vllm:8000/v1 --inference-model qwen \
    --mcp-url http://dav-docs-mcp.dav.svc:8080 \
    --consumer-content-path /path/to/consumer/repo --no-enable-thinking
```
Output: `<output-dir>/<run-id>/analyses/<uc-uuid>.yaml` + `run-summary.yaml`; failures land in
`failures/<uc-uuid>.error.txt` and the run continues (`--halt-on-error` to stop on first failure).

**Inside OpenShift (Tekton):** the `ansible/roles/dav/` role deploys the `dav-stage2` pipeline (steps:
`cleanup-workspace` → `sync-spec` ∥ `sync-corpus` → `run-corpus`), namespace `dav`, serviceaccount
`dav-pipeline-sa`, workspace `shared-data` (PVC `dav-workspace`). **Preferred: trigger via the Review
Console UI** (Runs tab → New Run). Or `tkn pipeline start dav-stage2 -n dav …`. See
`docs/operator-runbook.md`. Results land in `<workspace>/results/<run-id>/`, browsable from the console
**Results** tab.

**Compare two analyses (the regression gate):**
```bash
python -m dav.scripts.compare_analyses path/to/a.yaml path/to/b.yaml   # 0 = equivalent, 1 = changed
```

## Writing a good use case
Read the two exemplars under `examples/exemplar-ucs/` first. Then: `intent` is one sentence on what the
UC tests (not a paraphrase of the description); `success_criteria` are independently testable, observable
conditions ("audit log records the rejection" beats "handles the failure gracefully"); pick the most
specific `dimensions` vocab value; `expected_domain_interactions` point the analyzer at where evidence
should exist; set `failure_mode` to `happy_path` or a failure value; **no backstory** — the UC must be
self-explanatory.

## Adding a new consumer
1. Author a `consumer-profile.yaml` (see `examples/minimal-consumer/`).
2. Author a corpus of v1.0 UCs; validate with `UseCase.validate(profile)`.
3. Set up two git repos: spec (architecture docs) and corpus (UCs + `dav-version.yaml`).
4. Copy `ansible/inventory/group_vars/all/vars.local.yaml.example` → `vars.local.yaml`; set
   `consumer_spec_repo_url`, `consumer_corpus_repo_url`, inference endpoint, cluster apps domain.
5. Run the Ansible playbook against OpenShift (`docs/operator-runbook.md`).

## Common pitfalls
- **`consumer_version` empty on AnalysisMetadata** → add `consumer_version: <semver>` to the consumer's
  `dav-version.yaml`.
- **"value not in profile vocab"** → fix the UC value or add it to the consumer profile (and commit).
- **`generated_by.mode`/`source` validation fails** → use `pr-targeted` (hyphen, not `pr_targeted`) and
  one of `corpus | llm-unguided | llm-guided | human-authored` (`llm-generated` is **invalid**).
- **Verification N=1 warns** → intentional; use `--mode reproduce` for cheap single-sample.
- **`partially_supported` on an apparent happy path** → read `gaps_identified`; often the architecture
  supports it but the *docs* don't articulate it. The verdict is honest about what the spec says.

## Where to look first when extending DAV
- New UC schema field → `engine/src/dav/core/use_case_schema.py`
- New CLI flag → `engine/src/dav/stages/stage2_analyze.py` and/or `run_corpus.py`
- New consumer-profile field → `engine/src/dav/core/consumer_profile.py` + `examples/dcm-reference-profile.yaml`
- Prompt changes → `engine/src/dav/ai/prompts.py` (bump `STAGE2_PROMPT_VERSION`)
- New analyzer behavior → `engine/src/dav/ai/agent.py` (the tool-use loop)
- New deployment artifacts → `ansible/roles/dav/templates/`

## Conventions
- **Commits:** `--no-gpg-sign`, author `Chris Roadfeldt <chris@roadfeldt.com>`, trailer
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. GitHub `croadfeldt/dav`, default branch
  `main` (use `gh`).
- **PRs (CONTRIBUTING.md):** fork/branch/PR against `main`; **one subject per PR** (≤2–3k lines, split
  larger subjects on logical boundaries, never bundle unrelated subjects); lead with a short **"Why"**
  linking an ADR/design doc; include **tests** for new behavior; update `specs/`/ADRs when contracts or
  architecture change. **Spec changes are consumer-breaking by default** → companion ADR + migration
  note. ADRs go in `adr/` (next number, one decision each).
- **Further reading:** `DAV-AI-PROMPT.md` (engine-framework design rationale / rebuild guide),
  `review-console/AGENTS.md` (the operator web app — stack, auth/RBAC, tenancy, data model), `specs/`
  (the contracts), `adr/` (locked decisions), `docs/agent-integration.md` (how external agents
  authenticate to a running DAV via PAT).
