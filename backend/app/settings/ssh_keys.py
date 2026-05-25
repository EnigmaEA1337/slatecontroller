"""SSH keypair generation + persistence for device admin channels.

Generates Ed25519 keys (modern, short, fast). The private key is stored
Fernet-encrypted in the `device_secrets` table (keyed by `device_id` +
`kind='ssh_keypair'`); the public key + fingerprint + deployment marker
live in the same row's `metadata_json` blob.

Multi-device : every method takes a `device_slug` argument. The store
resolves it to a `DeviceRow.id` to upsert/read the matching
`device_secrets` row. There is no shared/global keypair anymore — each
adopted device carries its own.

Legacy migration : the alembic step
`f3a1c8d9e021_move_ssh_keypair_to_device_secrets` copies any pre-
existing `app_secrets[key='slate_ssh_keypair']` row onto the default
device, then drops the source row so we never read a stale one.
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

from app.db.models import DeviceRow, DeviceSecretRow
from app.exceptions import SlateError
from app.vpn.crypto import VPNCryptoError, decrypt, encrypt

# device_secrets.kind value. Kept in sync with `app/devices/store.py`.
SSH_KEYPAIR_SECRET_KIND = "ssh_keypair"


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
    """Per-device SSH keypair store.

    Every method takes a `device_slug` (the human-friendly identifier
    visible in the UI) and operates on the device's row in
    `device_secrets` where `kind = 'ssh_keypair'`.

    On miss (device exists, no keypair yet) `get_status` returns a
    `generated=False` snapshot ; `generate_and_store` creates the row ;
    `mark_deployed` requires the row to exist.

    If the device slug itself doesn't resolve, methods raise
    `SSHKeyError` rather than silently returning a fresh-state snapshot,
    so a typo in a route doesn't masquerade as "device has no key".
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def _load_secret(
        self, session: AsyncSession, device_slug: str,
    ) -> tuple[DeviceRow, DeviceSecretRow | None]:
        device = await session.scalar(
            select(DeviceRow).where(DeviceRow.slug == device_slug),
        )
        if device is None:
            raise SSHKeyError(f"unknown device slug {device_slug!r}")
        secret = await session.scalar(
            select(DeviceSecretRow).where(
                DeviceSecretRow.device_id == device.id,
                DeviceSecretRow.kind == SSH_KEYPAIR_SECRET_KIND,
            ),
        )
        return device, secret

    async def get_status(self, device_slug: str) -> SSHKeypairStatus:
        async with self._sf() as session:
            _device, secret = await self._load_secret(session, device_slug)
        if secret is None:
            return SSHKeypairStatus(
                generated=False,
                public_openssh=None,
                fingerprint_sha256=None,
                created_at=None,
                deployed_to_slate=False,
                deployed_at=None,
            )
        meta = secret.metadata_json or {}
        deployed_at_raw = meta.get("deployed_at")
        deployed_at = (
            datetime.fromisoformat(deployed_at_raw) if deployed_at_raw else None
        )
        return SSHKeypairStatus(
            generated=True,
            public_openssh=meta.get("public_openssh"),
            fingerprint_sha256=meta.get("fingerprint_sha256"),
            created_at=secret.created_at,
            deployed_to_slate=bool(meta.get("deployed_at")),
            deployed_at=deployed_at,
        )

    async def get_private_pem(self, device_slug: str) -> str | None:
        """Decrypt and return the stored private key, or None if not generated."""
        async with self._sf() as session:
            _device, secret = await self._load_secret(session, device_slug)
        if secret is None:
            return None
        try:
            return decrypt(secret.encrypted_value)
        except VPNCryptoError as exc:
            raise SSHKeyError(
                f"Cannot decrypt SSH private key for {device_slug!r}: {exc}"
            ) from exc

    async def generate_and_store(self, device_slug: str) -> SSHKeypair:
        """Replaces any existing keypair for this device. Caller is
        responsible for re-deploying the new public key to the Slate."""
        keypair = await asyncio.to_thread(_generate_keypair)
        encrypted = encrypt(keypair.private_pem)
        metadata: dict = {
            "public_openssh": keypair.public_openssh,
            "fingerprint_sha256": keypair.fingerprint_sha256,
            # Note: NOT setting deployed_at — generation alone doesn't deploy.
        }
        async with self._sf() as session:
            device, secret = await self._load_secret(session, device_slug)
            if secret is None:
                session.add(
                    DeviceSecretRow(
                        device_id=device.id,
                        kind=SSH_KEYPAIR_SECRET_KIND,
                        encrypted_value=encrypted,
                        metadata_json=metadata,
                    )
                )
            else:
                secret.encrypted_value = encrypted
                secret.metadata_json = metadata
            await session.commit()
        return keypair

    async def mark_deployed(self, device_slug: str) -> None:
        async with self._sf() as session:
            _device, secret = await self._load_secret(session, device_slug)
            if secret is None:
                raise SSHKeyError(
                    f"no keypair to mark deployed for {device_slug!r}",
                )
            meta = dict(secret.metadata_json or {})
            meta["deployed_at"] = datetime.now(UTC).isoformat()
            secret.metadata_json = meta
            await session.commit()

    async def revoke(self, device_slug: str) -> bool:
        """Delete the keypair from storage. Returns True if something was deleted."""
        async with self._sf() as session:
            _device, secret = await self._load_secret(session, device_slug)
            if secret is None:
                return False
            await session.delete(secret)
            await session.commit()
            return True
