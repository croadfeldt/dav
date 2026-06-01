"""
Tests for the #45b grounding nudge — the OFF-by-default, A/B-able prompt push
toward spec-anchored claims (build_stage2_system_prompt + the two-pass prompts).

Verifies DAV_GROUNDING_NUDGE gates the injection, parses truthy/falsy values,
and propagates to both pass-1 and pass-2 system prompts.

Run:  python -m dav.tests.test_grounding_nudge
"""
from __future__ import annotations

import os
import sys

from dav.ai import prompts

_failures = []
_MARKER = "Grounding emphasis (this run)"


def _build_with(val):
    """build_stage2_system_prompt with DAV_GROUNDING_NUDGE = val (None = unset),
    restoring the prior env value afterward."""
    old = os.environ.get("DAV_GROUNDING_NUDGE")
    if val is None:
        os.environ.pop("DAV_GROUNDING_NUDGE", None)
    else:
        os.environ["DAV_GROUNDING_NUDGE"] = val
    try:
        return prompts.build_stage2_system_prompt()
    finally:
        if old is None:
            os.environ.pop("DAV_GROUNDING_NUDGE", None)
        else:
            os.environ["DAV_GROUNDING_NUDGE"] = old


def test_nudge_off_by_default():
    assert _MARKER not in _build_with(None), "nudge must be OFF when env unset"


def test_nudge_on_for_truthy_values():
    for v in ("1", "true", "TRUE", "yes", "on", " on "):
        assert _MARKER in _build_with(v), f"nudge must be ON for {v!r}"


def test_nudge_off_for_falsy_values():
    for v in ("0", "", "false", "no", "off"):
        assert _MARKER not in _build_with(v), f"nudge must be OFF for {v!r}"


def test_nudge_propagates_to_both_passes():
    os.environ["DAV_GROUNDING_NUDGE"] = "1"
    try:
        assert _MARKER in prompts.build_pass1_findings_system_prompt(), "missing in pass-1"
        assert _MARKER in prompts.build_pass2_analysis_system_prompt(), "missing in pass-2"
    finally:
        os.environ.pop("DAV_GROUNDING_NUDGE", None)


def test_nudge_content_is_gated():
    # The distinctive nudge guidance appears only when on (and leans on the
    # confidence field rather than telling the model to drop claims).
    on = _build_with("1")
    assert "spec-anchored" in on, "nudge guidance missing when on"
    assert "confidence" in on, "nudge should reference the confidence field"
    assert "spec-anchored" not in _build_with(None), "nudge guidance leaked when off"


def main():
    tests = [
        test_nudge_off_by_default,
        test_nudge_on_for_truthy_values,
        test_nudge_off_for_falsy_values,
        test_nudge_propagates_to_both_passes,
        test_nudge_content_is_gated,
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
