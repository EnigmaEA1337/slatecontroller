"""SSH keypair generation + persistence for the Slate admin channel.

Generates Ed25519 keys (modern, short, fast). The private key is stored
Fernet-encrypted in the `app_secrets` table; the public key is plain text
(it's pasted into the Slate's `authorized_keys`).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AppSecretRow
from app.exceptions import SlateError
from app.vpn.crypto import VPNCryptoError, decrypt, encrypt

SSH_KEY_SECRET_KEY = "slate_ssh_keypair"


class SSHKeyError(SlateError):
    pass


@dataclass(frozen=True)
class SSHKeypair:
    public_openssh: str  # "ssh-ed25519 AAAA... comment"
    private_pem: str  # OpenSSH-format private key
    fingerprint_sha256: str  # "SHA256:abc…" (matches `ssh-keygen -l`)


@dataclass(frozen=True)
class SSHKeypairStatus:
    """Public-safe view: never includes private material."""

    generated: bool
    public_openssh: str | None
    fingerprint_sha256: str | None
    created_at: datetime | None
    deployed_to_slate: bool
    deployed_at: datetime | None


def _generate_keypair(comment: str = "slate-controller") -> SSHKeypair:
    """CPU-bound: caller should await asyncio.to_thread(_generate_keypair, ...)."""
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()

    private_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    public_openssh = pub.public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("ascii") + f" {comment}"

    # Fingerprint per OpenSSH convention: sha256 over the raw public key blob
    # (the base64 chunk in the middle of the OpenSSH text format).
    raw_pub_blob = base64.b64decode(public_openssh.split()[1])
    digest = hashlib.sha256(raw_pub_blob).digest()
    fingerprint = "SHA256:" + base64.b64encode(digest).decode().rstrip("=")

    return SSHKeypair(
        public_openssh=public_openssh,
        private_pem=private_pem,
        fingerprint_sha256=fingerprint,
    )


class SSHKeypairStore:
    """Async store for the (single) SSH keypair used by the backend → Slate channel."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def _load_row(self) -> AppSecretRow | None:
        async with self._sf() as session:
            return await session.scalar(
                select(AppSecretRow).where(AppSecretRow.key == SSH_KEY_SECRET_KEY)
            )

    async def get_status(self) -> SSHKeypairStatus:
        row = await self._load_row()
        if row is None:
            return SSHKeypairStatus(
                generated=False,
                public_openssh=None,
                fingerprint_sha256=None,
                created_at=None,
                deployed_to_slate=False,
                deployed_at=None,
            )
        meta = row.metadata_json or {}
        deployed_at_raw = meta.get("deployed_at")
        deployed_at = (
            datetime.fromisoformat(deployed_at_raw) if deployed_at_raw else None
        )
        return SSHKeypairStatus(
            generated=True,
            public_openssh=meta.get("public_openssh"),
            fingerprint_sha256=meta.get("fingerprint_sha256"),
            created_at=row.created_at,
            deployed_to_slate=bool(meta.get("deployed_at")),
            deployed_at=deployed_at,
        )

    async def get_private_pem(self) -> str | None:
        """Decrypt and return the stored private key, or None if not generated."""
        row = await self._load_row()
        if row is None:
            return None
        try:
            return decrypt(row.encrypted_value)
        except VPNCryptoError as exc:
            raise SSHKeyError(
                f"Cannot decrypt stored SSH private key: {exc}"
            ) from exc

    async def generate_and_store(self) -> SSHKeypair:
        """Replaces any existing keypair. Caller's responsibility to re-deploy."""
        keypair = await asyncio.to_thread(_generate_keypair)
        encrypted = encrypt(keypair.private_pem)
        metadata: dict = {
            "public_openssh": keypair.public_openssh,
            "fingerprint_sha256": keypair.fingerprint_sha256,
            # Note: NOT setting deployed_at — generation alone doesn't deploy.
        }
        async with self._sf() as session:
            existing = await session.scalar(
                select(AppSecretRow).where(AppSecretRow.key == SSH_KEY_SECRET_KEY)
            )
            if existing is None:
                session.add(
                    AppSecretRow(
                        key=SSH_KEY_SECRET_KEY,
                        encrypted_value=encrypted,
                        metadata_json=metadata,
                    )
                )
            else:
                existing.encrypted_value = encrypted
                existing.metadata_json = metadata
            await session.commit()
        return keypair

    async def mark_deployed(self) -> None:
        async with self._sf() as session:
            row = await session.scalar(
                select(AppSecretRow).where(AppSecretRow.key == SSH_KEY_SECRET_KEY)
            )
            if row is None:
                raise SSHKeyError("no keypair to mark as deployed")
            meta = dict(row.metadata_json or {})
            meta["deployed_at"] = datetime.now(UTC).isoformat()
            row.metadata_json = meta
            await session.commit()

    async def revoke(self) -> bool:
        """Delete the keypair from storage. Returns True if something was deleted."""
        async with self._sf() as session:
            row = await session.scalar(
                select(AppSecretRow).where(AppSecretRow.key == SSH_KEY_SECRET_KEY)
            )
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True
