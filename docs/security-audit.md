# DAV Security Audit & Hardening — 2026-06-05

**Scope:** the full DAV review-console (API `review-console/api/app/`, UI, ansible
deploy, live `dav` namespace). Conducted as a multi-dimension sweep
(authN/authZ, secrets/data exposure, injection/SSRF/traversal,
k8s/container/network). This document is the authoritative record of findings,
what was **resolved**, and what remains **recommended** (with rationale for
anything deferred).

**Headline:** one **live, exploitable auth bypass** (C1) and one **unauthenticated
cross-project data-exfil** (C2) were found and fixed, along with a cluster of
read endpoints that authenticated but did not authorize (cross-project reads),
two filesystem path-traversals, a git argument-injection, an archive-bomb DoS,
and a TLS-verification-disabled token transmission. The egress firewall shipped
earlier meaningfully bounds the SSRF surface.

---

## Severity summary

| ID | Finding | Sev | Status |
|----|---------|-----|--------|
| C1 | X-Forwarded-User header spoofing → impersonate any identity (incl. platform admin) on the relaxed-proxy `/api/` path | **Critical** | ✅ Resolved |
| C2 | `GET /api/export` — unauthenticated, unscoped export of **all** projects' use cases | **Critical** | ✅ Resolved |
| H1 | `GET /api/use-cases/{uuid}` — cross-project read (no project check) | High | ✅ Resolved |
| H2 | `GET /api/catalog` + `/suggestions` — caller-supplied `project_id`, no auth → cross-project enumeration | High | ✅ Resolved |
| H3 | `GET /api/sets/{id}` — cross-project read | High | ✅ Resolved |
| H4 | `GET /api/credentials` (+ by-id) — no auth/authz (metadata disclosure) | High | ✅ Resolved |
| H5 | `GET /api/results/{run}/uc/{uuid:path}` — path traversal → arbitrary `*.yaml` read | High | ✅ Resolved |
| H6 | `tail_turns` `file` query param — path traversal → arbitrary file read | High | ✅ Resolved |
| H7 | dav-docs-mcp Route serves the entire DCM corpus **unauthenticated** to the internet | High | ⏳ Remediation authored (default-off); needs watched rollout |
| H8 | Tekton `pipeline` SA bound to cluster `edit` over the `dav` namespace (operator-injected) → can read all Secrets / rewrite all workloads | High | 📋 Documented (needs pipeline-SA rework) |
| M1 | `GET /api/stage-context/{stage}` — no auth, caller-supplied `project_id` | Medium | ✅ Resolved |
| M2 | git `ls-remote` argument injection via `branch` (leading `-`) + PAT-in-stderr echo | Medium | ✅ Resolved |
| M3 | `/api/import` archive-bomb / unbounded-memory DoS | Medium | ✅ Resolved |
| M4 | MCP health poll sent the bearer token over a `verify=False` (unverified TLS) connection | Medium | ✅ Resolved |
| M5 | Session signing secret had no minimum-strength check; could reuse the Fernet key | Medium | ✅ Resolved |
| M6 | Break-glass admin password rendered as a plaintext `value:` in the API Deployment | Medium | ✅ Resolved |
| M7 | `model_configs.api_key` stored **plaintext at rest** (only masked on GET) | Medium | 📋 Documented (encrypt-at-rest; threads decrypt through engine call sites) |
| M8 | SSRF via user-supplied `sse_url` / `endpoint_url` / `repo_url` (health poll, model calls, ref-check) | Medium | 📋 Mitigated by egress firewall; hardening recommended |
| M9 | `alpine/git:latest` (unpinned, docker.io) init container — supply-chain swap vector | Medium | 📋 Documented (pin to digest) |
| M10 | OCP docker-strategy build pods run privileged + mount node kubelet creds | Medium | 📋 Documented (inherent to docker builds; limit who can build) |
| L1 | Sessions not revocable on password change (no epoch claim) | Low | 📋 Documented |
| L2 | Deployment templates rely on the cluster's `restricted-v2` SCC (no explicit securityContext) | Low | 📋 Documented (add defense-in-depth securityContext) |
| L3 | Floating image tags on postgres/oauth-proxy/cli (trusted registries) | Low | 📋 Documented (pin digests) |

---

## Resolved this pass

- **C1 — header-spoof auth bypass (`review-console-ui-nginx-cm.yaml.j2`).** In
  relaxed-proxy mode the oauth-proxy `--skip-auth-regex=^/api/` passes `/api/`
  through *without* authenticating, so a client-supplied `X-Forwarded-User`
  header reached the API and was trusted (`get_user`, `main.py:686`). On
  `dav.roadfeldt.com` an attacker could set `X-Forwarded-User: <any admin>` and
  act as them. **Fix:** the `/api/` nginx location now **clears**
  `X-Forwarded-User/Email` + `X-Auth-Request-User/Email` in relaxed mode —
  identity there comes only from the signed session cookie minted at the
  oauth-protected `/sso`. (Non-relaxed mode, where oauth-proxy authenticates
  `/api/`, still passes them through.) Mirrors the LB/TLS path which already did
  this. *Residual:* an in-cluster pod could still hit `dav-review-api:8000`
  directly with a spoofed header — a namespace foothold is required; documented
  as defense-in-depth (gate `X-Forwarded-*` trust on a proxy-shared marker).
- **C2 / H1–H4 / M1 — read endpoints now authorize + scope.** `export`,
  `use-cases/{uuid}`, `sets/{id}`, `catalog` + `suggestions`, `credentials`
  (list + by-id), and `stage-context` GET now resolve the **membership-validated**
  active project (`_active_project_id`), require the appropriate privilege
  (`project.data.read`, or `project.repos` for credentials), and filter every
  query by `project_id`. The caller-supplied `project_id` query params (catalog,
  stage-context) are gone — the project comes only from the validated
  `X-DAV-Project`. Catalog `suggestions` now constrains `uc_capabilities` to runs
  in the active project.
- **H5 / H6 — path traversal.** Added `results._safe_under(root, *parts)` (resolve
  + `relative_to` containment) and routed `get_analysis` (the `{uuid:path}`
  route) and `tail_turns` (the `file` query param) through it, so neither can
  escape the run's directory.
- **M2 — git arg-injection + PAT leak.** `_check_repo_ref` rejects a `branch`
  starting with `-`, adds the `--` end-of-options separator, and no longer echoes
  raw git stderr (which could contain the `x-access-token:<PAT>@` URL) — it
  returns a generic reachability message.
- **M3 — archive bomb.** `/api/import` caps the compressed upload (32 MiB), entry
  count (5000), and cumulative decompressed size (256 MiB), rejecting with 413.
- **M4 — MCP poll TLS.** The health poll dropped `verify=False`; it now verifies
  TLS (the poll carries the decrypted bearer token).
- **M5 — session secret strength.** `local_auth` refuses to enable sessions if the
  signing secret is < 32 bytes (fail closed) and warns when the Fernet key is
  reused for signing.
- **M6 — admin password to a Secret.** `DAV_DEFAULT_ADMIN_PASSWORD` now comes from
  a `dav-review-admin-bootstrap` Secret via `secretKeyRef` instead of a plaintext
  `value:` in the pod spec.

## Recommended / deferred (with rationale)

- **H7 — dav-docs-mcp unauthenticated exposure.** Remediation is the planned
  hardening: dedicated internal MetalLB IP (`10.0.90.23`) + DNS-01 TLS + an nginx
  **bearer-token** sidecar (FastMCP rebinds to `127.0.0.1`), DAV authenticating
  with the stored `mcp_server_configs.auth_token` (DAV-side already shipped). The
  ansible is authored **default-off** (`dav_docs_mcp_lb_enabled: false`) because
  it has a cert-issuance dependency and an OpenShift-UID-sensitive sidecar that
  warrant a **watched** first rollout. Enable + watch, then drop the public
  `*.apps` Route and retighten egress (drop `10.0.0.70/32`, add `10.0.90.23/32`).
- **H8 — pipeline SA `edit`.** Operator-injected `openshift-pipelines-edit`
  binding gives the default `pipeline` SA cluster-`edit` over the namespace. Run
  DAV pipelines under the least-priv `dav-pipeline-sa` and remove that binding.
  Deferred (changing the pipeline SA risks breaking the build/run path; needs a
  watched change).
- **M7 — model api_key at rest.** Add `api_key_encrypted`, Fernet-wrap on write,
  decrypt at the engine-call boundary, migrate existing rows. Deferred because
  threading decryption through every model-call site has breakage risk and
  warrants verification with a live run.
- **M8 — SSRF.** Bounded by the egress firewall (only node IPs, the model/MCP
  ingress, SMTP, internet are reachable; RFC1918 denied). Recommended extra:
  block loopback/link-local explicitly even within the allowlist; disable
  `follow_redirects` on the inference-probe; gate the probe routes behind project
  RBAC.
- **M9 / L3 — image pinning.** Pin `alpine/git` and the postgres/oauth-proxy/cli
  images to digests (or mirror internally). DAV-built images already resolve to
  digests via ImageStream triggers.
- **M10 / L2 — build-pod privilege & explicit securityContext.** Inherent to OCP
  docker-strategy builds; limit who can trigger builds (ties to H8). Add explicit
  `runAsNonRoot`/`drop ALL caps`/`seccomp RuntimeDefault` to the app deployment
  templates as defense-in-depth (the live pods already get this from
  `restricted-v2`).
- **L1 — session revocation.** Add a per-user session epoch bumped on password
  change so a stolen cookie can be invalidated before its 24h TTL.

---

## Done well (verified)

- **RBAC model + escalation guard.** Accounts×roles×privileges; the assign-role
  subset check prevents granting privileges you don't hold; role-privilege
  editing is platform-admin-only; `platform.admin` superuser bypass is explicit.
- **Resource-ownership pattern** (`_gate_resource` / `_require_priv_conn`) is
  correct and now applied to the read paths that had omitted it.
- **Secret masking is airtight** — `repos`/`credentials` row serializers whitelist
  columns and expose only `has_*` booleans; `_mcp_public` masks the new MCP token;
  model `api_key` masked on GET; `/api/me` + LDAP/SMTP GETs leak nothing.
  Decryption paths are never wired to an HTTP response. Logging never emits
  secrets.
- **Egress firewall** — tight, node `/32`s (not a `/24`), RFC1918 denied; bounds
  SSRF and lateral movement.
- **X-Forwarded stripping** on the LB/TLS path (and now the relaxed `/api/` path).
- **Crypto hygiene** — argon2 passwords, HMAC-SHA256 cookies with `compare_digest`,
  256-bit single-use invite/reset tokens with expiry, per-repo webhook HMAC.
- **SQL injection: none** — full asyncpg `$N` parameterization; the only
  identifier interpolation (`_gate_resource`, SET-clause builders) uses
  code-literal/whitelisted names. **Deserialization: `yaml.safe_load` only, no
  pickle.** **Shell: argv lists, no `shell=True`.**
- **k8s RBAC** — DAV-authored Roles are `resourceNames`-scoped with minimal verbs;
  no wildcards, no `secrets list/watch`. Live pods run under `restricted-v2`
  (non-root, dropped caps, seccomp). All credentials are proper Secrets; none in
  ConfigMaps. External listeners are TLS-only.
