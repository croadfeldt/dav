# DAV — Integrating Agents & Automation

_Living document. DAV changes often; update this when the auth model or the Agents UI
changes (house rule). Companion docs: `user-guide.md` (humans), `security-audit.md`
(the auth/security model), `review-console-design.md` (design)._

This guide explains how to give an **external agent or automation** (a CI job, a script, a
coding agent like a Claude work session, an MCP server, a webhook consumer) authenticated
access to DAV's API.

---

## 1. The model in one paragraph

DAV authenticates agents with **Personal Access Tokens (PATs)** — opaque bearer tokens of
the form `dav_pat_…`. A token is **bound to a DAV account** (an email/identity) and, when
presented, the request **acts as that account** and gets exactly that account's
**roles and privileges** (RBAC). There are no separate "token scopes" to learn: a token can
do whatever its account can do. So the unit of least-privilege is **the identity you bind the
token to** — give an agent a *dedicated* account with only the roles it needs, rather than
binding a token to a person (or to a platform admin) by default.

Only the **hash** of a token is stored. The secret is shown **once**, at creation. Tokens are
**revocable** and can carry an **expiry**.

---

## 2. Create a token

### Option A — the Agents panel (recommended)

1. Sign in to DAV as a **platform admin**.
2. Go to **Users & roles → 🤖 Agents**.
3. Under **Generate token**, fill in:
   - **Acts as (account email)** — the identity the token authenticates as (see §3).
   - **Label** — what the token is for (e.g. `ci-nightly`, `work-claude-session`). Shown in
     the list; pick something you'll recognize when it's time to rotate or revoke.
   - **Expires** — 30 / 90 / 365 days, or no expiration. Prefer a finite expiry.
4. Click **Generate token**. The secret appears **once** in a green card with a **Copy**
   button and a ready-to-use `curl` example. **Copy it now** and store it in the agent's
   secret store — it is not shown again.
5. The token appears in **Active tokens** with its status, last-used, and a **Revoke** button.

### Option B — the API (for scripting the mint itself)

`POST /api/tokens` (platform-admin only):

```bash
curl -s https://dav.roadfeldt.com/api/tokens \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"email":"ci-bot@roadfeldt.com","label":"ci-nightly","expires_at":"2027-01-01T00:00:00Z"}'
# -> {"ok":true,"email":"ci-bot@roadfeldt.com","token":"dav_pat_…","note":"Store this now …"}
```

`expires_at` is optional (omit / `null` for no expiry). The plaintext `token` is in the
response **once**.

---

## 3. Choose the right identity (least privilege)

A token inherits the **roles of the account it acts as**. Consequences:

- **A brand-new account with no role bindings can authenticate but do nothing.** That's the
  safe default — mint the token, then grant the account only the roles it needs in
  **Users & roles**.
- **Prefer a dedicated agent identity** (e.g. `ci-bot@roadfeldt.com`, `agent-…@roadfeldt.com`)
  over binding a token to a person. It keeps the audit trail clear (actions show the agent,
  not you) and lets you revoke/rotate without touching a human account.
- **Avoid binding agent tokens to a platform admin** unless the agent genuinely needs
  platform administration. Scope down: a read-only integration only needs view roles; a
  pipeline that runs evaluations needs the project run/execute roles, etc.

> A quick-unblock token may be bound to a real user (e.g. yourself) to get moving; treat that
> as temporary and migrate to a scoped agent identity.

---

## 4. Use the token

Send it as a **bearer** header on every request:

```bash
export DAV_TOKEN=dav_pat_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
curl -s https://dav.roadfeldt.com/api/me \
  -H "Authorization: Bearer $DAV_TOKEN"
```

`/api/me` is the best first call — it echoes back who the token authenticates as and the
exact privileges it has:

```json
{ "reviewer":"ci-bot@roadfeldt.com", "authenticated":true,
  "role":"project-viewer", "privileges":["project.view","project.data.read", …],
  "is_platform_admin":false, "approved":true, … }
```

If `authenticated` is `false`, the token wasn't accepted (wrong/expired/revoked, or the
header is malformed). If `authenticated` is `true` but `privileges` is empty, the **identity
has no roles** — grant some in Users & roles (§3).

Beyond `/api/me`, an agent uses the **same `/api/…` endpoints the web app uses** — there is no
separate agent API. Watch the browser's network tab while doing the task by hand to discover
the calls, then replay them with the bearer header. Common read endpoints include `/api/me`,
`/api/runs`, `/api/runs/status`. Project-scoped calls take a project via the `X-DAV-Project`
header (the UI sends the active project the same way).

---

## 5. TLS & reachability

- The agent must be able to reach the **public** route `https://dav.roadfeldt.com` from
  wherever it runs.
- If the edge certificate is the **homelab CA** (not a publicly-trusted CA), the agent needs
  the CA bundle: `curl --cacert /path/to/homelab-ca.pem …` (or install it into the agent's
  trust store). Don't disable verification in anything but a throwaway test.

---

## 6. Lifecycle: expiry, rotation, revocation

- **Expiry** — set one at creation. An expired token simply stops authenticating
  (`authenticated:false`).
- **Rotation** — generate a new token, deploy it to the agent, confirm it works (`/api/me`),
  then **revoke the old one**. Tokens are independent, so this is zero-downtime.
- **Revoke** — **Users & roles → Agents → Revoke** (or `DELETE /api/tokens/{id}`). Revocation
  is **immediate** (the token drops from the in-memory cache as well as being marked revoked).
- **Last used** — the Agents table shows each token's last-used time; use it to spot stale or
  unexpectedly-idle tokens.

```bash
# revoke token id 7
curl -s -X DELETE https://dav.roadfeldt.com/api/tokens/7 \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

---

## 7. Security practices

- **The secret is shown once.** Store it in the agent's secret manager (CI secret, Vault, a
  k8s Secret) — **never commit it to code or config**, and never paste it where it gets logged.
- **One token per agent/use** with a descriptive label, so revocation is surgical.
- **Least privilege via the bound identity** (§3) — don't reach for an admin-bound token.
- **Finite expiry + rotation** over long-lived tokens.
- Treat a leaked token like a leaked password: **revoke immediately**, then re-mint.

---

## 8. How it works (under the hood)

For the curious / for security review (full detail in `security-audit.md`):

- DAV's API sits behind an **oauth-proxy** that **skips `/api/`** (`--skip-auth-regex=^/api/`),
  so the **API authenticates `/api/` requests itself**.
- This is safe because the API service is **cluster-internal only** (no public route of its
  own); the fronting **nginx scrubs** client-supplied identity headers
  (`X-Forwarded-*`, `X-Auth-Request-*`) on `/api/` while passing the `Authorization` header
  through; and the API **fails closed** (401) when no valid credential resolves.
- `get_user` resolves identity in order: internal **service token** → **PAT** (this doc) →
  **app session cookie** (interactive users, established via the oauth-gated `/sso`). Forged
  identity headers are **not** trusted on the normal path — only the `/sso` bootstrap reads
  them, and it's oauth-gated.

So a PAT presented to `https://dav.roadfeldt.com/api/…` flows straight to the API's token
check and resolves to its bound account. No proxy configuration is needed per agent.

---

_Related: `user-guide.md` · `security-audit.md` · the Agents panel under Users & roles._
