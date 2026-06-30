# DAV Security — Combined Findings & Remediation Plan (2026-06-16)

_Merge of two independent adversarial audits: **Session A** (this session, Opus 4.8, 8 reviewers +
live cluster checks) → `dav-security-review.md`; **Session B** (Opus 4.6, 1M ctx, 6 reviewers) →
`2026-06-16-security-audit-handoff.md` + `-full.md`. Both authorized red-teams of Chris's own app.
Egress was cut as interim containment. Nothing remediated yet — this is the plan to chat through._

## The cross-check is the headline
Strong agreement on the fundamentals being sound (no SQLi, YAML safe_load, argon2, HMAC sessions
fail-closed, Fernet repo creds, zip-bomb guards). But each audit caught major issues the other
**missed** — which is exactly why two passes were worth it:

**Only Session B caught (we missed — mostly infra/ops + the MCP server, which we didn't scope):**
- **No DB backup strategy at all** (CRITICAL) — PVC loss = total unrecoverable loss of 66 tables. Biggest ops risk; neither of my reviewers checked backups.
- **dav-docs-mcp exposed to the internet with ZERO auth** (HIGH, Chain D) — anyone can `get_document` and exfiltrate the whole DCM/UDLM spec corpus. A secured LB sidecar exists but is disabled by default.
- Postgres runs **without TLS** (in-transit cleartext incl. the plaintext api_key).
- **Shell injection in Tekton task params** (unquoted `$(params.*)`).
- **Customer/NDA content leaked into logs** (LLM output snippets, `log.exception` with UC YAML, vLLM request logging on).
- **Account deletion doesn't revoke PATs** (Chain C — deleted user regains access via JIT).
- CORS wildcard + credentials; invitation tokens stored plaintext; legacy `code_repo_configs.token` not cleared post-migration; LDAP filter not escaped; engine container runs as root; no login rate-limit; default-admin `changeme`; Swagger/ReDoc exposed.
- **Enhancement→CI RCE** detailed: `enhancement_apply` `target_path` has no traversal/extension guard → LLM (via prompt injection) writes `.github/workflows/*.yml` → CI executes attacker code. (Currently blocked only by an accidental arity bug — do NOT fix that bug without the path guard.)

**Only Session A caught (they missed):**
- **`/api/bundles/{id}/attach` decorator bypass** — the route binds to an internal helper, not the guarded handler → unguarded.
- **A UC *Editor* can rewrite the live stage-2 evaluation prompt → "always pass"** (`prompt.manage` on `project-edit`, no enforced A/B gate). Integrity attack on the core verdict — neither of their reviewers flagged the privilege/always-pass angle.
- **Stored XSS via Scoping-Set name** — `esc()` does NOT escape `'`/`"`, so `onclick="…'${esc(name)}'…"` breaks out. **Session B explicitly marked XSS "clean."** This is the most important disagreement — there IS a real XSS class in the attribute/JS-string contexts.
- **No CSRF protection** (SameSite=Lax, no token); the **unpinned CDN import** for in-browser transcription; **`/api/import` cross-project overwrite** (uuid-only UPDATE); and the **recording-pipeline** specifics (NULL-project IDOR, 200 MB-in-Postgres DoS, ffmpeg no timeout, transcripts never swept) — all brand-new code they didn't deeply cover.
- **Live egress verdict:** confirmed open to the internet → the SSRF key-exfil was really exploitable (now contained by the egress cut).

**Both caught (high confidence):** DELETE /api/credentials unauth; `model_configs.api_key` plaintext (Session A confirmed a **live `sk-ant` key** in the DB); DB has no NetworkPolicy; SSRF via model endpoint_url; prompt-injection with no delimiting (B added the **external PR-comment vector** — worse, no DAV auth); the structural "**~60 endpoints rely on a no-op `_approval_gate`**" root cause; audit/retention/PII gaps; at-rest encryption gaps; **rotate the audit PAT** `dav_pat_RELY…`.

## Reconciled severity (merged, deduped)

### P0 — now
1. **Rotate secrets**: the live `sk-ant` api_key + the audit PAT `dav_pat_RELY…`. (A+B)
2. **DB backup** (pg_dump CronJob → encrypted storage, tested restore). (B) — *the one with no code fix and the worst worst-case.*
3. **Auth holes**: `GET /api/runs/{name}/turns` (B, serves NDA prompts unauth), `/api/bundles/{id}/attach` decorator (A), `DELETE /api/credentials` (A+B), `/api/analysis/roadmap` priv (A). One-liners.
4. **`esc()` → escape `'` and `"`** — kills the stored-XSS class (A; B missed it).
5. **MCP**: enable the auth LB (`dav_docs_mcp_lb_enabled: true`) + NetworkPolicy. (B)
6. **Enhancement path guard** before any CI write; keep the arity bug until the guard lands. (B; A had it HIGH)

### P1 — this week
7. **NetworkPolicy default-deny** + scope API ingress to the UI pod + DB ingress to api/worker. (A+B) — closes the in-namespace→DB chain.
8. **`_approval_gate` → unconditional** (global default-deny auth so a forgotten guard fails closed) — the structural root cause both audits hit. (A+B)
9. **Tenancy**: add `project_id` to orphan tables (`improvement_proposals`, `run_diagnoses`, `uc_analyses/gaps`, `assessment_findings`, `recording_jobs` NOT NULL) + scope the by-id IDOR readers + fix `/api/import` UPDATE. (A)
10. **Exception-detail leak** in `HTTPException(500, f"…{e}")` (23+ sites) → generic msg, log server-side. (B)
11. **Postgres TLS** (`sslmode=verify-full`). (B)
12. **Content out of logs** + vLLM `--disable-log-requests`. (B)
13. **CSRF**: SameSite=Strict + Origin check. (A) **CORS**: pin `CORS_ORIGINS`. (B)
14. **Restrict the live-prompt-apply privilege to admin + enforce the A/B gate.** (A)

### P2 — next
15. **Encrypt `model_configs.api_key`** (Fernet) + clear legacy `code_repo_configs.token`. (A+B)
16. **Prompt-injection defenses**: XML/nonce delimiters + "data not instructions" across all concat sites incl. the **PR-comment** vector; cap lengths; **schema-constrained output (#182 — also fixes the YAML-validation pain)**. (A+B)
17. **MCP symlink containment**; **Tekton param validation** (Pydantic regex on `RunTriggerIn`). (B)
18. **PAT hardening**: revoke on account delete; validate email-exists on mint; optional project-scope. (B)
19. **SSRF allowlist** (RFC1918/metadata/link-local denylist on every server-side fetch) — defense-in-depth now egress is cut. (A+B)
20. **Recording pipeline**: TTL sweeper (transcripts persist forever today), null bytes on all terminal states, job quota / size cap, ffmpeg `timeout=`. (A)
21. **Invitation tokens → hashed**. (B) **Pin the CDN import / self-host.** (A)

### P3 — backlog
Fernet key → vault; audit/PII retention CronJob; verify OSD encryption; LDAP filter escape; engine `USER 1001`; pin engine/MCP deps; login rate-limit + default-admin enforcement; disable Swagger/ReDoc; `--initial-branch` validation order.

## Provenance/compliance (#184) connection
The tenancy work (P1.9) adds the `project_id` keys, and the audit gaps (no before→after, reads
unaudited, token/credential/prompt/export actions undetailed) ARE the provenance gaps. The capstone
is an append-only `entity_history(entity_type, entity_id, project_id, actor, action, before, after, ts)`
+ a read-audit policy + populating `audit_log.detail/project_id/object_id` on the sensitive handlers.

## Open decisions for the chat
1. **Global default-deny auth** (P1.8): invasive but it's the structural fix both audits demand — do it, or surgical per-endpoint only?
2. **At-rest encryption**: app-layer Fernet on sensitive columns vs encrypted StorageClass/KMS for the DB volume (the Fernet key is co-resident, limiting app-layer value vs an in-namespace attacker) — which, or both?
3. **Recording storage**: keep capped+swept in-DB, or move to encrypted PVC/object store now?
4. **MCP**: is it meant to be internet-facing at all? If not, the simplest fix is no public route + same-namespace only.
5. **Sequencing**: I'd do P0 + the NetworkPolicy (P1.7) immediately (low app-risk, closes the scariest chains), then design the auth/tenancy/provenance pass (P1.8/9 + #184) as one coherent change.
