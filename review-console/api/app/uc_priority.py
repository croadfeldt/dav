"""UC priority projection for the console (spec 05 §6.8).

Mirrors the engine's normalize_priority (engine/src/dav/core/use_case_schema.py)
so the console can project a UC's `priority` into queryable label + score
columns and sort roadmap views — without importing the engine package, which
isn't installed in the API container. Keep the defaults and band ranges in sync
with the engine. Pure functions, no FastAPI/DB deps, so it's unit-testable on
its own (see test_uc_priority.py).
"""

from __future__ import annotations

from typing import Optional

# Label → default (band-midpoint) roadmap weight. Higher = build first.
PRIORITY_DEFAULTS: dict[str, int] = {
    "low": 20, "medium": 50, "high": 70, "critical": 90,
}
# Per-label valid score range; an author-set score must fall in its label's band.
PRIORITY_BAND_RANGES: dict[str, tuple[int, int]] = {
    "low": (0, 40), "medium": (41, 60), "high": (61, 80), "critical": (81, 100),
}


def normalize_priority(raw) -> tuple[Optional[str], Optional[int]]:
    """Resolve a UC `priority` value to (label, score). Raises ValueError on bad input.

    Accepts shorthand ("high"), nested dict ({label, score?}), or None (unranked).
    """
    if raw is None:
        return None, None
    if isinstance(raw, str):
        label = raw.strip().lower()
        if label not in PRIORITY_DEFAULTS:
            raise ValueError(f"priority '{raw}' not in {sorted(PRIORITY_DEFAULTS)}")
        return label, PRIORITY_DEFAULTS[label]
    if isinstance(raw, dict):
        label_raw = raw.get("label")
        if not isinstance(label_raw, str):
            raise ValueError("priority dict must have a string 'label'")
        label = label_raw.strip().lower()
        if label not in PRIORITY_DEFAULTS:
            raise ValueError(f"priority.label '{label_raw}' not in {sorted(PRIORITY_DEFAULTS)}")
        score = raw.get("score", PRIORITY_DEFAULTS[label])
        # bool is an int subclass; reject it explicitly so `score: true` isn't 1.
        if not isinstance(score, int) or isinstance(score, bool):
            raise ValueError(f"priority.score must be an integer, got {score!r}")
        lo, hi = PRIORITY_BAND_RANGES[label]
        if not (lo <= score <= hi):
            raise ValueError(f"priority.score {score} outside band for '{label}' (expected {lo}-{hi})")
        return label, score
    raise ValueError(f"priority must be a label string or mapping, got {type(raw).__name__}")


def derive_priority(parsed: dict) -> tuple[Optional[str], Optional[int]]:
    """Best-effort (label, score) for DB projection. Tolerant: returns (None, None)
    if priority is absent or unparseable (validation surfaces bad values separately)."""
    try:
        return normalize_priority(parsed.get("priority"))
    except ValueError:
        return None, None
