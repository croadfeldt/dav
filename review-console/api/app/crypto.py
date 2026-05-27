"""Fernet wrapper for at-rest encryption of per-repo credentials.

Reads the Fernet key from the DAV_FERNET_KEY env var (provided by the
dav-fernet-key Secret). If the key is missing or invalid, encrypt/decrypt
calls raise CryptoUnavailableError — the API surface translates this to a
clear 503 ("server is not configured to store encrypted secrets") rather
than a stack trace.

Localized here so that the eventual v2 swap to HashiCorp Vault (per
ADR-004 §D) only touches this module and the small handful of callsites
in repos.py.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger("dav-review-api.crypto")


class CryptoUnavailableError(RuntimeError):
    """Raised when the Fernet key is missing or invalid. Callers should
    surface this as a 503 with operator-actionable text."""


_KEY_ENV = "DAV_FERNET_KEY"
_fernet_singleton = None  # lazy-init; set on first encrypt/decrypt call


def _load_fernet():
    global _fernet_singleton
    if _fernet_singleton is not None:
        return _fernet_singleton

    try:
        from cryptography.fernet import Fernet
    except ImportError as e:
        raise CryptoUnavailableError(
            "cryptography package is not installed; cannot encrypt/decrypt "
            "repo credentials. Add to requirements.txt and redeploy."
        ) from e

    key = (os.environ.get(_KEY_ENV) or "").strip()
    if not key:
        raise CryptoUnavailableError(
            f"{_KEY_ENV} env var is not set. Generate a key with "
            "`python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'` and provide it via "
            "the dav-fernet-key Secret (vault_dav_fernet_key in Ansible). "
            "Without this, per-repo credentials cannot be stored or read."
        )

    try:
        _fernet_singleton = Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:
        # Common cause: key was generated with a different scheme or the
        # base64 padding is off. Fernet keys must be 32-byte URL-safe
        # base64-encoded values.
        raise CryptoUnavailableError(
            f"{_KEY_ENV} is set but invalid as a Fernet key: {e}. "
            "Fernet keys must be 32 bytes, URL-safe base64-encoded. "
            "Regenerate with Fernet.generate_key()."
        ) from e

    return _fernet_singleton


def is_available() -> bool:
    """Cheap probe — does NOT raise. Useful for health checks."""
    try:
        _load_fernet()
        return True
    except CryptoUnavailableError as e:
        log.warning("crypto: unavailable — %s", e)
        return False


def encrypt(plaintext: Optional[str]) -> Optional[str]:
    """Encrypt a plaintext string to a Fernet token (URL-safe base64).

    None / empty input returns None — the caller stores NULL in the DB
    column to mean "no credential set" (distinct from "encrypted empty
    string"). Use clear_*() to delete; use encrypt(value) to set/rotate.
    """
    if plaintext is None or plaintext == "":
        return None
    fernet = _load_fernet()
    token = fernet.encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt(token: Optional[str]) -> Optional[str]:
    """Decrypt a Fernet token. Returns None if the input is None.

    Raises CryptoUnavailableError if the key is missing or the token is
    invalid (e.g., encrypted with a different key, tampered with, or
    truncated). Callers must catch and handle — for the poller this
    means "skip this repo, log a clear warning so the operator can
    re-enter the credential".
    """
    if token is None or token == "":
        return None
    fernet = _load_fernet()
    try:
        return fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except Exception as e:
        # InvalidToken or similar — most likely the key changed since
        # the value was encrypted, or the column was corrupted.
        raise CryptoUnavailableError(
            f"failed to decrypt token (length={len(token)}): {e}. "
            "Most likely cause: the Fernet key has changed since this "
            "value was encrypted. Have the operator re-enter the "
            "credential via the Repos UI."
        ) from e
