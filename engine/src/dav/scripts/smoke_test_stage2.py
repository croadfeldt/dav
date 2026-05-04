#!/usr/bin/env python3
"""
Stage 2 engine smoke test.

Runs a hand-authored use case through stage 2 end-to-end against
whatever inference endpoint and MCP server are available. Designed
to be run from inside an OpenShift pod in the dav
namespace.

Usage (from inside the cluster):
    python scripts/smoke_test_stage2.py

Or override endpoints via env vars:
    INFERENCE_URL=http://your-inference.example/v1 \\
    INFERENCE_MODEL=your-model-name \\
    MCP_URL=http://dav-docs-mcp.dav.svc:8080 \\
    python scripts/smoke_test_stage2.py

This is intentionally a command-line script, not a unit test.
Unit tests come later when we have more structure to test against.
"""

from __future__ import annotations

import os
import sys
import logging
from pathlib import Path

# Path hack so this works both from the engine dir and from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dav.core.use_case_schema import (
    UseCase, Scenario, Actor, Dimensions, GeneratedBy, DomainInteraction,
)
from dav.ai.client import InferenceClient, EndpointConfig
from dav.ai.mcp_tools import McpClient
from dav.ai.agent import Stage2Agent, AgentConfig

SAMPLE_USE_CASE = UseCase(
    uuid="uc-smoke-test-001",
    handle="smoke/new-vm-standard-profile",
    version="1.0.0",
    generated_by=GeneratedBy(
        mode="authoring",
        source="human-authored",
        model=None,
        prompt_version="smoke-1.0",
    ),
    tags=["smoke-test", "compute", "standard-profile"],
    scenario=Scenario(
        description=(
            "A tenant administrator requests a new virtual machine with "
            "standard multi-tenant isolation. The request must be policy-"
            "checked, allocated to an eligible compute provider, and fulfilled "
            "with the tenant's isolation boundary enforced."
        ),
        actor=Actor(
            persona="tenant-admin",
            profile="standard",
        ),
        intent="Provision a new VM for a standard-profile tenant",
        success_criteria=[
            "VM is provisioned and reachable",
            "Tenant isolation policy is applied",
            "Request is auditable",
        ],
        dimensions=Dimensions(
            lifecycle_phase="new_request",
            resource_complexity="single_no_deps",
            policy_complexity="single_gatekeeper",
            provider_landscape="single_eligible",
            governance_context="standard_governance",
            failure_mode="happy_path",
        ),
        profile="standard",
        expected_domain_interactions=[
            DomainInteraction(domain="policy", interaction="tenant isolation gatekeeper check"),
            DomainInteraction(domain="provider", interaction="compute service provider allocates VM"),
            DomainInteraction(domain="data", interaction="resource record created with tenancy fields"),
        ],
    ),
)

def main():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    inference_url = os.environ.get(
        "INFERENCE_URL",
        "http://inference.example.local:8000/v1",
    )
    inference_model = os.environ.get(
        "INFERENCE_MODEL",
        "qwen",
    )
    mcp_url = os.environ.get(
        "MCP_URL",
        "http://dav-docs-mcp.dav.svc:8080",
    )

    print(f"Stage 2 smoke test")
    print(f"  Inference:  {inference_url}")
    print(f"  Model:      {inference_model}")
    print(f"  MCP:        {mcp_url}")
    print()

    inference = InferenceClient(primary=EndpointConfig(
        url=inference_url,
        model=inference_model,
        label="smoke",
    ))
    mcp = McpClient(server_url=mcp_url)

    # Pre-flight: check inference and MCP are reachable
    print("Pre-flight: inference.list_models()")
    try:
        models = inference.list_models()
        print(f"  → {models[:3]}")
    except Exception as e:
        print(f"  FAILED: {e}")
        print("Abort — can't reach inference endpoint")
        sys.exit(1)

    print("Pre-flight: mcp.call(list_documents)")
    mcp_result = mcp.call("list_documents", {})
    if not mcp_result.ok:
        print(f"  FAILED: {mcp_result.error}")
        print("Abort — can't reach MCP server")
        sys.exit(1)
    print(f"  → received {len(mcp_result.result)} chars of document listing")

    # Validate our sample use case
    errors = SAMPLE_USE_CASE.validate()
    if errors:
        print(f"Sample use case failed validation: {errors}")
        sys.exit(2)
    print(f"\nUse case valid: {SAMPLE_USE_CASE.uuid}")

    # Run stage 2
    print("\nRunning stage 2 agent loop...")
    agent = Stage2Agent(
        inference=inference,
        mcp=mcp,
        config=AgentConfig(
            max_tool_calls=15,      # keep smoke test short
            temperature=0.0,
            max_tokens=4096,
            use_guided_json=False,  # 14B may not support guided_json
        ),
    )
    analysis = agent.analyze(SAMPLE_USE_CASE)

    print(f"\n✓ Analysis produced:")
    print(f"  Verdict:      {analysis.summary.verdict}")
    print(f"  Confidence:   {analysis.summary.overall_confidence}")
    print(f"  Components:   {len(analysis.components_required)}")
    print(f"  Data touched: {len(analysis.data_model_touched)}")
    print(f"  Capabilities: {len(analysis.capabilities_invoked)}")
    print(f"  Providers:    {len(analysis.provider_types_involved)}")
    print(f"  Policy modes: {len(analysis.policy_modes_required)}")
    print(f"  Gaps:         {len(analysis.gaps_identified)}")
    print(f"  Tool calls:   {analysis.analysis_metadata.tool_call_count}")
    print(f"  Tokens used:  {analysis.analysis_metadata.total_tokens}")

    if analysis.summary.notes:
        print(f"\n  Summary notes:\n    {analysis.summary.notes}")

    print(f"\n  Sample component rationale:")
    if analysis.components_required:
        c = analysis.components_required[0]
        print(f"    {c.id}: {c.rationale[:200]}")
        print(f"    spec_refs: {c.spec_refs}")

if __name__ == "__main__":
    main()
