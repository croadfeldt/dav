"""Hard MCP namespace scoping (run-source epic increment).

The run-level DAV_SPEC_NAMESPACES_FILTER used to be prompt-hint-only while
the UC-level list was enforced; both now feed the same hard gate via
resolve_spec_scope."""
from dav.ai.agent import resolve_spec_scope


def test_uc_scope_alone_is_enforced():
    assert resolve_spec_scope(["dcm-spec"], []) == ["dcm-spec"]


def test_run_filter_alone_is_enforced():
    """The increment: an operator's per-run filter is a hard gate even for
    UCs that declare no scope of their own."""
    assert resolve_spec_scope([], ["fixture-spec"]) == ["fixture-spec"]


def test_intersection_uc_cannot_widen_run_scope():
    assert resolve_spec_scope(["dcm-spec", "udlm-spec"], ["udlm-spec"]) == ["udlm-spec"]


def test_disjoint_scopes_keep_run_filter_not_fall_open():
    assert resolve_spec_scope(["dcm-spec"], ["fixture-spec"]) == ["fixture-spec"]


def test_unscoped_when_neither_set():
    assert resolve_spec_scope([], []) == []
