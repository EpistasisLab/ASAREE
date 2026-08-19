"""Symmetric encryption for ASAREE's own secrets at rest — per-user LLM API
keys, specifically. Mirrors Motoro's ``services.encryption`` shape, but
keyed off ``AsareeSettings.encryption_key``, ASAREE's own secret: core's
version is explicitly a single server-side key with no user in the picture,
which is the wrong shape for what this needs to protect.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet

from asaree.config import get_settings

_fernet: Fernet | None = None


def _derive_key(secret: str) -> bytes:
    """A valid Fernet key (32 bytes, urlsafe-base64) from an arbitrary secret.

    Fernet requires that specific format; ``encryption_key`` is just a plain
    configured string, the same as ``auth_secret_key`` — deriving rather than
    requiring the operator to already know Fernet's format.
    """
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_derive_key(get_settings().encryption_key))
    return _fernet


def encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    return _get_fernet().decrypt(token.encode()).decode()
