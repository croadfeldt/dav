"""Plain-text think blocks (Qwen3.8) and per-call chat_template_kwargs."""
from dav.ai.client import EndpointConfig, InferenceClient, _split_text_reasoning


def test_split_qwen38_shape_only_closing_tag():
    r, a = _split_text_reasoning("I should say no.\n</think>\n\nNo, TLS 1.0 is deprecated.")
    assert r == "I should say no." and a == "No, TLS 1.0 is deprecated."


def test_split_full_block():
    r, a = _split_text_reasoning("<think>\nthinking\n</think>\n{\"verdict\": \"supported\"}")
    assert r == "thinking" and a == '{"verdict": "supported"}'


def test_split_no_tag_is_passthrough():
    assert _split_text_reasoning("plain answer") == ("", "plain answer")
    assert _split_text_reasoning("") == ("", "")


def test_split_truncated_reasoning_has_no_answer():
    # No closing tag: cannot tell reasoning from answer -> unchanged (caller decides).
    assert _split_text_reasoning("still thinking about {") == ("", "still thinking about {")


def _client(**cfg):
    return InferenceClient(EndpointConfig(url="http://x/v1", model="m", label="t", **cfg))


def test_build_body_merges_per_call_kwargs_over_endpoint():
    c = _client(chat_template_kwargs={"enable_thinking": True, "reasoning_effort": "medium"})
    body = c._build_body(c.primary, [], None, 0.0, 128, None, None,
                         {"enable_thinking": False})
    assert body["chat_template_kwargs"] == {"enable_thinking": False, "reasoning_effort": "medium"}
    assert c.primary.chat_template_kwargs == {"enable_thinking": True, "reasoning_effort": "medium"}


def test_build_body_without_override_is_unchanged():
    c = _client(chat_template_kwargs={"enable_thinking": True})
    assert c._build_body(c.primary, [], None, 0.0, 128, None, None)["chat_template_kwargs"] == {"enable_thinking": True}
    c2 = _client()
    assert "chat_template_kwargs" not in c2._build_body(c2.primary, [], None, 0.0, 128, None, None)
