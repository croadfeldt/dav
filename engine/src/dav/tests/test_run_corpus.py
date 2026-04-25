"""
Tests for the stage 2 corpus runner.

Run:  python -m dav.tests.test_run_corpus
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import yaml

from dav.ai.agent import AgentConfig
from dav.core.consumer_profile import get_dcm_reference_profile
from dav.core.use_case_schema import (
    Actor, Dimensions, Scenario, UseCase, GeneratedBy, UseCaseMetadata,
    Analysis, AnalysisMetadata, AnalysisSummary, Verdict, Confidence,
    normalize_confidence,
)
from dav.stages.run_corpus import (
    CorpusUcResult,
    gather_corpus, derive_run_id, resolve_sample_count_and_seeds,
    run_one_uc, write_run_summary, write_failure_report,
    write_uc_analysis,
    _DEFAULT_SAMPLE_COUNT,
)

_failures: list[str] = []

def assert_eq(actual, expected, label: str) -> None:
    if actual != expected:
        _failures.append(f"{label}: got {actual!r}, expected {expected!r}")

def assert_true(cond: bool, label: str) -> None:
    if not cond:
        _failures.append(f"{label}: expected truthy")

# --- Fixtures ---

def _valid_v1_uc_dict(uuid="uc-test-001", handle="test/category/descriptor") -> dict:
    """v1.0-shape UC matching DCM reference profile vocab."""
    return {
        "uuid": uuid,
        "handle": handle,
        "scenario": {
            "description": "A test scenario for the runner.",
            "actor": {"persona": "consumer", "profile": "standard"},
            "intent": "Run a UC through the corpus runner",
            "success_criteria": ["UC analyzed end-to-end"],
            "dimensions": {
                "lifecycle_phase": "new_request",
                "resource_complexity": "single_no_deps",
                "policy_complexity": "system_defaults_only",
                "provider_landscape": "single_eligible",
                "governance_context": "standard_governance",
                "failure_mode": "happy_path",
            },
            "profile": "standard",
            "expected_domain_interactions": [],
        },
        "generated_by": {"mode": "regression", "source": "human-authored"},
        "tags": [],
        "metadata": {},
    }

def _stub_analysis(uc_uuid: str = "uc-test-001") -> Analysis:
    """A minimal Analysis matching v1.0 shape, for mocking run_samples."""
    return Analysis(
        use_case_uuid=uc_uuid,
        analysis_metadata=AnalysisMetadata(
            model="test-model",
            timestamp="2026-04-25T18:00:00Z",
            stage2_run_id="run-test",
        ),
        summary=AnalysisSummary(
            verdict="supported",
            overall_confidence=normalize_confidence("high"),
            notes="Test stub",
        ),
    )

# --- gather_corpus tests ---

def test_gather_corpus_single_file():
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "uc.yaml"
        f.write_text("uuid: x")
        result = gather_corpus(f)
        assert_eq(result, [f], "single file returned")

def test_gather_corpus_recursive():
    with tempfile.TemporaryDirectory() as td:
        Path(td, "a.yaml").write_text("x: y")
        Path(td, "sub").mkdir()
        Path(td, "sub", "b.yaml").write_text("x: y")
        Path(td, "sub", "c.yml").write_text("x: y")
        result = gather_corpus(Path(td))
        names = sorted(p.name for p in result)
        assert_eq(names, ["a.yaml", "b.yaml", "c.yml"], "recursive gather")

def test_gather_corpus_skips_backup():
    with tempfile.TemporaryDirectory() as td:
        Path(td, "a.yaml").write_text("x: y")
        Path(td, "a.yaml.backup").write_text("x: y")
        result = gather_corpus(Path(td))
        names = sorted(p.name for p in result)
        assert_eq(names, ["a.yaml"], "backup skipped")

def test_gather_corpus_skips_dotfiles():
    with tempfile.TemporaryDirectory() as td:
        Path(td, "a.yaml").write_text("x: y")
        Path(td, ".hidden.yaml").write_text("x: y")
        result = gather_corpus(Path(td))
        names = sorted(p.name for p in result)
        assert_eq(names, ["a.yaml"], "dotfile skipped")

def test_gather_corpus_returns_sorted():
    """Iteration order must be stable."""
    with tempfile.TemporaryDirectory() as td:
        for name in ["zeta.yaml", "alpha.yaml", "mu.yaml"]:
            Path(td, name).write_text("x: y")
        result = gather_corpus(Path(td))
        names = [p.name for p in result]
        assert_eq(names, ["alpha.yaml", "mu.yaml", "zeta.yaml"], "sorted")

def test_gather_corpus_empty_dir_returns_empty():
    with tempfile.TemporaryDirectory() as td:
        result = gather_corpus(Path(td))
        assert_eq(result, [], "empty dir → empty list")

def test_gather_corpus_nonexistent_returns_empty():
    result = gather_corpus(Path("/definitely/does/not/exist"))
    assert_eq(result, [], "nonexistent → empty")

# --- derive_run_id tests ---

def test_derive_run_id_format():
    files = [Path("/a/b.yaml"), Path("/a/c.yaml")]
    fixed_now = datetime(2026, 4, 25, 18, 30, 0, tzinfo=timezone.utc)
    run_id = derive_run_id(files, now_utc=fixed_now)
    assert_true(run_id.startswith("2026-04-25T18-30-00Z-"),
                f"timestamp prefix; got {run_id}")
    parts = run_id.split("-")
    # Hash is the last segment after the timestamp parts
    suffix = run_id.rsplit("-", 1)[-1]
    assert_eq(len(suffix), 7, "7-char hash")

def test_derive_run_id_stable_for_same_corpus():
    files = [Path("/a/b.yaml"), Path("/a/c.yaml")]
    fixed_now = datetime(2026, 4, 25, 18, 30, 0, tzinfo=timezone.utc)
    r1 = derive_run_id(files, now_utc=fixed_now)
    r2 = derive_run_id(files, now_utc=fixed_now)
    assert_eq(r1, r2, "same corpus + same now → same run-id")

def test_derive_run_id_changes_with_corpus():
    fixed_now = datetime(2026, 4, 25, 18, 30, 0, tzinfo=timezone.utc)
    r1 = derive_run_id([Path("/a/b.yaml")], now_utc=fixed_now)
    r2 = derive_run_id([Path("/a/b.yaml"), Path("/a/c.yaml")], now_utc=fixed_now)
    assert_true(r1 != r2, "different corpus → different run-id")

def test_derive_run_id_order_independent():
    """Sorting inside derive_run_id means input order doesn't matter."""
    fixed_now = datetime(2026, 4, 25, 18, 30, 0, tzinfo=timezone.utc)
    r1 = derive_run_id([Path("/a/b.yaml"), Path("/a/c.yaml")], now_utc=fixed_now)
    r2 = derive_run_id([Path("/a/c.yaml"), Path("/a/b.yaml")], now_utc=fixed_now)
    assert_eq(r1, r2, "input order doesn't change run-id")

# --- resolve_sample_count_and_seeds tests ---

def test_resolve_verification_default():
    n, seeds = resolve_sample_count_and_seeds(
        mode="verification", requested_count=None,
        seed_override=None, uc_uuid="uc-test-001",
    )
    assert_eq(n, _DEFAULT_SAMPLE_COUNT["verification"], "verification default N")
    assert_eq(len(seeds), n, "seeds length matches N")

def test_resolve_reproduce_forces_n1():
    n, seeds = resolve_sample_count_and_seeds(
        mode="reproduce", requested_count=5,
        seed_override=None, uc_uuid="uc-test-001",
    )
    assert_eq(n, 1, "reproduce forces N=1")
    assert_eq(len(seeds), 1, "single seed")

def test_resolve_explore_default():
    n, seeds = resolve_sample_count_and_seeds(
        mode="explore", requested_count=None,
        seed_override=None, uc_uuid="uc-test-001",
    )
    assert_eq(n, _DEFAULT_SAMPLE_COUNT["explore"], "explore default N")

def test_resolve_seeds_derive_from_uuid():
    """Same UC uuid → same seeds across calls."""
    n1, s1 = resolve_sample_count_and_seeds(
        mode="verification", requested_count=3,
        seed_override=None, uc_uuid="uc-stable",
    )
    n2, s2 = resolve_sample_count_and_seeds(
        mode="verification", requested_count=3,
        seed_override=None, uc_uuid="uc-stable",
    )
    assert_eq(s1, s2, "seeds stable for same UC")

def test_resolve_seeds_differ_per_uuid():
    n1, s1 = resolve_sample_count_and_seeds(
        mode="verification", requested_count=3,
        seed_override=None, uc_uuid="uc-a",
    )
    n2, s2 = resolve_sample_count_and_seeds(
        mode="verification", requested_count=3,
        seed_override=None, uc_uuid="uc-b",
    )
    assert_true(s1 != s2, "different UCs → different seeds")

def test_resolve_seed_override():
    n, seeds = resolve_sample_count_and_seeds(
        mode="verification", requested_count=3,
        seed_override=42, uc_uuid="uc-test-001",
    )
    assert_eq(seeds, [42, 43, 44], "override + sequential samples")

def test_resolve_seed_override_reproduce():
    n, seeds = resolve_sample_count_and_seeds(
        mode="reproduce", requested_count=None,
        seed_override=99, uc_uuid="uc-test-001",
    )
    assert_eq(seeds, [99], "reproduce uses override")

# --- run_one_uc tests ---

def test_run_one_uc_happy_path():
    """A valid UC + mocked run_samples → success result with file written."""
    p = get_dcm_reference_profile()
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        uc_path = td_path / "uc.yaml"
        with uc_path.open("w") as f:
            yaml.safe_dump(_valid_v1_uc_dict(), f)
        run_dir = td_path / "run"
        run_dir.mkdir()

        with mock.patch("dav.stages.run_corpus.run_samples",
                        return_value=[_stub_analysis()]):
            result = run_one_uc(
                uc_path=uc_path,
                run_dir=run_dir,
                inference_factory=lambda: None,
                mcp_factory=lambda: None,
                config=AgentConfig(sample_count=1, seed=42),
                mode="reproduce",
                consumer_profile=p,
                consumer_content_path=None,
            )
        assert_eq(result.success, True, f"happy path success; got error={result.error}")
        assert_eq(result.uc_uuid, "uc-test-001", "uuid captured")
        assert_true(result.output_path is not None and result.output_path.exists(),
                    "output file written")
        assert_eq(result.sample_count, 1, "sample count")

def test_run_one_uc_load_failure():
    """Invalid YAML → failure result, no exception."""
    p = get_dcm_reference_profile()
    with tempfile.TemporaryDirectory() as td:
        uc_path = Path(td) / "bad.yaml"
        uc_path.write_text("not: valid: yaml:")  # malformed
        run_dir = Path(td) / "run"
        run_dir.mkdir()
        result = run_one_uc(
            uc_path=uc_path, run_dir=run_dir,
            inference_factory=lambda: None, mcp_factory=lambda: None,
            config=AgentConfig(sample_count=1),
            mode="reproduce",
            consumer_profile=p, consumer_content_path=None,
        )
        assert_eq(result.success, False, "load failure → not success")
        assert_true("load" in (result.error or "").lower(),
                    f"load error reported; got: {result.error}")

def test_run_one_uc_validation_failure():
    """UC with invalid vocab → failure result."""
    p = get_dcm_reference_profile()
    with tempfile.TemporaryDirectory() as td:
        uc_path = Path(td) / "bad.yaml"
        uc_data = _valid_v1_uc_dict()
        # Inject an unknown vocab value
        uc_data["scenario"]["dimensions"]["governance_context"] = "not_in_vocab"
        with uc_path.open("w") as f:
            yaml.safe_dump(uc_data, f)
        run_dir = Path(td) / "run"
        run_dir.mkdir()
        result = run_one_uc(
            uc_path=uc_path, run_dir=run_dir,
            inference_factory=lambda: None, mcp_factory=lambda: None,
            config=AgentConfig(sample_count=1),
            mode="reproduce",
            consumer_profile=p, consumer_content_path=None,
        )
        assert_eq(result.success, False, "validation failure")
        assert_true("validation" in (result.error or "").lower(),
                    f"validation reported; got: {result.error}")

def test_run_one_uc_analyzer_failure():
    """run_samples raises → failure result, no exception leaks."""
    p = get_dcm_reference_profile()
    with tempfile.TemporaryDirectory() as td:
        uc_path = Path(td) / "uc.yaml"
        with uc_path.open("w") as f:
            yaml.safe_dump(_valid_v1_uc_dict(), f)
        run_dir = Path(td) / "run"
        run_dir.mkdir()

        with mock.patch("dav.stages.run_corpus.run_samples",
                        side_effect=RuntimeError("inference timeout")):
            result = run_one_uc(
                uc_path=uc_path, run_dir=run_dir,
                inference_factory=lambda: None, mcp_factory=lambda: None,
                config=AgentConfig(sample_count=1),
                mode="reproduce",
                consumer_profile=p, consumer_content_path=None,
            )
        assert_eq(result.success, False, "analyzer failure")
        assert_true("inference timeout" in (result.error or ""),
                    f"error message preserved; got: {result.error}")

def test_run_one_uc_explore_writes_directory():
    """Explore mode writes per-sample files + variance.yaml in a subdirectory."""
    p = get_dcm_reference_profile()
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        uc_path = td_path / "uc.yaml"
        with uc_path.open("w") as f:
            yaml.safe_dump(_valid_v1_uc_dict(), f)
        run_dir = td_path / "run"
        run_dir.mkdir()

        # 3 stub samples
        samples = [_stub_analysis() for _ in range(3)]
        with mock.patch("dav.stages.run_corpus.run_samples",
                        return_value=samples):
            result = run_one_uc(
                uc_path=uc_path, run_dir=run_dir,
                inference_factory=lambda: None, mcp_factory=lambda: None,
                config=AgentConfig(sample_count=3, seed=42),
                mode="explore",
                consumer_profile=p, consumer_content_path=None,
            )
        assert_eq(result.success, True, "explore happy path")
        assert_true(result.output_dir is not None and result.output_dir.is_dir(),
                    "explore output dir created")
        files = sorted(p.name for p in result.output_dir.iterdir())
        assert_true("sample-00.yaml" in files, f"sample files present; got {files}")
        assert_true("variance.yaml" in files, f"variance present; got {files}")

def test_run_one_uc_verification_with_multiple_samples_merges():
    """Verification mode with N>1 samples calls merge_analyses."""
    p = get_dcm_reference_profile()
    with tempfile.TemporaryDirectory() as td:
        uc_path = Path(td) / "uc.yaml"
        with uc_path.open("w") as f:
            yaml.safe_dump(_valid_v1_uc_dict(), f)
        run_dir = Path(td) / "run"
        run_dir.mkdir()

        samples = [_stub_analysis() for _ in range(3)]
        with mock.patch("dav.stages.run_corpus.run_samples", return_value=samples), \
             mock.patch("dav.stages.run_corpus.merge_analyses",
                        return_value=samples[0]) as mmerge:
            result = run_one_uc(
                uc_path=uc_path, run_dir=run_dir,
                inference_factory=lambda: None, mcp_factory=lambda: None,
                config=AgentConfig(sample_count=3),
                mode="verification",
                consumer_profile=p, consumer_content_path=None,
            )
        assert_eq(result.success, True, "verification path")
        assert_eq(mmerge.call_count, 1, "merger called once")

def test_run_one_uc_verification_n1_skips_merge():
    """Verification mode with N=1 sample does NOT call merge_analyses."""
    p = get_dcm_reference_profile()
    with tempfile.TemporaryDirectory() as td:
        uc_path = Path(td) / "uc.yaml"
        with uc_path.open("w") as f:
            yaml.safe_dump(_valid_v1_uc_dict(), f)
        run_dir = Path(td) / "run"
        run_dir.mkdir()

        with mock.patch("dav.stages.run_corpus.run_samples",
                        return_value=[_stub_analysis()]), \
             mock.patch("dav.stages.run_corpus.merge_analyses") as mmerge:
            result = run_one_uc(
                uc_path=uc_path, run_dir=run_dir,
                inference_factory=lambda: None, mcp_factory=lambda: None,
                config=AgentConfig(sample_count=1),
                mode="verification",
                consumer_profile=p, consumer_content_path=None,
            )
        assert_eq(result.success, True, "trivial verification")
        assert_eq(mmerge.call_count, 0, "merger NOT called for N=1")

# --- write_run_summary tests ---

def test_write_run_summary_structure():
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        results = [
            CorpusUcResult(
                uc_uuid="uc-1", uc_handle="t/a", uc_path=Path("/a.yaml"),
                success=True, output_path=td_path / "analyses/uc-1.yaml",
                wall_time_seconds=1.5, sample_count=3,
            ),
            CorpusUcResult(
                uc_uuid="uc-2", uc_handle="t/b", uc_path=Path("/b.yaml"),
                success=False, error="boom",
                wall_time_seconds=0.5, sample_count=0,
            ),
        ]
        out = write_run_summary(
            run_dir=td_path, run_id="2026-X-abc1234",
            mode="verification", results=results,
            runner_started_at="2026-04-25T18:00:00Z",
            runner_total_seconds=2.0,
        )
        assert_true(out.exists(), "summary file written")
        with out.open() as f:
            data = yaml.safe_load(f)
        assert_eq(data["total_ucs"], 2, "total UCs counted")
        assert_eq(data["successful"], 1, "successful count")
        assert_eq(data["failed"], 1, "failed count")
        assert_eq(data["mode"], "verification", "mode preserved")
        assert_eq(data["run_id"], "2026-X-abc1234", "run id preserved")
        assert_eq(len(data["ucs"]), 2, "per-UC entries")
        # Failed UC should have error key, success shouldn't
        failed_entry = next(u for u in data["ucs"] if u["status"] == "failed")
        assert_true("error" in failed_entry, "failed UC has error")
        success_entry = next(u for u in data["ucs"] if u["status"] == "success")
        assert_true("error" not in success_entry, "success UC has no error")

def test_write_run_summary_empty_results():
    """Edge case: no UCs ran at all."""
    with tempfile.TemporaryDirectory() as td:
        out = write_run_summary(
            run_dir=Path(td), run_id="empty-run",
            mode="verification", results=[],
            runner_started_at="2026-04-25T18:00:00Z",
            runner_total_seconds=0.1,
        )
        with out.open() as f:
            data = yaml.safe_load(f)
        assert_eq(data["total_ucs"], 0, "zero total")
        assert_eq(data["successful"], 0, "zero success")
        assert_eq(data["mean_uc_wall_time_seconds"], 0.0, "no division-by-zero")

# --- write_failure_report tests ---

def test_write_failure_report():
    with tempfile.TemporaryDirectory() as td:
        result = CorpusUcResult(
            uc_uuid="uc-failed-001", uc_handle="test/h",
            uc_path=Path("/path/to/uc.yaml"),
            success=False, error="something broke",
            wall_time_seconds=2.5, sample_count=0,
        )
        write_failure_report(Path(td), result)
        out_file = Path(td) / "failures" / "uc-failed-001.error.txt"
        assert_true(out_file.exists(), "failure file written")
        text = out_file.read_text()
        assert_true("uc-failed-001" in text, "uuid in report")
        assert_true("something broke" in text, "error message in report")
        assert_true("test/h" in text, "handle in report")

def test_write_failure_report_handles_slash_in_uuid():
    """UUIDs with slashes get sanitized for filesystem safety."""
    with tempfile.TemporaryDirectory() as td:
        result = CorpusUcResult(
            uc_uuid="uc-with/slash", uc_handle="t/h",
            uc_path=Path("/p"), success=False,
            error="x", wall_time_seconds=0.0, sample_count=0,
        )
        write_failure_report(Path(td), result)
        out_file = Path(td) / "failures" / "uc-with_slash.error.txt"
        assert_true(out_file.exists(), "slash sanitized to underscore")

# --- Run ---

def main():
    tests = [
        test_gather_corpus_single_file,
        test_gather_corpus_recursive,
        test_gather_corpus_skips_backup,
        test_gather_corpus_skips_dotfiles,
        test_gather_corpus_returns_sorted,
        test_gather_corpus_empty_dir_returns_empty,
        test_gather_corpus_nonexistent_returns_empty,
        test_derive_run_id_format,
        test_derive_run_id_stable_for_same_corpus,
        test_derive_run_id_changes_with_corpus,
        test_derive_run_id_order_independent,
        test_resolve_verification_default,
        test_resolve_reproduce_forces_n1,
        test_resolve_explore_default,
        test_resolve_seeds_derive_from_uuid,
        test_resolve_seeds_differ_per_uuid,
        test_resolve_seed_override,
        test_resolve_seed_override_reproduce,
        test_run_one_uc_happy_path,
        test_run_one_uc_load_failure,
        test_run_one_uc_validation_failure,
        test_run_one_uc_analyzer_failure,
        test_run_one_uc_explore_writes_directory,
        test_run_one_uc_verification_with_multiple_samples_merges,
        test_run_one_uc_verification_n1_skips_merge,
        test_write_run_summary_structure,
        test_write_run_summary_empty_results,
        test_write_failure_report,
        test_write_failure_report_handles_slash_in_uuid,
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
