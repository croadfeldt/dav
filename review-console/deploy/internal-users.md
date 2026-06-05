# Internal users + email invites — activation

The app-side is built and **non-breaking**: OCP/FreeIPA users keep authenticating
via the oauth-proxy header exactly as before. Internal (invited) users need three
deployment-side things turned on. Until then, a platform admin can already create
invites and copy the link, but external invitees can't reach the app yet.

## What's already shipped (no action needed)
- `users.password_hash` (argon2) + `user_invitations` tables (applied on API boot).
- `app/local_auth.py`: argon2 hashing + HMAC-signed session cookie (`dav_session`).
- `get_user()` accepts an app **session cookie** OR the oauth-proxy header (cookie first).
- Endpoints: `POST /api/invites` (admin), `GET /api/invites`, `DELETE /api/invites/{token}`,
  public `GET /api/invites/{token}` + `POST /api/invites/{token}/accept`, `POST /api/auth/login`,
  `POST /api/auth/logout`. The `/api/auth/*` and `/api/invites/*` paths are exempt from the
  approval gate (invitees aren't approved until they accept).
- UI: Config → Users & Access → Projects → Members → **Invite by email**; and a public
  accept overlay shown when the URL has `?invite=<token>`.

## 1. Session secret (required for any internal login)
Sessions are signed with `DAV_SESSION_SECRET`, falling back to the existing
`DAV_FERNET_KEY`. If you already set `dav-fernet-key`, nothing to do. Otherwise add
`DAV_SESSION_SECRET` (any long random string) to the `dav-ldap` (or another) Secret.
With no secret, `accept`/`login` return 503 and OCP/header auth still works.

## 2. SMTP (optional — for real invite emails)
Without SMTP, invites still work: the admin copies the link and shares it. To send
email, add to a Secret consumed by the API (`dav-ldap` envFrom already covers extra keys,
or create `dav-smtp`):

```
DAV_SMTP_HOST=smtp.roadfeldt.com
DAV_SMTP_PORT=587
DAV_SMTP_USER=dav
DAV_SMTP_PASSWORD=...
DAV_SMTP_FROM=dav@roadfeldt.com
DAV_SMTP_TLS=true
DAV_BASE_URL=https://dav-review.apps.ocp.roadfeldt.com   # so invite links are absolute
```

## 3. Relax the oauth-proxy so invitees can reach the app (required)
The SPA + API sit behind the oauth-proxy, which blocks anyone not authenticated by OCP.
Activation is **opt-in** via the Ansible var `review_console_relaxed_proxy: true` (default
false → nothing changes). Set it, set `DAV_REQUIRE_AUTH=true` on the API, and re-run
Ansible. This applies:

- oauth-proxy **skip-auth** for `^/$`, `^/index.html`, `^/assets/`, `^/favicon`, `^/api/`
  — so the SPA and the whole API are reachable without an OCP login.
- A dedicated **`/sso`** path that is deliberately **NOT** skipped → stays OCP-protected;
  nginx routes it to `POST /api/auth/sso`, which reads the `X-Forwarded` headers, mints an
  app **session cookie**, and 303s back to `/`. OCP users hit this once (the "Sign in with
  OpenShift" button); thereafter their skip-auth `/api/*` calls use the cookie.
- `DAV_REQUIRE_AUTH=true` makes the API authorize **everyone** itself (session cookie OR
  header), so a skip-auth `/api/*` is not open. Approval is then strict: only users in the
  approved set (LDAP group + internal/invited + the default admin) get in. **Bootstrap
  admins (`DAV_LDAP_BOOTSTRAP_ADMINS`) are always approved** — break-glass so an
  identity/email mismatch can't lock you out.

> RE2 (Go regexp, oauth-proxy) has no negative lookahead, which is why `/sso` is a separate
> path rather than an exclusion inside `^/api/`.

### OCP users are NOT enabled by default
`/api/auth/sso` provisions the OCP user (cross-pollinated by email — reuses an existing row
with the same email) but does **not** approve them. Until a platform admin enables them in
Config → Users & Access, they see an "account pending approval" screen. This is intentional
per the directive that OCP users are valid identities but opt-in for access.

## Flow once activated
1. Platform admin: Config → Users & Access → Projects → create a project → **Invite** a
   user by email (role: editor/viewer/admin). Link is emailed (or copied).
2. Invitee opens `…/?invite=<token>` → sets a display name + password → joins the project,
   gets a session, lands in the app.
3. Returning internal users authenticate via the app login (`POST /api/auth/login`).

## Still open (tracked)
- Identity **cross-pollination by email**: today OCP users are keyed by username and
  internal users by email — the same person via both sources is two rows. Merge-by-email
  is the follow-up.
- `POST /api/auth/sso` (OCP→session) + auto-approve OCP users.
- Platform-admin internal-user management view (list/disable/reset) beyond invites.
