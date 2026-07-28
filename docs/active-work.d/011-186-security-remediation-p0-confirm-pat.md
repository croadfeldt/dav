## ✅ SHIPPED 2026-06-17 — #186 security remediation (P0 confirm + PAT-on-delete guard)
**Why:** finish the #186 remediation per the runbook — confirm the P0 work is actually in place and
close any remaining clearly-in-scope, low-risk code guard.
- **P0 confirmed present** (commit `0f18c0a`): esc() quote-escaping, `DELETE /api/credentials` auth,
  `/api/bundles/{bid}/attach` decorator on the real handler, `/api/analysis/roadmap` read guard,
  `GET /api/runs/{name}/turns` authenticated read guard, and the enhancement `target_path`
  traversal/extension/CI-file guard. Secret rotation (#190) reported done.
- **NEW guard [Chain C, PAT hardening]:** `DELETE /api/accounts/{reviewer}` now revokes the deleted
  account's PATs (`UPDATE api_tokens SET revoked_at=now()`) + reloads the token cache, so a deleted
  user can no longer regain access via a still-valid Personal Access Token. Break-glass default-admin
  stays a deactivate (the gate already enforces `enabled`).
- **Intentionally left for Chris** (need a decision / cluster eyes — not invented scope): global
  default-deny auth (P1.8, ~60 endpoints, needs persona walk-through), NetworkPolicy default-deny,
  Postgres TLS, DB-backup CronJob, MCP auth-LB + netpol, the broad exception-detail-leak sweep,
  tenancy/IDOR `project_id` columns, and the SSRF allowlist (touches live model-call paths).

