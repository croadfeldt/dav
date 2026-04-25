"""Corpus loader — walks a directory tree and yields reviewable files.

Used by main.py at startup when CORPUS_MODE=directory (git-clone init
container pattern). Mirrors the logic in scripts/build-corpus-json.py.
"""
from __future__ import annotations

import fnmatch
import logging
from pathlib import Path
from typing import Iterator

log = logging.getLogger("dav-review-api.corpus_loader")

DEFAULT_INCLUDE = [
    "*.md", "*.yml", "*.yaml", "*.json", "*.sql", "*.py",
    "*.go", "*.rst", "*.txt", "*.toml", "*.ini",
]
DEFAULT_EXCLUDE = [
    ".git", "node_modules", ".venv", "__pycache__", "dist", "build",
    "*.pyc", "*.lock",
]
MAX_FILE_BYTES = 512 * 1024


def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def walk_corpus(
    root: Path,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> Iterator[dict]:
    """Yield {path, content} dicts for every qualifying file under root.

    `path` is always forward-slash-separated relative to `root`, regardless
    of OS. Binary files, oversized files, and unreadable files are skipped
    with a warning.
    """
    include = include or DEFAULT_INCLUDE
    exclude = exclude or DEFAULT_EXCLUDE
    root = root.resolve()

    if not root.is_dir():
        log.error("corpus root %s is not a directory", root)
        return

    count = skipped = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(_matches(part, exclude) for part in rel.parts):
            continue
        if not _matches(path.name, include):
            continue
        try:
            raw = path.read_bytes()
        except OSError as e:
            log.warning("unreadable: %s (%s)", rel, e)
            skipped += 1
            continue
        if len(raw) > MAX_FILE_BYTES:
            log.warning("oversized (%d bytes): %s", len(raw), rel)
            skipped += 1
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            log.warning("binary (skipped): %s", rel)
            skipped += 1
            continue
        count += 1
        yield {"path": str(rel).replace("\\", "/"), "content": text}

    log.info("corpus walk complete: %d files included, %d skipped", count, skipped)


def parse_patterns(s: str | None) -> list[str] | None:
    """Parse a comma-separated env var into a pattern list (or None if empty)."""
    if not s:
        return None
    items = [p.strip() for p in s.split(",") if p.strip()]
    return items or None
