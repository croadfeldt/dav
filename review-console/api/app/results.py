"""DAV workspace results reader.

Scans the shared pipeline workspace PVC (mounted read-only) for run-summary.yaml
and per-UC analysis YAML files written by dav.stages.run_corpus.

Workspace layout expected:
  <DAV_WORKSPACE_PATH>/results/<run-id>/
      run-summary.yaml
      analyses/<uc-uuid>.yaml          (verification / reproduce mode)
      analyses/<uc-uuid>/              (explore mode)
          sample-00.yaml
          sample-01.yaml
          variance.yaml
      failures/<uc-uuid>.error.txt
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger("dav-review-api.results")

WORKSPACE_PATH = os.environ.get("DAV_WORKSPACE_PATH", "/workspace")
RESULTS_SUBDIR = os.environ.get("DAV_RESULTS_SUBDIR", "results")


def _results_root() -> Path:
    return Path(WORKSPACE_PATH) / RESULTS_SUBDIR


def is_available() -> bool:
    return _results_root().exists()


def _safe_load(path: Path) -> Optional[dict]:
    try:
        return yaml.safe_load(path.read_text())
    except Exception as e:
        log.warning("YAML parse error at %s: %s", path, e)
        return None


def list_runs() -> list[dict]:
    """List all completed run directories, newest first."""
    root = _results_root()
    if not root.exists():
        return []
    runs = []
    for run_dir in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
        if not run_dir.is_dir():
            continue
        summary_path = run_dir / "run-summary.yaml"
        if not summary_path.exists():
            continue
        s = _safe_load(summary_path)
        if s is None:
            continue
        runs.append({
            "run_id":                  s.get("run_id", run_dir.name),
            "mode":                    s.get("mode"),
            "started_at":              s.get("started_at"),
            "finished_at":             s.get("finished_at"),
            "runner_total_seconds":    s.get("runner_total_seconds"),
            "total_ucs":               s.get("total_ucs", 0),
            "successful":              s.get("successful", 0),
            "failed":                  s.get("failed", 0),
            "total_samples":           s.get("total_samples", 0),
        })
    return runs


def get_run_summary(run_id: str) -> Optional[dict]:
    """Return the full run-summary.yaml dict for a given run_id."""
    path = _results_root() / run_id / "run-summary.yaml"
    if not path.exists():
        return None
    return _safe_load(path)


def get_analysis(run_id: str, uc_uuid: str) -> Optional[dict]:
    """Return the analysis output for a specific UC within a run.

    Handles both verification/reproduce mode (single .yaml file) and
    explore mode (directory of sample files + variance.yaml).
    """
    run_dir = _results_root() / run_id

    # Verification / reproduce: <run-dir>/analyses/<uuid>.yaml
    single = run_dir / "analyses" / f"{uc_uuid}.yaml"
    if single.exists():
        data = _safe_load(single)
        if data is not None:
            data["_source"] = "single"
        return data

    # Explore mode: <run-dir>/analyses/<uuid>/
    explore_dir = run_dir / "analyses" / uc_uuid
    if explore_dir.is_dir():
        samples = []
        for f in sorted(explore_dir.glob("sample-*.yaml")):
            s = _safe_load(f)
            if s:
                samples.append(s)
        variance = None
        vp = explore_dir / "variance.yaml"
        if vp.exists():
            variance = _safe_load(vp)
        return {"_source": "explore", "samples": samples, "variance": variance}

    # Failure case: check failures/ dir
    safe_uuid = uc_uuid.replace("/", "_")
    failure = run_dir / "failures" / f"{safe_uuid}.error.txt"
    if failure.exists():
        return {"_source": "failure", "error": failure.read_text()}

    return None
