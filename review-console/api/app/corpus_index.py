"""Corpus index: parse + validate UC files at sync time.

P1 of docs/scope-first-class-plan.md (all decision points ruled 2026-07-27).
The corpus sweep already clones every registered corpus repo and caches file
contents; this module turns that same pass into a queryable index — one row per
UC per namespace, dimension-validated against the corpus's published
vocabulary — so scope and predicted quarantine exist BEFORE a run launches
instead of being discovered at stage 2 ("corpus: 8 files" ... "running 1 UC(s)").

Framework-free on purpose: tests import this without fastapi installed. The
trigger-preflight helper made that mistake first; not repeated here.
"""
from __future__ import annotations

from typing import Optional

import yaml

VOCABULARY_FILENAME = "DIMENSION-VOCABULARY.yaml"

DIMENSION_KEYS = (
    "lifecycle_phase", "resource_complexity", "policy_complexity",
    "provider_landscape", "governance_context", "failure_mode",
)


def load_vocabulary(entries: list[dict]) -> Optional[dict]:
    """Find and parse the published dimension vocabulary among synced entries.

    The file is byte-identical across repos by the model side's design, so the
    first parseable hit wins. Returns the {dimension: [values]} mapping, or None
    when no repo in the sweep publishes it — in which case validation is
    SKIPPED (valid=None), never guessed against a private list: a private copy
    is the fork that silently quarantined 85% of the UDLM corpus (dav#83).
    """
    for e in entries:
        if not e["path"].endswith(VOCABULARY_FILENAME):
            continue
        try:
            doc = yaml.safe_load(e["content"]) or {}
        except yaml.YAMLError:
            continue
        dims = doc.get("dimensions")
        if isinstance(dims, dict) and dims:
            return dims
    return None


def parse_uc(content: str) -> Optional[dict]:
    """Parse a corpus file into UC index fields, or None if it is not a UC.

    Not-a-UC is a normal outcome (ground-truth files, vocabulary, READMEs walk
    through the same sweep); callers COUNT these rather than dropping them
    silently — an uncounted skip is how 7 of 8 fixture UCs vanished without a
    trace.
    """
    try:
        doc = yaml.safe_load(content)
    except yaml.YAMLError:
        return None
    if not isinstance(doc, dict):
        return None
    uuid, handle = doc.get("uuid"), doc.get("handle")
    scenario = doc.get("scenario")
    if not (uuid and handle and isinstance(scenario, dict)):
        return None
    explicit = doc.get("success_semantics")
    # Mirrors the engine's inference (dav#63): explicit value wins; the
    # must-reject family refuses by construction.
    semantics = explicit or ("refuse" if str(handle).startswith("must-reject") else "realize")
    return {
        "uuid": str(uuid),
        "handle": str(handle),
        "family": str(handle).split("/", 1)[0],
        "success_semantics": semantics,
        "dimensions": scenario.get("dimensions") or {},
    }


def validate_dimensions(dims: dict, vocab: dict) -> list[str]:
    """Reasons this UC's dimensions would quarantine, empty when clean.

    Only keys the vocabulary defines are checked; exact, case-sensitive match —
    fuzziness would hide exactly the mistake this predicts.
    """
    reasons = []
    for key in DIMENSION_KEYS:
        allowed = vocab.get(key)
        value = dims.get(key)
        if allowed and value is not None and value not in allowed:
            reasons.append(f"dimensions.{key}={value!r} not in the published vocabulary")
    return reasons


def build_index_rows(namespace: str, entries: list[dict],
                     vocab: Optional[dict], repo_sha: Optional[str]) -> tuple[list[dict], dict]:
    """Turn one namespace's synced entries into index rows + honest stats.

    valid semantics: True/False when a vocabulary was available, None when it
    was not (validation skipped, not passed) — consumers must render None as
    "unvalidated", never as green.
    """
    rows, stats = [], {"ucs": 0, "non_uc": 0, "invalid": 0}
    for e in entries:
        if e["path"].endswith(VOCABULARY_FILENAME):
            stats["non_uc"] += 1
            continue
        uc = parse_uc(e["content"])
        if uc is None:
            stats["non_uc"] += 1
            continue
        if vocab is None:
            valid, reason = None, None
        else:
            reasons = validate_dimensions(uc["dimensions"], vocab)
            valid, reason = (len(reasons) == 0), ("; ".join(reasons) or None)
        stats["ucs"] += 1
        if valid is False:
            stats["invalid"] += 1
        rows.append({
            "namespace": namespace, "path": e["path"],
            "uc_uuid": uc["uuid"], "handle": uc["handle"], "family": uc["family"],
            "success_semantics": uc["success_semantics"], "dimensions": uc["dimensions"],
            "valid": valid, "invalid_reason": reason, "repo_sha": repo_sha,
        })
    return rows, stats
