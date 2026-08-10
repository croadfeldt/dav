#!/usr/bin/env python3
"""Nightly fixture battery — trigger, wait, score, store. Stdlib + pyyaml only.

Runs in a CronJob using the dav-engine image (git + python + pyyaml present),
against whatever model is serving on the inference route — deliberately: the
nightly row measures the PRODUCTION default, so a model swap shows up in the
trend as a labeled step, not a mystery.

Ground truth travels from this clone to the API (expected/ is intentionally
never a corpus/spec role, so the DB does not have it). Scores land in
fixture_scores with engine_commit attached — regressions arrive with the
commit that caused them.

Env: DAV_API_URL, DAV_API_TOKEN, INFERENCE_URL, DAV_FIXTURES_PROJECT (default
760), SAMPLE_COUNT (default 3).
"""
from __future__ import annotations

import json
import os
import pathlib
import ssl
import sys
import time
import urllib.error
import urllib.request

import yaml

API = os.environ["DAV_API_URL"].rstrip("/")
TOKEN = os.environ["DAV_API_TOKEN"]
INFER = os.environ.get("INFERENCE_URL", "https://r9700.llm.ocp.roadfeldt.com/v1").rstrip("/")
PROJECT = os.environ.get("DAV_FIXTURES_PROJECT", "760")
N = int(os.environ.get("SAMPLE_COUNT", "3"))
HERE = pathlib.Path(__file__).resolve().parent

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE  # in-cluster service / self-signed route


def call(method: str, url: str, body: dict | None = None, timeout: int = 120):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body else None)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("X-DAV-Project", PROJECT)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return json.load(r)


def main() -> int:
    # Which model is serving? Measure it, don't assume it.
    with urllib.request.urlopen(f"{INFER}/models", timeout=30, context=_CTX) as r:
        model = json.load(r)["data"][0]["id"]
    print(f"serving model: {model}", flush=True)

    expected = [yaml.safe_load(f.read_text())
                for f in sorted((HERE / "expected").glob("*.yaml"))]
    print(f"ground truth: {len(expected)} expectations", flush=True)

    run_name = call("POST", f"{API}/api/runs", {
        "mode": "verification", "selection_mode": "corpus",
        "name": f"NIGHTLY battery ({model} n={N}"
                + (" no-think)" if os.environ.get("BATTERY_ENABLE_THINKING") == "false" else ")"), "category": "ad-hoc",
        "inference_endpoint": INFER, "inference_model": model,
        "sample_count": N, "uc_concurrency": 2,
        "corpus_namespaces": ["fixtures"], "spec_namespaces": ["fixtures-spec"],
        # Tri-state: BATTERY_ENABLE_THINKING env "false" runs the no-think
        # config; unset/empty keeps the model default. Stamped in the run
        # name so calibration rows are self-describing.
        **({"enable_thinking": False} if os.environ.get("BATTERY_ENABLE_THINKING") == "false" else {}),
    })["run"]["name"]
    print(f"run: {run_name}", flush=True)

    # Wait for terminal phase, then for auto-ingest to surface the run_id.
    # Ceiling: a hung run must not wedge tomorrow's cron (Forbid policy).
    # Env-tunable because it is MODEL-DEPENDENT: the 32B clears the v1 suite
    # in ~20 min, but Qwen3.6-27B with thinking took ~23 min/UC on the v2
    # 28-UC suite (~5.5 h) and a healthy run died at the old fixed 120.
    ceiling_min = int(os.environ.get("BATTERY_CEILING_MIN", "120"))
    run_id, phase = None, "?"
    for _ in range(ceiling_min):
        time.sleep(60)
        for r in call("GET", f"{API}/api/runs?limit=10")["runs"]:
            if (r.get("name") or r.get("run_name")) == run_name:
                phase, run_id = r.get("phase"), r.get("run_id")
                break
        print(f"  phase={phase} run_id={run_id}", flush=True)
        if run_id and phase in ("Succeeded", "Failed", "Completed"):
            break
    if not run_id:
        print("FATAL: no run_id — run never reached ingest inside the ceiling")
        return 1

    # The ingest sweep converges within one pass after the run completes;
    # compute 409s while the DB still holds a partial snapshot (a 4-of-12
    # partial once scored recall 0.10 against a 12/12 run). Retry, bounded.
    for _ in range(30):
        try:
            out = call("POST", f"{API}/api/fixture-scores/compute",
                       {"run_id": run_id, "expected": expected, "source": "nightly"})
            print(json.dumps(out, indent=2))
            # The scores are still worth recording for a failed run, but the JOB must
            # not report success. `ok` in the payload above is the SCORING API saying
            # scoring worked — not a statement about the run. Exiting 0 on phase=Failed
            # is why ten consecutive nights of 22-of-28 UC losses showed up as a green
            # CronJob and nobody noticed (2026-07-31 .. 2026-08-09).
            if phase == "Failed":
                print(f"FAILING THE JOB: run {run_id} finished with phase={phase} — "
                      f"scores above are from a partial run and are not a quality signal",
                      flush=True)
                return 2
            return 0
        except urllib.error.HTTPError as e:
            if e.code != 409:
                raise
            print("ingest not converged yet; retrying in 60s", flush=True)
            time.sleep(60)
    print("FATAL: ingest never converged inside the retry window")
    return 1


if __name__ == "__main__":
    sys.exit(main())
