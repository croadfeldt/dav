"""The pipeline/Task parameter seam.

Two production incidents came from this seam, both invisible to every other test
because each file is valid on its own:

  1. dav-stage2 forwarded `max-tokens` / `request-timeout-seconds` before the
     Task declared them — Tekton rejects the whole PipelineRun at admission.
  2. A Task-level `default: 1700` was silently overridden because the *pipeline*
     also declared the param with `default: ""` and passed it down. A pipeline
     param default is not a fallback, it is a value that gets passed.

So these tests render the Jinja templates and check the two directions that
Tekton itself only checks at run time, in the cluster, when it is too late.
"""
from pathlib import Path
import re

import jinja2
import pytest

yaml = pytest.importorskip("yaml")

_ROLE = Path(__file__).resolve().parents[4] / "ansible" / "roles" / "dav" / "templates"
_TASK = _ROLE / "tekton-tasks" / "dav-run-corpus.yaml.j2"
_PIPELINE = _ROLE / "pipeline-stage2.yaml.j2"

pytestmark = pytest.mark.skipif(
    not (_TASK.exists() and _PIPELINE.exists()),
    reason="ansible role templates not present in this checkout")

# Values are irrelevant to the seam; ChainableUndefined keeps any var we forgot
# from exploding so the test fails on real seam problems, not on fixture drift.
_VARS = dict(inference_primary_endpoint="http://inference/v1",
             inference_primary_model="model",
             inference_topology="topo",
             dav_namespace="dav",
             dav_stage2_request_timeout_seconds=1700,
             dav_api_token_audience="dav-api")


def _render(path: Path) -> dict:
    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    return yaml.safe_load(env.from_string(path.read_text()).render(**_VARS))


@pytest.fixture(scope="module")
def rendered():
    return _render(_TASK), _render(_PIPELINE)


def _run_corpus_tasks(pipeline):
    return [t for t in pipeline["spec"]["tasks"]
            if "run-corpus" in (t.get("taskRef") or {}).get("name", "")]


def test_templates_render_to_the_expected_kinds(rendered):
    task, pipeline = rendered
    assert task["kind"] == "Task"
    assert pipeline["kind"] == "Pipeline"


def test_every_forwarded_param_is_declared_on_the_task(rendered):
    """Incident 1: Tekton rejects the PipelineRun at admission, so a missing
    declaration takes down every run, not just the one using the new param."""
    task, pipeline = rendered
    declared = {p["name"] for p in task["spec"]["params"]}
    for t in _run_corpus_tasks(pipeline):
        undeclared = sorted({p["name"] for p in t.get("params", [])} - declared)
        assert not undeclared, f"pipeline forwards params the Task does not declare: {undeclared}"


def test_every_referenced_pipeline_param_is_declared_on_the_pipeline(rendered):
    """The mirror case: $(params.x) in a forwarded value with no pipeline-level
    declaration of x."""
    _, pipeline = rendered
    declared = {p["name"] for p in pipeline["spec"]["params"]}
    for t in _run_corpus_tasks(pipeline):
        for p in t.get("params", []):
            for ref in re.findall(r"\$\(params\.([a-z0-9-]+)\)", str(p.get("value", ""))):
                assert ref in declared, f"$(params.{ref}) is not declared on the Pipeline"


@pytest.mark.parametrize("name", ["pass1-inference-endpoint", "pass1-inference-model"])
def test_optional_routing_params_default_empty_at_both_levels(rendered, name):
    """Incident 2, applied to per-stage routing. The engine's "no pass-1 backend
    = single model" branch is selected by ABSENCE. A non-empty default anywhere
    in the chain would silently route pass 1 away from the configured model on
    every run that never asked for routing."""
    task, pipeline = rendered
    for obj, where in ((task, "Task"), (pipeline, "Pipeline")):
        decl = next((p for p in obj["spec"]["params"] if p["name"] == name), None)
        assert decl is not None, f"{name} not declared on the {where}"
        assert decl.get("default", "") == "", (
            f"{where} default for {name} must be empty — a default here is passed "
            f"down as a value and turns routing on for runs that never asked")
