"""
Stage 2 orchestration with three runtime modes.

CLI/library entry point for running stage 2 analysis over a use case.
adds the verification / reproduce / explore mode dispatch:

  verification (default)
    N samples (default 3), low temperature, deterministic decoding,
    ensemble merge into one Analysis with sample_annotations populated.
    Use for CI/regression.

  reproduce
    1 sample, greedy (temp 0.0), seed derived from UC uuid (override
    with --seed), cache_prompt=False. Produces byte-identical output
    on repeated runs (modulo timestamp). Use for debug, audit exemplar,
    bisection.

  explore
    N samples (default 10), high temperature, no merge. Emits per-sample
    Analyses to <output_dir>/sample-NN.yaml and a variance report to
    <output_dir>/variance.yaml. Use for UC authoring and adversarial
    poke-testing of the architecture.

CLI usage:

    # verification (default)
    python -m dav.stages.stage2_analyze \\
        --use-case path/to/case.yaml \\
        --inference-endpoint http://host/v1 \\
        --inference-model qwen \\
        --mcp-url http://mcp:8080 \\
        --output path/to/analysis.yaml

    # reproduce
    python -m dav.stages.stage2_analyze --mode reproduce \\
        --use-case path/to/case.yaml --output path/to/analysis.yaml \\
        ...other flags...

    # explore (writes a directory, not a single file)
    python -m dav.stages.stage2_analyze --mode explore \\
        --sample-count 10 \\
        --use-case path/to/case.yaml --output path/to/explore-output/ \\
        ...other flags...

Programmatic API:

    config = AgentConfig(sample_count=3, sample_concurrency=1)
    samples = run_samples(use_case, inference_factory, mcp_factory, config)
    merged = merge_analyses(samples, sample_seeds=[...])
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Callable

import yaml

from dav.core.use_case_schema import UseCase, Analysis
from dav.core.ensemble import merge_analyses
from dav.core.explore import build_variance_report
from dav.ai.client import InferenceClient, EndpointConfig
from dav.ai.mcp_tools import McpClient
from dav.ai.agent import Stage2Agent, AgentConfig, AgentError

log = logging.getLogger(__name__)

# Default sample counts per mode (override via --sample-count).
_DEFAULT_SAMPLE_COUNT = {
    "verification": 3,
    "reproduce": 1,
    "explore": 10,
}

# Default temperatures per mode.
_DEFAULT_TEMPERATURE = {
    "verification": 0.2,
    "reproduce": 0.0,
    "explore": 0.7,
}

# Default cache_prompt setting per mode.
#
# llama.cpp's prompt cache (cache_prompt=true) reuses the KV-cache from prior
# requests when the prefix matches. This is a 5-10x speedup for agentic
# workloads, where each turn extends the previous request's prompt by a
# small delta — the entire conversation history above the new tokens is a
# perfect prefix match. Without caching, the server re-prefills the full
# context on every turn, which scales linearly with conversation length.
#
# The historical concern with cache_prompt was non-determinism: KV values
# computed during a prior request live in a specific FP trajectory, and
# reusing them at argmax-tie boundaries can flip token decisions vs. a
# cold-cache run. See llama.cpp discussion #10311.
#
# DAV's framing is "predictable correctness" via N-sample ensemble + vote,
# not strict determinism. Cache-induced logit-level variance is in
# distribution for that framing — verification mode merges N samples; the
# tiny variance the cache might introduce is dwarfed by the variance the
# ensemble already absorbs.
#
# Per-mode defaults:
#   - reproduce: False. The explicit purpose of this mode is byte-identical
#     reruns for audit exemplars; cache-related variance defeats that.
#   - verification: True. Default mode for CI/regression. The 5-10x speedup
#     is essential at production scale; ensemble already handles variance.
#   - explore: True. Variance-surfacing mode; cache-related variance is fine.
#
# Override with --cache-prompt (force on) or --no-cache-prompt (force off).
_DEFAULT_CACHE_PROMPT = {
    "verification": True,
    "reproduce": False,
    "explore": True,
}
_DEFAULT_SAMPLER_PARAMS = {
    # See run_corpus.py for the full diagnosis context. The llama.cpp
    # server defaults (--top-k 1) override per-request unsent fields.
    # Variance-wanting modes must explicitly set top_k/top_p/min_p.
    "verification": {"top_k": 40, "top_p": 0.95, "min_p": 0.05},
    "explore":      {"top_k": 40, "top_p": 0.95, "min_p": 0.05},
    "reproduce":    {"top_k": 1, "top_p": None, "min_p": None},
}

def derive_seed_from_uuid(uc_uuid: str) -> int:
    """Derive a stable seed from a UC uuid for reproduce mode.

    SHA-256 the uuid, take the first 8 bytes, convert to int, mod 2^31 - 1
    (positive int range that vLLM accepts). Same uuid → same seed always.
    """
    digest = hashlib.sha256(uc_uuid.encode("utf-8")).digest()
    raw = int.from_bytes(digest[:8], "big")
    return raw % ((1 << 31) - 1)

def run_stage2(
    use_case: UseCase,
    inference_client: InferenceClient,
    mcp_client: McpClient,
    config: AgentConfig | None = None,
    consumer_profile=None,
    consumer_content_path=None,
):
    """Run stage 2 on one use case and return the Analysis. Single-sample API.

    Preserved for backward compatibility with pre-δ.2 callers. New callers
    that need verification or explore behavior should use run_samples().

    optional `consumer_profile` parameterizes prompts and JSON
    schema. Defaults to the module-level default profile (DCM reference
    unless explicitly overridden via set_default_profile()).

    optional `consumer_content_path` lets the agent read
    `consumer_version` from the consumer's `dav-version.yaml`.
    """
    agent = Stage2Agent(
        inference=inference_client, mcp=mcp_client,
        config=config, consumer_profile=consumer_profile,
        consumer_content_path=consumer_content_path,
    )
    return agent.analyze(use_case)

def run_samples(
    use_case: UseCase,
    inference_factory: Callable[[], InferenceClient],
    mcp_factory: Callable[[], McpClient],
    config: AgentConfig,
    sample_seeds: list[int] | None = None,
    consumer_profile=None,
    consumer_content_path=None,
) -> list[Analysis]:
    """Run N stage-2 samples and return them as a list, in seed order.

    Each sample gets its own Stage2Agent and McpClient instance (McpClient
    is not thread-safe due to fastmcp async-context state). InferenceClient
    is shared across samples since it has no per-request mutable state and
    the underlying requests library is thread-safe.

    Concurrency:
        config.sample_concurrency == 1: serial execution (default).
        config.sample_concurrency > 1: ThreadPoolExecutor with that many
            workers. Each worker constructs its own McpClient via
            mcp_factory() and runs one sample.

    Seeds:
        If sample_seeds is None, generates [config.seed + i for i in range(N)]
        when config.seed is set, else [i for i in range(N)].

    `consumer_profile` parameterizes prompts and JSON schema for
    each sample's Stage2Agent. Defaults to the module-level default profile.

    Returns:
        list[Analysis] in the same order as sample_seeds (NOT completion order).
    """
    n = config.sample_count
    if n < 1:
        raise ValueError(f"sample_count must be >= 1, got {n}")
    if sample_seeds is None:
        if config.seed is not None:
            sample_seeds = [config.seed + i for i in range(n)]
        else:
            sample_seeds = list(range(n))
    if len(sample_seeds) != n:
        raise ValueError(
            f"sample_seeds length {len(sample_seeds)} != sample_count {n}"
        )

    # Shared inference client (thread-safe). McpClient is per-sample.
    inference = inference_factory()

    def _run_one(seed: int) -> Analysis:
        mcp = mcp_factory()
        agent = Stage2Agent(
            inference=inference, mcp=mcp, config=config,
            consumer_profile=consumer_profile,
            consumer_content_path=consumer_content_path,
        )
        agent._sample_seed = seed
        log.info("starting sample with seed=%d", seed)
        return agent.analyze(use_case)

    if config.sample_concurrency <= 1 or n == 1:
        # Serial path
        results: list[Analysis] = []
        for seed in sample_seeds:
            results.append(_run_one(seed))
        return results

    # Parallel path: preserve seed order in the output even though completion
    # order may differ.
    log.info(
        "running %d samples with concurrency=%d", n, config.sample_concurrency
    )
    results_by_seed: dict[int, Analysis] = {}
    with ThreadPoolExecutor(max_workers=config.sample_concurrency) as ex:
        future_to_seed = {ex.submit(_run_one, s): s for s in sample_seeds}
        for fut in as_completed(future_to_seed):
            s = future_to_seed[fut]
            results_by_seed[s] = fut.result()
    return [results_by_seed[s] for s in sample_seeds]

def _resolve_sample_count_and_seeds(
    args: argparse.Namespace,
    use_case: UseCase,
) -> tuple[int, list[int]]:
    """Return (sample_count, sample_seeds) based on mode + CLI overrides."""
    mode = args.mode
    n = args.sample_count if args.sample_count is not None else _DEFAULT_SAMPLE_COUNT[mode]

    if mode == "reproduce":
        if n != 1:
            log.warning(
                "reproduce mode forces sample_count=1 (got --sample-count %d, ignoring)", n
            )
        n = 1
        # Reproduce mode: seed from UC uuid unless user specified --seed
        if args.seed is not None:
            seeds = [args.seed]
        else:
            seeds = [derive_seed_from_uuid(use_case.uuid)]
        return n, seeds

    # verification / explore
    if mode == "verification" and n == 1:
        log.warning(
            "verification mode with --sample-count 1: merger runs trivially with "
            "sample_annotations populated as 1/1 consensus. Consider --mode reproduce "
            "for a cheaper single-sample run."
        )

    # Seeds: --seed sets the base; samples use [seed, seed+1, ..., seed+n-1].
    # If --seed not given, base = derive_seed_from_uuid(uc.uuid) for stability
    # across reruns (same UC, same seeds).
    base_seed = args.seed if args.seed is not None else derive_seed_from_uuid(use_case.uuid)
    seeds = [base_seed + i for i in range(n)]
    return n, seeds

def _cli():
    parser = argparse.ArgumentParser(
        description="Run DAV stage 2 analysis on a use case."
    )
    parser.add_argument("--use-case", required=True, type=Path,
                        help="Path to a use case YAML file")
    parser.add_argument("--inference-endpoint", required=True,
                        help="OpenAI-compatible base URL (e.g. http://host/v1)")
    parser.add_argument("--inference-model", required=True,
                        help="Model name served at the endpoint")
    parser.add_argument("--fallback-endpoint", default=None,
                        help="Optional fallback endpoint URL")
    parser.add_argument("--fallback-model", default=None)
    parser.add_argument("--mcp-url", required=True,
                        help="dav-docs-mcp base URL (e.g. http://host:8080)")
    parser.add_argument("--output", required=True, type=Path,
                        help="Path to write analysis YAML (verification/reproduce) "
                             "or directory for per-sample YAMLs + variance.yaml (explore)")
    parser.add_argument("--max-tool-calls", type=int, default=30)
    parser.add_argument("--temperature", type=float, default=None,
                        help="Override mode default. Defaults: verification=0.2, "
                             "reproduce=0.0, explore=0.7.")
    parser.add_argument("--top-k", type=int, default=None,
                        help="Override mode-default top-k. Defaults: "
                             "verification=40, explore=40, reproduce=1 (greedy). "
                             "Pass 0 to disable top-k filtering.")
    parser.add_argument("--top-p", type=float, default=None,
                        help="Override mode-default top-p. Defaults: "
                             "verification=0.95, explore=0.95, reproduce=unset.")
    parser.add_argument("--min-p", type=float, default=None,
                        help="Override mode-default min-p. Defaults: "
                             "verification=0.05, explore=0.05, reproduce=unset.")
    parser.add_argument("--max-tokens", type=int, default=6144)
    parser.add_argument("--no-guided-json", action="store_true",
                        help="Disable vLLM guided_json decoding (for endpoints that don't support it)")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--enable-thinking",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Control Qwen3-style reasoning via chat_template_kwargs. "
             "Default: unset (let server template decide). Use "
             "--no-enable-thinking for tool-use loops where reasoning "
             "burns budget without informing tool_calls.",
    )

    # mode dispatch
    parser.add_argument(
        "--mode",
        choices=("verification", "reproduce", "explore"),
        default="verification",
        help="Stage-2 runtime mode (default: verification). "
             "verification: N samples + ensemble merge. "
             "reproduce: 1 sample, greedy, seed from UC uuid. "
             "explore: N samples at high temp, no merge, variance report.",
    )
    parser.add_argument(
        "--sample-count", type=int, default=None,
        help="Override default sample count for the mode "
             "(verification=3, reproduce=1, explore=10).",
    )
    parser.add_argument(
        "--sample-concurrency", type=int, default=1,
        help="Run samples in parallel with this many workers (default 1, serial). "
             "Each worker uses its own MCP client; inference client is shared.",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Seed for sample 0 (subsequent samples use seed+1, seed+2, ...). "
             "Default: derived from UC uuid for stability.",
    )
    parser.add_argument(
        "--cache-prompt",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable/disable llama.cpp cross-request KV cache reuse. "
             "Default: derive from --mode (verification=True, reproduce=False, "
             "explore=True). Use --cache-prompt to force on (5-10x speedup at "
             "the cost of cache-induced logit variance), or --no-cache-prompt "
             "to force off (cold prefill on every turn; only use this if you "
             "need verification-mode bit-exactness across reruns and have "
             "thought hard about why).",
    )
    # consumer profile selection
    parser.add_argument(
        "--consumer-profile", type=Path, default=None,
        help="Path to a consumer profile YAML (overrides MCP / DCM-default). "
             "See examples/minimal-consumer/consumer-profile.yaml for shape.",
    )
    parser.add_argument(
        "--consumer-profile-from-mcp", action="store_true",
        help="If set and --consumer-profile is not given, fetch the consumer "
             "profile from the MCP server's get_consumer_profile tool. Falls "
             "back to the built-in DCM reference profile if MCP fails.",
    )
    parser.add_argument(
        "--consumer-content-path", type=Path, default=None,
        help="Path to the consumer's content tree (the cloned consumer repo). "
             "Used to read consumer_version from dav-version.yaml at the root, "
             "which gets stamped onto AnalysisMetadata.consumer_version. "
             "When unset, that field stays empty.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # load consumer profile before anything else, so validators,
    # prompts, and JSON schema all see the right vocabulary.
    from dav.core.consumer_profile import load_profile, set_default_profile
    consumer_profile = load_profile(
        path=args.consumer_profile,
        mcp_url=args.mcp_url if args.consumer_profile_from_mcp else None,
        fall_back_to_dcm=True,
    )
    # Set as module-level default so any code that doesn't take an explicit
    # profile picks it up (e.g. ad-hoc UC YAML loads, prompt-builder defaults).
    set_default_profile(consumer_profile)
    log.info(
        "consumer profile loaded: framework=%s consumer_id=%s",
        consumer_profile.framework_name, consumer_profile.consumer_id,
    )

    # Load use case
    with args.use_case.open() as f:
        uc_data = yaml.safe_load(f)
    use_case = UseCase.from_dict(uc_data)
    errors = use_case.validate(consumer_profile)
    if errors:
        print(f"Use case failed validation:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(2)

    # Resolve mode-driven sample count and seeds
    sample_count, sample_seeds = _resolve_sample_count_and_seeds(args, use_case)
    temperature = args.temperature if args.temperature is not None else _DEFAULT_TEMPERATURE[args.mode]
    cache_prompt = args.cache_prompt if args.cache_prompt is not None else _DEFAULT_CACHE_PROMPT[args.mode]

    # Resolve sampler params: CLI override > mode default. Diagnosed
    # 2026-04-26 — see run_corpus.py / client.py for full context. Modes
    # that want sampler-driven variance must send top_k/top_p/min_p
    # explicitly because llama.cpp server CLI defaults override unsent
    # fields.
    sampler_defaults = _DEFAULT_SAMPLER_PARAMS[args.mode]
    top_k = args.top_k if args.top_k is not None else sampler_defaults["top_k"]
    top_p = args.top_p if args.top_p is not None else sampler_defaults["top_p"]
    min_p = args.min_p if args.min_p is not None else sampler_defaults["min_p"]

    log.info(
        "stage2 mode=%s sample_count=%d sample_concurrency=%d temperature=%s "
        "cache_prompt=%s seeds=%s top_k=%s top_p=%s min_p=%s",
        args.mode, sample_count, args.sample_concurrency, temperature,
        cache_prompt, sample_seeds, top_k, top_p, min_p,
    )

    # Build chat_template_kwargs from --enable-thinking / --no-enable-thinking.
    tmpl_kwargs = None
    if args.enable_thinking is not None:
        tmpl_kwargs = {"enable_thinking": args.enable_thinking}

    # Factories so each sample gets a fresh McpClient (not thread-safe).
    def _make_inference() -> InferenceClient:
        primary = EndpointConfig(
            url=args.inference_endpoint,
            model=args.inference_model,
            label="primary",
            chat_template_kwargs=tmpl_kwargs,
            cache_prompt=cache_prompt,
            deterministic=(args.mode != "explore"),
            top_k=top_k,
            top_p=top_p,
            min_p=min_p,
        )
        fallback = None
        if args.fallback_endpoint:
            fallback = EndpointConfig(
                url=args.fallback_endpoint,
                model=args.fallback_model or args.inference_model,
                label="fallback",
                chat_template_kwargs=tmpl_kwargs,
                cache_prompt=cache_prompt,
                deterministic=(args.mode != "explore"),
                top_k=top_k,
                top_p=top_p,
                min_p=min_p,
            )
        return InferenceClient(primary=primary, fallback=fallback)

    def _make_mcp() -> McpClient:
        return McpClient(server_url=args.mcp_url)

    config = AgentConfig(
        max_tool_calls=args.max_tool_calls,
        temperature=temperature,
        max_tokens=args.max_tokens,
        use_guided_json=not args.no_guided_json,
        sample_count=sample_count,
        sample_concurrency=args.sample_concurrency,
    )

    # Run
    runner_started = time.monotonic()
    try:
        samples = run_samples(
            use_case=use_case,
            inference_factory=_make_inference,
            mcp_factory=_make_mcp,
            config=config,
            sample_seeds=sample_seeds,
            consumer_profile=consumer_profile,
            consumer_content_path=args.consumer_content_path,
        )
    except AgentError as e:
        print(f"Stage 2 failed: {e}", file=sys.stderr)
        sys.exit(1)
    runner_elapsed = time.monotonic() - runner_started

    # Mode-specific output handling
    if args.mode == "explore":
        _write_explore_output(args.output, samples, sample_seeds, use_case)
    elif args.mode == "reproduce":
        # Single sample, write directly (no merger)
        _write_single_analysis(args.output, samples[0])
    else:
        # verification: merge samples, write merged
        merged = merge_analyses(samples, sample_seeds=sample_seeds)
        # Override wall_time_seconds to reflect total runner time
        # (handles serial sums and parallel maxes uniformly).
        merged.analysis_metadata.wall_time_seconds = runner_elapsed
        _write_single_analysis(args.output, merged)

    # Summary
    _print_summary(args.mode, samples, sample_seeds, args.output, runner_elapsed)

def _write_single_analysis(path: Path, analysis: Analysis) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        yaml.safe_dump(
            analysis.to_dict(), f,
            sort_keys=False, default_flow_style=False, allow_unicode=True,
        )

def _write_explore_output(
    output_dir: Path,
    samples: list[Analysis],
    sample_seeds: list[int],
    use_case: UseCase,
) -> None:
    """Write per-sample YAMLs and a variance.yaml report into output_dir."""
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

def _print_summary(
    mode: str,
    samples: list[Analysis],
    sample_seeds: list[int],
    output_path: Path,
    elapsed: float,
) -> None:
    print()
    print(f"✓ Stage 2 ({mode}) complete:")
    print(f"  Samples:        {len(samples)} (seeds: {sample_seeds})")
    print(f"  Wall time:      {elapsed:.1f}s")
    print(f"  Output:         {output_path}")

    if mode == "explore":
        # Per-sample summary only
        for i, s in enumerate(samples):
            print(f"  sample-{i:02d}: verdict={s.summary.verdict}, "
                  f"confidence={s.summary.overall_confidence.label}, "
                  f"gaps={len(s.gaps_identified)}, "
                  f"tcc={s.analysis_metadata.tool_call_count}")
        return

    # verification / reproduce: summarize the single Analysis (merged or sole sample)
    if mode == "verification":
        # Re-merge for summary purposes (cheap; the file was just merged above)
        merged = merge_analyses(samples, sample_seeds=sample_seeds)
        sa = merged.sample_annotations
        print(f"  Verdict:        {merged.summary.verdict} "
              f"(votes: {sa.verdict_votes}{', tied' if sa.verdict_tied else ''})")
        print(f"  Confidence:     {merged.summary.overall_confidence.label} "
              f"(score {merged.summary.overall_confidence.score})")
        print(f"  Components:     {len(merged.components_required)}")
        print(f"  Capabilities:   {len(merged.capabilities_invoked)}")
        print(f"  Gaps:           {len(merged.gaps_identified)}")
        print(f"  Tool calls:     {merged.analysis_metadata.tool_call_count} (summed)")
        print(f"  Tokens used:    {merged.analysis_metadata.total_tokens} (summed)")
    else:  # reproduce
        s = samples[0]
        print(f"  Verdict:        {s.summary.verdict}")
        print(f"  Confidence:     {s.summary.overall_confidence.label}")
        print(f"  Components:     {len(s.components_required)}")
        print(f"  Capabilities:   {len(s.capabilities_invoked)}")
        print(f"  Gaps:           {len(s.gaps_identified)}")
        print(f"  Tool calls:     {s.analysis_metadata.tool_call_count}")
        print(f"  Tokens used:    {s.analysis_metadata.total_tokens}")

if __name__ == "__main__":
    _cli()
