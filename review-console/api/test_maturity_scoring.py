"""Tests for the Maturity Wall scoring/CRUD logic (#147 slice 2).

Pure-logic + a fake-asyncpg-conn check of the LLM-vs-human provenance rules. No live DB.
Run directly: `python test_maturity_scoring.py` (from review-console/api), or via pytest.
"""
import asyncio
import json
import sys

from app import maturity_scoring as ms

_failures: list[str] = []


def eq(actual, expected, label):
    if actual != expected:
        _failures.append(f"{label}: got {actual!r}, expected {expected!r}")


def truthy(actual, label):
    if not actual:
        _failures.append(f"{label}: expected truthy, got {actual!r}")


# ── _coerce_maturity ──────────────────────────────────────────────────────────
def test_coerce_maturity():
    eq(ms._coerce_maturity(0), 0, "0 is valid (Manual)")
    eq(ms._coerce_maturity(5), 5, "5 is valid (Highly Optimized)")
    eq(ms._coerce_maturity("3"), 3, "numeric string coerces")
    eq(ms._coerce_maturity(6), None, "out-of-range high -> None")
    eq(ms._coerce_maturity(-1), None, "out-of-range low -> None")
    eq(ms._coerce_maturity("-"), None, "'-' (Not Assessed) -> None")
    eq(ms._coerce_maturity("n/a"), None, "'n/a' -> None")
    eq(ms._coerce_maturity(None), None, "None -> None")
    eq(ms._coerce_maturity("banana"), None, "garbage -> None")


# ── build_scoring_prompt ────────────────────────────────────────────────────────
def _framework():
    return {
        "scale": [{"value": 0, "label": "Manual"}, {"value": 5, "label": "Highly Optimized"}],
        "states": [
            {"key": "current", "label": "Current", "kind": "current"},
            {"key": "phase-1", "label": "Phase 1", "kind": "target"},
            {"key": "desired", "label": "Desired", "kind": "desired"},
        ],
        "bands": [{"band": "B1", "categories": [
            {"label": "Cat A", "capabilities": [
                {"id": "cap-1", "label": "Provisioning"},
                {"id": "cap-2", "label": "Observability"},
            ]},
        ]}],
    }


def test_build_scoring_prompt_targets_only_and_lists_caps():
    fw = _framework()
    system, user = ms.build_scoring_prompt(
        fw, [{"capability_handle": "Provisioning", "category": "Cat A", "maturity": 2,
              "state": "partial", "notes": "ev"}], fw["states"])
    truthy("JSON" in system, "system asks for JSON only")
    truthy("cap-1" in user, "user lists capability ids")
    truthy("phase-1" in user and "desired" in user, "user lists target+desired states")
    eq("current" in user.split("Target states to score")[1].split("Framework capabilities")[0], False,
       "current state is excluded from the scoring target list")
    truthy("Provisioning" in user, "findings embedded as evidence")


# ── parse_scoring_response ──────────────────────────────────────────────────────
def test_parse_scoring_response_filters_invalid():
    raw = json.dumps({"scores": [
        {"capability_id": "cap-1", "state": "phase-1", "maturity": 3, "rationale": "ok"},
        {"capability_id": "cap-1", "state": "phase-1", "maturity": 9},      # out of range -> dropped
        {"capability_id": "ghost", "state": "phase-1", "maturity": 3},      # unknown cap -> dropped
        {"capability_id": "cap-2", "state": "current", "maturity": 3},      # non-target state -> dropped
        {"capability_id": "cap-2", "state": "desired", "maturity": 4, "rationale": "y"},
    ]})
    out = ms.parse_scoring_response(raw, {"cap-1", "cap-2"}, {"phase-1", "desired"})
    eq(len(out), 2, "only the two valid rows survive")
    eq({(o["capability_id"], o["state"]) for o in out},
       {("cap-1", "phase-1"), ("cap-2", "desired")}, "kept the right cells")


def test_parse_scoring_response_strips_code_fence():
    raw = "```json\n" + json.dumps({"scores": [
        {"capability_id": "cap-1", "state": "phase-1", "maturity": 1}]}) + "\n```"
    out = ms.parse_scoring_response(raw, {"cap-1"}, {"phase-1"})
    eq(len(out), 1, "code-fenced JSON still parses")


def test_parse_scoring_response_rejects_non_json():
    try:
        ms.parse_scoring_response("not json at all", {"cap-1"}, {"phase-1"})
        _failures.append("non-JSON should raise ValueError")
    except ValueError:
        pass


# ── persist provenance rules (fake conn) ────────────────────────────────────────
class _FakeConn:
    """Captures executed SQL + args and simulates the conflict-WHERE row-count contract."""
    def __init__(self, human_protected_returns_zero=False):
        self.calls = []
        self._human_zero = human_protected_returns_zero

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        # persist_llm_scores inspects the command tag; a human-protected upsert returns UPDATE 0.
        if "source <> 'human'" in sql and self._human_zero:
            return "UPDATE 0"
        return "INSERT 0 1"


def test_persist_llm_scores_counts_and_protects_human():
    async def run():
        conn = _FakeConn(human_protected_returns_zero=False)
        n = await ms.persist_llm_scores(
            conn, "a-id", [{"capability_id": "c1", "state": "phase-1", "maturity": 3, "rationale": "r"}],
            updated_by="alice")
        eq(n, 1, "one LLM cell written")
        truthy("source <> 'human'" in conn.calls[0][0], "LLM upsert guards human cells in the DO UPDATE WHERE")
        truthy("'llm'" in conn.calls[0][0], "LLM write stamps source='llm'")

        conn2 = _FakeConn(human_protected_returns_zero=True)
        n2 = await ms.persist_llm_scores(
            conn2, "a-id", [{"capability_id": "c1", "state": "phase-1", "maturity": 3}],
            updated_by="alice")
        eq(n2, 0, "a human-occupied cell is NOT overwritten by the LLM pass (counted as skipped)")
    asyncio.run(run())


def test_apply_overrides_stamps_human_provenance():
    async def run():
        conn = _FakeConn()
        n = await ms.apply_overrides(
            conn, "a-id",
            [{"capability_id": "c1", "state": "phase-1", "maturity": 4, "rationale": "human says so"},
             {"framework_capability_id": "c2", "state_key": "desired", "maturity": None}],  # clear to '-'
            updated_by="bob")
        eq(n, 2, "both overrides applied (incl. the null = Not Assessed clear)")
        for sql, _ in conn.calls:
            truthy("'human'" in sql, "every override stamps source='human'")
            truthy("updated_by" in sql and "updated_at=now()" in sql, "override carries who/when provenance")
    asyncio.run(run())


def test_apply_overrides_skips_incomplete_rows():
    async def run():
        conn = _FakeConn()
        n = await ms.apply_overrides(
            conn, "a-id", [{"state": "phase-1", "maturity": 3}],  # no capability id -> skipped
            updated_by="bob")
        eq(n, 0, "an override without a capability id is skipped, not written")
    asyncio.run(run())


def _run_all():
    g = globals()
    tests = [v for k, v in g.items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    if _failures:
        print(f"FAIL ({len(_failures)}):")
        for f in _failures:
            print("  -", f)
        sys.exit(1)
    print(f"ok — {len(tests)} maturity-scoring tests passed")


if __name__ == "__main__":
    _run_all()
