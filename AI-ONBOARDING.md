# Using DAV

This document brings an LLM up to speed on **using** the DAV framework: running analyses, writing use cases, configuring a consumer, interpreting outputs. For design rationale and reconstruction guidance, see `DAV-AI-PROMPT.md`.

## What DAV does

DAV (DCM Architecture Validation) is a framework for **validating that an architectural specification supports a defined set of use cases**. It runs an LLM agent over a consumer's spec content (architecture docs, capability inventories) and produces a structured Analysis YAML for each use case: which components are required, which data model entities are touched, which capabilities are invoked, what gaps exist, and an overall verdict (`supported` / `partially_supported` / `not_supported`).

DAV is not a test runner for code. It's a test runner for *specifications* — the documents that describe the architecture before code exists. The first consumer is DCM (the Red Hat FlightPath sovereign-cloud framework); DAV is designed so other consumers can plug in by shipping a `consumer-profile.yaml` and pointing DAV at their spec repo.

## Repo layout you'll encounter

```
dav/
├── adr/                          # architecture decision records
│   ├── 001-dav-consumer-agnostic-framework.md
│   └── 002-dcm-integration-model.md
├── ansible/                      # OpenShift deployment role
│   ├── inventory/
│   ├── playbook.yaml
│   └── roles/dav/
├── docs/                         # design docs, system spec
├── engine/                       # the Python framework
│   └── src/dav/
│       ├── ai/                   # agent.py (stage 2 LLM agent)
│       │   ├── agent.py          # Stage2Agent — the tool-use loop
│       │   ├── client.py         # InferenceClient — OpenAI-compatible HTTP
│       │   ├── mcp_tools.py      # McpClient — fastmcp wrapper for spec tools
│       │   └── prompts.py        # build_stage2_system_prompt() etc.
│       ├── core/                 # framework essentials
│       │   ├── consumer_profile.py     # ConsumerProfile + loaders
│       │   ├── ensemble.py             # merge_analyses() — verification merger
│       │   ├── explore.py              # build_variance_report() — explore mode
│       │   ├── use_case_schema.py      # UseCase, Analysis, dataclasses, builders
│       │   └── version.py              # engine_version_string() etc.
│       ├── evaluator/
│       │   └── compare.py        # semantic comparator for Analysis YAMLs
│       ├── stages/
│       │   ├── stage2_analyze.py # single-UC CLI
│       │   └── run_corpus.py     # corpus iteration CLI
│       ├── scripts/
│       │   └── compare_analyses.py     # CLI wrapping evaluator.compare
│       └── tests/                # 166 tests across 7 suites, all green
├── examples/
│   ├── dcm-reference-profile.yaml      # DCM's consumer profile
│   ├── exemplar-ucs/                   # sample v1.0 use cases
│   └── minimal-consumer/               # synthetic non-DCM consumer
├── specs/                        # versioned specifications
│   ├── 05-use-case-schema.md
│   ├── 07-analysis-output-schema.md
│   └── ...
├── README.md
├── AI-ONBOARDING.md                  # this file
└── DAV-AI-PROMPT.md             # design narrative
```

## Core concepts

### Consumer

The thing being validated. DCM is a consumer; a future BookCatalog service could be another. A consumer ships a content tree (typically two repos):

- **Spec repo** — architecture documentation (Markdown files, ADRs, capability lists). DAV's MCP server clones this and serves the docs to the agent via tool calls.
- **Corpus repo** — use cases (YAML files matching v1.0 UseCase schema), plus a `dav-version.yaml` manifest declaring `consumer_version`.

A consumer also defines a **consumer profile** (`consumer-profile.yaml`) that lists their controlled vocabularies — what `lifecycle_phase` values are allowed for their domain, what `provider_types` exist, etc. The DCM reference profile is built into the engine for backward compatibility; other consumers ship their own.

### Use case (v1.0 schema)

A YAML file describing one architectural test scenario. Required fields:

```yaml
uuid: uc-<slug>                  # must start with "uc-"
handle: <category>/<descriptor>  # e.g. "registration/happy-path"
scenario:
  description: "Free-form prose describing the scenario."
  actor:
    persona: <role>              # e.g. "librarian", "operator"
    profile: <profile>           # must be in consumer profile's profiles[]
  intent: "What should be accomplished."
  success_criteria:              # list of testable conditions
    - ...
  dimensions:
    lifecycle_phase: <value>     # must be in consumer profile vocab
    resource_complexity: <value>
    policy_complexity: <value>
    provider_landscape: <value>
    governance_context: <value>
    failure_mode: <value>
  profile: <profile>             # repeated; future split point
  expected_domain_interactions:  # optional, but improves analysis quality
    - domain: <area>
      interaction: "What happens here"
generated_by:
  mode: regression               # regression | pr_targeted | authoring
  source: human-authored         # or "llm-generated"
tags: []                         # optional
```

Validate a use case against a profile:

```python
import yaml
from dav.core.use_case_schema import UseCase
from dav.core.consumer_profile import load_profile

profile = load_profile(path="examples/dcm-reference-profile.yaml")
with open("path/to/uc.yaml") as f:
    uc = UseCase.from_dict(yaml.safe_load(f))
errors = uc.validate(profile)
assert not errors, errors
```

See `examples/exemplar-ucs/` for two reference UCs (one happy path, one gap discovery).

### Analysis (v1.0 schema)

DAV's output. One Analysis per UC, written as YAML:

```yaml
use_case_uuid: uc-...
analysis_metadata:
  model: <model name>
  timestamp: 2026-04-25T18:00:00Z
  engine_version: <git describe output>
  engine_commit: <full SHA>
  consumer_version: <from consumer's dav-version.yaml>
  stage2_run_id: <uuid>
  wall_time_seconds: 42.7
  sample_seeds: [12345]
  sample_count: 1
  mode: verification              # only in verification-mode merged outputs
components_required: [...]
data_model_touched: [...]
capabilities_invoked: [...]
provider_types_involved: [...]
policy_modes_required: [...]
gaps_identified: [...]
summary:
  verdict: supported | partially_supported | not_supported
  overall_confidence:
    label: high | medium | low
    band: { lower: ..., upper: ... }
  notes: ...
sample_annotations: ...           # only in verification-mode merged outputs
```

Severity uses 5 labels (critical / major / moderate / minor / advisory). Confidence uses 3 (high / medium / low) with optional band metadata for ensemble merging.

### Three runtime modes

DAV stage 2 runs in one of three modes. Default is `verification`:

| Mode | Default samples | Default temp | Output | When to use |
|------|-----------------|--------------|--------|-------------|
| `verification` | 3 | 0.2 | One merged Analysis YAML | CI, regression, the cross-run-comparable default |
| `reproduce` | 1 (forced) | 0.0 | One Analysis YAML | Audit exemplar, debug, bisection — byte-identical on rerun against same UC |
| `explore` | 10 | 0.7 | Per-sample YAMLs + variance.yaml | UC authoring, adversarial poke-testing |

The framing is **predictable correctness**, not strict determinism. Verification mode runs N samples and merges via majority vote (ties resolved conservatively, confidence capped at medium on tied verdicts). Reproduce mode is the closest to deterministic — same UC produces same seed produces same output (modulo timestamp). Explore mode is intentionally noisy to surface variance.

## How to run

### Single-UC analysis

```bash
python -m dav.stages.stage2_analyze \
    --use-case path/to/uc.yaml \
    --output path/to/analysis.yaml \
    --inference-endpoint http://your-vllm:8000/v1 \
    --inference-model qwen3-32b-q8 \
    --mcp-url http://dav-docs-mcp.dav.svc:8080 \
    --consumer-content-path /path/to/consumer/repo \
    --no-enable-thinking \
    --max-tool-calls 30
```

Add `--mode reproduce` for deterministic single-sample. Add `--consumer-profile path/to/profile.yaml` to override the DCM default.

### Whole-corpus analysis

```bash
python -m dav.stages.run_corpus \
    --corpus-path path/to/use-cases/ \
    --output-dir path/to/runs/ \
    --inference-endpoint http://your-vllm:8000/v1 \
    --inference-model qwen3-32b-q8 \
    --mcp-url http://dav-docs-mcp.dav.svc:8080 \
    --consumer-content-path /path/to/consumer/repo \
    --no-enable-thinking
```

Output goes to `<output-dir>/<run-id>/` containing `analyses/<uc-uuid>.yaml` per UC plus `run-summary.yaml`. Failed UCs land in `failures/<uc-uuid>.error.txt` and the run continues (default). Pass `--halt-on-error` to stop on first failure.

### Inside OpenShift (Tekton)

The Ansible role at `ansible/roles/dav/` deploys a Tekton pipeline (`dav-stage2`) that:

1. Clones the consumer's spec repo into `<workspace>/spec/`
2. Clones the consumer's corpus repo into `<workspace>/corpus/`
3. Runs `dav-run-corpus` against `<workspace>/corpus/<corpus-uc-subpath>/`

Trigger via webhook (push or PR), or manually:

```bash
tkn pipeline start dav-stage2 \
    -n dav \
    --workspace name=shared-data,claimName=dav-workspace \
    --param mode=verification \
    --serviceaccount dav-pipeline-sa \
    --use-param-defaults
```

Override defaults: `--param consumer-spec-repo-url=...`, `--param mode=reproduce`, `--param corpus-uc-subpath=use-cases`, etc. See [`docs/operator-runbook.md`](docs/operator-runbook.md) for the full deploy + smoke + real-run workflow.

### Comparing two analyses

```bash
python -m dav.scripts.compare_analyses \
    path/to/analysis-a.yaml \
    path/to/analysis-b.yaml
```

Returns 0 if architecturally equivalent, 1 if changed. Use this as the regression gate.

## Writing a good use case

Read the two exemplars under `examples/exemplar-ucs/` first. Then:

- **`intent`** is one sentence describing what the UC tests. Don't paraphrase the description — say what success looks like.
- **`success_criteria`** is a list of independently testable conditions. If any one fails, the UC fails. Write them as observable conditions, not aspirational outcomes ("audit log records the rejection" beats "the system handles the failure gracefully").
- **`dimensions`** classify the UC. Vocabulary values come from the consumer profile. Pick the most specific value that fits.
- **`expected_domain_interactions`** point the analyzer at where in the spec evidence should exist. Each `domain` should map to a documented area (a doc handle, a section title). The `interaction` is what should happen there.
- **`failure_mode: happy_path`** for happy paths; one of the failure values for gap-discovery UCs. The mode shapes the analyzer's expectations.
- **No backstory.** Anyone reading the UC should understand what it tests without consulting external docs. If you find yourself writing "this is a regression test for the bug we found last week," cut it.

## Adding a new consumer

1. Author a `consumer-profile.yaml` (see `examples/minimal-consumer/consumer-profile.yaml` for shape).
2. Author a corpus of v1.0 UCs in your conventions; validate them with `UseCase.validate(profile)`.
3. Set up two git repos: spec (your architecture docs) and corpus (UCs + `dav-version.yaml`).
4. Copy `ansible/inventory/group_vars/all/vars.local.yaml.example` to `vars.local.yaml` and fill in `consumer_spec_repo_url` and `consumer_corpus_repo_url` (plus the other required site-specific values — inference endpoint, cluster apps domain).
5. Run the Ansible playbook against your OpenShift cluster (see `docs/operator-runbook.md`).

The MCP server (deployed by the playbook) will serve your spec docs to the analyzer; the pipeline will iterate your corpus.

## Common pitfalls

- **The `consumer_version` field on AnalysisMetadata is empty.** Add a `consumer_version: <semver>` line to your consumer's `dav-version.yaml`. The framework looks for that key.
- **Validation fails with "value not in profile vocab".** Either fix the UC's value or add the value to your consumer profile (then commit the profile change to your consumer repo).
- **Verification mode N=1 logs a warning.** Intentional. Use `--mode reproduce` for cheaper single-sample runs; verification with N=1 is rarely what you want.
- **`--cache-prompt` breaks reproducibility.** It's off by default for this reason. Only turn it on for explore mode if you want throughput over byte-identical sampling.
- **Stage 2 returns a `partially_supported` verdict on what looks like a happy path.** Read the `gaps_identified` section. Often the architecture *does* support the UC but the docs don't articulate it — the verdict is honest about what the spec says, not what the implementation could do.

## When something goes wrong

- Check the run summary first (`run-summary.yaml`). It records per-UC status with error messages for failures.
- Failed UCs have a `failures/<uc-uuid>.error.txt` with the full traceback or error string.
- For analyzer-level issues (timeouts, MCP unreachable), check the engine container logs: `oc logs pod/dav-engine-...`
- For schema validation issues, validate your corpus locally: load each UC with `UseCase.from_dict()` and call `.validate(profile)` against your consumer profile. Profile vocab mismatches surface as plain strings.
- For "the LLM is making things up" issues, run `--mode reproduce` once to capture a clean exemplar, then `--mode explore` against the same UC to see how much variance exists. High variance suggests the spec content is ambiguous; low variance with wrong content suggests the spec content is misleading.

## Where to look first when extending DAV

- **New UC schema field** → `engine/src/dav/core/use_case_schema.py`
- **New CLI flag** → `engine/src/dav/stages/stage2_analyze.py` and/or `run_corpus.py`
- **New consumer-profile field** → `engine/src/dav/core/consumer_profile.py` + `examples/dcm-reference-profile.yaml`
- **Prompt changes** → `engine/src/dav/ai/prompts.py` (and bump `STAGE2_PROMPT_VERSION`)
- **New analyzer behavior** → `engine/src/dav/ai/agent.py` (the tool-use loop)
- **New deployment artifacts** → `ansible/roles/dav/templates/`

For deeper architectural context, read `DAV-AI-PROMPT.md`. For the locked design decisions, read the ADRs.
