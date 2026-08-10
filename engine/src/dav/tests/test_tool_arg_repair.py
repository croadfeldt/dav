"""Tool-call argument repair (agent._coerce_tool_args).

Motivating incident (2026-07-31 .. 2026-08-09): every nightly fixture battery lost
most of its use cases — 22 of 28 on 08-09 — to the same 400 from the inference
server:

    primary returned 400: {"message":"Extra data: line 1 column 99 (char 98)"}

vLLM's qwen3_xml tool parser emits `arguments` that are valid JSON followed by
trailing garbage, most often a stray closing brace. The agent already detected this
and logged it, but repaired only the copy it EXECUTED — the malformed text stayed in
the conversation, went back to the server on the next turn, and was rejected.

The damage is a suffix, so the model's intent is recoverable: raw_decode keeps the
leading value and drops the tail. These cases are the ones observed in the failing
runs plus the degenerate inputs around them; the invariant that matters is that the
wire value is ALWAYS parseable JSON, because that is what the server re-parses.
"""
import json

import pytest

from dav.ai.agent import _coerce_tool_args

# The exact string from the 2026-08-08 run logs (uc-fx00000004, turn 7).
REAL_FAILURE = (
    '{"handle": "dcm/DCM-Capabilities-Matrix.md", '
    '"section_title": "FIX-FED-COMPAT-001"}}'
)


@pytest.mark.parametrize(
    "raw,expect_args,expect_repaired",
    [
        (REAL_FAILURE,
         {"handle": "dcm/DCM-Capabilities-Matrix.md", "section_title": "FIX-FED-COMPAT-001"},
         True),
        ('{"query": "credential rotation", "max_results": 10}',
         {"query": "credential rotation", "max_results": 10}, False),
        ('{"handle": "a"}{"handle": "b"}', {"handle": "a"}, True),   # two objects concatenated
        ('{"a": 1}   \n', {"a": 1}, False),                          # trailing whitespace is not damage
        ('', {}, False),
        ('   ', {}, False),
        ('not json at all', {}, True),
        ({"already": "dict"}, {"already": "dict"}, False),
    ],
)
def test_coerce_tool_args(raw, expect_args, expect_repaired):
    args, wire, note = _coerce_tool_args(raw)
    assert args == expect_args
    assert (note is not None) is expect_repaired
    # The invariant the incident turned on: whatever we put back on the wire must
    # survive the server re-parsing it.
    json.loads(wire)


def test_real_failure_round_trips_to_intent():
    """The stray brace costs one character, not the whole use case."""
    args, wire, note = _coerce_tool_args(REAL_FAILURE)
    assert json.loads(wire) == args
    assert "dropped 1 trailing char" in note


def test_wellformed_arguments_are_passed_through_untouched():
    raw = '{"handle": "x", "section_title": "y"}'
    _, wire, note = _coerce_tool_args(raw)
    assert wire == raw and note is None
