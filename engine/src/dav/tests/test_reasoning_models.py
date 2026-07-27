"""Reasoning-model support: don't starve the answer with the thinking.

gpt-oss (harmony) and similar models emit chain-of-thought in `reasoning_content`
BEFORE `content`. DAV narrows max_tokens as context fills, so a budget sized for
the answer alone gets spent thinking and the turn returns empty — observed live as
`AgentError: final response had no content` after the model had, in its reasoning,
correctly worked out the answer.

The engine's original assumption was that thinking can be switched off (`/no_think`).
That holds for Qwen and not for gpt-oss, where reasoning is intrinsic to the chat
template. These tests pin the behaviour that replaced it: detect, widen, retry once.
"""
from unittest.mock import patch

from dav.ai.client import ChatResponse, EndpointConfig, InferenceClient


def _client() -> InferenceClient:
    return InferenceClient(primary=EndpointConfig(url="http://x/v1", model="m"))


def _resp(content="", reasoning="", tools=None) -> ChatResponse:
    return ChatResponse(content=content, tool_calls=tools or [], finish_reason="stop",
                        usage={}, endpoint_used="primary", reasoning_content=reasoning)


def test_response_carries_reasoning_content():
    """Regression: it was parsed and discarded, so the retry could never trigger."""
    assert _resp(reasoning="thinking...").reasoning_content == "thinking..."


def test_budget_untouched_until_reasoning_is_observed():
    c = _client()
    assert c._reasoning_budget(512) == 512          # non-reasoning models unaffected


def test_budget_widens_once_reasoning_is_observed():
    c = _client()
    c._reasoning_observed = True
    assert c._reasoning_budget(512) == 512 * c.REASONING_HEADROOM


def test_budget_respects_the_floor():
    """The agent narrows max_tokens as context fills; below the floor a reasoning
    model cannot both finish thinking and emit an analysis."""
    c = _client()
    c._reasoning_observed = True
    assert c._reasoning_budget(64) == c.REASONING_MIN_TOKENS


def test_empty_content_with_reasoning_triggers_one_widened_retry():
    c = _client()
    calls: list[int] = []

    def fake_post(endpoint, body):
        calls.append(body.get("max_tokens"))
        # first attempt: thought itself out of budget; second: answers
        if len(calls) == 1:
            return _resp(reasoning="I have worked out the answer but have no room")
        return _resp(content='{"verdict":"supported"}')

    with patch.object(InferenceClient, "_post", side_effect=fake_post, autospec=False):
        out = c.chat(messages=[], max_tokens=200)

    assert out.content == '{"verdict":"supported"}'
    assert len(calls) == 2, "expected exactly one retry"
    assert calls[1] > calls[0], "retry must widen the budget"
    assert c._reasoning_observed is True


def test_no_retry_when_content_is_present():
    c = _client()
    calls = []

    def fake_post(endpoint, body):
        calls.append(body)
        return _resp(content="fine", reasoning="also thought about it")

    with patch.object(InferenceClient, "_post", side_effect=fake_post, autospec=False):
        c.chat(messages=[], max_tokens=200)
    assert len(calls) == 1


def test_no_retry_when_the_model_called_a_tool():
    """Empty content plus a tool call is the normal agent loop, not a failure."""
    c = _client()
    calls = []

    def fake_post(endpoint, body):
        calls.append(body)
        return _resp(reasoning="deciding", tools=[{"function": {"name": "search_docs"}}])

    with patch.object(InferenceClient, "_post", side_effect=fake_post, autospec=False):
        c.chat(messages=[], max_tokens=200)
    assert len(calls) == 1


def test_retry_happens_at_most_once_per_client():
    """Second starved turn must not retry again — the budget is already widened."""
    c = _client()
    c._reasoning_observed = True
    calls = []

    def fake_post(endpoint, body):
        calls.append(body)
        return _resp(reasoning="still thinking")

    with patch.object(InferenceClient, "_post", side_effect=fake_post, autospec=False):
        c.chat(messages=[], max_tokens=200)
    assert len(calls) == 1
