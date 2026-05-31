"""Self-improvement loop (Phase 1) regression tests — pure modules only.

Run from review-console/api/:  python3 test_self_improvement.py

No pytest dependency (matches the engine's lightweight test style). The
load-bearing property under test: the diagnoser re-derives the fixes that the
OSAC 2026-05-29/30 stabilization made by hand. If a future change breaks that
mapping, this fails.
"""
import asyncio
import sys

from app import failure_taxonomy as ft
from app import diagnose as dg
from app import experiment_eval as ev

_fails = 0


def check(cond, msg):
    global _fails
    mark = "ok  " if cond else "FAIL"
    if not cond:
        _fails += 1
    print(f"  [{mark}] {msg}")


def _tax(model, max_tokens, error_msgs):
    summary = {
        "run_id": "t", "mode": "verification", "total_ucs": 15,
        "successful": 15 - len(error_msgs), "failed": len(error_msgs),
        "effective_sampling": {"model": model, "use_key": "evaluation_verification",
                               "sent": {"max_tokens": max_tokens}},
    }
    failures = [{"uc_uuid": f"u{i}", "uc_handle": "test/x", "error_text": "Error:\n" + m}
                for i, m in enumerate(error_msgs)]
    return ft.build_taxonomy(summary, failures)


def test_classify():
    print("classify_error — real error strings from the OSAC chain:")
    cases = [
        ("inference failed at turn 14: primary returned 504: <html>504 Gateway Time-out</html>", "route_504"),
        ("could not parse final analysis as JSON: unbalanced braces", "output_truncation"),
        ("Extra data: line 1 column 4500 (char 4499)", "output_truncation"),
        ("invalid severity label 'low'; expected one of [...]", "severity_reject"),
        ("invalid confidence label 'certain'", "confidence_reject"),
        ("agent never emitted a final analysis; max_tool_calls exhausted", "budget_exhausted"),
        ("maximum context length is 32768 tokens", "context_overflow"),
        ("a brand new error nobody has classified", "unknown"),
    ]
    for msg, expect in cases:
        got = ft.classify_error(msg)["signature_class"]
        check(got == expect, f"{expect:18} <- {msg[:50]}  (got {got})")
    # capture group
    check(ft.classify_error("invalid severity label 'medium'")["captured"] == "medium",
          "captures the rejected label ('medium')")


def test_rules_rederive_fixes():
    print("diagnose_rules — re-derives this session's fixes:")

    # 504 (092466) → lower max_tokens / route timeout, high confidence
    p = dg.diagnose_rules(_tax("qwen3-32b", 16384, ["AgentError: inference failed at turn 18: primary returned 504"]))
    check(any(x["kind"] == "profile" and "max_tokens" in x["target"].lower() and x["confidence"] == "high"
              and "16384" in x["proposed_change"] for x in p),
          "route_504 → lower dav_stage2_max_tokens (cites current 16384)")

    # severity 'low' (101108) → add alias low→minor, code, high confidence
    p = dg.diagnose_rules(_tax("qwen3-32b", 10240, ["ValueError: invalid severity label 'low'"]))
    check(any(x["kind"] == "code" and "SEVERITY_ALIASES" in x["target"]
              and "'low' → 'minor'" in x["proposed_change"] for x in p),
          "severity_reject 'low' → alias low→minor in _SEVERITY_ALIASES")

    # severity 'high' → high→major (whole-scale mapping)
    p = dg.diagnose_rules(_tax("qwen3-32b", 10240, ["invalid severity label 'high'"]))
    check(any("'high' → 'major'" in x["proposed_change"] for x in p),
          "severity_reject 'high' → high→major (maps the whole L/M/H scale)")

    # truncation (109569) → max_tokens vs route timeout nuance, medium
    p = dg.diagnose_rules(_tax("qwen25-72b-awq", 10240, ["could not parse final analysis as JSON: unbalanced braces"]))
    check(any(x["kind"] == "profile" and "route timeout" in x["proposed_change"].lower() for x in p),
          "output_truncation → raise max_tokens IF route timeout allows (the tradeoff)")

    # fishing/budget → tool/resolver fix, NOT prompt hardening (the v1.9 lesson)
    p = dg.diagnose_rules(_tax("qwen3-coder-30b", 16384, ["agent never emitted a final analysis; max_tool_calls exhausted"]))
    check(any(x["kind"] == "tool" and "harden" in x["proposed_change"].lower() for x in p),
          "budget_exhausted → fix the tool/resolver, explicitly NOT prompt hardening")


def test_merge_and_rank():
    print("merge_and_rank — dedup + ordering:")
    rule = dg._proposal("route_504", "profile", "max_tokens", "r", "c", "e", "high", source="rule")
    llm_dup = dg._proposal("route_504", "profile", "Max_Tokens", "r", "c", "e", "high", source="llm")
    llm_new = dg._proposal("x", "tool", "retry tool", "r", "c", "e", "medium", source="llm")
    merged = dg.merge_and_rank([rule], [llm_dup, llm_new])
    check(len(merged) == 2, "drops the LLM duplicate of a rule's (kind,target)")
    check(merged[0]["source"] == "rule" and merged[0]["confidence"] == "high",
          "ranks high-confidence rule first")


def test_extract_json_array():
    print("_extract_json_array — robust to fences/prose:")
    check(dg._extract_json_array('```json\n[{"kind":"x"}]\n```') == [{"kind": "x"}], "fenced json")
    check(dg._extract_json_array('Sure! [{"kind":"y"}] hope that helps') == [{"kind": "y"}], "prose-wrapped")
    check(dg._extract_json_array("no array here") == [], "no array → []")
    check(dg._extract_json_array("") == [], "empty → []")


async def test_diagnose_degrades():
    print("diagnose — degrades to rules-only without an LLM:")
    tax = _tax("qwen3-32b", 16384, ["primary returned 504"])
    d = await dg.diagnose(tax, call_fn=None)
    check(d["llm_attempted"] is False and d["used_llm"] is False, "no call_fn → llm not attempted")
    check(len(d["proposals"]) >= 1 and d["rule_count"] >= 1, "still produces rule proposals")


def _run(total, succ, fail_errs):
    summary = {"total_ucs": total, "successful": succ, "failed": len(fail_errs),
               "effective_sampling": {"model": "m", "sent": {}}}
    failures = [{"uc_uuid": f"u{i}", "uc_handle": "x", "error_text": "Error:\n" + e}
                for i, e in enumerate(fail_errs)]
    return ev.score_run(summary, failures)


def test_phase2_gate():
    print("Phase 2 gate — A/B decisions on this session's real runs:")
    r088639 = _run(15, 0, ["primary returned 504"] * 15)   # 0/15 route_504
    r092466 = _run(15, 11, ["primary returned 504"] * 4)    # 11/15 route_504
    r103283 = _run(15, 15, [])                              # 15/15 clean
    r109569 = _run(6, 5, ["unbalanced braces"])             # 5/6 output_truncation
    check(ev.gate(r088639, r103283)["verdict"] == "promote", "fixes applied (0/15→15/15) → promote")
    check(ev.gate(r092466, r103283)["verdict"] == "promote", "partial→full (11/15→15/15) → promote")
    check(ev.gate(r103283, r092466)["verdict"] == "revert", "regression (15/15→11/15) → revert")
    # The v1.9 guardrail: a NEW high-severity failure class blocks promotion.
    g = ev.gate(r103283, r109569)
    check(g["verdict"] == "revert" and "output_truncation" in g.get("new_high_sev", []),
          "new failure mode (output_truncation) → revert, even before checking success")
    check(ev.gate(r103283, r103283)["verdict"] == "inconclusive", "no change → inconclusive (ties don't promote)")
    # min_delta: a tiny win below the margin is inconclusive.
    a = _run(10, 5, ["x"] * 5)
    b = _run(10, 6, ["x"] * 4)  # +0.10
    check(ev.gate(a, b, min_delta=0.2)["verdict"] == "inconclusive", "win below min_delta → inconclusive")
    check(ev.gate(a, b, min_delta=0.05)["verdict"] == "promote", "win above min_delta → promote")


def _run_x(total, succ, fail_errs, distinct_gaps, consistency=None):
    summary = {"total_ucs": total, "successful": succ, "failed": len(fail_errs),
               "effective_sampling": {"model": "m", "sent": {}}}
    failures = [{"uc_uuid": f"u{i}", "uc_handle": "x", "error_text": "Error:\n" + e}
                for i, e in enumerate(fail_errs)]
    expl = {"distinct_gaps": distinct_gaps, "total_gaps": distinct_gaps,
            "mean_gaps_per_uc": round(distinct_gaps / max(total, 1), 2),
            "ucs_with_gaps": total, "consistency": consistency}
    return ev.score_run(summary, failures, exploration=expl)


def test_phase2_exploration():
    print("Phase 2 exploration — depth/consistency measured, advisory by default:")
    s = _run_x(10, 10, [], distinct_gaps=20, consistency=0.8)
    check(s.get("exploration", {}).get("distinct_gaps") == 20, "score_run records exploration block")
    check("exploration" not in _run(10, 10, []), "no exploration arg → no block (back-compat)")

    base = _run_x(10, 10, [], distinct_gaps=15)
    cand = _run_x(10, 10, [], distinct_gaps=22)            # success tie, +7 distinct gaps
    g = ev.gate(base, cand)
    check(g["verdict"] == "inconclusive", "tie + more gaps, tie-breaker OFF → inconclusive (default)")
    check(g.get("exploration_delta", {}).get("distinct_gaps") == 7, "exploration_delta surfaced (advisory)")
    check("exploration_delta" not in ev.gate(_run(10, 10, []), _run(10, 10, [])),
          "no exploration data → no exploration_delta key (back-compat)")

    check(ev.gate(base, cand, exploration_min_delta=5)["verdict"] == "promote",
          "tie + +7 gaps ≥ exploration_min_delta=5 → promote (opt-in)")
    check(ev.gate(base, cand, exploration_min_delta=10)["verdict"] == "inconclusive",
          "+7 gaps < exploration_min_delta=10 → still inconclusive")

    # The guardrail wins: a NEW high-sev class blocks promotion even with far more
    # gaps and the exploration tie-breaker enabled.
    b2 = _run_x(10, 9, ["primary returned 504"], distinct_gaps=15)        # 9/10, route_504
    c2 = _run_x(10, 9, ["unbalanced braces"], distinct_gaps=40)           # 9/10, NEW output_truncation
    gg = ev.gate(b2, c2, exploration_min_delta=5)
    check(gg["verdict"] == "revert" and "output_truncation" in gg.get("new_high_sev", []),
          "tie + +25 gaps but NEW high-sev → revert (exploration never overrides the guardrail)")


def main():
    test_classify()
    test_rules_rederive_fixes()
    test_merge_and_rank()
    test_extract_json_array()
    asyncio.run(test_diagnose_degrades())
    test_phase2_gate()
    test_phase2_exploration()
    print()
    if _fails:
        print(f"FAILED: {_fails} check(s)")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
