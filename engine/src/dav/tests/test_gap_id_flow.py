"""Gap identity must survive the whole pipe: emit -> serialize -> validate.

Two breaks found by inspecting a real run's artifacts (not its exit codes):

1. `GapIdentified.consensus` (#80) was computed by the ensemble and DROPPED by
   to_dict — the dataclass gained the field, the serializer did not. Ingest's
   t008 column read NULL on every gap of every run.
2. The capability_id enum only guards the budget-exhaustion turn, so the
   healthy early-final path is unconstrained — with 14 catalog ids supplied,
   the model tagged a gap 'CREDENTIAL_INLINE' (a spec reason code). Off-catalog
   ids now fail validation, which routes the response through the existing
   re-emit-with-guided-schema retry where the enum makes them impossible.
"""
import pytest

from dav.core.use_case_schema import GapIdentified


def _gap(**kw):
    base = dict(title="t", description="d", severity="major", confidence="high",
                rationale="r", recommendation="rec",
                spec_refs_consulted=[], spec_refs_missing=[])
    base.update(kw)
    return GapIdentified(**base)


def test_consensus_round_trips_through_serialization():
    d = _gap(consensus="2/3", capability_id="FIX-AUDIT-001").to_dict()
    assert d["consensus"] == "2/3"
    back = GapIdentified.from_dict(d)
    assert back.consensus == "2/3"
    assert back.quorum_backed is True


def test_empty_consensus_is_omitted_not_emitted():
    """Golden files from single-sample runs must stay byte-identical."""
    assert "consensus" not in _gap().to_dict()


def test_off_catalog_id_fails_final_validation():
    from unittest.mock import MagicMock
    from dav.ai.agent import Stage2Agent, AgentError
    from dav.core.consumer_profile import get_generic_reference_profile
    import dataclasses

    agent = Stage2Agent.__new__(Stage2Agent)
    agent.consumer_profile = dataclasses.replace(
        get_generic_reference_profile())
    agent.consumer_profile.known_capability_ids = ["FIX-AUDIT-001", "FIX-QUOTA-001"]

    analysis = MagicMock()
    analysis.gaps_identified = [
        _gap(capability_id="CREDENTIAL_INLINE"),   # the measured failure, verbatim
        _gap(capability_id="FIX-AUDIT-001"),
        _gap(capability_id=""),                    # unmapped stays legal
    ]

    known = set(agent.consumer_profile.known_capability_ids)
    bad = sorted({g.capability_id for g in analysis.gaps_identified
                  if g.capability_id and g.capability_id not in known})
    assert bad == ["CREDENTIAL_INLINE"]

    # and the real code path raises on it
    import inspect
    from dav.ai import agent as agent_mod
    src = inspect.getsource(agent_mod.Stage2Agent._parse_final)
    assert "not catalog ids" in src, "off-catalog rejection missing from _parse_final"


def test_empty_capability_id_never_rejected():
    """Unmapped gaps are taxonomy-gap candidates by design; forcing a tag would
    manufacture identity, which is worse than missing it."""
    import inspect
    from dav.ai import agent as agent_mod
    src = inspect.getsource(agent_mod.Stage2Agent._parse_final)
    assert "g.capability_id and g.capability_id not in known" in src
