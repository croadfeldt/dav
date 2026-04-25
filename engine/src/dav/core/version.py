"""
Engine + consumer version metadata helpers.

populate AnalysisMetadata.engine_version, engine_commit, and
consumer_version with values discovered at runtime, so analyses carry
provenance about what code and what consumer content produced them.

All three helpers degrade gracefully — they return empty strings rather
than raising when:
  - The engine isn't running from a git checkout
  - git is not installed
  - The consumer content path doesn't exist or has no version manifest

Empty-string sentinels match the existing AnalysisMetadata defaults,
so callers that don't pass these fields keep working unchanged.

Caching: engine_version_string() and engine_commit_string() shell out to
git, which is slow. Both cache their results at module level; the first
call pays the subprocess cost, subsequent calls are free. The cache is
process-lifetime (deliberately — git state shouldn't change mid-process
under normal conditions).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)

# Module-level caches. Populated lazily on first call. None means "not yet
# computed"; empty string means "computed and unavailable".
_ENGINE_VERSION_CACHE: Optional[str] = None
_ENGINE_COMMIT_CACHE: Optional[str] = None

def _run_git(args: list[str], cwd: Path) -> str:
    """Run a git subcommand and return stdout, or empty string on any failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        log.debug("git %s failed: %s", " ".join(args), e)
        return ""
    if result.returncode != 0:
        log.debug(
            "git %s returned %d: %s",
            " ".join(args), result.returncode, result.stderr.strip()[:200],
        )
        return ""
    return result.stdout.strip()

def _engine_repo_root() -> Path:
    """Best-effort path to the engine's source tree.

    Walks up from this file's location looking for a directory that
    contains a `.git` directory. Returns this file's parent if no
    .git is found (the caller will get an empty string from git, which
    is the desired graceful degradation).
    """
    here = Path(__file__).resolve()
    for ancestor in [here, *here.parents]:
        if (ancestor / ".git").exists():
            return ancestor
    # Fall back to the immediate package root; git ops will fail but
    # subprocess won't crash (capture_output prevents stderr leakage).
    return here.parent

def engine_version_string() -> str:
    """Return a human-readable engine version, e.g. "v0.1.0-15-gdeadbeef".

    Sources from `git describe --tags --always --dirty` on the engine's
    source tree. Returns empty string if not in a git checkout, git is
    unavailable, or no tags exist (rare — `--always` falls back to the
    short SHA if no tags are reachable).
    """
    global _ENGINE_VERSION_CACHE
    if _ENGINE_VERSION_CACHE is not None:
        return _ENGINE_VERSION_CACHE
    root = _engine_repo_root()
    _ENGINE_VERSION_CACHE = _run_git(
        ["describe", "--tags", "--always", "--dirty"], root
    )
    return _ENGINE_VERSION_CACHE

def engine_commit_string() -> str:
    """Return the full SHA-1 of the engine's HEAD commit.

    Returns empty string on failure. Cached for the process lifetime.
    """
    global _ENGINE_COMMIT_CACHE
    if _ENGINE_COMMIT_CACHE is not None:
        return _ENGINE_COMMIT_CACHE
    root = _engine_repo_root()
    _ENGINE_COMMIT_CACHE = _run_git(["rev-parse", "HEAD"], root)
    return _ENGINE_COMMIT_CACHE

def reset_caches() -> None:
    """Clear the version caches (test-only utility)."""
    global _ENGINE_VERSION_CACHE, _ENGINE_COMMIT_CACHE
    _ENGINE_VERSION_CACHE = None
    _ENGINE_COMMIT_CACHE = None

def consumer_version_string(consumer_path: Path | str | None) -> str:
    """Read the consumer's version from a manifest file in its content tree.

    Looks for `dav-version.yaml` at the root of the consumer path. The
    YAML's `version` field is returned (e.g. "1.0.0"). Falls back to
    an empty string if:
      - consumer_path is None
      - The path doesn't exist
      - dav-version.yaml is absent
      - The YAML doesn't have a `version` key
      - The file isn't valid YAML

    The consumer path comes from the runner — typically a CLI flag
    (--consumer-content-path) or an env var pointing at the cloned
    consumer repo. When no path is given, this returns "" and the
    field stays empty on the resulting Analysis.

    See examples/minimal-consumer/dav-version.yaml for the manifest
    shape we look for.
    """
    if consumer_path is None:
        return ""
    p = Path(consumer_path)
    if not p.exists():
        log.debug("consumer path does not exist: %s", p)
        return ""
    manifest = p / "dav-version.yaml"
    if not manifest.exists():
        log.debug("dav-version.yaml not found at %s", manifest)
        return ""
    try:
        with manifest.open("r") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as e:
        log.debug("failed to read %s: %s", manifest, e)
        return ""
    if not isinstance(data, dict):
        log.debug("%s does not contain a YAML mapping", manifest)
        return ""
    # Prefer explicit `consumer_version`; fall back to generic `version`.
    # Both keys are documented in PHASE-EPSILON-2-README.md. We DO NOT read
    # `schema_version` — that field has a different meaning (DAV-version
    # compatibility range, not consumer-specific version).
    for key in ("consumer_version", "version"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    log.debug(
        "%s has no string consumer_version or version field; "
        "consumer should add `consumer_version: <semver>` to populate this",
        manifest,
    )
    return ""
