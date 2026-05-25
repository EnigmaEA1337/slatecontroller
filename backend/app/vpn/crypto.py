"""Symmetric encryption for sensitive VPN data (WG private keys).

Uses Fernet (AES-128-CBC + HMAC) with a key derived from `JWT_SECRET`. The
derivation is namespaced so the same secret can be reused for different
domains without key collision.

Caveat: if `JWT_SECRET` changes, all stored ciphertext becomes unreadable.
The user-facing README must call this out.
"""

from __future__ import annotations

import base64
from hashlib import sha256

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.exceptions import SlateError


class VPNCryptoError(SlateError):
    """Encryption/decryption failed (e.g. JWT_SECRET changed)."""


_KEY_NAMESPACE = b"slate-vpn-config-v1:"


def _fernet() -> Fernet:
    secret = get_settings().jwt_secret.encode("utf-8")
    digest = sha256(_KEY_NAMESPACE + secret).digest()  # 32 bytes
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(token: bytes) -> str:
    try:
        return _fernet().decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise VPNCryptoError(
            "Cannot decrypt VPN config — JWT_SECRET likely changed since storage."
        ) from exc
