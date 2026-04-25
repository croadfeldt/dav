"""
Tests for stage2_analyze orchestration: mode dispatch, seed derivation,
sample iteration (serial + parallel), and output handling.

Mocks Stage2Agent.analyze() to avoid real LLM calls — these tests verify
the orchestration logic, not the agent itself.

Run:  python -m dav.tests.test_stage2_orchestration
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest import mock

import yaml

from dav.core.use_case_schema import (
    Analysis, AnalysisMetadata, AnalysisSummary,
    UseCase, Scenario, Actor, Dimensions, GeneratedBy, UseCaseMetadata,
    ComponentRequired, GapIdentified,
    normalize_severity, normalize_confidence,
)
from dav.stages.stage2_analyze import (
    derive_seed_from_uuid, run_samples, _resolve_sample_count_and_seeds,
    _DEFAULT_SAMPLE_COUNT, _DEFAULT_TEMPERATURE,
)
from dav.ai.agent import AgentConfig
import argparse

_failures: list[str] = []

def assert_eq(actual, expected, label: str) -> None:
    if actual != expected:
        _failures.append(f"{label}: got {actual!r}, expected {expected!r}")

def assert_true(cond: bool, label: str) -> None:
    if not cond:
        _failures.append(f"{label}: expected truthy")

def assert_raises(fn, exc, label: str) -> None:
    try:
        fn()
    except exc:
        return
    except Exception as e:
        _failures.append(f"{label}: expected {exc.__name__}, got {type(e).__name__}: {e}")
        return
    _failures.append(f"{label}: expected {exc.__name__}, no exception raised")

# --- Builders ---

def _mk_use_case(uuid: str = "uc-stage2-test-001") -> UseCase:
    return UseCase(
        uuid=uuid,
        handle="test/case",
        scenario=Scenario(
            description="test scenario",
            actor=Actor(persona="developer", profile="dev"),
            intent="test intent",
            success_criteria=["criterion 1"],
            dimensions=Dimensions(
                lifecycle_phase="new_request",
                resource_complexity="single_no_deps",
                policy_complexity="system_defaults_only",
                provider_landscape="single_eligible",
                governance_context="standard_governance",
                failure_mode="happy_path",
            ),
            profile="dev",
        ),
        generated_by=GeneratedBy(
            mode="regression",
            source="human-authored",
        ),
        metadata=UseCaseMetadata(),
    )

def _mk_analysis(uuid: str, verdict: str = "supported") -> Analysis:
    return Analysis(
        use_case_uuid=uuid,
        analysis_metadata=AnalysisMetadata(model="mock", tool_call_count=3,
                                            total_tokens=100, wall_time_seconds=1.0),
        summary=AnalysisSummary(
            verdict=verdict,
            overall_confidence=normalize_confidence("medium"),
            notes="mock",
        ),
        components_required=[
            ComponentRequired(
                id="mock_comp", role="x", rationale="y", spec_refs=[],
                confidence=normalize_confidence("high"),
            ),
        ],
    )

# --- derive_seed_from_uuid tests ---

def test_seed_is_deterministic_per_uuid():
    s1 = derive_seed_from_uuid("uc-foo-001")
    s2 = derive_seed_from_uuid("uc-foo-001")
    assert_eq(s1, s2, "same uuid → same seed")

def test_seed_differs_across_uuids():
    s1 = derive_seed_from_uuid("uc-foo-001")
    s2 = derive_seed_from_uuid("uc-foo-002")
    assert_true(s1 != s2, "different uuids → different seeds")

def test_seed_in_valid_range():
    s = derive_seed_from_uuid("uc-test-001")
    assert_true(0 <= s < (1 << 31) - 1, "seed in valid int31 range")

# --- _resolve_sample_count_and_seeds ---

def _mk_args(mode: str, sample_count: int | None = None, seed: int | None = None) -> argparse.Namespace:
    return argparse.Namespace(mode=mode, sample_count=sample_count, seed=seed)

def test_resolve_verification_default():
    uc = _mk_use_case()
    args = _mk_args("verification")
    n, seeds = _resolve_sample_count_and_seeds(args, uc)
    assert_eq(n, 3, "verification default n=3")
    assert_eq(len(seeds), 3, "3 seeds")
    base = derive_seed_from_uuid(uc.uuid)
    assert_eq(seeds, [base, base + 1, base + 2], "seeds = [base, base+1, base+2]")

def test_resolve_verification_n1_warns_and_runs():
    """N=1 verification still runs (decision #3) — warning is logged but no error."""
    uc = _mk_use_case()
    args = _mk_args("verification", sample_count=1)
    n, seeds = _resolve_sample_count_and_seeds(args, uc)
    assert_eq(n, 1, "n=1 honored")
    assert_eq(len(seeds), 1, "1 seed")

def test_resolve_reproduce_default_uses_uuid_seed():
    uc = _mk_use_case()
    args = _mk_args("reproduce")
    n, seeds = _resolve_sample_count_and_seeds(args, uc)
    assert_eq(n, 1, "reproduce always n=1")
    assert_eq(seeds, [derive_seed_from_uuid(uc.uuid)], "uuid-derived seed")

def test_resolve_reproduce_explicit_seed_overrides_uuid():
    uc = _mk_use_case()
    args = _mk_args("reproduce", seed=999)
    n, seeds = _resolve_sample_count_and_seeds(args, uc)
    assert_eq(seeds, [999], "explicit seed wins")

def test_resolve_reproduce_forces_n1():
    """reproduce mode with --sample-count > 1 ignores the override and uses 1."""
    uc = _mk_use_case()
    args = _mk_args("reproduce", sample_count=5)
    n, seeds = _resolve_sample_count_and_seeds(args, uc)
    assert_eq(n, 1, "reproduce forces n=1")

def test_resolve_explore_default():
    uc = _mk_use_case()
    args = _mk_args("explore")
    n, seeds = _resolve_sample_count_and_seeds(args, uc)
    assert_eq(n, 10, "explore default n=10")
    assert_eq(len(seeds), 10, "10 seeds")

def test_resolve_explicit_sample_count_override():
    uc = _mk_use_case()
    args = _mk_args("verification", sample_count=5)
    n, _ = _resolve_sample_count_and_seeds(args, uc)
    assert_eq(n, 5, "--sample-count 5 honored")

# --- run_samples (with mocked agent) ---

def test_run_samples_serial():
    """3 samples, serial. Results in seed order."""
    uc = _mk_use_case()
    config = AgentConfig(sample_count=3, sample_concurrency=1, seed=100)

    mock_inference = mock.MagicMock()
    mock_mcp = mock.MagicMock()

    seeds_seen = []

    def fake_analyze(self, use_case):
        seeds_seen.append(self._sample_seed)
        return _mk_analysis(use_case.uuid)

    with mock.patch("dav.ai.agent.Stage2Agent.analyze", new=fake_analyze):
        results = run_samples(
            use_case=uc,
            inference_factory=lambda: mock_inference,
            mcp_factory=lambda: mock_mcp,
            config=config,
            sample_seeds=[100, 101, 102],
        )

    assert_eq(len(results), 3, "3 results")
    assert_eq(seeds_seen, [100, 101, 102], "serial seed order preserved")

def test_run_samples_seed_order_preserved_under_parallel():
    """4 samples, concurrency=4. Results must be in seed order even though
    completion order is non-deterministic."""
    uc = _mk_use_case()
    config = AgentConfig(sample_count=4, sample_concurrency=4, seed=200)

    mock_inference = mock.MagicMock()

    def fake_analyze(self, use_case):
        # Sleep duration inversely proportional to seed → sample with seed 203
        # finishes first, sample with seed 200 finishes last. Tests that
        # results come back in seed order regardless.
        time.sleep((205 - self._sample_seed) * 0.02)
        a = _mk_analysis(use_case.uuid)
        a.analysis_metadata.stage2_run_id = f"seed-{self._sample_seed}"
        return a

    with mock.patch("dav.ai.agent.Stage2Agent.analyze", new=fake_analyze):
        results = run_samples(
            use_case=uc,
            inference_factory=lambda: mock_inference,
            mcp_factory=lambda: mock.MagicMock(),
            config=config,
            sample_seeds=[200, 201, 202, 203],
        )

    assert_eq(len(results), 4, "4 results")
    run_ids = [r.analysis_metadata.stage2_run_id for r in results]
    assert_eq(run_ids, ["seed-200", "seed-201", "seed-202", "seed-203"],
              "results in seed order")

def test_run_samples_default_seeds():
    """No sample_seeds provided → derived from config.seed."""
    uc = _mk_use_case()
    config = AgentConfig(sample_count=3, sample_concurrency=1, seed=500)

    mock_inference = mock.MagicMock()
    seeds_seen = []

    def fake_analyze(self, use_case):
        seeds_seen.append(self._sample_seed)
        return _mk_analysis(use_case.uuid)

    with mock.patch("dav.ai.agent.Stage2Agent.analyze", new=fake_analyze):
        run_samples(
            use_case=uc,
            inference_factory=lambda: mock_inference,
            mcp_factory=lambda: mock.MagicMock(),
            config=config,
        )

    assert_eq(seeds_seen, [500, 501, 502], "default seeds = [seed, seed+1, seed+2]")

def test_run_samples_no_config_seed():
    """config.seed is None and no sample_seeds → seeds = [0, 1, ..., n-1]."""
    uc = _mk_use_case()
    config = AgentConfig(sample_count=3, sample_concurrency=1, seed=None)

    seeds_seen = []

    def fake_analyze(self, use_case):
        seeds_seen.append(self._sample_seed)
        return _mk_analysis(use_case.uuid)

    with mock.patch("dav.ai.agent.Stage2Agent.analyze", new=fake_analyze):
        run_samples(
            use_case=uc,
            inference_factory=lambda: mock.MagicMock(),
            mcp_factory=lambda: mock.MagicMock(),
            config=config,
        )
    assert_eq(seeds_seen, [0, 1, 2], "fallback seeds [0, 1, 2]")

def test_run_samples_validates_count():
    uc = _mk_use_case()
    config = AgentConfig(sample_count=0)
    assert_raises(
        lambda: run_samples(
            use_case=uc,
            inference_factory=lambda: mock.MagicMock(),
            mcp_factory=lambda: mock.MagicMock(),
            config=config,
        ),
        ValueError, "sample_count=0 rejected"
    )

def test_run_samples_validates_seeds_length():
    uc = _mk_use_case()
    config = AgentConfig(sample_count=3, seed=100)
    assert_raises(
        lambda: run_samples(
            use_case=uc,
            inference_factory=lambda: mock.MagicMock(),
            mcp_factory=lambda: mock.MagicMock(),
            config=config,
            sample_seeds=[1, 2],   # length 2, but sample_count=3
        ),
        ValueError, "seeds length mismatch"
    )

def test_run_samples_each_sample_gets_fresh_mcp():
    """Each sample must get its own McpClient (it's not thread-safe)."""
    uc = _mk_use_case()
    config = AgentConfig(sample_count=3, sample_concurrency=1, seed=100)

    mcp_instances = []

    def fake_analyze(self, use_case):
        mcp_instances.append(id(self.mcp))
        return _mk_analysis(use_case.uuid)

    with mock.patch("dav.ai.agent.Stage2Agent.analyze", new=fake_analyze):
        run_samples(
            use_case=uc,
            inference_factory=lambda: mock.MagicMock(),
            mcp_factory=lambda: mock.MagicMock(),
            config=config,
            sample_seeds=[100, 101, 102],
        )

    assert_eq(len(set(mcp_instances)), 3, "each sample got fresh McpClient")

def test_run_samples_inference_is_shared():
    """InferenceClient is constructed once and shared across samples."""
    uc = _mk_use_case()
    config = AgentConfig(sample_count=3, sample_concurrency=1, seed=100)

    inf_call_count = [0]
    shared_inf = mock.MagicMock()

    def make_inf():
        inf_call_count[0] += 1
        return shared_inf

    inference_seen = []

    def fake_analyze(self, use_case):
        inference_seen.append(id(self.inference))
        return _mk_analysis(use_case.uuid)

    with mock.patch("dav.ai.agent.Stage2Agent.analyze", new=fake_analyze):
        run_samples(
            use_case=uc,
            inference_factory=make_inf,
            mcp_factory=lambda: mock.MagicMock(),
            config=config,
            sample_seeds=[100, 101, 102],
        )

    assert_eq(inf_call_count[0], 1, "inference_factory called once")
    assert_eq(len(set(inference_seen)), 1, "all samples share one InferenceClient")

# --- Run ---

def main():
    tests = [
        test_seed_is_deterministic_per_uuid,
        test_seed_differs_across_uuids,
        test_seed_in_valid_range,
        test_resolve_verification_default,
        test_resolve_verification_n1_warns_and_runs,
        test_resolve_reproduce_default_uses_uuid_seed,
        test_resolve_reproduce_explicit_seed_overrides_uuid,
        test_resolve_reproduce_forces_n1,
        test_resolve_explore_default,
        test_resolve_explicit_sample_count_override,
        test_run_samples_serial,
        test_run_samples_seed_order_preserved_under_parallel,
        test_run_samples_default_seeds,
        test_run_samples_no_config_seed,
        test_run_samples_validates_count,
        test_run_samples_validates_seeds_length,
        test_run_samples_each_sample_gets_fresh_mcp,
        test_run_samples_inference_is_shared,
    ]
    for t in tests:
        try:
            t()
        except Exception as e:
            _failures.append(f"{t.__name__} threw: {type(e).__name__}: {e}")
    if _failures:
        print(f"FAIL: {len(_failures)} assertion(s)/error(s):")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"OK: {len(tests)} tests passed")

if __name__ == "__main__":
    main()
