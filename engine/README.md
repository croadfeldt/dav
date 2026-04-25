# DAV Engine

Python implementation of DAV's inference pipeline: stages, LLM client,
tool-use agent, MCP client, ensemble merger, and core data types.

## Layout

```
engine/
├── Containerfile       Container image spec
├── pyproject.toml      Package declaration (src layout)
├── requirements.txt    Runtime dependencies (authoritative)
├── README.md
└── src/
    └── dav/            The `dav` Python package
        ├── __init__.py
        ├── ai/         LLM client, agent, MCP tools, prompts
        ├── core/       Schemas, profiles, ensemble merger, version
        ├── evaluator/  Analysis comparison (compare.py)
        ├── scripts/    CLI helpers (compare, migrate)
        ├── stages/     Pipeline stage entry points
        │   ├── stage2_analyze.py    Single-UC analysis
        │   └── run_corpus.py        Whole-corpus iteration
        └── tests/      Test suite (200+ tests)
```

## Install

```bash
cd engine/
pip install -e .
```

After install, modules are importable as `dav.ai.client`, `dav.core.use_case_schema`, etc.

For container builds, the Ansible role at `ansible/roles/dav/tasks/engine.yaml`
ships this directory to the OpenShift BuildConfig as build context.

## Running stages directly

Single-UC analysis:

```bash
python -m dav.stages.stage2_analyze \
    --use-case path/to/case.yaml \
    --inference-endpoint http://host/v1 \
    --inference-model qwen \
    --mcp-url http://mcp.host:8080 \
    --output path/to/analysis.yaml
```

Whole-corpus analysis:

```bash
python -m dav.stages.run_corpus \
    --corpus-path path/to/use-cases/ \
    --output-dir path/to/runs/ \
    --inference-endpoint http://host/v1 \
    --inference-model qwen \
    --mcp-url http://mcp.host:8080
```

See `../AI-ONBOARDING.md` for full operational guidance.

## Tests

Each test suite is runnable standalone. No pytest dependency.

```bash
python -m dav.tests.test_consumer_profile
python -m dav.tests.test_schema_v1
python -m dav.tests.test_ensemble
python -m dav.tests.test_explore
python -m dav.tests.test_stage2_orchestration
python -m dav.tests.test_version
python -m dav.tests.test_migrate_uc_to_v1
python -m dav.tests.test_run_corpus
```

All tests should report `OK: <N> tests passed`.

## References

- `../AI-ONBOARDING.md` — operational onboarding
- `../DAV-AI-PROMPT.md` — design narrative and reconstruction recipe
- `../specs/05-use-case-schema.md` — Use Case input contract
- `../specs/07-analysis-output-schema.md` — Analysis output contract
