## F3 — Audit log (who did what + login/logout/timeout)
**Status: not started. Largest item.** Related: task #78 (login history in Users & Roles).
- Auth surface: `POST /api/auth/login` (main.py:2526, sets session cookie ~2377),
  `POST /api/auth/logout` (main.py:2544, deletes cookie), `/api/auth/sso` (2551),
  `/api/me` (1472). Sessions: `local_auth.py` (`make_session`/`read_session`,
  HMAC-signed, expiry baked into token → "timeout" = token expired). LDAP path:
  `ldap_auth.py`. Auth middleware around main.py:855-945.
- **Design (proposed):**
  - `audit_log` table in schema.sql: id, ts, actor_email, actor_source(local/ldap/sso),
    project_id (nullable=global), action (verb), object_type, object_id, summary,
    ip, user_agent, outcome(success/denied/error), detail JSONB.
  - **Action capture:** a small helper `record_audit(conn, request, action, …)` called
    at mutating endpoints (run trigger, UC create/approve, RBAC change, repo/cred edit,
    project change, etc.). Prefer an explicit helper over blanket middleware so we log
    intent + object, not just method/path. Optionally a middleware fallback for
    coverage of all non-GET 2xx.
  - **Auth events:** record login(success/fail), logout, and session-timeout (emit when
    a request arrives with an expired/invalid session cookie that had been valid — detect
    in the auth dependency). Capture ip + user_agent.
  - **UI:** an "Audit" view (platform-admin = all; project scope = members' actions),
    filter by actor/action/object/date; reuse the Users & Roles area (#78). RBAC-gate it.
- **Open Qs for user:** retention window? per-project visibility rules? include read
  actions or mutations + auth only? PII/IP storage ok?

