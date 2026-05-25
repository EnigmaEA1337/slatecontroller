"""Tests for the VPN private-key crypto layer."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.vpn.crypto import VPNCryptoError, decrypt, encrypt


def test_roundtrip_string() -> None:
    plaintext = "oFAEYO9j7gNQwAAr3pe9LH+UMyqoLBHRzkS+QCDuHWY="
    token = encrypt(plaintext)
    assert isinstance(token, bytes) and token != plaintext.encode()
    assert decrypt(token) == plaintext


def test_each_encrypt_produces_different_ciphertext() -> None:
    """Fernet uses a fresh IV every call → identical plaintext → different token."""
    plaintext = "same private key"
    assert encrypt(plaintext) != encrypt(plaintext)


def test_decrypt_rejects_tampered_ciphertext() -> None:
    token = bytearray(encrypt("secret"))
    token[-5] ^= 0x01  # flip a bit
    with pytest.raises(VPNCryptoError):
        decrypt(bytes(token))


def test_decrypt_with_wrong_secret_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Encrypt with current secret, swap, decrypt → should fail loudly."""
    token = encrypt("secret")

    # Wipe Settings cache and override JWT_SECRET.
    get_settings.cache_clear()
    monkeypatch.setenv("JWT_SECRET", "totally-different-secret")
    try:
        with pytest.raises(VPNCryptoError):
            decrypt(token)
    finally:
        get_settings.cache_clear()
