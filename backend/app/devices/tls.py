"""TLS pinning helper.

The Slate's JSON-RPC endpoint uses a self-signed cert. Rather than disable
TLS verification wholesale, we pin the leaf cert's SHA256 fingerprint at
adoption time and verify it on every subsequent connection.

This protects against MITM after the adoption handshake. If the user
re-flashes or re-keys the Slate, the fingerprint changes and the
controller will refuse to talk until the user re-adopts.
"""

from __future__ import annotations

import asyncio
import hashlib
import socket
import ssl
from dataclasses import dataclass


@dataclass(frozen=True)
class TLSCertInfo:
    fingerprint_sha256: str  # uppercase hex, colon-separated ("AB:CD:…")
    subject: str  # CN= …
    issuer: str
    not_before: str  # ISO-ish
    not_after: str


def _format_fingerprint(raw: bytes) -> str:
    """Format a digest as `AB:CD:EF:…` like openssl / browsers do."""
    hex_str = raw.hex().upper()
    return ":".join(hex_str[i : i + 2] for i in range(0, len(hex_str), 2))


def _fetch_cert_sync(host: str, port: int, timeout: float) -> TLSCertInfo:
    """Synchronous cert fetch via stdlib ssl — caller wraps in to_thread."""
    ctx = ssl._create_unverified_context()
    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_sock.settimeout(timeout)
    der: bytes = b""
    with ctx.wrap_socket(raw_sock, server_hostname=host) as sock:
        sock.connect((host, port))
        der = sock.getpeercert(binary_form=True) or b""

    if not der:
        raise RuntimeError(f"empty cert from {host}:{port}")

    digest = hashlib.sha256(der).digest()

    # Parse a bit of metadata for display. We use the higher-level ssl
    # functions that operate on the PEM rather than digging into ASN.1.
    pem = ssl.DER_cert_to_PEM_cert(der)
    cert_info = _parse_pem_metadata(pem)

    return TLSCertInfo(
        fingerprint_sha256=_format_fingerprint(digest),
        subject=cert_info.get("subject", ""),
        issuer=cert_info.get("issuer", ""),
        not_before=cert_info.get("notBefore", ""),
        not_after=cert_info.get("notAfter", ""),
    )


def _parse_pem_metadata(pem: str) -> dict[str, str]:
    """Best-effort metadata extraction from a PEM cert without external deps."""
    out: dict[str, str] = {}
    try:
        # Use cryptography if available — it's in the dep tree already (Fernet).
        from cryptography import x509
        from cryptography.hazmat.primitives.serialization import Encoding

        cert = x509.load_pem_x509_certificate(pem.encode("ascii"))
        # Round-trip to assert it parses; values come from the .subject etc.
        _ = cert.public_bytes(Encoding.PEM)
        out["subject"] = cert.subject.rfc4514_string()
        out["issuer"] = cert.issuer.rfc4514_string()
        out["notBefore"] = cert.not_valid_before_utc.isoformat()
        out["notAfter"] = cert.not_valid_after_utc.isoformat()
    except Exception:  # noqa: BLE001 — best-effort metadata only
        pass
    return out


async def fetch_cert(host: str, port: int = 443, timeout: float = 5.0) -> TLSCertInfo:  # noqa: ASYNC109
    """Connect to host:port and return the peer cert info (no verification).

    The `timeout` is the SSL socket timeout (passed through to the sync helper),
    not an asyncio cancellation timeout — so the linter's ASYNC109 doesn't apply.
    """
    return await asyncio.to_thread(_fetch_cert_sync, host, port, timeout)


async def verify_fingerprint(
    host: str,
    port: int,
    expected_fingerprint: str,
    *,
    timeout: float = 5.0,  # noqa: ASYNC109
) -> bool:
    """Return True iff the leaf cert's SHA256 matches `expected_fingerprint`."""
    info = await fetch_cert(host, port, timeout)
    return _normalize_fp(info.fingerprint_sha256) == _normalize_fp(expected_fingerprint)


def _normalize_fp(fp: str) -> str:
    return fp.upper().replace(":", "").strip()
