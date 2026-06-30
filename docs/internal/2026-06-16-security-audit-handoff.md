# DAV Security Audit — Handoff for Remediation Session

_This document summarizes the findings from a comprehensive security audit performed on 2026-06-16 by a separate Claude session (Opus 4.6, 1M context). It is intended to be consumed by another Claude instance that will implement the remediations. The full detailed report with file:line references is at `docs/internal/2026-06-16-security-audit-full.md` in the same repo._

---

## How This Audit Was Conducted

Six parallel analysis agents examined the DAV codebase at `/Users/chris/git/dav`, each focused on a different attack surface:

1. **Authentication, authorization, session management** — auth bypass, cookie security, PAT handling, RBAC, header injection, privilege escalation
2. **Injection and input validation** — SQL injection (all ~13,000 lines of raw asyncpg queries), YAML deserialization, command injection, SSRF, path traversal, LLM prompt injection
3. **Secrets and infrastructure** — hardcoded credentials, container security, network exposure, TLS, dependency vulnerabilities, Ansible vault
4. **Creative/adversarial attack chains** — red-team scenarios chaining multiple weaknesses into exploits
5. **Logging and data leakage** — request body logging, LLM prompt/response logging, error response internals, vLLM config
6. **Database security** — data at rest encryption, backup strategy, credential storage, PII inventory, retention policy

The codebase has strong fundamentals. SQL injection is clean (all parameterized), YAML uses `safe_load` everywhere, path traversal defenses are robust, password hashing uses argon2, HMAC session signing is correct, and Fernet encryption for repo credentials is properly implemented. The 2026-06-05 internal audit (`docs/security-audit.md`) resolved earlier critical findings (C1-C2, H1-H6 from that audit).

The new findings cluster around **data protection gaps** — particularly important given that the system processes NDA-protected content from FSI customers (Barclays, PNC, Truist, US Bank, JPMC).

---

## Findings Requiring Remediation

### P0 — Fix Immediately (5 findings)

**1. No database backup strategy (CRITICAL)**
- There is zero backup mechanism — no `pg_dump`, no CronJob, no WAL archiving, no replication
- Single-replica PostgreSQL with `Recreate` strategy on RWO PVC
- A PVC deletion, storage corruption, or failed migration = total unrecoverable data loss of 66 tables
- **Fix:** Create a CronJob running `pg_dump --format=custom` to encrypted storage. Implement retention (7 daily + 4 weekly). Test restore.

**2. Turns endpoint serves full LLM prompts without project-scoped auth (CRITICAL)**
- `GET /api/runs/{name}/turns` at `main.py:3575-3606`
- Has no `get_user()` or `require_priv()` call — only a `validations.ENABLED` check
- Any authenticated console user can read any run's complete prompts and LLM responses regardless of project membership
- Prompts contain full customer UC YAML with NDA-protected descriptions, intents, success criteria
- **Fix:** Add `user = await get_user(request)` + project scoping via `_active_project_id`. Verify the run belongs to the caller's active project before returning data.

**3. MCP server exposed to internet without authentication (HIGH)**
- `ansible/roles/dav/templates/mcp-docs-deployment.yaml.j2:194-207`
- The MCP Route has TLS but zero auth — anyone can call `search_docs`, `get_document` and exfiltrate the full DCM/UDLM spec corpus
- A secured LB sidecar with bearer token auth already exists in the codebase but is disabled by default (`dav_docs_mcp_lb_enabled: false` in `defaults/main.yml`)
- No NetworkPolicy restricts MCP ingress
- **Fix:** Set `dav_docs_mcp_lb_enabled: true` in the deployment vars. Add a NetworkPolicy for `dav-docs-mcp` pods restricting ingress to same-namespace only.

**4. DELETE /api/credentials has zero auth guard (HIGH)**
- `main.py:9474-9492`
- The `delete_credential_api` function has no `request: Request` parameter and calls no auth guard
- The sibling `update_credential_api` (line 9448) correctly calls `get_user(request)`
- In non-multiuser mode, callable without any authentication
- **Fix:** Add `request: Request` parameter and `await require_role(request, "admin")`.

**5. Enhancement apply allows arbitrary file write → code execution via CI (CRITICAL)**
- `enhancement_apply.py:59-61` — `target_path` property does `split("/", 1)[1]` with zero path validation
- No check for `..`, no extension allowlist, no absolute path rejection
- Attack chain: prompt injection in UC → LLM emits enhancement targeting `.github/workflows/backdoor.yml` → PR auto-created → CI executes attacker code
- Cross-namespace drift warnings are non-blocking (log + proceed)
- **Also:** `_enh_apply_pr_body` is called with 5 args at `main.py:12950` but defined with 4 params at line 12991 — arity bug means enhancement-apply is currently broken (accidental safety)
- **Fix:** Add path validation before constructing `repo_path`: reject paths containing `..` or starting with `/`. Add extension allowlist (`.md`, `.yaml`, `.yml`, `.txt`, `.rst`). Deny CI patterns (`.github/`, `Makefile`, `Dockerfile`, `*.sh`, `*.py`). Fix the arity bug.

---

### P1 — Fix This Sprint (7 findings)

**6. 23+ exception details leaked in HTTP 500 responses (CRITICAL)**
- `main.py` — at least 23 locations with pattern `raise HTTPException(500, f"...{e}")`
- Python exception str() output in response bodies exposes: table/column names, SQL text, constraint names, partial row data
- **Fix:** Replace with generic messages; log `e` server-side only. Grep for `HTTPException(500` and `HTTPException(503` across main.py.

**7. PostgreSQL runs without TLS (HIGH)**
- `review-console-db-deployment.yaml.j2` — no certificates, no `ssl = on`, no `sslmode` in DSN
- DB password and query results (including plaintext API keys) transit in cleartext
- **Fix:** Generate TLS cert for PostgreSQL pod, configure `ssl = on`, add `?sslmode=verify-full` to DSN, pass `ssl=` context to `asyncpg.create_pool()` at `main.py:336`.

**8. No NetworkPolicy on database pod (HIGH)**
- API has a NetworkPolicy but DB pod (port 5432) has none
- Any pod in any namespace can attempt to connect
- **Fix:** Add NetworkPolicy allowing ingress on 5432 only from pods with the API label selector within the `dav` namespace.

**9. Shell injection in Tekton task params (HIGH)**
- `ansible/roles/dav/templates/tekton-tasks/dav-git-sync.yaml.j2:63,69-70,81`
- All `$(params.*)` expansions are unquoted in shell scripts
- `RunTriggerIn` Pydantic model accepts plain strings with no regex validation
- **Fix:** Quote all `$(params.*)` in shell scripts. Add Pydantic validators to `RunTriggerIn` at `main.py:1541-1548`: URL regex for repo URLs, `^[a-zA-Z0-9._/-]+$` for branches, `^[0-9a-f]{40}$` for commit SHAs.

**10. Customer content logged via LLM output snippets (HIGH)**
- Cache hit log at `main.py:11107-11109` emits first/last 160 chars of LLM analysis
- JSON parse failure at `agent.py:1142` logs first 500 chars
- Reasoning content at `client.py:569-578` logs first 200 chars
- vLLM has no `--disable-log-requests` in `ansible/roles/dav/templates/vllm-tier3.yaml.j2`
- `log.exception()` at `uc_assist.py:191,329` includes full traceback with local vars containing UC YAML
- **Fix:** Remove content snippets from log statements (replace with length-only indicators). Add `--disable-log-requests` and `VLLM_LOGGING_LEVEL=WARNING` to vLLM deployment. Replace `log.exception()` with `log.warning()` in content-handling paths.

**11. Account deletion doesn't revoke PATs (MEDIUM)**
- `main.py:2085-2089` — deletes from `users` and `rbac_account_roles` but NOT from `api_tokens`
- Deleted user's PAT remains valid → JIT re-provisions a new user account on next use
- **Fix:** Add `DELETE FROM api_tokens WHERE lower(email)=$1` to the account deletion flow. Call `api_tokens.load_cache(pool)` after.

**12. CORS wildcard with credentials (MEDIUM)**
- `main.py:890-897` — `CORS_ORIGINS` defaults to `"*"`, never set in deployment template
- With `allow_credentials=True`, Starlette reflects the requesting origin, allowing any website to make credentialed cross-origin requests
- **Fix:** Add `CORS_ORIGINS` env var to `review-console-api-deployment.yaml.j2` set to the configured `review_console_hostname`.

---

### P2 — Fix Next Sprint (8 findings)

**13. 8 LLM prompt injection vectors with no defenses (HIGH)**
- Highest risk: PR comment bodies from GitHub injected into LLM prompts at `main.py:4590-4607` — no DAV auth required, any external repo contributor can inject
- Other vectors: UC YAML fields (`uc_assist.py:177-183`), bulk text 120K chars (`uc_assist.py:317`), assessment artifacts (`main.py:8011`), stage context (`main.py:11251`), prompt assist (`main.py:12117`), arch review gap data (`arch_review.py:46-97`)
- System prompts intentionally exposed at `GET /api/arch-review/prompt` (`main.py:12564`)
- **Fix:** Wrap all user content in XML-style delimiter tags in prompt construction (`prompts.py`, `uc_assist.py`). Add `max_length` to `ManagedUCIn.yaml_content`. Add output schema validation for structured LLM outputs. For PR comments, cap body length before prompt inclusion.

**14. `model_configs.api_key` stored plaintext (HIGH)**
- `schema.sql:319` — plaintext TEXT column, masked on GET but raw in DB
- **Fix:** Add `api_key_encrypted` column, Fernet-wrap on write, decrypt at engine-call boundary, migrate existing rows. Same pattern as `managed_repos.github_pat_encrypted`.

**15. Legacy `code_repo_configs.token` not cleared after migration (HIGH)**
- `main.py:750-883` — ADR-006 migration copies to Fernet-encrypted column but never NULLs the source
- **Fix:** After confirmed migration, `UPDATE code_repo_configs SET token = NULL`. Then drop the column.

**16. Indirect prompt injection via managed repos into MCP (HIGH)**
- `mcp/dav-docs-mcp/server.py:127,131` — `rglob("*.md")` with `read_text()`, no sanitization, follows symlinks
- Attacker with commit access to a managed repo can inject adversarial instructions or create symlinks to sensitive files
- **Fix:** Add symlink containment check before `read_text()`. Reject symlinks or files outside the repo root.

**17. 60+ endpoints lack endpoint-level auth guards (HIGH)**
- Many GET endpoints rely solely on `_approval_gate` middleware, which is conditional on `_REQUIRE_AUTH`
- In default configuration (no `_REQUIRE_AUTH`, no LDAP), the gate is a no-op → endpoints are unauthenticated
- **Fix:** Either make `_approval_gate` unconditional (always enforce auth), or add `get_user(request)` to every data-returning endpoint. The former is less work and more robust.

**18. PATs are user-scoped, not project-scoped (MEDIUM-HIGH)**
- `api_tokens` table has no `project_id` column — a compromised PAT exposes every project the user has roles on
- PAT minting doesn't validate target email exists — minting for nonexistent email auto-creates user via JIT
- **Fix:** Add optional `project_id` column to `api_tokens`. Validate email exists + enabled before minting.

**19. SSRF via model probe endpoint (MEDIUM)**
- `main.py:10806-10826` — authenticated user with config privileges can set `endpoint_url` to any URL including internal/metadata IPs
- Probe makes GET request and reflects status codes + parsed JSON back to caller
- **Fix:** Add URL denylist for RFC1918, link-local (169.254.x.x), and cloud metadata ranges.

**20. Invitation tokens stored plaintext (MEDIUM)**
- `schema.sql:785-786` — `user_invitations.token` stores the plaintext token as the primary key
- Compare with `api_tokens.token_hash` which correctly stores SHA256
- **Fix:** Store SHA256 hash, show plaintext once at creation time.

---

### P3 — Backlog (10 findings)

| # | Finding | File | Fix |
|---|---------|------|-----|
| 21 | Fernet key in vars.local.yaml not vault-encrypted | `vars.local.yaml:140` | Move to `vault.yaml` |
| 22 | No data retention policy — unbounded PII | `audit_log` table | Implement cleanup CronJob |
| 23 | Storage encryption at rest unverified | DB PVC | Verify OSD encryption |
| 24 | `--initial-branch` validation ordering | `main.py:9211` | Call `_validate_branch()` before `_git_init()` |
| 25 | LDAP filter value not escaped | `ldap_auth.py:79` | Use `escape_filter_chars()` |
| 26 | Engine Containerfile runs as root | `engine/Containerfile` | Add `USER 1001` |
| 27 | Engine + MCP deps unpinned (`>=`) | `requirements.txt` | Pin to exact versions |
| 28 | No login rate limiting | `main.py:2920-2946` | Per-account lockout after N failures |
| 29 | Default admin with `changeme` password | `main.py:1223-1250` | Server-side must-change enforcement |
| 30 | Swagger/ReDoc exposed without auth | `main.py:888` | Add `docs_url=None, redoc_url=None` |

---

## Attack Chains to Be Aware Of

These are multi-step exploits that chain findings together:

**Chain A: Prompt Injection → Code Execution**
UC author → prompt injection in UC description (Finding 13) → LLM emits malicious enhancement targeting `.github/workflows/` (Finding 5) → PR auto-created → CI executes attacker code.
_Currently blocked by arity bug in `_enh_apply_pr_body`. Fixing that bug without fixing the path validation re-enables this chain._

**Chain B: Repo Poisoning → Code Execution**
Attacker commits poisoned markdown to managed repo (Finding 16) → MCP indexes it → LLM reads via `get_document` → injection influences enhancement output → malicious PR (Finding 5).

**Chain C: Deleted User Persistence**
Admin deletes user → PAT survives (Finding 11) → JIT re-provisions account → user regains access across all former projects.

**Chain D: Unauthenticated Spec Exfiltration**
Internet attacker → discover MCP Route hostname → call `get_document` (Finding 3) → exfiltrate full spec corpus. Zero auth required.

---

## What's Clean (Don't Waste Time Here)

These areas were thoroughly audited and found secure:

- **SQL injection** — All queries parameterized. ~22 f-string interpolations in SQL all use code-controlled constants.
- **YAML deserialization** — All `yaml.safe_load()`. No bare `yaml.load()` anywhere.
- **Path traversal (API)** — `os.path.realpath()` + `startswith()` containment, `..` rejection, `relative_to()` checks.
- **XSS (Frontend)** — `esc()` HTML entity encoding applied consistently across ~427 `innerHTML` sites.
- **Git credential logging** — Uses `type(ex).__name__` not `str(ex)`. PATs never in log output.
- **Password hashing** — argon2 with proper parameters.
- **HMAC session signing** — SHA256, `compare_digest`, min 32-byte key, expiry validation.
- **Fernet credential encryption** — Fail-closed (503 if key unavailable).
- **K8s Secrets** — All via `secretKeyRef`, never plaintext values.
- **Tekton webhook auth** — HMAC signature validation via github interceptor.

---

## PII Inventory (for retention policy design)

| PII Type | Tables/Columns |
|----------|---------------|
| Email addresses | `users.email`, `user_invitations.email`, `api_tokens.email`, `account_identities.alias` |
| IP addresses | `audit_log.ip` |
| User agent strings | `audit_log.user_agent` |
| GitHub usernames | `pr_comments.author_login`, `pr_comments.author_url` |
| Activity timestamps | `users.last_seen`, `api_tokens.last_used_at` |
| Audit attribution | `created_by`, `updated_by`, etc. across ~30 tables |

---

## Implementation Notes

- The repo is at `/Users/chris/git/dav`
- API entrypoint: `review-console/api/app/main.py` (~13,500 lines)
- Deploy via: `cd /Users/chris/git/dav && ansible-playbook ansible/playbook.yaml -e @ansible/inventory/group_vars/all/vars.local.yaml`
- Vault pass: `/Users/chris/git/dav/.vault_pass` (set `ANSIBLE_VAULT_PASSWORD_FILE`)
- The existing security audit from 2026-06-05 is at `docs/security-audit.md` (committed, public)
- All files in `docs/internal/` are gitignored — keep it that way
- Follow existing code patterns: asyncpg parameterized queries, `require_priv()` for auth, `HTTPException` with structured detail dicts
- **The PAT `dav_pat_RELY...` used during this audit session must be rotated** — it appears in conversation context

---

## Suggested Approach for Remediation

1. Start with P0 items — they're mostly small fixes (add auth guards, add path validation, enable existing MCP LB). DB backups is the largest P0 item.
2. For P1, the biggest effort is the exception detail cleanup (23+ locations) — a single pass through main.py replacing `f"...{e}"` patterns.
3. For P2, prompt injection defenses and model_configs encryption are the meatiest items.
4. Don't fix the `_enh_apply_pr_body` arity bug (Finding 5) until the path validation is in place — the bug is currently providing accidental safety against Chain A.
