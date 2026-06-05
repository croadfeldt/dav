"""Internal (local) users: password hashing + app-native signed sessions.

Identity is unified by **email** across sources. OCP/FreeIPA users are identified
by the oauth-proxy headers (unchanged). *Internal* users authenticate
app-natively: their password is stored as an argon2 hash, and a successful login
issues an HMAC-signed session cookie that `get_user` accepts in place of the
proxy header. This lets internal users (who cannot pass the oauth-proxy) use the
app once the proxy is relaxed (skip-auth) on the API + login paths.

The signing secret comes from DAV_SESSION_SECRET, falling back to the existing
DAV_FERNET_KEY. With no secret, sessions are disabled (internal login returns
503) but OCP/header auth keeps working.
"""
import os
import json
import hmac
import time
import base64
import hashlib
import logging
from typing import Optional

log = logging.getLogger("dav-review-api.local_auth")

SESSION_COOKIE = "dav_session"
SESSION_TTL = int(os.environ.get("DAV_SESSION_TTL", "86400"))  # seconds (24h)
_SECRET = (os.environ.get("DAV_SESSION_SECRET")
           or os.environ.get("DAV_FERNET_KEY") or "").encode()
# A short signing key yields forgeable session cookies → impersonation. Require
# ≥32 bytes; otherwise refuse to enable sessions (fail closed) and warn loudly.
# (A dedicated DAV_SESSION_SECRET is preferred over reusing the Fernet key.)
_MIN_SECRET_LEN = 32
if _SECRET and len(_SECRET) < _MIN_SECRET_LEN:
    log.error("DAV session signing secret is too short (%d < %d bytes) — sessions "
              "DISABLED. Set a strong DAV_SESSION_SECRET (>=32 bytes).",
              len(_SECRET), _MIN_SECRET_LEN)
    _SECRET = b""
if os.environ.get("DAV_SESSION_SECRET") is None and _SECRET:
    log.warning("DAV_SESSION_SECRET unset — reusing DAV_FERNET_KEY for session "
                "signing (key reuse across purposes). Provision a dedicated "
                "DAV_SESSION_SECRET.")


def sessions_enabled() -> bool:
    return bool(_SECRET)


# ── password hashing (argon2) ────────────────────────────────────────────────
def hash_password(pw: str) -> str:
    from argon2 import PasswordHasher
    return PasswordHasher().hash(pw)


def verify_password(stored_hash: str, pw: str) -> bool:
    from argon2 import PasswordHasher
    try:
        PasswordHasher().verify(stored_hash, pw)
        return True
    except Exception:
        return False


# ── signed session token ─────────────────────────────────────────────────────
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_session(email: str) -> str:
    payload = _b64(json.dumps({"email": email, "exp": int(time.time()) + SESSION_TTL}).encode())
    sig = _b64(hmac.new(_SECRET, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def read_session(token: str) -> Optional[str]:
    """Return the session's email if the token is valid + unexpired, else None."""
    if not token or not _SECRET:
        return None
    try:
        payload, sig = token.split(".", 1)
        expect = _b64(hmac.new(_SECRET, payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expect):
            return None
        data = json.loads(_unb64(payload))
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        return data.get("email")
    except Exception:
        return None
