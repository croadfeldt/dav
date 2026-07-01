#!/usr/bin/env python3
"""dav-to-jira — pipeline script that reads use cases from DAV and outputs Jira stories.

Usage:
    python3 dav-to-jira.py --set-id 27                  # dry-run (default): print stories to stdout
    python3 dav-to-jira.py --set-id 27 --format json     # output as JSON (for piping to Jira MCP)
    python3 dav-to-jira.py --set-id 27 --uc uc-seed-001a # single UC only

Env:
    DAV_BASE_URL     DAV API base (default: https://10.0.90.22:8843)
    DAV_TOKEN_FILE   path to bearer token file (default: ~/.claude-work/.dav-token)
    DAV_PROJECT_ID   DAV project ID (default: 20)

Definitive UC list (34 stories):
    14 pipeline UCs (uc-pipeline-*) — new, in git YAML
    13 Section 16 UCs — kept, no pipeline overlap
     7 trifecta companion UCs — Piotr-feedback validation
     6 Section 16 UCs REMOVED (replaced by pipeline UCs):
       uc-baremetal-pxe-provision → uc-pipeline-bm-001
       uc-6e1e27735e9c → uc-pipeline-drift-001
       uc-126b4231c0f8 → uc-pipeline-rehydrate-001
       uc-b53c099c325d → uc-pipeline-rto-001
       uc-a4f95cd66c7c → uc-pipeline-portable-001
       uc-c600fab7 → uc-pipeline-idempotent-001
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.request
from pathlib import Path
from typing import Any

DAV_BASE = os.getenv("DAV_BASE_URL", "https://10.0.90.22:8843")
DAV_PROJECT = os.getenv("DAV_PROJECT_ID", "20")
TOKEN_FILE = Path(os.getenv("DAV_TOKEN_FILE", os.path.expanduser("~/.claude-work/.dav-token")))
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "FLPATH")

GATE_PRIORITY = {
    "G4": "Highest",
    "G1": "High",
    "G3": "High",
    "G2": "Medium",
    "G6": "Medium",
    "G5": "Medium",
    "G7": "Low",
    "G8": "Lowest",
}

UC_GATE_MAP = {
    "uc-895e5ab0": ["G1"],
    "uc-a4a4f8def3ca": ["G1", "G3"],
    "uc-seed-001a": ["G1", "G2", "G6", "G7"],
    "uc-seed-009a": ["G1"],
    "uc-8b603f5a": ["G2"],
    "uc-seed-004a": ["G2"],
    "uc-73071912": ["G3"],
    "uc-a537b0a9": ["G1", "G3"],
    "uc-4908573a": ["G3"],
    "uc-seed-006a": ["G4", "G6"],
    "uc-seed-007a": ["G7"],
    "uc-seed-005a": ["G7"],
    "uc-cd9b798f": ["G8"],
    "uc-policy-resolution-capability": ["G1", "G7"],
    "uc-policy-applicability-data-model": ["G1", "G7"],
    "uc-profile-resolution-capability": ["G1"],
    "uc-profile-approved-list-data-model": ["G1"],
    "uc-audit-chain-proofs-capability": ["G7"],
    "uc-audit-chain-output-verification": ["G7"],
    "uc-audit-chain-data-model": ["G7"],
    "uc-pipeline-authn-001": ["G1"],
    "uc-pipeline-bm-001": ["G1", "G4"],
    "uc-pipeline-cluster-001": ["G1", "G4"],
    "uc-pipeline-cp-001": ["G1"],
    "uc-pipeline-catalog-001": ["G1", "G3"],
    "uc-pipeline-composite-001": ["G1", "G2", "G3"],
    "uc-pipeline-4state-001": ["G1", "G2"],
    "uc-pipeline-sov-001": ["G1", "G5"],
    "uc-pipeline-drift-001": ["G2", "G6"],
    "uc-pipeline-idempotent-001": ["G2", "G6"],
    "uc-pipeline-rehydrate-001": ["G1", "G3", "G4", "G5"],
    "uc-pipeline-rto-001": ["G4", "G5"],
    "uc-pipeline-portable-001": ["G4", "G6"],
    "uc-pipeline-profile-001": ["G1", "G4"],
}

UC_WORKSTREAM_MAP = {
    "uc-895e5ab0": "WS-D",
    "uc-a4a4f8def3ca": "WS-C",
    "uc-seed-001a": "WS-B",
    "uc-seed-009a": "WS-F",
    "uc-8b603f5a": "WS-I",
    "uc-seed-004a": "WS-B",
    "uc-73071912": "WS-I",
    "uc-a537b0a9": "WS-B",
    "uc-4908573a": "WS-B",
    "uc-seed-006a": "WS-B",
    "uc-seed-007a": "WS-E",
    "uc-seed-005a": "WS-C",
    "uc-cd9b798f": "WS-H",
    "uc-policy-resolution-capability": "WS-F",
    "uc-policy-applicability-data-model": "WS-F",
    "uc-profile-resolution-capability": "WS-F",
    "uc-profile-approved-list-data-model": "WS-F",
    "uc-audit-chain-proofs-capability": "WS-I",
    "uc-audit-chain-output-verification": "WS-I",
    "uc-audit-chain-data-model": "WS-I",
    "uc-pipeline-authn-001": "WS-B",
    "uc-pipeline-bm-001": "WS-A",
    "uc-pipeline-cluster-001": "WS-A",
    "uc-pipeline-cp-001": "WS-B",
    "uc-pipeline-catalog-001": "WS-C",
    "uc-pipeline-composite-001": "WS-B",
    "uc-pipeline-4state-001": "WS-D",
    "uc-pipeline-sov-001": "WS-F",
    "uc-pipeline-drift-001": "WS-B",
    "uc-pipeline-idempotent-001": "WS-B",
    "uc-pipeline-rehydrate-001": "WS-A",
    "uc-pipeline-rto-001": "WS-E",
    "uc-pipeline-portable-001": "WS-B",
    "uc-pipeline-profile-001": "WS-F",
}

UC_WEEK_MAP = {
    "uc-895e5ab0": "wk2",
    "uc-a4a4f8def3ca": "wk3",
    "uc-seed-001a": "wk2-3",
    "uc-seed-009a": "wk3",
    "uc-8b603f5a": "wk3",
    "uc-seed-004a": "wk3",
    "uc-73071912": "wk3",
    "uc-a537b0a9": "wk3",
    "uc-4908573a": "wk3",
    "uc-seed-006a": "wk4",
    "uc-seed-007a": "wk5",
    "uc-seed-005a": "wk5",
    "uc-cd9b798f": "wk3-5",
    "uc-policy-resolution-capability": "wk3",
    "uc-policy-applicability-data-model": "wk3",
    "uc-profile-resolution-capability": "wk3",
    "uc-profile-approved-list-data-model": "wk3",
    "uc-audit-chain-proofs-capability": "wk5",
    "uc-audit-chain-output-verification": "wk5",
    "uc-audit-chain-data-model": "wk5",
    "uc-pipeline-authn-001": "wk2",
    "uc-pipeline-bm-001": "wk2",
    "uc-pipeline-cluster-001": "wk2",
    "uc-pipeline-cp-001": "wk2",
    "uc-pipeline-catalog-001": "wk3",
    "uc-pipeline-composite-001": "wk3",
    "uc-pipeline-4state-001": "wk2-3",
    "uc-pipeline-sov-001": "wk3",
    "uc-pipeline-drift-001": "wk4",
    "uc-pipeline-idempotent-001": "wk4",
    "uc-pipeline-rehydrate-001": "wk4",
    "uc-pipeline-rto-001": "wk4",
    "uc-pipeline-portable-001": "wk5",
    "uc-pipeline-profile-001": "wk5",
}


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _token() -> str:
    return TOKEN_FILE.read_text().strip()


def dav_get(path: str) -> Any:
    url = f"{DAV_BASE}{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {_token()}",
        "X-DAV-Project": DAV_PROJECT,
    })
    with urllib.request.urlopen(req, context=_ssl_ctx()) as resp:
        return json.loads(resp.read())


def get_set_members(set_id: int) -> list[str]:
    data = dav_get(f"/api/sets/{set_id}")
    return [m["uc_uuid"] for m in data.get("members", [])]


def get_uc(uuid: str) -> dict:
    return dav_get(f"/api/use-cases/{uuid}")


def get_capability_map(set_id: int) -> dict:
    try:
        return dav_get(f"/api/analysis/uc-capability-map?set_id={set_id}")
    except Exception:
        return {}


def get_eval_results(set_id: int) -> dict:
    try:
        data = dav_get(f"/api/results/uc-latest?set_id={set_id}")
        results = data if isinstance(data, list) else data.get("results", [])
        return {r["uc_uuid"]: r for r in results if isinstance(r, dict) and "uc_uuid" in r}
    except Exception:
        return {}


def uc_to_jira_story(uc_data: dict, capabilities: list[str], eval_result: dict | None) -> dict:
    parsed = uc_data.get("parsed", {})
    scenario = parsed.get("scenario", {})
    uuid = parsed.get("uuid", uc_data.get("uuid", ""))
    handle = parsed.get("handle", "")
    description = scenario.get("description", "")
    intent = scenario.get("intent", "")
    success_criteria = scenario.get("success_criteria", [])
    dimensions = scenario.get("dimensions", {})
    domain_interactions = scenario.get("expected_domain_interactions", [])
    tags = parsed.get("tags", [])

    uuid_prefix = uuid.split("-")[0] + "-" + uuid.split("-")[1] if "-" in uuid else uuid
    gates = UC_GATE_MAP.get(uuid, UC_GATE_MAP.get(uuid_prefix, []))
    workstream = UC_WORKSTREAM_MAP.get(uuid, UC_WORKSTREAM_MAP.get(uuid_prefix, ""))
    week = UC_WEEK_MAP.get(uuid, UC_WEEK_MAP.get(uuid_prefix, ""))

    best_gate = min(gates, key=lambda g: list(GATE_PRIORITY.keys()).index(g)) if gates else ""
    priority = GATE_PRIORITY.get(best_gate, "Medium")

    summary = f"[{workstream}] {handle.split('/')[-1].replace('-', ' ').title()}"
    jira_project = JIRA_PROJECT_KEY

    labels = [f"gate-{g.lower()}" for g in gates]
    labels.append(workstream.lower().replace("-", ""))
    labels.append(f"demo-{week}" if week else "unscheduled")
    labels.append(f"dav-{uuid}")
    if uuid.startswith("uc-pipeline-"):
        labels.append("pipeline")
        pipeline_meta = parsed.get("metadata", {}).get("pipeline", {})
        act = pipeline_meta.get("act")
        if act is not None:
            labels.append(f"act-{act}")
    labels.extend(tags[:5])

    desc_parts = [
        f"**Intent:** {intent}",
        "",
        f"**Description:** {description}",
        "",
        f"**DAV UUID:** `{uuid}`",
        f"**Handle:** `{handle}`",
        f"**Workstream:** {workstream}",
        f"**Demo Week:** {week}",
        f"**Gates:** {', '.join(gates)}",
    ]

    if dimensions:
        desc_parts.append("")
        desc_parts.append("**Dimensions:**")
        for k, v in dimensions.items():
            desc_parts.append(f"- {k}: `{v}`")

    if domain_interactions:
        desc_parts.append("")
        desc_parts.append("**Expected Domain Interactions:**")
        for di in domain_interactions:
            desc_parts.append(f"- **{di.get('domain', '')}:** {di.get('interaction', '')}")

    if capabilities:
        desc_parts.append("")
        desc_parts.append(f"**Capabilities:** {', '.join(capabilities)}")

    if eval_result:
        verdict = eval_result.get("verdict", "pending")
        desc_parts.append("")
        desc_parts.append(f"**DAV Eval Status:** {verdict}")
        if eval_result.get("error_reason"):
            desc_parts.append(f"**Error:** {eval_result['error_reason']}")

    ac_parts = []
    for i, criterion in enumerate(success_criteria, 1):
        ac_parts.append(f"# {criterion}")

    return {
        "project": jira_project,
        "summary": summary,
        "description": "\n".join(desc_parts),
        "acceptance_criteria": "\n".join(ac_parts),
        "priority": priority,
        "labels": labels,
        "uuid": uuid,
        "handle": handle,
        "gates": gates,
        "workstream": workstream,
        "week": week,
    }


def format_story_text(story: dict) -> str:
    lines = [
        f"{'=' * 80}",
        f"SUMMARY:    {story['summary']}",
        f"PROJECT:    {story['project']}",
        f"PRIORITY:   {story['priority']}",
        f"WORKSTREAM: {story['workstream']}",
        f"WEEK:       {story['week']}",
        f"GATES:      {', '.join(story['gates'])}",
        f"LABELS:     {', '.join(story['labels'][:8])}",
        f"DAV UUID:   {story['uuid']}",
        "",
        "DESCRIPTION:",
        story["description"],
        "",
        "ACCEPTANCE CRITERIA:",
        story["acceptance_criteria"],
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="DAV → Jira pipeline")
    parser.add_argument("--set-id", type=int, default=27, help="DAV scoping set ID")
    parser.add_argument("--uc", type=str, help="Single UC UUID (skip set lookup)")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    parser.add_argument("--mapping-only", action="store_true", help="Only output UCs in the mapping (skip unknown)")
    args = parser.parse_args()

    if args.uc:
        uuids = [args.uc]
    else:
        print(f"Fetching scoping set {args.set_id}...", file=sys.stderr)
        uuids = get_set_members(args.set_id)
        print(f"  {len(uuids)} UCs in set", file=sys.stderr)

    print("Fetching capability map...", file=sys.stderr)
    cap_map = get_capability_map(args.set_id)
    cap_edges = cap_map.get("edges", [])
    uc_caps: dict[str, list[str]] = {}
    for edge in cap_edges:
        uc_id = edge.get("uc", "")
        cap_id = edge.get("cap", "")
        uc_caps.setdefault(uc_id, []).append(cap_id)

    print("Fetching eval results...", file=sys.stderr)
    eval_results = get_eval_results(args.set_id)

    stories = []
    for uuid in sorted(uuids):
        if args.mapping_only and uuid not in UC_GATE_MAP:
            uuid_prefix = "-".join(uuid.split("-")[:2]) if "-" in uuid else uuid
            if uuid_prefix not in UC_GATE_MAP:
                continue

        print(f"  Fetching {uuid}...", file=sys.stderr)
        try:
            uc_data = get_uc(uuid)
        except Exception as e:
            print(f"  ERROR fetching {uuid}: {e}", file=sys.stderr)
            continue

        caps = uc_caps.get(uuid, [])
        eval_r = eval_results.get(uuid)
        story = uc_to_jira_story(uc_data, caps, eval_r)
        stories.append(story)

    if args.format == "json":
        json.dump(stories, sys.stdout, indent=2)
        print()
    else:
        for story in stories:
            print(format_story_text(story))

    print(f"\n{'=' * 80}", file=sys.stderr)
    print(f"Total stories: {len(stories)}", file=sys.stderr)
    by_ws = {}
    for s in stories:
        by_ws.setdefault(s["workstream"], []).append(s["uuid"])
    for ws in sorted(by_ws):
        print(f"  {ws}: {len(by_ws[ws])} stories", file=sys.stderr)


if __name__ == "__main__":
    main()
