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
import shutil
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


def delete_run_dir(run_id: str) -> bool:
    """Delete a run's results directory under the workspace root. Returns True if
    a directory was removed. Path-safe: `run_id` must be a bare directory name
    that resolves strictly inside the results root (guards against traversal)."""
    if not run_id or "/" in run_id or "\\" in run_id or run_id in (".", ".."):
        return False
    root = _results_root().resolve()
    target = (root / run_id).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return False  # escapes the results root — refuse
    if target == root or not target.is_dir():
        return False
    shutil.rmtree(target)
    return True


def _safe_under(root, *parts):
    """Join `parts` under `root` and return the resolved Path only if it stays
    strictly inside `root`. Returns None on traversal (a `..`/absolute component
    that escapes). Use for any filesystem read built from request input."""
    root = root.resolve()
    try:
        target = root.joinpath(*[str(p) for p in parts]).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        return None
    return target


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


def list_inflight_progress(max_scan: int = 60) -> list[dict]:
    """All in-flight ``run-progress.yaml`` files (one per active workspace run-dir), each parsed
    and tagged with ``_run_dir``. The engine doesn't record the PipelineRun name, so concurrent
    runs are correlated to distinct dirs by start time — this returns every candidate so the
    caller can assign them uniquely (``find_progress_near`` returns only the single nearest, which
    collides when two runs overlap). Bounded to the newest ``max_scan`` dirs; only non-terminal
    (``running``) progress is returned."""
    root = _results_root()
    out: list[dict] = []
    if not root.exists():
        return out
    scanned = 0
    for run_dir in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
        if not run_dir.is_dir():
            continue
        scanned += 1
        if scanned > max_scan:
            break
        prog_path = run_dir / "run-progress.yaml"
        if not prog_path.exists():
            continue
        p = _safe_load(prog_path)
        if not p or p.get("phase") not in (None, "running"):
            continue
        p["_run_dir"] = run_dir.name
        out.append(p)
    return out


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
    # Path-traversal guard: file_name is request input and must not escape the
    # run's turns/ dir (`../../etc/passwd`, absolute paths, etc.).
    path = _safe_under(_results_root(), run_id, "turns", file_name)
    if path is None or not path.is_file():
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


def get_failures(run_id: str) -> list[dict]:
    """Read a run's `failures/<uuid>.error.txt` files.

    Returns [{uc_uuid, uc_handle, error_text}] — the durable failure record the
    self-improvement loop's failure taxonomy consumes. The uuid comes from the
    filename; the handle is parsed from the `UC: <handle> (<uuid>)` header line.
    """
    fail_dir = _results_root() / run_id / "failures"
    if not fail_dir.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(fail_dir.glob("*.error.txt")):
        try:
            text = p.read_text()
        except Exception as e:
            log.warning("failure read error %s: %s", p, e)
            continue
        uc_uuid = p.name[: -len(".error.txt")]
        handle = None
        first = text.split("\n", 1)[0]
        if first.startswith("UC:"):
            # "UC: <handle> (<uuid>)"
            body = first[3:].strip()
            handle = body.split(" (", 1)[0].strip() or None
        out.append({"uc_uuid": uc_uuid, "uc_handle": handle, "error_text": text})
    return out


def _extract_verdict(analysis: dict) -> Optional[str]:
    """Pull the top-level verdict out of an analysis YAML dict.

    Analysis YAMLs written by the engine nest the verdict under
    summary.verdict. Explore-mode multi-sample analyses surface it via
    sample_annotations.consensus_verdict or the first sample's verdict.
    """
    if not analysis:
        return None
    # Single / reproduce mode: summary.verdict
    summary = analysis.get("summary")
    if isinstance(summary, dict):
        v = summary.get("verdict")
        if v:
            return v
    # Explore mode: consensus from sample_annotations
    sa = analysis.get("sample_annotations")
    if isinstance(sa, dict):
        v = sa.get("consensus_verdict")
        if v:
            return v
    # Explore mode: first sample's verdict
    for sample in analysis.get("samples") or []:
        if isinstance(sample, dict):
            s = sample.get("summary") or {}
            v = s.get("verdict") if isinstance(s, dict) else None
            if v:
                return v
    return None


def get_run_summary_enriched(run_id: str) -> Optional[dict]:
    """Return run-summary.yaml enriched with per-UC verdict from analysis files.

    The engine's run-summary.yaml only records status (success/failed) per UC.
    Verdicts live in analyses/<uc_uuid>.yaml. This function patches each UC
    entry with its verdict so the Results UC list can display and group by it.
    """
    summary = get_run_summary(run_id)
    if summary is None:
        return None
    run_dir = _results_root() / run_id
    analyses_dir = run_dir / "analyses"
    for uc in summary.get("ucs") or []:
        uc_uuid = uc.get("uc_uuid")
        if not uc_uuid or uc.get("verdict"):
            continue
        # Single-file analysis (verification / reproduce)
        single = analyses_dir / f"{uc_uuid}.yaml"
        if single.exists():
            data = _safe_load(single)
            if data:
                v = _extract_verdict(data)
                if v:
                    uc["verdict"] = v
            continue
        # Explore mode: directory of sample files
        explore_dir = analyses_dir / uc_uuid
        if explore_dir.is_dir():
            # Aggregate from sample_annotations in first sample or variance
            vp = explore_dir / "variance.yaml"
            if vp.exists():
                vdata = _safe_load(vp)
                if vdata:
                    v = _extract_verdict({"sample_annotations": vdata.get("sample_annotations")})
                    if v:
                        uc["verdict"] = v
                        continue
            for sf in sorted(explore_dir.glob("sample-*.yaml")):
                sdata = _safe_load(sf)
                if sdata:
                    v = _extract_verdict(sdata)
                    if v:
                        uc["verdict"] = v
                        break
    return summary


def compare_runs(run_id_a: str, run_id_b: str) -> dict:
    """Compare two runs side-by-side.

    Returns per-UC verdict diff, gap diff, and summary-level deltas.
    Both run_ids must correspond to directories found under results_root.
    """
    sum_a = get_run_summary_enriched(run_id_a)
    sum_b = get_run_summary_enriched(run_id_b)
    if sum_a is None:
        raise FileNotFoundError(f"run {run_id_a!r} not found")
    if sum_b is None:
        raise FileNotFoundError(f"run {run_id_b!r} not found")

    # Index UCs by uuid for quick lookup
    ucs_a = {u["uc_uuid"]: u for u in (sum_a.get("ucs") or [])}
    ucs_b = {u["uc_uuid"]: u for u in (sum_b.get("ucs") or [])}

    all_uuids = sorted(set(ucs_a) | set(ucs_b))

    uc_diffs = []
    verdict_changes = 0
    for uuid in all_uuids:
        ua = ucs_a.get(uuid)
        ub = ucs_b.get(uuid)
        va = (ua or {}).get("verdict") or ("failed" if (ua or {}).get("status") == "failed" else None)
        vb = (ub or {}).get("verdict") or ("failed" if (ub or {}).get("status") == "failed" else None)
        changed = va != vb
        if changed:
            verdict_changes += 1

        # Pull gap IDs from analysis files for a richer diff
        gaps_a = _get_gap_ids(run_id_a, uuid) if ua else []
        gaps_b = _get_gap_ids(run_id_b, uuid) if ub else []
        gaps_added   = [g for g in gaps_b if g not in gaps_a]
        gaps_removed = [g for g in gaps_a if g not in gaps_b]

        uc_diffs.append({
            "uc_uuid":     uuid,
            "uc_handle":   (ua or ub or {}).get("uc_handle"),
            "verdict_a":   va,
            "verdict_b":   vb,
            "changed":     changed,
            "gaps_added":  gaps_added,
            "gaps_removed": gaps_removed,
            "wall_time_a": (ua or {}).get("wall_time_seconds"),
            "wall_time_b": (ub or {}).get("wall_time_seconds"),
            "only_in_a":   ub is None,
            "only_in_b":   ua is None,
        })

    def _safe_delta(a, b):
        if a is not None and b is not None:
            return round(b - a, 2)
        return None

    return {
        "run_a": run_id_a,
        "run_b": run_id_b,
        "summary_a": {k: sum_a.get(k) for k in
                      ("mode", "started_at", "finished_at", "runner_total_seconds",
                       "total_ucs", "successful", "failed", "total_samples")},
        "summary_b": {k: sum_b.get(k) for k in
                      ("mode", "started_at", "finished_at", "runner_total_seconds",
                       "total_ucs", "successful", "failed", "total_samples")},
        "delta": {
            "wall_time_seconds": _safe_delta(sum_a.get("runner_total_seconds"),
                                             sum_b.get("runner_total_seconds")),
            "successful": _safe_delta(sum_a.get("successful"), sum_b.get("successful")),
            "failed":     _safe_delta(sum_a.get("failed"),     sum_b.get("failed")),
            "verdict_changes": verdict_changes,
        },
        "uc_diffs": uc_diffs,
    }


def _gaps_of(data: Optional[dict]) -> set:
    """Stable identities of the gaps in one analysis dict. Gap entries carry no
    `gap_id` field — they're keyed by `title` — so identity is the normalised
    title (`gap_id` is still honoured first in case a future schema adds it)."""
    if not data:
        return set()
    out = set()
    for g in data.get("gaps_identified") or []:
        if not isinstance(g, dict):
            continue
        key = g.get("gap_id") or g.get("title")
        if key:
            out.add(" ".join(str(key).lower().split()))
    return out


def _sample_gap_sets(run_id: str, uc_uuid: str) -> list:
    """Gap-ID set per analysis sample for a UC: one entry in verification mode,
    one per `sample-*.yaml` in explore mode. Empty list when the UC has no
    analysis (e.g. it failed)."""
    run_dir = _results_root() / run_id
    single = run_dir / "analyses" / f"{uc_uuid}.yaml"
    if single.exists():
        d = _safe_load(single)
        return [_gaps_of(d)] if d is not None else []
    explore_dir = run_dir / "analyses" / uc_uuid
    if explore_dir.is_dir():
        sets = []
        for sf in sorted(explore_dir.glob("sample-*.yaml")):
            d = _safe_load(sf)
            if d is not None:
                sets.append(_gaps_of(d))
        return sets
    return []


def _get_gap_ids(run_id: str, uc_uuid: str) -> list[str]:
    """Gap IDs from a UC's analysis (verification: the file; explore: 1st sample)."""
    sets = _sample_gap_sets(run_id, uc_uuid)
    return sorted(sets[0]) if sets else []


def _mean_pairwise_jaccard(sets: list) -> float:
    """Mean Jaccard overlap across all sample pairs — 1.0 = every sample found
    the same gaps (perfectly consistent exploration), lower = the model wanders."""
    n = len(sets)
    if n < 2:
        return 1.0
    vals = []
    for i in range(n):
        for j in range(i + 1, n):
            a, b = sets[i], sets[j]
            u = a | b
            vals.append(len(a & b) / len(u) if u else 1.0)
    return sum(vals) / len(vals)


def get_run_exploration(run_id: str) -> Optional[dict]:
    """Exploration-depth / consistency metrics for a run — the quality lever the
    2026-05-30 72B eval isolated (the 72B explored fewer distinct gaps than the
    32B despite being larger). Aggregates gap IDs across the run's UCs:

      distinct_gaps     breadth: unique gap IDs found across all UCs
      total_gaps        depth: sum of per-UC distinct gaps
      mean_gaps_per_uc  total_gaps / UCs analysed
      ucs_with_gaps     UCs that surfaced >=1 gap
      consistency       explore mode only: mean cross-sample gap-set Jaccard
                        (1.0 = identical findings every sample); None when there
                        is a single verification sample (nothing to compare).

    Counts are NOT a correctness signal on their own (a model can inflate them by
    hallucinating gaps), so callers treat this as advisory next to success_rate.
    """
    summ = get_run_summary_enriched(run_id)
    if not summ:
        return None
    total_gaps = ucs_with_gaps = ucs_analyzed = 0
    distinct: set = set()
    consistencies: list = []
    for uc in (summ.get("ucs") or []):
        uuid = uc.get("uc_uuid")
        if not uuid:
            continue
        gap_sets = _sample_gap_sets(run_id, uuid)
        if not gap_sets:
            continue
        ucs_analyzed += 1
        uc_union = set().union(*gap_sets)
        total_gaps += len(uc_union)
        distinct |= uc_union
        if uc_union:
            ucs_with_gaps += 1
        if len(gap_sets) > 1:
            consistencies.append(_mean_pairwise_jaccard(gap_sets))
    return {
        "ucs_analyzed": ucs_analyzed,
        "total_gaps": total_gaps,
        "distinct_gaps": len(distinct),
        "mean_gaps_per_uc": round(total_gaps / ucs_analyzed, 2) if ucs_analyzed else 0.0,
        "ucs_with_gaps": ucs_with_gaps,
        "consistency": round(sum(consistencies) / len(consistencies), 3) if consistencies else None,
    }


def _load_analysis_samples(run_id: str, uc_uuid: str) -> list[dict]:
    """Full analysis dict per sample for a UC (verification: the single file;
    explore: each sample-*.yaml). Empty when the UC has no analysis (e.g. failed)."""
    run_dir = _results_root() / run_id
    single = run_dir / "analyses" / f"{uc_uuid}.yaml"
    if single.exists():
        d = _safe_load(single)
        return [d] if isinstance(d, dict) else []
    explore_dir = run_dir / "analyses" / uc_uuid
    if explore_dir.is_dir():
        out = []
        for sf in sorted(explore_dir.glob("sample-*.yaml")):
            d = _safe_load(sf)
            if isinstance(d, dict):
                out.append(d)
        return out
    return []


def get_run_shallowness(run_id: str, thresholds=None) -> Optional[dict]:
    """Per-UC shallow-analysis flags for a run's *successful* analyses + a rollup.

    The failure-driven self-improvement loop never sees a successful-but-thin
    analysis. This scores each UC's grounding density (distinct spec refs,
    ungrounded-claim ratio, tool calls — see app.shallowness) and flags the
    shallow ones; shallow UCs sort to the front. Advisory only. Returns None when
    the run has no readable summary.
    """
    from . import shallowness as _sh
    summ = get_run_summary_enriched(run_id)
    if not summ:
        return None
    th = thresholds or _sh.ShallowThresholds()
    ucs_out: list[dict] = []
    n_eligible = n_shallow = 0
    spec_ref_sum = 0.0
    for uc in (summ.get("ucs") or []):
        uuid = uc.get("uc_uuid")
        if not uuid:
            continue
        samples = _load_analysis_samples(run_id, uuid)
        if not samples:
            continue
        scored = [_sh.score_and_flag(s, th) for s in samples]
        agg = _sh.aggregate_samples(scored)
        ucs_out.append({"uc_uuid": uuid, "uc_handle": uc.get("uc_handle"),
                        "run_verdict": uc.get("verdict"), **agg})
        if agg.get("eligible"):
            n_eligible += 1
            if isinstance(agg.get("distinct_spec_refs"), (int, float)):
                spec_ref_sum += agg["distinct_spec_refs"]
            if agg.get("shallow"):
                n_shallow += 1
    # Shallow UCs first, then ascending grounding (thinnest at the top).
    ucs_out.sort(key=lambda r: (
        not r.get("shallow"),
        r["distinct_spec_refs"] if isinstance(r.get("distinct_spec_refs"), (int, float)) else 1e9,
    ))
    return {
        "run_id": run_id,
        "thresholds": th.to_dict(),
        "ucs_analyzed": len(ucs_out),
        "ucs_eligible": n_eligible,
        "ucs_shallow": n_shallow,
        "shallow_fraction": round(n_shallow / n_eligible, 3) if n_eligible else None,
        "mean_distinct_spec_refs": round(spec_ref_sum / n_eligible, 2) if n_eligible else None,
        "ucs": ucs_out,
    }


def get_analysis(run_id: str, uc_uuid: str) -> Optional[dict]:
    """Return the analysis output for a specific UC within a run.

    Handles both verification/reproduce mode (single .yaml file) and
    explore mode (directory of sample files + variance.yaml).
    """
    run_dir = _results_root() / run_id

    # Path-traversal guard: uc_uuid comes from a `:path` route so it can contain
    # `/` and `..` — confine both lookups strictly under the run's analyses/ dir.
    single = _safe_under(_results_root(), run_id, "analyses", f"{uc_uuid}.yaml")
    if single is not None and single.exists():
        data = _safe_load(single)
        if data is not None:
            data["_source"] = "single"
        return data

    # Explore mode: <run-dir>/analyses/<uuid>/
    explore_dir = _safe_under(_results_root(), run_id, "analyses", uc_uuid)
    if explore_dir is not None and explore_dir.is_dir():
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
