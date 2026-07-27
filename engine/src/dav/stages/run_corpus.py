"""
Stage 2 corpus runner — iterate over a directory of v1.0 use cases,
analyze each one, and write per-UC outputs plus a unified run summary.

. Engine-side iteration in a single Python process: one Tekton
task wraps this CLI; per-UC failure isolation is handled in the loop.
No Tekton matrix, no per-UC PipelineRun fan-out.

Output layout:

    <output_dir>/<run-id>/
        analyses/
            <uc-uuid>.yaml          # one file per successful UC
        failures/
            <uc-uuid>.error.txt     # one file per failed UC (when any)
        run-summary.yaml            # top-level summary

run-id is "<iso-timestamp>-<corpus-hash>" where corpus-hash is the first
7 hex chars of SHA-256 over the sorted corpus file list. Stable across
runs against the same corpus contents (modulo timestamp); bumps when
files are added/removed/renamed. NOT a content hash — modifying a UC's
content doesn't change the corpus hash. The run-id encodes which UCs
were run, not what was in them.

CLI usage:

    python -m dav.stages.run_corpus \\
        --corpus-path path/to/use-cases \\
        --output-dir path/to/runs \\
        --inference-endpoint http://host/v1 \\
        --inference-model qwen \\
        --mcp-url http://mcp:8080 \\
        --consumer-content-path path/to/consumer-repo \\
        [--mode verification|reproduce|explore] \\
        [--halt-on-error]

Per-UC mode behavior follows stage2_analyze:
- verification: N samples (default 3), low temperature, ensemble merge
- reproduce: N=1, greedy, seed derived from UC uuid
- explore: N samples (default 10), high temperature, no merge,
  outputs go to analyses/<uc-uuid>/sample-NN.yaml + variance.yaml

Failure semantics:
- Default: continue-on-error. Failed UCs get a failures/<uuid>.error.txt
  entry plus a status: failed line in run-summary.yaml. Other UCs proceed.
- --halt-on-error: stop the corpus run on the first UC failure.

Exit code: 0 if all UCs succeeded, 1 if any failed.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from dav.ai.agent import AgentConfig
from dav.ai.client import EndpointConfig, InferenceClient, InferenceError
from dav.ai.mcp_tools import McpClient
from dav.core.use_case_schema import UseCase, Analysis
from dav.core.ensemble import merge_analyses
from dav.core.explore import build_variance_report
from dav.stages.stage2_analyze import run_samples, derive_seed_from_uuid

log = logging.getLogger(__name__)

# Mode-specific defaults — kept in sync with stage2_analyze._DEFAULT_*.
# Duplicating intentionally: run_corpus deliberately doesn't import
# private constants from stage2_analyze. If these defaults ever drift
# between the two callers, the divergence is the bug to fix, not this
# duplication.
_DEFAULT_SAMPLE_COUNT = {
    "verification": 3,
    "reproduce": 1,
    "explore": 10,
}
_DEFAULT_TEMPERATURE = {
    "verification": 0.2,
    "reproduce": 0.0,
    "explore": 0.7,
}
_DEFAULT_CACHE_PROMPT = {
    "verification": True,
    "reproduce": False,
    "explore": True,
}
# Fallback per-turn output budget when neither --max-tokens nor the
# use_profile provides one. Kept at the historical run_corpus default.
_DEFAULT_MAX_TOKENS = 4096

# Assumed steady-state decode throughput (tok/s) on the reference stack
# (dual R9700, Qwen3-32B Q8 observed ~20-26 tok/s). Used ONLY for the
# advisory run-start warning that flags max_tokens/request-timeout math
# that cannot work (a max_tokens budget the endpoint can't emit inside
# the per-request timeout); it is never a limit.
_ASSUMED_DECODE_TOK_PER_S = 20

_DEFAULT_SAMPLER_PARAMS = {
    # Diagnosed 2026-04-26: some llama.cpp inference servers ship
    # --top-k 1 as a CLI default, making them greedy regardless of
    # per-request temperature or seed unless top_k is explicitly overridden.
    # Per-request fields override CLI defaults per-field; unsent fields keep
    # CLI values. So variance-wanting modes MUST set top_k/top_p/min_p in the
    # body. These are llama.cpp's standard "balanced" sampler values.
    "verification": {"top_k": 40, "top_p": 0.95, "min_p": 0.05},
    "explore":      {"top_k": 40, "top_p": 0.95, "min_p": 0.05},
    # Reproduce mode wants strict greedy. Explicit top_k=1 is more portable
    # than relying on a server-side default; works regardless of which
    # llama.cpp / vLLM / Ollama instance we're talking to.
    "reproduce":    {"top_k": 1, "top_p": None, "min_p": None},
}

@dataclasses.dataclass
class CorpusUcResult:
    """Outcome of running stage 2 on one UC from the corpus."""
    uc_uuid: str
    uc_handle: str
    uc_path: Path
    success: bool
    output_path: Optional[Path] = None       # for verification / reproduce
    output_dir: Optional[Path] = None        # for explore
    wall_time_seconds: float = 0.0
    sample_count: int = 0
    error: Optional[str] = None

def gather_corpus(corpus_path: Path) -> list[Path]:
    """Find all .yaml/.yml files under corpus_path that look like UCs.

    Skips files with .backup suffix (artifacts of the migration tool)
    and dot-files. Returns sorted list for stable iteration order.
    """
    if corpus_path.is_file():
        return [corpus_path]
    if not corpus_path.is_dir():
        return []
    files = []
    for pattern in ("*.yaml", "*.yml"):
        files.extend(corpus_path.rglob(pattern))
    files = [
        f for f in files
        if not f.name.endswith(".backup")
        and not f.name.startswith(".")
        # The multi-corpus git-sync task drops a corpus-manifest.yaml at
        # the corpus root (source provenance + clone status); it's run
        # metadata, not a UC.
        and f.name != "corpus-manifest.yaml"
    ]
    return sorted(files)


def read_corpus_manifest(corpus_path: Path) -> Optional[dict]:
    """Read corpus-manifest.yaml written by dav-git-sync-multi-corpus at the
    corpus root, if present. Returns the parsed dict or None (legacy
    single-source syncs and direct CLI runs have no manifest — that's fine).
    Malformed manifests are logged and treated as absent; provenance
    reporting must never block a run."""
    if not corpus_path.is_dir():
        return None
    manifest_path = corpus_path / "corpus-manifest.yaml"
    if not manifest_path.is_file():
        return None
    try:
        with manifest_path.open("r") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        log.warning("corpus manifest %s unreadable (%s) — ignoring", manifest_path, e)
        return None
    if not isinstance(data, dict):
        log.warning("corpus manifest %s is not a mapping — ignoring", manifest_path)
        return None
    return data

def derive_run_id(corpus_files: list[Path], now_utc: Optional[datetime] = None) -> str:
    """Compute a run-id: <iso-timestamp>-<corpus-hash>."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    timestamp = now_utc.strftime("%Y-%m-%dT%H-%M-%SZ")
    paths_text = "\n".join(str(f) for f in sorted(corpus_files))
    h = hashlib.sha256(paths_text.encode("utf-8")).hexdigest()[:7]
    return f"{timestamp}-{h}"

def resolve_sample_count_and_seeds(
    *,
    mode: str,
    requested_count: Optional[int],
    seed_override: Optional[int],
    uc_uuid: str,
) -> tuple[int, list[int]]:
    """Return (sample_count, sample_seeds) for one UC.

    Logic mirrors stage2_analyze._resolve_sample_count_and_seeds but
    takes primitive params instead of an argparse namespace. reproduce
    forces N=1; verification and explore use the mode default unless
    requested_count is given. Seeds derive from uc_uuid for run
    stability unless seed_override is set.
    """
    n = requested_count if requested_count is not None else _DEFAULT_SAMPLE_COUNT[mode]

    if mode == "reproduce":
        if n != 1:
            log.warning(
                "reproduce mode forces sample_count=1 (got %d, ignoring)", n,
            )
        n = 1
        if seed_override is not None:
            seeds = [seed_override]
        else:
            seeds = [derive_seed_from_uuid(uc_uuid)]
        return n, seeds

    base_seed = (
        seed_override if seed_override is not None
        else derive_seed_from_uuid(uc_uuid)
    )
    seeds = [base_seed + i for i in range(n)]
    return n, seeds

def write_uc_analysis(path: Path, analysis: Analysis) -> None:
    """Write a single Analysis YAML to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        yaml.safe_dump(
            analysis.to_dict(), f,
            sort_keys=False, default_flow_style=False, allow_unicode=True,
        )

def _stamp_run_level_metadata(
    analysis: Analysis,
    *,
    run_id: str,
    mode: str,
    endpoint_url: str,
    inference_topology: str,
    stage: str = "stage2",
    parent_run_id: str = "",
) -> Analysis:
    """Populate AnalysisMetadata fields the agent can't know.

    The Stage2Agent populates fields it has direct access to (model,
    timestamp, tool_call_count, total_tokens, stage2_run_id, wall_time,
    sample_seeds, engine_version/commit, consumer_version). Several other
    AnalysisMetadata fields are run-level context the runner knows but
    the agent does not:

      run_id              — the corpus run identifier (timestamp-suffix)
      mode                — verification | reproduce | explore
      endpoint_url        — the OpenAI /v1 URL the runner is dispatching to
      inference_topology  — operator-supplied topology label (optional)
      stage               — fixed 'stage2' for now; placeholder for when
                            multi-stage runs land
      parent_run_id       — empty for direct CLI invocations; will be set
                            for triggered runs (e.g. when a webhook-driven
                            run links back to its trigger)

    These get stamped onto the (already-merged) Analysis's metadata in
    place and the same Analysis is returned for chaining. Mutating the
    metadata directly is correct here — it's a fresh dataclass that has
    no aliasing back to per-sample analyses (merge_analyses uses
    dataclasses.replace which deep-copies).
    """
    meta = analysis.analysis_metadata
    meta.run_id = run_id
    meta.mode = mode
    meta.endpoint_url = endpoint_url
    meta.inference_topology = inference_topology
    meta.stage = stage
    meta.parent_run_id = parent_run_id
    return analysis

def write_uc_explore_output(
    output_dir: Path,
    samples: list[Analysis],
    sample_seeds: list[int],
) -> None:
    """Write per-sample analyses + variance.yaml for explore mode."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for i, sample in enumerate(samples):
        path = output_dir / f"sample-{i:02d}.yaml"
        with path.open("w") as f:
            yaml.safe_dump(
                sample.to_dict(), f,
                sort_keys=False, default_flow_style=False, allow_unicode=True,
            )
    variance = build_variance_report(samples, sample_seeds=sample_seeds)
    with (output_dir / "variance.yaml").open("w") as f:
        yaml.safe_dump(
            variance.to_dict(), f,
            sort_keys=False, default_flow_style=False, allow_unicode=True,
        )

def run_one_uc(
    *,
    uc_path: Path,
    run_dir: Path,
    inference_factory,
    mcp_factory,
    pass1_inference_factory=None,
    config: AgentConfig,
    mode: str,
    consumer_profile,
    consumer_content_path: Optional[Path],
    run_id: str = "",
    endpoint_url: str = "",
    inference_topology: str = "",
) -> CorpusUcResult:
    """Run stage 2 on a single UC; write outputs into run_dir.

    Returns a CorpusUcResult. Does NOT raise on stage-2 failure — caller
    decides whether to continue or halt based on result.success.
    """
    uc_started = time.monotonic()
    # Load UC YAML
    try:
        with uc_path.open("r") as f:
            uc_data = yaml.safe_load(f)
        use_case = UseCase.from_dict(uc_data)
    except Exception as e:
        return CorpusUcResult(
            uc_uuid="<load-failed>",
            uc_handle="<load-failed>",
            uc_path=uc_path,
            success=False,
            error=f"failed to load UC: {type(e).__name__}: {e}",
            wall_time_seconds=time.monotonic() - uc_started,
        )

    # Validate UC against profile
    errors = use_case.validate(consumer_profile)
    if errors:
        return CorpusUcResult(
            uc_uuid=use_case.uuid, uc_handle=use_case.handle, uc_path=uc_path,
            success=False,
            error=f"UC failed validation: {'; '.join(errors)}",
            wall_time_seconds=time.monotonic() - uc_started,
        )

    # Resolve mode-appropriate sample count and seeds
    sample_count, sample_seeds = resolve_sample_count_and_seeds(
        mode=mode,
        requested_count=config.sample_count,
        seed_override=config.seed,
        uc_uuid=use_case.uuid,
    )
    config_for_uc = dataclasses.replace(config, sample_count=sample_count)

    log.info(
        "running UC %s (handle=%s, mode=%s, samples=%d)",
        use_case.uuid, use_case.handle, mode, sample_count,
    )

    # Per-UC turns log. Each sample writes <uc_uuid>.seed-<N>.jsonl into
    # the run's turns/ dir. The DAV review-console drawer tails these for
    # the live prompts/responses panel.
    turns_dir = run_dir / "turns"
    # Pass the dir; run_samples derives per-seed file names from use_case.uuid
    turns_path = turns_dir

    # Run samples
    try:
        samples = run_samples(
            pass1_inference_factory=pass1_inference_factory,
            use_case=use_case,
            inference_factory=inference_factory,
            mcp_factory=mcp_factory,
            config=config_for_uc,
            sample_seeds=sample_seeds,
            consumer_profile=consumer_profile,
            consumer_content_path=consumer_content_path,
            turns_log_path=turns_path,
        )
    except Exception as e:
        return CorpusUcResult(
            uc_uuid=use_case.uuid, uc_handle=use_case.handle, uc_path=uc_path,
            success=False,
            error=f"stage 2 failed: {type(e).__name__}: {e}",
            wall_time_seconds=time.monotonic() - uc_started,
            sample_count=sample_count,
        )

    # Write output (mode-dependent)
    if mode == "explore":
        uc_explore_dir = run_dir / "analyses" / use_case.uuid
        # Stamp run-level metadata onto each per-sample analysis so explore
        # outputs carry the same provenance as verification/reproduce outputs.
        for sample in samples:
            _stamp_run_level_metadata(
                sample,
                run_id=run_id,
                mode=mode,
                endpoint_url=endpoint_url,
                inference_topology=inference_topology,
            )
        write_uc_explore_output(uc_explore_dir, samples, sample_seeds)
        return CorpusUcResult(
            uc_uuid=use_case.uuid, uc_handle=use_case.handle, uc_path=uc_path,
            success=True, output_dir=uc_explore_dir,
            wall_time_seconds=time.monotonic() - uc_started,
            sample_count=sample_count,
        )

    # verification or reproduce
    if mode == "verification" and len(samples) > 1:
        merged = merge_analyses(samples, sample_seeds=sample_seeds)
    else:
        merged = samples[0]

    _stamp_run_level_metadata(
        merged,
        run_id=run_id,
        mode=mode,
        endpoint_url=endpoint_url,
        inference_topology=inference_topology,
    )
    out_path = run_dir / "analyses" / f"{use_case.uuid}.yaml"
    write_uc_analysis(out_path, merged)
    return CorpusUcResult(
        uc_uuid=use_case.uuid, uc_handle=use_case.handle, uc_path=uc_path,
        success=True, output_path=out_path,
        wall_time_seconds=time.monotonic() - uc_started,
        sample_count=sample_count,
    )

def _parse_engine_json(blob: Optional[str], name: str) -> Optional[dict]:
    """Lenient parser for the --capabilities-json / --use-profile-json CLI
    flags. Accepts empty/missing as no-op (returns None). Logs a warning
    on malformed JSON and falls back to no-op rather than crashing the
    run — the engine still has mode defaults to fall back on, and a
    typo in the operator's profile shouldn't kill an A/B in progress.

    Accepts either inline JSON or `@/path/to/file` syntax (matches curl).
    The file path form avoids shell-quoting headaches when the Tekton
    task script needs to thread JSON with spaces through OPTIONAL_ARGS.
    """
    if not blob or not blob.strip() or blob.strip() in ("{}", "null"):
        return None
    if blob.startswith("@"):
        path = blob[1:]
        try:
            with open(path, "r") as f:
                blob = f.read()
        except Exception as e:
            log.warning("--%s @%s: cannot read file (%s) — ignoring", name, path, e)
            return None
        if not blob.strip() or blob.strip() in ("{}", "null"):
            return None
    import json as _json
    try:
        out = _json.loads(blob)
    except Exception as e:
        log.warning("--%s: ignoring malformed JSON (%s)", name, e)
        return None
    if not isinstance(out, dict):
        log.warning("--%s: expected JSON object, got %s — ignoring", name, type(out).__name__)
        return None
    return out


def write_run_summary(
    *,
    run_dir: Path,
    run_id: str,
    mode: str,
    results: list[CorpusUcResult],
    runner_started_at: str,
    runner_total_seconds: float,
    effective_sampling: Optional[dict] = None,
    quarantined: Optional[list[dict]] = None,
    phase: Optional[str] = None,
    corpus_sources: Optional[list[dict]] = None,
) -> Path:
    """Write the unified run-summary.yaml at the top of run_dir.

    phase: when set (the incremental per-UC rewrites pass "running"), a
    `phase:` key is included so consumers can tell an in-flight snapshot
    from a terminal summary. The final post-loop write omits it, keeping
    the terminal schema exactly as before.
    corpus_sources: per-source provenance from corpus-manifest.yaml
    (multi-source syncs); omitted from the summary when absent (legacy
    single-source runs).
    """
    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]
    total_samples = sum(r.sample_count for r in successful)
    mean_wall = (
        sum(r.wall_time_seconds for r in successful) / len(successful)
        if successful else 0.0
    )
    summary = {
        "run_id": run_id,
        "mode": mode,
        **({"phase": phase} if phase else {}),
        **({"corpus_sources": corpus_sources} if corpus_sources else {}),
        "started_at": runner_started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "runner_total_seconds": round(runner_total_seconds, 2),
        "total_ucs": len(results),
        "successful": len(successful),
        "failed": len(failed),
        "total_samples": total_samples,
        "mean_uc_wall_time_seconds": round(mean_wall, 2),
        # Ingest validation gate (DR §6): UCs excluded before the analyze pass
        # because they could not load or failed profile validation.
        "quarantined": len(quarantined or []),
        **({"quarantined_ucs": quarantined} if quarantined else {}),
        # Per-(model, use) capabilities + profile system (DAV migration 014).
        # `effective_sampling.sent` is what actually went out on every
        # request body for this run; `effective_sampling.dropped` is what
        # the engine suppressed at body-build time because a capability
        # flag forbade it. Together with mode + model these are sufficient
        # to reproduce the run without DAV state.
        **(
            {"effective_sampling": effective_sampling}
            if effective_sampling else {}
        ),
        "ucs": [
            {
                "uc_uuid": r.uc_uuid,
                "uc_handle": r.uc_handle,
                "uc_path": str(r.uc_path),
                "status": "success" if r.success else "failed",
                "wall_time_seconds": round(r.wall_time_seconds, 2),
                "sample_count": r.sample_count,
                **({"output_path": str(r.output_path)} if r.output_path else {}),
                **({"output_dir": str(r.output_dir)} if r.output_dir else {}),
                **({"error": r.error} if r.error else {}),
            }
            for r in results
        ],
    }
    out_path = run_dir / "run-summary.yaml"
    with out_path.open("w") as f:
        yaml.safe_dump(
            summary, f, sort_keys=False, default_flow_style=False, width=120,
        )
    return out_path

def write_failure_report(run_dir: Path, result: CorpusUcResult) -> None:
    """Write a single failure's details to failures/<uc-uuid>.error.txt."""
    failures_dir = run_dir / "failures"
    failures_dir.mkdir(parents=True, exist_ok=True)
    safe_uuid = result.uc_uuid.replace("/", "_")
    out = failures_dir / f"{safe_uuid}.error.txt"
    out.write_text(
        f"UC: {result.uc_handle} ({result.uc_uuid})\n"
        f"Path: {result.uc_path}\n"
        f"Wall time: {result.wall_time_seconds:.2f}s\n"
        f"Sample count: {result.sample_count}\n"
        f"\n"
        f"Error:\n{result.error}\n"
    )


# ── Ingest validation gate (operating-model DR §6) ───────────────────────────
# Validate the corpus with the REAL engine loader BEFORE the analyze pass, and
# partition into valid vs quarantined. The DR's ingest→analyze split made
# concrete: invalid UCs are excluded from the (expensive) LLM analyze pass and
# recorded with a precise reason + phase, instead of surfacing mid-run as a
# cryptic per-UC load failure. The loader (UseCase.from_dict — tolerant of
# unknown metadata keys) + UseCase.validate(profile) are the single source of
# validity; this gate adds no second schema.
def validate_corpus_files(
    corpus_files: list[Path], consumer_profile,
) -> tuple[list[Path], list[dict]]:
    """Partition corpus UC files into (valid_paths, quarantined). Each quarantine
    entry is {path, uuid, handle, phase: 'load'|'validate', reason}. Pure and
    read-only: no LLM, no GPU, no network."""
    valid: list[Path] = []
    quarantined: list[dict] = []
    for path in corpus_files:
        try:
            with path.open("r") as f:
                uc_data = yaml.safe_load(f)
            use_case = UseCase.from_dict(uc_data)
        except Exception as e:
            quarantined.append({
                "path": str(path), "uuid": None, "handle": None,
                "phase": "load", "reason": f"{type(e).__name__}: {e}",
            })
            continue
        errors = use_case.validate(consumer_profile)
        if errors:
            quarantined.append({
                "path": str(path), "uuid": use_case.uuid, "handle": use_case.handle,
                "phase": "validate", "reason": "; ".join(errors),
            })
            continue
        valid.append(path)
    return valid, quarantined


def write_quarantine_report(run_dir: Path, quarantined: list[dict]) -> Path:
    """Write quarantine.yaml listing every UC excluded from analyze + why.
    Always written (even when empty) so consumers can distinguish 'gate ran,
    nothing quarantined' from 'gate never ran'."""
    out = run_dir / "quarantine.yaml"
    with out.open("w") as f:
        yaml.safe_dump(
            {"count": len(quarantined), "quarantined": quarantined},
            f, sort_keys=False, default_flow_style=False,
        )
    return out


def preflight_inference(
    client: InferenceClient,
    *,
    budget_seconds: float = 90.0,
) -> bool:
    """Verify the inference endpoint answers BEFORE entering the UC loop.

    Production incident: the endpoint was down at run start and every UC
    'failed' in ~0.02s — 8 UCs burned before anyone noticed. A cheap
    GET /models (client.list_models) with retries catches that up front.
    Retries with exponential backoff (2s → 15s cap) until the endpoint
    answers or budget_seconds is exhausted. Returns True when healthy,
    False when the endpoint never answered — caller aborts the run.
    """
    deadline = time.monotonic() + budget_seconds
    attempt = 0
    delay = 2.0
    while True:
        attempt += 1
        try:
            models = client.list_models()
            log.info(
                "preflight: inference endpoint healthy (attempt %d, models: %s)",
                attempt, models,
            )
            return True
        except InferenceError as e:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.error(
                    "preflight: inference endpoint never answered within "
                    "%.0fs (%d attempt(s)): %s",
                    budget_seconds, attempt, e,
                )
                return False
            sleep_s = min(delay, remaining)
            log.warning(
                "preflight: inference endpoint not ready (attempt %d): %s — "
                "retrying in %.0fs",
                attempt, e, sleep_s,
            )
            time.sleep(sleep_s)
            delay = min(delay * 2, 15.0)


def _cli():
    parser = argparse.ArgumentParser(
        description="Run DAV stage 2 across an entire UC corpus.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--corpus-path", type=Path, required=True,
        help="Directory containing v1.0 use-case YAMLs (recursive).",
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True,
        help="Directory to write run results into. A subdirectory named "
             "<run-id> will be created inside it.",
    )
    parser.add_argument(
        "--inference-endpoint", type=str, required=True,
        help="OpenAI-compatible /v1 endpoint URL.",
    )
    parser.add_argument(
        "--inference-model", type=str, required=True,
        help="Model name to send to the endpoint.",
    )
    parser.add_argument(
        "--pass1-inference-endpoint", type=str, default=None,
        help="OPTIONAL separate OpenAI-compatible endpoint for the two-pass EXPLORE "
             "phase (pass 1). Pass 1 is retrieval + summarisation over many MCP turns "
             "and rewards recall; pass 2 is the grounded verdict and rewards precision. "
             "Unset = one model for both (unchanged behaviour).")
    parser.add_argument(
        "--pass1-inference-model", type=str, default=None,
        help="Model name for --pass1-inference-endpoint. Required when that is set.")
    parser.add_argument(
        "--inference-api-key-env", type=str, default=None,
        help="Name of the env var holding the inference API key for external "
             "frontier models (e.g. CLAUDE_API_KEY / OPENAI_API_KEY), injected "
             "from a Secret. Falls back to DAV_INFERENCE_API_KEY. Local vLLM "
             "needs none.",
    )
    parser.add_argument(
        "--inference-topology", type=str, default="",
        help="Operator-supplied label describing the inference topology "
             "(e.g. 'dual-r9700-tp2-q8' or 'single-l4-fp16'). Stamped onto "
             "AnalysisMetadata.inference_topology for run provenance. "
             "Optional; defaults to empty string.",
    )
    parser.add_argument(
        "--mcp-url", type=str, required=True,
        help="MCP server URL (the dav-docs-mcp serving consumer specs).",
    )
    parser.add_argument(
        "--consumer-content-path", type=Path, default=None,
        help="Path to the consumer's content tree (for consumer_version "
             "stamping on AnalysisMetadata).",
    )
    parser.add_argument(
        "--consumer-profile", type=Path, default=None,
        help="Path to a consumer profile YAML.",
    )
    parser.add_argument(
        "--mode", choices=["verification", "reproduce", "explore"],
        default="verification",
        help="Stage 2 runtime mode (default: verification).",
    )
    parser.add_argument(
        "--sample-count", type=int, default=None,
        help="Override the mode's default sample count.",
    )
    parser.add_argument(
        "--sample-concurrency", type=int, default=1,
        help="Parallel samples per UC (default 1, serial).",
    )
    parser.add_argument(
        "--uc-concurrency", type=int, default=1,
        help="How many UCs to analyze in parallel (default 1, serial). UCs are "
             "independent (per-UC seeds derive from the UC uuid, per-UC MCP/"
             "inference clients come from factories), so this is a pure wall-"
             "clock win — vLLM batches the streams on the same GPUs. Effective "
             "in-flight requests = uc_concurrency × sample_concurrency.",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Override the mode's default seed.",
    )
    parser.add_argument(
        "--cache-prompt",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable/disable llama.cpp KV-cache reuse. Default: derived from "
             "--mode (verification=True, reproduce=False, explore=True). "
             "Use --no-cache-prompt to force off if you need reproduce-mode "
             "bit-exactness in a verification run.",
    )
    parser.add_argument(
        "--max-tool-calls", type=int, default=30,
    )
    parser.add_argument(
        "--max-tokens", type=int, default=None,
        help="Per-turn LLM output token budget. Default: None, which "
             "resolves as use_profile['max_tokens'] if the DAV profile "
             "provides one, else 4096. (The default used to BE 4096, which "
             "made an explicit --max-tokens 4096 indistinguishable from "
             "'not set' and silently shadowed the profile value.)",
    )
    parser.add_argument(
        "--request-timeout-seconds", type=int, default=None,
        help="Per-request HTTP timeout for inference calls (seconds). "
             "Default: None → 900. Must satisfy "
             "max_tokens / decode-throughput(tok/s) < this value, or long "
             "generations die client-side; a run-start WARNING flags that.",
    )
    parser.add_argument(
        "--temperature", type=float, default=None,
        help="Override the mode's default temperature.",
    )
    parser.add_argument(
        "--top-k", type=int, default=None,
        help="Override the mode's default top-k. Defaults: verification=40, "
             "explore=40, reproduce=1 (greedy). Pass 0 to disable top-k "
             "filtering. See client.py EndpointConfig docstring for the "
             "diagnosis that motivated explicit per-mode sampler params.",
    )
    parser.add_argument(
        "--top-p", type=float, default=None,
        help="Override the mode's default top-p (nucleus sampling). "
             "Defaults: verification=0.95, explore=0.95, reproduce=unset.",
    )
    parser.add_argument(
        "--min-p", type=float, default=None,
        help="Override the mode's default min-p. Defaults: verification=0.05, "
             "explore=0.05, reproduce=unset.",
    )
    parser.add_argument(
        "--no-enable-thinking", action="store_true",
        help="Disable Qwen3 thinking-mode (recommended for stage 2).",
    )
    parser.add_argument(
        "--halt-on-error", action="store_true",
        help="Stop the corpus run on first UC failure. Default: continue.",
    )
    # Per-(model, use) override system (DAV migration 014). When DAV's
    # API triggers the PipelineRun it resolves the model's capabilities
    # row and the matching use_profile and threads them through as JSON
    # strings. Engine applies use_profile params as a layer between
    # mode defaults and explicit CLI overrides; capabilities filters
    # what actually goes out on the wire.
    parser.add_argument(
        "--use-key", type=str, default=None,
        help="DAV model_use_profiles use_key for this run (one of: "
             "evaluation_verification, evaluation_explore, "
             "evaluation_reproduce, arch_review, uc_assist, enhancement). "
             "Recorded in run-summary.yaml.effective_sampling.use_key.",
    )
    parser.add_argument(
        "--capabilities-json", type=str, default=None,
        help="JSON object with the model_configs.capabilities row for the "
             "target inference model. Engine drops disallowed params per "
             "EndpointConfig.capabilities. Empty / missing = no filtering.",
    )
    parser.add_argument(
        "--use-profile-json", type=str, default=None,
        help="JSON object with the model_use_profiles.params row for "
             "(this model × this use_key). Overrides mode defaults for "
             "any keys it sets; CLI overrides still win. Empty / missing "
             "= mode defaults apply.",
    )
    parser.add_argument(
        "--uc-handles", type=str, default=None,
        help="Comma-separated list of UC `handle:` values. If set, only UCs "
             "whose handle matches one of these are processed; other corpus "
             "files are skipped. Combine with --uc-uuids for OR semantics. "
             "If neither flag is set, the whole corpus runs (existing behavior).",
    )
    parser.add_argument(
        "--uc-uuids", type=str, default=None,
        help="Comma-separated list of UC `uuid:` values. Same semantics as "
             "--uc-handles but matches on UUID — useful for UCs without a "
             "stable handle.",
    )
    parser.add_argument(
        "--managed-uc-uuids", type=str, default=None,
        help="Comma-separated list of managed UC UUIDs to fetch from the "
             "console API and include alongside corpus UCs. Used by the "
             "console's Test evaluation flow for unpushed managed UCs — "
             "they're materialized into a temp dir at run start and treated "
             "like any other UC. Requires --console-api-url.",
    )
    parser.add_argument(
        "--known-capability-ids", type=str, default=None,
        help="Comma-separated catalog capability ids (the consumer's "
             "capability_catalog cap_keys). When set, gaps_identified[].capability_id "
             "is enum-constrained to these in guided-JSON and the allowed set is "
             "rendered into the prompt, so gaps are tagged to a real catalog "
             "capability for stable cross-run identity (ADR-009). Omitted/empty = "
             "unconstrained free string (existing behavior). Injected by the console "
             "trigger from the active project's catalog.",
    )
    parser.add_argument(
        "--console-api-url", type=str, default=None,
        help="Base URL of the DAV review console API (e.g. "
             "http://dav-review-api.dav.svc.cluster.local:8000). Required "
             "when --managed-uc-uuids is set.",
    )
    parser.add_argument(
        "--log-level", default="INFO",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Load consumer profile
    from dav.core.consumer_profile import load_profile, set_default_profile
    consumer_profile = load_profile(
        path=args.consumer_profile, fall_back_to_generic=True,
    )
    # ADR-009 gap identity: overlay the console-supplied catalog capability ids onto
    # the profile so guided-JSON enum-constrains gaps[].capability_id + the prompt lists
    # them. Deduped, order-preserving; empty/absent = unconstrained (existing behavior).
    _known_caps = [c.strip() for c in (args.known_capability_ids or "").split(",") if c.strip()]
    if _known_caps:
        _seen: set[str] = set()
        consumer_profile.known_capability_ids = [
            c for c in _known_caps if not (c in _seen or _seen.add(c))
        ]
        log.info("gap-identity: %d catalog capability id(s) supplied for guided-JSON",
                 len(consumer_profile.known_capability_ids))
    set_default_profile(consumer_profile)
    log.info(
        "consumer profile: %s (%s)",
        consumer_profile.framework_name, consumer_profile.consumer_id,
    )

    # Gather corpus (unconditionally — we may filter or replace it below)
    corpus_files = gather_corpus(args.corpus_path)
    log.info("corpus: %d files at %s", len(corpus_files), args.corpus_path)

    # Selection-aware gathering. Per R1 in the console design doc:
    # when ANY explicit selection is provided (handles, uuids, managed),
    # the engine runs ONLY the explicit selection — never the whole
    # corpus subpath as a fallback.
    handles_filter = {h.strip() for h in (args.uc_handles or "").split(",") if h.strip()}
    uuids_filter   = {u.strip() for u in (args.uc_uuids   or "").split(",") if u.strip()}
    managed_uuids  = [u.strip() for u in (args.managed_uc_uuids or "").split(",") if u.strip()]
    has_explicit  = bool(handles_filter or uuids_filter or managed_uuids)

    # Step 1: filter corpus_files to matching handles/uuids (if any).
    # If no corpus filter but managed UCs were requested, drop corpus
    # entirely (we'll only run the materialized managed UCs).
    if handles_filter or uuids_filter:
        import yaml as _yaml
        filtered = []
        for path in corpus_files:
            try:
                with path.open() as fh:
                    data = _yaml.safe_load(fh) or {}
            except Exception as e:
                log.warning("uc-filter: skipping unreadable %s (%s)", path, e)
                continue
            if not isinstance(data, dict):
                continue
            h = (data.get("handle") or "").strip()
            u = (data.get("uuid") or "").strip()
            if (h and h in handles_filter) or (u and u in uuids_filter):
                filtered.append(path)
        skipped = len(corpus_files) - len(filtered)
        log.info(
            "uc-filter: corpus %d → %d (skipped %d) handles=%d uuids=%d",
            len(corpus_files), len(filtered), skipped,
            len(handles_filter), len(uuids_filter),
        )
        corpus_files = filtered
    elif has_explicit:
        # Only managed UCs were requested — don't run anything from the
        # corpus subpath even if it would otherwise auto-populate.
        log.info(
            "uc-filter: only managed UCs requested (%d) — clearing %d corpus file(s)",
            len(managed_uuids), len(corpus_files),
        )
        corpus_files = []

    # Step 2: materialize managed UCs (always added, never filtered out).
    if managed_uuids:
        if not args.console_api_url:
            print("ERROR: --managed-uc-uuids requires --console-api-url",
                  file=sys.stderr)
            return 2
        import tempfile, httpx
        scratch = Path(tempfile.mkdtemp(prefix="dav-managed-ucs-"))
        log.info("managed-ucs: fetching %d UC(s) from %s → %s",
                 len(managed_uuids), args.console_api_url, scratch)
        base = args.console_api_url.rstrip("/")
        fetched = 0
        # The console API gates /api/use-cases behind auth. As a trusted
        # in-cluster service we present our Kubernetes ServiceAccount *projected
        # token* (audience-scoped, short-lived, auto-rotated) as a Bearer token;
        # the API validates it via TokenReview and authorizes us as the
        # system:engine identity — no shared static secret. The Tekton task
        # mounts the projected token at DAV_API_TOKEN_PATH; the kubelet refreshes
        # the file in place, so we read it fresh at call time.
        token_path = os.environ.get(
            "DAV_API_TOKEN_PATH", "/var/run/secrets/dav/api-token")

        def _svc_headers():
            try:
                with open(token_path) as tf:
                    tok = tf.read().strip()
                if tok:
                    return {"Authorization": f"Bearer {tok}"}
            except FileNotFoundError:
                log.warning("managed-ucs: SA token %s not mounted — fetch will "
                            "401 if the API requires auth", token_path)
            except Exception as e:
                log.warning("managed-ucs: could not read SA token %s (%s)",
                            token_path, e)
            return {}

        with httpx.Client(timeout=30.0) as cx:
            for uid in managed_uuids:
                try:
                    r = cx.get(f"{base}/api/use-cases/{uid}", headers=_svc_headers())
                    if r.status_code != 200:
                        log.warning(
                            "managed-ucs: skip %s (HTTP %s: %s)",
                            uid, r.status_code, r.text[:200],
                        )
                        continue
                    data = r.json() or {}
                    yaml_content = data.get("yaml_content") or ""
                    if not yaml_content:
                        log.warning("managed-ucs: skip %s (empty yaml_content)", uid)
                        continue
                    p = scratch / f"{uid}.yaml"
                    p.write_text(yaml_content)
                    corpus_files.append(p)
                    fetched += 1
                except Exception as e:
                    log.warning("managed-ucs: fetch failed for %s: %s", uid, e)
        log.info("managed-ucs: materialized %d / %d UC(s)", fetched, len(managed_uuids))

    if not corpus_files:
        msg = "no UC YAMLs to run"
        if has_explicit:
            msg += " (explicit selection matched nothing)"
        else:
            msg += f" under {args.corpus_path}"
        print(f"ERROR: {msg}", file=sys.stderr)
        return 2

    # Build run-id and run dir
    run_id = derive_run_id(corpus_files)
    run_dir = args.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log.info("run id: %s (output: %s)", run_id, run_dir)

    # Ingest validation gate (operating-model DR §6): validate every UC with the
    # real engine loader+profile BEFORE the analyze pass. Invalid UCs are
    # quarantined (excluded, with reason) so we never spend an LLM call on a UC
    # that cannot load/validate, and the failure is legible up front.
    corpus_files, _quarantined = validate_corpus_files(corpus_files, consumer_profile)
    write_quarantine_report(run_dir, _quarantined)
    if _quarantined:
        log.warning(
            "ingest-gate: quarantined %d UC(s) (see %s); analyzing %d valid UC(s)",
            len(_quarantined), run_dir / "quarantine.yaml", len(corpus_files),
        )
        for q in _quarantined:
            log.warning("ingest-gate: quarantine [%s] %s — %s",
                        q["phase"], q.get("handle") or q["path"], q["reason"])
    if not corpus_files:
        print("ERROR: all UCs quarantined by the ingest validation gate; "
              f"nothing to analyze (see {run_dir / 'quarantine.yaml'})",
              file=sys.stderr)
        return 2

    # Resolution order for every tunable: explicit CLI flag > use_profile
    # from DAV > mode default in code. Capabilities filter drops disallowed
    # params at body-build time in client._build_body.
    capabilities = _parse_engine_json(args.capabilities_json, "capabilities-json") or {}
    use_profile  = _parse_engine_json(args.use_profile_json, "use-profile-json") or {}

    if args.temperature is not None:
        temperature = args.temperature
    elif "temperature" in use_profile:
        temperature = use_profile["temperature"]
    else:
        temperature = _DEFAULT_TEMPERATURE[args.mode]
    if args.cache_prompt is None:
        cache_prompt = use_profile.get("cache_prompt", _DEFAULT_CACHE_PROMPT[args.mode])
    else:
        cache_prompt = args.cache_prompt

    sampler_defaults = _DEFAULT_SAMPLER_PARAMS[args.mode]
    top_k = args.top_k if args.top_k is not None else use_profile.get("top_k", sampler_defaults["top_k"])
    top_p = args.top_p if args.top_p is not None else use_profile.get("top_p", sampler_defaults["top_p"])
    min_p = args.min_p if args.min_p is not None else use_profile.get("min_p", sampler_defaults["min_p"])

    # max_tokens: explicit CLI > use_profile > default. The CLI default is
    # None (not a value sentinel), so an explicit --max-tokens 4096 is a
    # real override — the old `!= 4096` sentinel silently shadowed it.
    if args.max_tokens is not None:
        max_tokens = args.max_tokens
    else:
        max_tokens = use_profile.get("max_tokens", _DEFAULT_MAX_TOKENS)

    # Per-request HTTP timeout: explicit CLI > EndpointConfig default (900).
    request_timeout = (
        args.request_timeout_seconds
        if args.request_timeout_seconds is not None else 900
    )
    # Advisory timeout math (the OSAC 504s: max_tokens the endpoint could
    # not emit inside the route/client timeout). Assumed throughput is the
    # commented constant above, not a measurement of THIS endpoint.
    est_worst_case_s = max_tokens / _ASSUMED_DECODE_TOK_PER_S
    if est_worst_case_s > request_timeout:
        log.warning(
            "max_tokens=%d at an assumed ~%d tok/s is ~%.0fs of generation, "
            "which exceeds the per-request timeout of %ds — a legitimately "
            "long generation WILL die client-side. Lower --max-tokens or "
            "raise --request-timeout-seconds.",
            max_tokens, _ASSUMED_DECODE_TOK_PER_S,
            est_worst_case_s, request_timeout,
        )

    log.info(
        "corpus mode=%s temperature=%s cache_prompt=%s sample_count=%s "
        "sample_concurrency=%d top_k=%s top_p=%s min_p=%s max_tokens=%s "
        "request_timeout=%ss",
        args.mode, temperature, cache_prompt,
        args.sample_count or _DEFAULT_SAMPLE_COUNT[args.mode],
        args.sample_concurrency, top_k, top_p, min_p, max_tokens,
        request_timeout,
    )

    config = AgentConfig(
        max_tool_calls=args.max_tool_calls,
        max_tokens=max_tokens,
        temperature=temperature,
        seed=args.seed,
        sample_count=args.sample_count or _DEFAULT_SAMPLE_COUNT[args.mode],
        sample_concurrency=args.sample_concurrency,
    )

    chat_template_kwargs = {"enable_thinking": False} if args.no_enable_thinking else None
    # use_profile may override chat_template_kwargs too — last writer wins.
    if "chat_template_kwargs" in use_profile and use_profile["chat_template_kwargs"] is not None:
        chat_template_kwargs = use_profile["chat_template_kwargs"]
    # External frontier models (Claude/OpenAI) need a real key; local vLLM
    # ignores it. Resolve from the named env var (--inference-api-key-env, e.g.
    # CLAUDE_API_KEY, injected from a Secret) or the generic DAV_INFERENCE_API_KEY.
    _key_env = getattr(args, "inference_api_key_env", None)
    inference_api_key = (
        (os.environ.get(_key_env) if _key_env else None)
        or os.environ.get("DAV_INFERENCE_API_KEY")
        or "no-key-needed"
    )
    primary = EndpointConfig(
        url=args.inference_endpoint,
        model=args.inference_model,
        api_key=inference_api_key,
        timeout_seconds=request_timeout,
        chat_template_kwargs=chat_template_kwargs,
        cache_prompt=cache_prompt,
        top_k=top_k,
        top_p=top_p,
        min_p=min_p,
        temperature=temperature,
        max_tokens=max_tokens,
        capabilities=capabilities,
        use_key=args.use_key,
    )
    from dav.ai.client import effective_sampling as _eff_sampling
    effective_block = _eff_sampling(primary)
    if effective_block["dropped"]:
        log.info(
            "effective_sampling: use_key=%s sent=%s dropped=%s capabilities=%s",
            effective_block["use_key"], effective_block["sent"],
            effective_block["dropped"], effective_block["capabilities"],
        )
    else:
        log.info(
            "effective_sampling: use_key=%s sent=%s capabilities=%s",
            effective_block["use_key"], effective_block["sent"],
            effective_block["capabilities"],
        )
    # Optional pass-1 backend. Inherits sampling/capability config from the primary
    # so only the destination differs — otherwise a routing change would silently
    # become a sampling change too, and any quality delta would be unattributable.
    pass1_primary = None
    if args.pass1_inference_endpoint:
        if not args.pass1_inference_model:
            parser.error("--pass1-inference-model is required with --pass1-inference-endpoint")
        pass1_primary = dataclasses.replace(
            primary,
            url=args.pass1_inference_endpoint,
            model=args.pass1_inference_model,
            label="pass1",
        )
        log.info("per-stage routing: pass1 -> %s (%s) | pass2 -> %s (%s)",
                 args.pass1_inference_model, args.pass1_inference_endpoint,
                 args.inference_model, args.inference_endpoint)

    def _make_inference():
        return InferenceClient(primary=primary)

    def _make_pass1_inference():
        return InferenceClient(primary=pass1_primary) if pass1_primary else None
    def _make_mcp():
        return McpClient(server_url=args.mcp_url)

    # Run-start preflight: prove the endpoint answers BEFORE the UC loop.
    # (Production incident: endpoint down at run start → every UC failed in
    # ~0.02s and the whole corpus burned before anyone noticed.) Skipped for
    # Anthropic endpoints — list_models targets the OpenAI-compatible
    # GET /models with Bearer auth, which Anthropic rejects.
    from dav.ai.client import _is_anthropic
    if _is_anthropic(primary):
        log.info("preflight: skipping /models check for Anthropic endpoint")
    elif not preflight_inference(_make_inference()):
        print(
            f"ERROR: inference endpoint {args.inference_endpoint} did not "
            "answer the preflight health check; aborting before the UC loop "
            "so the corpus is not burned against a dead endpoint.",
            file=sys.stderr,
        )
        return 2

    # Corpus source provenance (multi-source syncs write corpus-manifest.yaml
    # at the corpus root; legacy single-source runs have none — skip quietly).
    corpus_manifest = read_corpus_manifest(args.corpus_path)
    corpus_sources: Optional[list[dict]] = None
    if corpus_manifest:
        corpus_sources = corpus_manifest.get("sources") or None
        failed_sources = [
            s for s in (corpus_sources or []) if s.get("status") != "ok"
        ]
        if failed_sources:
            log.warning("=" * 72)
            log.warning(
                "CORPUS INCOMPLETE: %d of %d configured corpus source(s) "
                "FAILED to sync — this run covers a PARTIAL corpus:",
                len(failed_sources), len(corpus_sources or []),
            )
            for s in failed_sources:
                log.warning(
                    "  failed source: namespace=%s repo=%s branch=%s",
                    s.get("namespace"), s.get("repo_url"), s.get("branch"),
                )
            log.warning("=" * 72)

    # Run the corpus
    runner_started_at = datetime.now(timezone.utc).isoformat()
    runner_started = time.monotonic()
    results: list[CorpusUcResult] = []
    halted = False
    progress_path = run_dir / "run-progress.yaml"

    def _write_progress(current_index: int, current_uc_path: str | None,
                        phase: str = "running") -> None:
        """Write/overwrite a small progress file the console can poll while the
        run is in flight. Cheap (~200 bytes), invariant: always reflects the
        last completed UC + the next one being processed. Errors are swallowed
        — progress reporting must never disrupt the run itself."""
        try:
            succ = sum(1 for r in results if r.success)
            failed_n = sum(1 for r in results if not r.success)
            payload = {
                "run_id": run_id,
                "phase": phase,                  # running | completed | halted
                "total_ucs": len(corpus_files),
                "current_index": current_index,  # 1-based; index of UC being worked
                "current_uc_path": current_uc_path,
                "completed": len(results),
                "succeeded": succ,
                "failed": failed_n,
                "started_at": runner_started_at,
                "elapsed_seconds": round(time.monotonic() - runner_started, 2),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            with progress_path.open("w") as f:
                yaml.safe_dump(payload, f, sort_keys=False, default_flow_style=False)
        except Exception as e:
            log.warning("progress write failed: %s", e)

    def _run_uc(i: int, uc_path):
        log.info("[%d/%d] %s", i, len(corpus_files), uc_path)
        return run_one_uc(
            uc_path=uc_path,
            run_dir=run_dir,
            inference_factory=_make_inference,
            pass1_inference_factory=_make_pass1_inference,
            mcp_factory=_make_mcp,
            config=config,
            mode=args.mode,
            consumer_profile=consumer_profile,
            consumer_content_path=args.consumer_content_path,
            run_id=run_id,
            endpoint_url=args.inference_endpoint,
            inference_topology=args.inference_topology,
        )

    def _write_summary_snapshot() -> None:
        """Rewrite run-summary.yaml with running totals + phase: running after
        every finished UC. A cancelled/OOM-killed run previously left NO
        run-summary.yaml at all, and the console skips summary-less run dirs
        — hours of finished analyses invisible. Same swallow-errors contract
        as _write_progress: reporting must never disrupt the run."""
        try:
            write_run_summary(
                run_dir=run_dir, run_id=run_id, mode=args.mode,
                results=list(results), runner_started_at=runner_started_at,
                runner_total_seconds=time.monotonic() - runner_started,
                effective_sampling=effective_block,
                quarantined=_quarantined,
                phase="running",
                corpus_sources=corpus_sources,
            )
        except Exception as e:
            log.warning("incremental run-summary write failed: %s", e)

    def _record(result) -> bool:
        """Append + log one finished UC; returns True when halt-on-error fires.
        Called only from the main thread (serial loop or as_completed loop)."""
        results.append(result)
        _write_summary_snapshot()
        if not result.success:
            log.warning(
                "UC %s failed (%.2fs): %s",
                result.uc_uuid, result.wall_time_seconds, result.error,
            )
            write_failure_report(run_dir, result)
            if args.halt_on_error:
                log.error("--halt-on-error set; stopping after first failure")
                return True
        else:
            log.info(
                "UC %s done (%.2fs, %d sample(s))",
                result.uc_uuid, result.wall_time_seconds, result.sample_count,
            )
        return False

    uc_concurrency = max(1, int(getattr(args, "uc_concurrency", 1) or 1))
    _write_progress(current_index=0, current_uc_path=None, phase="running")
    if uc_concurrency == 1:
        # Serial path — semantics identical to the original loop.
        for i, uc_path in enumerate(corpus_files, 1):
            _write_progress(current_index=i, current_uc_path=str(uc_path), phase="running")
            result = _run_uc(i, uc_path)
            if _record(result):
                halted = True
                break
    else:
        # Concurrent path — UCs are independent agent loops (per-UC clients via
        # the factories; per-UC seeds derive from the UC uuid, so results are
        # order-independent and comparable to serial runs). vLLM batches the
        # concurrent streams, so aggregate throughput scales while per-UC
        # latency stays roughly flat. results/progress are mutated only from
        # this (main) thread via as_completed. halt-on-error cancels UCs not
        # yet started; in-flight UCs run to completion.
        from concurrent.futures import ThreadPoolExecutor, as_completed
        log.info("uc-concurrency=%d: running %d UC(s) with up to %d in flight",
                 uc_concurrency, len(corpus_files), uc_concurrency)
        with ThreadPoolExecutor(max_workers=uc_concurrency) as pool:
            futures = {
                pool.submit(_run_uc, i, uc_path): (i, uc_path)
                for i, uc_path in enumerate(corpus_files, 1)
            }
            for fut in as_completed(futures):
                i, uc_path = futures[fut]
                try:
                    result = fut.result()
                except Exception as e:                      # belt-and-suspenders
                    log.error("UC %s crashed the worker: %s", uc_path, e)
                    continue
                _write_progress(current_index=len(results) + 1,
                                current_uc_path=str(uc_path), phase="running")
                if _record(result):
                    halted = True
                    for pending in futures:
                        pending.cancel()
                    break
    runner_total = time.monotonic() - runner_started
    _write_progress(
        current_index=len(results),
        current_uc_path=None,
        phase="halted" if halted else "completed",
    )

    summary_path = write_run_summary(
        run_dir=run_dir, run_id=run_id, mode=args.mode,
        results=results, runner_started_at=runner_started_at,
        runner_total_seconds=runner_total,
        effective_sampling=effective_block,
        quarantined=_quarantined,
        corpus_sources=corpus_sources,
    )
    log.info("run summary written: %s", summary_path)

    successful = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)
    print(f"\nRun {run_id}")
    print(f"  Output:    {run_dir}")
    print(f"  Total:     {len(results)} UC(s)")
    print(f"  Succeeded: {successful}")
    print(f"  Failed:    {failed}")
    if halted:
        print(f"  Halted:    yes (--halt-on-error fired after {len(results)} UC(s))")
    print(f"  Wall time: {runner_total:.2f}s")

    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(_cli())
