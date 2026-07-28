"""The trigger→pipeline param contract: nothing silently dropped at the boundary.

Two boundary-drop incidents in two days established the class:
  - oc start-build --build-arg silently never arrived (#98);
  - the stage2-context param was declared + consumed by the TASK but never
    declared/forwarded by the PIPELINE — so every "prompt context applied" run
    actually ran the baseline prompt, and the E2 battery A/B measured nothing.

This test pins the contract mechanically: every param name the console trigger
appends (validations.py `params.append({"name": ...})`) must be BOTH declared
in the stage-2 Pipeline template's params AND referenced ($(params.X)) in its
body — declared-but-unforwarded is exactly the E2 failure.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
VALIDATIONS = REPO / "review-console" / "api" / "app" / "validations.py"
PIPELINE = REPO / "ansible" / "roles" / "dav" / "templates" / "pipeline-stage2.yaml.j2"


def _trigger_param_names() -> set[str]:
    src = VALIDATIONS.read_text()
    names = set(re.findall(r'params\.append\(\{"name":\s*"([a-z0-9-]+)"', src))
    assert names, "no trigger params found — the extraction regex rotted"
    return names


def test_every_trigger_param_is_declared_and_consumed_by_the_pipeline():
    text = PIPELINE.read_text()
    declared = set(re.findall(r'^\s*- name:\s*([a-z0-9-]+)\s*$', text, re.M))
    referenced = set(re.findall(r'\$\(params\.([a-z0-9-]+)\)', text))
    missing_decl = sorted(n for n in _trigger_param_names() if n not in declared)
    assert not missing_decl, (
        f"trigger sends param(s) the Pipeline never declares — they are silently "
        f"dropped at the PipelineRun boundary: {missing_decl}")
    unforwarded = sorted(n for n in _trigger_param_names() if n not in referenced)
    assert not unforwarded, (
        f"Pipeline declares but never forwards param(s) — the task's default "
        f"silently wins: {unforwarded}")
