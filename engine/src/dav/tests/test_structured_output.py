"""Structured output must actually reach the server.

DAV asked for JSON-constrained output by setting `extra_body: {guided_json: ...}`
in a hand-built request body. `extra_body` is an OpenAI *client-library* concept
— the Python SDK lifts its contents to the top level before sending. Nothing
lifts it when you build the JSON yourself, so the key travelled as an unknown
field and both servers ignored it.

Verified live before writing these tests, same prompt and schema each time:

    extra_body.guided_json   vLLM: prose    llama.cpp: prose
    guided_json (top level)  vLLM: JSON     llama.cpp: n/a (vLLM extension)
    response_format          vLLM: JSON     llama.cpp: JSON

So the bug was not "llama.cpp lacks a feature" — enforcement was inert
everywhere, including the recovery path that re-asks "once with guided schema"
after a parse failure. That path was re-asking with no constraint, which is why
retries kept returning the same unparseable prose.

These tests assert the wire shape, because that is the thing that was wrong: the
call succeeded, the response was well-formed, and only the constraint was
missing.
"""
from unittest.mock import patch

from dav.ai.client import ChatMessage, EndpointConfig, InferenceClient, ToolDefinition

SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string"}},
    "required": ["verdict"],
}


def _capture(**chat_kwargs) -> dict:
    """Return the JSON body the client would put on the wire."""
    client = InferenceClient(primary=EndpointConfig(url="http://x/v1", model="m"))
    seen: dict = {}

    def fake_request(endpoint, body, *a, **kw):
        seen.update(body)
        raise RuntimeError("stop after body construction")

    with patch.object(InferenceClient, "_post", side_effect=fake_request, autospec=False):
        try:
            client.chat(messages=[ChatMessage(role="user", content="hi")], **chat_kwargs)
        except Exception:
            pass
    return seen


def test_schema_is_sent_as_response_format():
    body = _capture(guided_json_schema=SCHEMA)
    rf = body.get("response_format")
    assert rf, "no response_format on the wire — the schema is not being enforced"
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["schema"] == SCHEMA


def test_extra_body_is_never_sent():
    """Regression guard. This key is silently dropped by every server, so its
    presence means someone reintroduced an unenforced constraint."""
    body = _capture(guided_json_schema=SCHEMA)
    assert "extra_body" not in body


def test_no_response_format_when_no_schema_requested():
    """Unconstrained turns (tool-calling turns) must stay unconstrained —
    forcing JSON on a turn that should emit a tool call would break the loop."""
    body = _capture()
    assert "response_format" not in body


def test_schema_survives_alongside_tools():
    """The recovery path re-emits with a schema while tools are still declared;
    the two must not clobber each other in the body."""
    tool = ToolDefinition(name="search_docs", description="d",
                          parameters={"type": "object", "properties": {}})
    body = _capture(tools=[tool], guided_json_schema=SCHEMA)
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["tools"], "tools dropped when a schema was requested"
