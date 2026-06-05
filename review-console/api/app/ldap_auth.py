"""LDAP-backed user approval. Config-driven (a dict), so it can come from the
in-app settings (platform-admin UI) or from env as a fallback.

Identity is established upstream by the oauth-proxy (X-Forwarded-User/-Email);
LDAP decides *approval* via membership in a configured group. Approved users are
synced into the `users` table by the caller. Env keys (fallback when the in-app
setting is empty):

  DAV_LDAP_URL DAV_LDAP_BIND_DN DAV_LDAP_BIND_PASSWORD DAV_LDAP_USER_BASE
  DAV_LDAP_GROUP_DN DAV_LDAP_USER_ATTR DAV_LDAP_MAIL_ATTR DAV_LDAP_NAME_ATTR
  DAV_LDAP_MEMBER_ATTR DAV_LDAP_START_TLS DAV_LDAP_ENFORCE DAV_LDAP_BOOTSTRAP_ADMINS
"""
import os
import logging

log = logging.getLogger("dav-review-api.ldap")

# Bootstrap admins stay env-only (a break-glass that survives a bad DB setting).
BOOTSTRAP_ADMINS = [x.strip().lower() for x in
                    os.environ.get("DAV_LDAP_BOOTSTRAP_ADMINS", "").split(",") if x.strip()]


def env_config() -> dict:
    """LDAP config from env (the fallback when no in-app setting exists)."""
    return {
        "url":          os.environ.get("DAV_LDAP_URL", "").strip(),
        "bind_dn":      os.environ.get("DAV_LDAP_BIND_DN", "").strip(),
        "bind_password": os.environ.get("DAV_LDAP_BIND_PASSWORD", ""),
        "user_base":    os.environ.get("DAV_LDAP_USER_BASE", "").strip(),
        "group_dn":     os.environ.get("DAV_LDAP_GROUP_DN", "").strip(),
        "user_attr":    os.environ.get("DAV_LDAP_USER_ATTR", "uid").strip(),
        "mail_attr":    os.environ.get("DAV_LDAP_MAIL_ATTR", "mail").strip(),
        "name_attr":    os.environ.get("DAV_LDAP_NAME_ATTR", "cn").strip(),
        "member_attr":  os.environ.get("DAV_LDAP_MEMBER_ATTR", "member").strip(),
        "start_tls":    os.environ.get("DAV_LDAP_START_TLS", "false").lower() == "true",
        "enforce":      os.environ.get("DAV_LDAP_ENFORCE", "false").lower() == "true",
    }


def is_configured(cfg: dict) -> bool:
    """Usable when a server URL and an approval group are set."""
    return bool(cfg.get("url") and cfg.get("group_dn"))


def fetch_approved_users(cfg: dict) -> list[dict]:
    """Return [{username, email, display_name}] for members of cfg['group_dn'].
    Synchronous (ldap3) — call via asyncio.to_thread. Raises on LDAP error."""
    from ldap3 import Server, Connection, ALL, BASE, SUBTREE

    url = cfg["url"]
    user_attr = cfg.get("user_attr", "uid")
    mail_attr = cfg.get("mail_attr", "mail")
    name_attr = cfg.get("name_attr", "cn")
    member_attr = cfg.get("member_attr", "member")
    user_base = cfg.get("user_base", "")
    group_dn = cfg["group_dn"]

    server = Server(url, use_ssl=url.lower().startswith("ldaps"), get_info=ALL)
    conn = Connection(server, cfg.get("bind_dn") or None,
                      cfg.get("bind_password") or None, auto_bind=True)
    try:
        if cfg.get("start_tls"):
            conn.start_tls()
        conn.search(group_dn, "(objectClass=*)", search_scope=BASE, attributes=[member_attr])
        members: list[str] = []
        if conn.entries and member_attr in conn.entries[0]:
            members = [str(v) for v in conn.entries[0][member_attr].values]

        users: list[dict] = []
        seen: set[str] = set()
        for m in members:
            if not m:
                continue
            if "=" in m and "," in m:
                ok = conn.search(m, "(objectClass=*)", search_scope=BASE,
                                 attributes=[user_attr, mail_attr, name_attr])
            else:
                base = user_base or group_dn
                ok = conn.search(base, f"({user_attr}={m})", search_scope=SUBTREE,
                                 attributes=[user_attr, mail_attr, name_attr])
            if not (ok and conn.entries):
                continue
            e = conn.entries[0]
            uname = str(e[user_attr].value) if user_attr in e and e[user_attr].value else (m if "=" not in m else "")
            email = str(e[mail_attr].value) if mail_attr in e and e[mail_attr].value else ""
            name  = str(e[name_attr].value) if name_attr in e and e[name_attr].value else (uname or email)
            key = (uname or email).lower()
            if key and key not in seen:
                seen.add(key)
                users.append({"username": uname, "email": email, "display_name": name})
        return users
    finally:
        try:
            conn.unbind()
        except Exception:
            pass
