"""Per-stage model routing: pass 1 may use a different backend than pass 2.

Pins the selection rule and — more importantly — that the default path is
completely unchanged when no pass-1 backend is configured. A routing bug that
silently sent pass 2 to the cheap model would be invisible in output shape and
would quietly degrade every verdict, so these assert the *destination*, not just
that a call happened.
"""
from dav.ai.agent import Stage2Agent
from dav.ai.client import EndpointConfig, InferenceClient


def _client(model: str, url: str = "http://x/v1") -> InferenceClient:
    return InferenceClient(primary=EndpointConfig(url=url, model=model))


def _agent(pass1: InferenceClient | None = None) -> Stage2Agent:
    # mcp is never touched by _client(); None keeps the fixture honest about scope.
    return Stage2Agent(inference=_client("strong-model"), mcp=None, inference_pass1=pass1)


def test_no_pass1_backend_uses_primary_everywhere():
    a = _agent()
    for label in (None, "pass1", "pass2"):
        a._pass_label = label
        assert a._client().primary.model == "strong-model"


def test_pass1_label_routes_to_pass1_backend():
    a = _agent(pass1=_client("fast-model"))
    a._pass_label = "pass1"
    assert a._client().primary.model == "fast-model"


def test_pass2_stays_on_the_strong_backend():
    """The whole point: judgment must not silently fall to the cheap model."""
    a = _agent(pass1=_client("fast-model"))
    a._pass_label = "pass2"
    assert a._client().primary.model == "strong-model"


def test_unlabelled_phase_stays_on_primary():
    # single-pass mode never sets _pass_label; it must not accidentally route away.
    a = _agent(pass1=_client("fast-model"))
    assert a._client().primary.model == "strong-model"


def test_pass1_backend_is_a_distinct_client():
    p1 = _client("fast-model", url="http://fast/v1")
    a = _agent(pass1=p1)
    a._pass_label = "pass1"
    assert a._client() is p1
    assert a._client().primary.url == "http://fast/v1"
    a._pass_label = "pass2"
    assert a._client() is not p1
