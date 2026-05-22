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


def find_progress_near(started_at_iso: str, tolerance_seconds: int = 120) -> Optional[dict]:
    """Find the in-flight run-progress.yaml whose started_at is closest to the
    supplied PipelineRun start time. Returns the parsed dict (with the run_dir
    name included), or None if no recent progress file matches.

    The workspace run_id (`2026-05-22T15-15-56Z-<hash>`) is timestamp-prefixed
    so a sorted descending walk lands on the newest run first; we then check
    the start time inside run-progress.yaml is within `tolerance_seconds` of
    the supplied PipelineRun start time. This is the lightweight correlation
    between the PipelineRun and the workspace run-dir.
    """
    from datetime import datetime
    try:
        target = datetime.fromisoformat(started_at_iso.replace("Z", "+00:00"))
    except Exception:
        return None
    root = _results_root()
    if not root.exists():
        return None
    for run_dir in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
        if not run_dir.is_dir():
            continue
        prog_path = run_dir / "run-progress.yaml"
        if not prog_path.exists():
            continue
        p = _safe_load(prog_path)
        if not p:
            continue
        try:
            st = datetime.fromisoformat(str(p.get("started_at", "")).replace("Z", "+00:00"))
        except Exception:
            continue
        if abs((st - target).total_seconds()) <= tolerance_seconds:
            p["_run_dir"] = run_dir.name
            return p
        # Once we're past the target by more than tolerance, the next (older)
        # ones can only be further away — short-circuit.
        if st < target and (target - st).total_seconds() > tolerance_seconds:
            break
    return None


def list_turns_files(run_id: str) -> list[str]:
    """List the turns/*.jsonl files in a run directory. Each corresponds to
    one (UC, sample) — filename pattern: <uc_uuid>.seed-<N>.jsonl."""
    turns_dir = _results_root() / run_id / "turns"
    if not turns_dir.is_dir():
        return []
    return sorted(p.name for p in turns_dir.iterdir()
                  if p.is_file() and p.suffix == ".jsonl")


def tail_turns(run_id: str, file_name: str, since_offset: int = 0,
               max_records: int = 500) -> dict:
    """Read a turns JSONL file from `since_offset` bytes onward; parse new
    records. Returns {records, next_offset, total_lines}. Designed for
    incremental tail-polling — UI keeps the offset between calls.
    """
    import json as _json
    path = _results_root() / run_id / "turns" / file_name
    if not path.is_file():
        return {"records": [], "next_offset": since_offset, "total_lines": 0,
                "error": "not found"}
    try:
        size = path.stat().st_size
        # If the file was truncated/rotated, reset
        if since_offset > size:
            since_offset = 0
        with path.open("rb") as f:
            f.seek(since_offset)
            raw = f.read()
        next_offset = since_offset + len(raw)
        text = raw.decode("utf-8", errors="replace")
        # Only emit complete lines; partial trailing line stays in the file
        # and gets picked up on the next poll once a newline is written.
        if text and not text.endswith("\n"):
            # Reduce next_offset so we re-read the partial line next time
            last_nl = text.rfind("\n")
            if last_nl >= 0:
                next_offset = since_offset + last_nl + 1
                text = text[:last_nl + 1]
            else:
                # No newline at all — wait for one
                return {"records": [], "next_offset": since_offset,
                        "total_lines": 0}
        records: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(_json.loads(line))
            except Exception:
                # Skip malformed lines but keep going
                continue
            if len(records) >= max_records:
                break
        return {"records": records, "next_offset": next_offset,
                "total_lines": len(records)}
    except Exception as e:
        log.warning("tail_turns error %s/%s: %s", run_id, file_name, e)
        return {"records": [], "next_offset": since_offset,
                "total_lines": 0, "error": str(e)}


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
