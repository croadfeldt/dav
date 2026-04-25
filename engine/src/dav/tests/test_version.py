"""
Tests for engine + consumer version helpers.

Run:  python -m dav.tests.test_version
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import yaml

from dav.core import version as version_mod
from dav.core.version import (
    engine_version_string, engine_commit_string,
    consumer_version_string, reset_caches,
)

_failures: list[str] = []

def assert_eq(actual, expected, label: str) -> None:
    if actual != expected:
        _failures.append(f"{label}: got {actual!r}, expected {expected!r}")

def assert_true(cond: bool, label: str) -> None:
    if not cond:
        _failures.append(f"{label}: expected truthy")

# --- engine_version_string tests ---

def test_engine_version_calls_git_describe():
    reset_caches()
    fake = mock.MagicMock()
    fake.returncode = 0
    fake.stdout = "v0.1.0-3-gabc1234\n"
    fake.stderr = ""
    with mock.patch("subprocess.run", return_value=fake) as mrun:
        result = engine_version_string()
    assert_eq(result, "v0.1.0-3-gabc1234", "version stripped of newline")
    # Verify it called git describe
    args = mrun.call_args[0][0]
    assert_true("describe" in args, f"called git describe; got {args}")
    assert_true("--tags" in args, "with --tags")
    assert_true("--always" in args, "with --always")
    assert_true("--dirty" in args, "with --dirty")

def test_engine_version_caches():
    reset_caches()
    fake = mock.MagicMock()
    fake.returncode = 0
    fake.stdout = "v1.0.0\n"
    fake.stderr = ""
    with mock.patch("subprocess.run", return_value=fake) as mrun:
        v1 = engine_version_string()
        v2 = engine_version_string()
        v3 = engine_version_string()
    assert_eq(v1, "v1.0.0", "first call result")
    assert_eq(v2, v1, "second call cached")
    assert_eq(v3, v1, "third call cached")
    assert_eq(mrun.call_count, 1, "subprocess called only once due to cache")

def test_engine_version_returns_empty_on_failure():
    reset_caches()
    fake = mock.MagicMock()
    fake.returncode = 128       # git error code
    fake.stdout = ""
    fake.stderr = "fatal: not a git repository\n"
    with mock.patch("subprocess.run", return_value=fake):
        result = engine_version_string()
    assert_eq(result, "", "empty on git failure")

def test_engine_version_returns_empty_when_git_missing():
    reset_caches()
    with mock.patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
        result = engine_version_string()
    assert_eq(result, "", "empty when git binary missing")

def test_engine_version_returns_empty_on_timeout():
    reset_caches()
    with mock.patch("subprocess.run",
                    side_effect=subprocess.TimeoutExpired("git", 10)):
        result = engine_version_string()
    assert_eq(result, "", "empty on timeout")

# --- engine_commit_string tests ---

def test_engine_commit_calls_rev_parse():
    reset_caches()
    fake = mock.MagicMock()
    fake.returncode = 0
    fake.stdout = "abc1234567890abcdef1234567890abcdef12345\n"
    fake.stderr = ""
    with mock.patch("subprocess.run", return_value=fake) as mrun:
        result = engine_commit_string()
    assert_eq(result, "abc1234567890abcdef1234567890abcdef12345",
              "full sha returned")
    args = mrun.call_args[0][0]
    assert_true("rev-parse" in args, f"called git rev-parse; got {args}")
    assert_true("HEAD" in args, "for HEAD")

def test_engine_commit_caches():
    reset_caches()
    fake = mock.MagicMock()
    fake.returncode = 0
    fake.stdout = "deadbeef\n"
    fake.stderr = ""
    with mock.patch("subprocess.run", return_value=fake) as mrun:
        c1 = engine_commit_string()
        c2 = engine_commit_string()
    assert_eq(c1, "deadbeef", "first call")
    assert_eq(c2, "deadbeef", "second cached")
    assert_eq(mrun.call_count, 1, "single subprocess invocation")

# --- consumer_version_string tests ---

def test_consumer_version_returns_empty_for_none():
    assert_eq(consumer_version_string(None), "", "None path")

def test_consumer_version_returns_empty_for_missing_path():
    assert_eq(consumer_version_string("/definitely/does/not/exist"), "",
              "missing path")

def test_consumer_version_returns_empty_when_manifest_missing():
    with tempfile.TemporaryDirectory() as td:
        # Directory exists but no dav-version.yaml inside
        result = consumer_version_string(td)
        assert_eq(result, "", "no manifest")

def test_consumer_version_reads_consumer_version_field():
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "dav-version.yaml"
        with manifest.open("w") as f:
            yaml.safe_dump({"consumer_version": "2.3.1"}, f)
        result = consumer_version_string(td)
    assert_eq(result, "2.3.1", "consumer_version field read")

def test_consumer_version_falls_back_to_version_field():
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "dav-version.yaml"
        with manifest.open("w") as f:
            yaml.safe_dump({"version": "1.0.0-rc1"}, f)
        result = consumer_version_string(td)
    assert_eq(result, "1.0.0-rc1", "version field fallback")

def test_consumer_version_consumer_version_takes_precedence():
    """If both consumer_version and version are present, consumer_version wins."""
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "dav-version.yaml"
        with manifest.open("w") as f:
            yaml.safe_dump({
                "consumer_version": "explicit",
                "version": "fallback",
            }, f)
        result = consumer_version_string(td)
    assert_eq(result, "explicit", "consumer_version takes precedence")

def test_consumer_version_returns_empty_for_legacy_manifest():
    """Manifest with only schema_version/engine_minimum_version (no version field).

    The existing examples/minimal-consumer/dav-version.yaml has this shape.
    Should return empty string — schema_version is NOT a fallback, since it
    means something different (DAV-version compatibility range).
    """
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "dav-version.yaml"
        with manifest.open("w") as f:
            yaml.safe_dump({
                "schema_version": 1.0,
                "engine_minimum_version": "1.0.0",
                "engine_maximum_version": "1.99.0",
            }, f)
        result = consumer_version_string(td)
    assert_eq(result, "", "schema_version is not a fallback")

def test_consumer_version_returns_empty_for_invalid_yaml():
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "dav-version.yaml"
        manifest.write_text("not: valid: yaml: this is broken")
        result = consumer_version_string(td)
    assert_eq(result, "", "invalid YAML")

def test_consumer_version_returns_empty_for_non_mapping():
    """YAML that's a list, not a mapping."""
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "dav-version.yaml"
        with manifest.open("w") as f:
            yaml.safe_dump(["not", "a", "mapping"], f)
        result = consumer_version_string(td)
    assert_eq(result, "", "non-mapping YAML")

def test_consumer_version_strips_whitespace():
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "dav-version.yaml"
        with manifest.open("w") as f:
            yaml.safe_dump({"consumer_version": "  1.0.0  "}, f)
        result = consumer_version_string(td)
    assert_eq(result, "1.0.0", "version stripped")

# --- Run ---

def main():
    tests = [
        test_engine_version_calls_git_describe,
        test_engine_version_caches,
        test_engine_version_returns_empty_on_failure,
        test_engine_version_returns_empty_when_git_missing,
        test_engine_version_returns_empty_on_timeout,
        test_engine_commit_calls_rev_parse,
        test_engine_commit_caches,
        test_consumer_version_returns_empty_for_none,
        test_consumer_version_returns_empty_for_missing_path,
        test_consumer_version_returns_empty_when_manifest_missing,
        test_consumer_version_reads_consumer_version_field,
        test_consumer_version_falls_back_to_version_field,
        test_consumer_version_consumer_version_takes_precedence,
        test_consumer_version_returns_empty_for_legacy_manifest,
        test_consumer_version_returns_empty_for_invalid_yaml,
        test_consumer_version_returns_empty_for_non_mapping,
        test_consumer_version_strips_whitespace,
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
