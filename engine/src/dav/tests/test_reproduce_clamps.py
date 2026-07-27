"""Reproduce mode must clamp concurrency to 1 — the clamp IS the guarantee.

Measured 2026-07-27 on identical fixture corpora, temperature 0.0, top_k 1,
cache_prompt off, all confirmed applied:

    uc_concurrency=2   two passes: 12 and 18 gaps, ZERO overlap
    uc_concurrency=1   two passes: 15 gaps, IDENTICAL sets

The divergence is outside the engine's control once requests are concurrent:
UCs in flight share the inference server's batches, and batch composition
changes floating-point reduction order, flipping argmax at ties even under
strict greedy decoding. So a caller's concurrency request cannot be honored in
reproduce mode without silently voiding the mode they asked for.
"""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "stages" / "run_corpus.py").read_text()


def _clamp_blocks():
    """Fixed-window slice after each clamp condition — a lazy regex ending at
    '= 1' truncated at the '!= 1' inside the condition itself and made this
    test fail its own code. The window comfortably covers warning + assignment."""
    return [SRC[m.start():m.start() + 700]
            for m in re.finditer(r'if args\.mode == "reproduce"', SRC)]


def test_both_concurrency_knobs_are_clamped_in_reproduce_mode():
    blocks = _clamp_blocks()
    joined = "\n".join(blocks)
    assert "uc_concurrency" in joined, "uc_concurrency clamp missing"
    assert "sample_concurrency" in joined, "sample_concurrency clamp missing"


def test_clamps_warn_rather_than_silently_overriding():
    """The caller asked for something they cannot have; the log must say so and
    say why — a silent override is how the next person re-runs at conc 2 and
    trusts the result."""
    for block in _clamp_blocks():
        assert "log.warning" in block, f"clamp without a warning: {block[:80]}"


def test_uc_clamp_sits_before_the_executor_choice():
    """The clamp must precede the serial-vs-pool branch, or it decorates a
    decision already taken."""
    clamp = SRC.index('if args.mode == "reproduce" and uc_concurrency != 1')
    branch = SRC.index("if uc_concurrency == 1:")
    assert clamp < branch


def test_sample_clamp_sits_before_agent_config():
    clamp = SRC.index('if args.mode == "reproduce" and (args.sample_concurrency or 1) != 1')
    cfg = SRC.index("sample_concurrency=args.sample_concurrency")
    assert clamp < cfg
