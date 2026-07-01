#!/usr/bin/env bash
# Create the test Jira story for vm-standard-provision (uc-seed-001a)
# Run from a session with Jira MCP connected, or set JIRA_HOST/JIRA_EMAIL/JIRA_API_TOKEN

set -euo pipefail

JIRA_HOST="${JIRA_HOST:-https://issues.redhat.com}"
JIRA_EMAIL="${JIRA_EMAIL:?Set JIRA_EMAIL}"
JIRA_API_TOKEN="${JIRA_API_TOKEN:?Set JIRA_API_TOKEN}"

echo "Creating test story in FLPATH..."

RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X POST "${JIRA_HOST}/rest/api/2/issue" \
  -H "Content-Type: application/json" \
  -u "${JIRA_EMAIL}:${JIRA_API_TOKEN}" \
  -d '{
  "fields": {
    "project": {"key": "FLPATH"},
    "issuetype": {"name": "Story"},
    "summary": "[WS-B] Vm Standard Provision",
    "priority": {"name": "High"},
    "labels": [
      "gate-g1", "gate-g2", "gate-g6", "gate-g7",
      "wsb", "demo-wk2-3", "dav-uc-seed-001a",
      "pipeline", "compute", "vm", "happy-path"
    ],
    "description": "**Intent:** Provision a new VM in the standard profile\n\n**Description:** An application team requests a new virtual machine in the standard profile. The platform must run the applicable policy checks before allocation, allocate the VM through an eligible service provider, and produce an auditable record of the provisioning.\n\n**DAV UUID:** uc-seed-001a\n**Handle:** compute/vm-standard-provision\n**Workstream:** WS-B\n**Demo Week:** wk2-3\n**Gates:** G1, G2, G6, G7\n\n**Dimensions:**\n- lifecycle_phase: new_request\n- resource_complexity: single_no_deps\n- policy_complexity: single_validation\n- provider_landscape: single_eligible\n- governance_context: standard_governance\n- failure_mode: happy_path\n\n**Expected Domain Interactions:**\n- *policy:* resolved-profile validation evaluates the request before allocation\n- *provider:* service provider allocates the VM resource\n- *data:* resource record created\n- *audit:* provisioning event recorded\n\n---\n\n**Acceptance Criteria:**\n# VM is created and reachable for the requesting team\n# Applicable (resolved-profile) policies are evaluated before allocation\n# Provisioning is recorded in the audit trail with actor, intent, and outcome\n# The request is idempotent — repeating it does not create duplicate VMs"
  }
}')

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -n -1)

if [ "$HTTP_CODE" = "201" ]; then
  KEY=$(echo "$BODY" | python3 -c "import json,sys; print(json.load(sys.stdin)['key'])")
  echo "Created: ${JIRA_HOST}/browse/${KEY}"
else
  echo "FAILED (HTTP ${HTTP_CODE}):"
  echo "$BODY" | python3 -m json.tool 2>/dev/null || echo "$BODY"
  exit 1
fi
