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
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from dav.ai.agent import AgentConfig
from dav.ai.client import EndpointConfig, InferenceClient
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
_DEFAULT_SAMPLER_PARAMS = {
    # Diagnosed 2026-04-26: the inference server (vis.roadfeldt.com:8000)
    # ships --top-k 1 as a CLI default, making it greedy regardless of
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
    ]
    return sorted(files)

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

    # Run samples
    try:
        samples = run_samples(
            use_case=use_case,
            inference_factory=inference_factory,
            mcp_factory=mcp_factory,
            config=config_for_uc,
            sample_seeds=sample_seeds,
            consumer_profile=consumer_profile,
            consumer_content_path=consumer_content_path,
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

def write_run_summary(
    *,
    run_dir: Path,
    run_id: str,
    mode: str,
    results: list[CorpusUcResult],
    runner_started_at: str,
    runner_total_seconds: float,
) -> Path:
    """Write the unified run-summary.yaml at the top of run_dir."""
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
        "started_at": runner_started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "runner_total_seconds": round(runner_total_seconds, 2),
        "total_ucs": len(results),
        "successful": len(successful),
        "failed": len(failed),
        "total_samples": total_samples,
        "mean_uc_wall_time_seconds": round(mean_wall, 2),
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
        "--max-tokens", type=int, default=4096,
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
        path=args.consumer_profile, fall_back_to_dcm=True,
    )
    set_default_profile(consumer_profile)
    log.info(
        "consumer profile: %s (%s)",
        consumer_profile.framework_name, consumer_profile.consumer_id,
    )

    # Gather corpus
    corpus_files = gather_corpus(args.corpus_path)
    if not corpus_files:
        print(f"ERROR: no UC YAMLs found under {args.corpus_path}", file=sys.stderr)
        return 2
    log.info("corpus: %d files at %s", len(corpus_files), args.corpus_path)

    # Build run-id and run dir
    run_id = derive_run_id(corpus_files)
    run_dir = args.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log.info("run id: %s (output: %s)", run_id, run_dir)

    # Resolve mode-specific defaults
    if args.temperature is None:
        temperature = _DEFAULT_TEMPERATURE[args.mode]
    else:
        temperature = args.temperature
    if args.cache_prompt is None:
        cache_prompt = _DEFAULT_CACHE_PROMPT[args.mode]
    else:
        cache_prompt = args.cache_prompt

    # Resolve sampler params: CLI override > mode default. CLI overrides
    # are absolute (None means "no override; use mode default"); to send
    # nothing for a field, set the mode default to None.
    sampler_defaults = _DEFAULT_SAMPLER_PARAMS[args.mode]
    top_k = args.top_k if args.top_k is not None else sampler_defaults["top_k"]
    top_p = args.top_p if args.top_p is not None else sampler_defaults["top_p"]
    min_p = args.min_p if args.min_p is not None else sampler_defaults["min_p"]

    log.info(
        "corpus mode=%s temperature=%s cache_prompt=%s sample_count=%s "
        "sample_concurrency=%d top_k=%s top_p=%s min_p=%s",
        args.mode, temperature, cache_prompt,
        args.sample_count or _DEFAULT_SAMPLE_COUNT[args.mode],
        args.sample_concurrency, top_k, top_p, min_p,
    )

    config = AgentConfig(
        max_tool_calls=args.max_tool_calls,
        max_tokens=args.max_tokens,
        temperature=temperature,
        seed=args.seed,
        sample_count=args.sample_count or _DEFAULT_SAMPLE_COUNT[args.mode],
        sample_concurrency=args.sample_concurrency,
    )

    chat_template_kwargs = {"enable_thinking": False} if args.no_enable_thinking else None
    primary = EndpointConfig(
        url=args.inference_endpoint,
        model=args.inference_model,
        chat_template_kwargs=chat_template_kwargs,
        cache_prompt=cache_prompt,
        top_k=top_k,
        top_p=top_p,
        min_p=min_p,
    )
    def _make_inference():
        return InferenceClient(primary=primary)
    def _make_mcp():
        return McpClient(server_url=args.mcp_url)

    # Run the corpus
    runner_started_at = datetime.now(timezone.utc).isoformat()
    runner_started = time.monotonic()
    results: list[CorpusUcResult] = []
    halted = False
    for i, uc_path in enumerate(corpus_files, 1):
        log.info("[%d/%d] %s", i, len(corpus_files), uc_path)
        result = run_one_uc(
            uc_path=uc_path,
            run_dir=run_dir,
            inference_factory=_make_inference,
            mcp_factory=_make_mcp,
            config=config,
            mode=args.mode,
            consumer_profile=consumer_profile,
            consumer_content_path=args.consumer_content_path,
            run_id=run_id,
            endpoint_url=args.inference_endpoint,
            inference_topology=args.inference_topology,
        )
        results.append(result)
        if not result.success:
            log.warning(
                "UC %s failed (%.2fs): %s",
                result.uc_uuid, result.wall_time_seconds, result.error,
            )
            write_failure_report(run_dir, result)
            if args.halt_on_error:
                log.error("--halt-on-error set; stopping after first failure")
                halted = True
                break
        else:
            log.info(
                "UC %s done (%.2fs, %d sample(s))",
                result.uc_uuid, result.wall_time_seconds, result.sample_count,
            )
    runner_total = time.monotonic() - runner_started

    summary_path = write_run_summary(
        run_dir=run_dir, run_id=run_id, mode=args.mode,
        results=results, runner_started_at=runner_started_at,
        runner_total_seconds=runner_total,
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
