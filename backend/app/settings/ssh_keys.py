"""SSH keypair — the CONTROLLER's identity, controller-wide.

The keypair represents the controller's SSH client identity, exactly the
same as `~/.ssh/id_ed25519` on a human's machine. It is *not* per-device :
one identity authenticates to every adopted Slate, with the matching
public key sitting in each device's `/etc/dropbear/authorized_keys`.

Storage : Fernet-encrypted private key + metadata in
`app_secrets[key='controller_ssh_keypair']`. The metadata blob carries
the public OpenSSH line, fingerprint, creation date, and a per-device
deployment ledger (`deployed_to: {slug: timestamp}`) so we know which
adopted devices already have the public side and which still need it
pushed.

**This survives device deletion / factory reset / re-adoption.** A
Slate that gets factory-reset just needs the same public key re-pushed
into its authorized_keys — the controller's private side never moves.

Migration history :
  - originally lived in `app_secrets[key='slate_ssh_keypair']`
  - 2026-05-25 moved INCORRECTLY to `device_secrets[kind='ssh_keypair']`
    on the assumption that multi-device meant per-device key — but it
    just means many servers know the same public key
  - 2026-06-02 moved back to `app_secrets[key='controller_ssh_keypair']`,
    this time with explicit semantics (Bug I)

The `device_slug` parameter on most methods is kept for API surface
backwards compatibility but is effectively ignored : there is only one
keypair, regardless of which device the caller is asking about. Routes
that probe a specific device's deployment status read the per-device
flag from the metadata ledger.
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

# Single app_secrets row that holds the controller's SSH client identity.
APP_SECRET_KEY = "controller_ssh_keypair"


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
    deployed_to_slate: bool   # True iff this specific device slug is in
                              # the deployment ledger
    deployed_at: datetime | None  # the timestamp for *that* slug


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

    raw_pub_blob = base64.b64decode(public_openssh.split()[1])
    digest = hashlib.sha256(raw_pub_blob).digest()
    fingerprint = "SHA256:" + base64.b64encode(digest).decode().rstrip("=")

    return SSHKeypair(
        public_openssh=public_openssh,
        private_pem=private_pem,
        fingerprint_sha256=fingerprint,
    )


class SSHKeypairStore:
    """Controller-wide SSH keypair store.

    All read/write hit a single `app_secrets[key='controller_ssh_keypair']`
    row. Per-device deployment status is tracked inside the metadata's
    `deployed_to` dict.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def _load(
        self, session: AsyncSession,
    ) -> AppSecretRow | None:
        return await session.scalar(
            select(AppSecretRow).where(AppSecretRow.key == APP_SECRET_KEY),
        )

    def _status_from_row(
        self, row: AppSecretRow | None, device_slug: str,
    ) -> SSHKeypairStatus:
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
        deployed_to: dict = meta.get("deployed_to") or {}
        deployed_at_raw = deployed_to.get(device_slug)
        deployed_at = (
            datetime.fromisoformat(deployed_at_raw) if deployed_at_raw else None
        )
        return SSHKeypairStatus(
            generated=True,
            public_openssh=meta.get("public_openssh"),
            fingerprint_sha256=meta.get("fingerprint_sha256"),
            created_at=row.created_at,
            deployed_to_slate=deployed_at is not None,
            deployed_at=deployed_at,
        )

    async def get_status(self, device_slug: str) -> SSHKeypairStatus:
        """Return the status from THIS device's perspective.

        The `generated` / `public_openssh` / `fingerprint_sha256` fields
        are global (same regardless of slug). The `deployed_to_slate`
        and `deployed_at` fields reflect whether the public side has
        been pushed to the named device's authorized_keys.
        """
        async with self._sf() as session:
            row = await self._load(session)
        return self._status_from_row(row, device_slug)

    async def get_private_pem(self, device_slug: str = "") -> str | None:  # noqa: ARG002
        """Decrypt and return the stored private key, or None if not generated.

        `device_slug` is ignored — there is only one keypair.
        """
        async with self._sf() as session:
            row = await self._load(session)
        if row is None:
            return None
        try:
            return decrypt(row.encrypted_value)
        except VPNCryptoError as exc:
            raise SSHKeyError(
                f"Cannot decrypt controller SSH private key: {exc}",
            ) from exc

    async def generate_and_store(self, device_slug: str = "") -> SSHKeypair:  # noqa: ARG002
        """Generate a fresh keypair and replace any existing one.

        Wipes the per-device deployment ledger : a brand-new key has not
        been deployed to anyone yet, regardless of past state. The caller
        is responsible for re-deploying to every adopted device that
        wants to keep working.

        `device_slug` is ignored — the keypair is global. The parameter
        is kept for backwards-compatible route signatures.
        """
        keypair = await asyncio.to_thread(_generate_keypair)
        encrypted = encrypt(keypair.private_pem)
        metadata: dict = {
            "public_openssh": keypair.public_openssh,
            "fingerprint_sha256": keypair.fingerprint_sha256,
            "deployed_to": {},  # slug → ISO timestamp
        }
        async with self._sf() as session:
            row = await self._load(session)
            if row is None:
                session.add(
                    AppSecretRow(
                        key=APP_SECRET_KEY,
                        encrypted_value=encrypted,
                        metadata_json=metadata,
                    ),
                )
            else:
                row.encrypted_value = encrypted
                row.metadata_json = metadata
                row.updated_at = datetime.now(UTC)
            await session.commit()
        return keypair

    async def mark_deployed(self, device_slug: str) -> None:
        """Record that the public key was successfully pushed to <device_slug>.

        Per-device : only updates this slug's entry in the deployment
        ledger. Other devices' entries stay as-is.
        """
        async with self._sf() as session:
            row = await self._load(session)
            if row is None:
                raise SSHKeyError(
                    "no controller keypair to mark deployed",
                )
            meta = dict(row.metadata_json or {})
            deployed_to: dict = dict(meta.get("deployed_to") or {})
            deployed_to[device_slug] = datetime.now(UTC).isoformat()
            meta["deployed_to"] = deployed_to
            row.metadata_json = meta
            row.updated_at = datetime.now(UTC)
            await session.commit()

    async def revoke(self, device_slug: str = "") -> bool:  # noqa: ARG002
        """Delete the global keypair from storage. Returns True if something
        was deleted.

        ⚠ Caller-beware : every adopted device that authenticated via this
        key will need a fresh keypair pushed to its authorized_keys.

        `device_slug` is ignored — revocation is global. The parameter is
        kept for backwards-compatible route signatures. If a route wants
        to only forget that a specific device was deployed (without
        nuking the keypair), it should call `forget_device_deployment`.
        """
        async with self._sf() as session:
            row = await self._load(session)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def forget_device_deployment(self, device_slug: str) -> None:
        """Remove `device_slug` from the deployment ledger without touching
        the keypair itself. Call this when deleting a device — its row in
        `deployed_to` is now meaningless, but the controller's identity
        stays intact for every other adopted Slate."""
        async with self._sf() as session:
            row = await self._load(session)
            if row is None:
                return
            meta = dict(row.metadata_json or {})
            deployed_to: dict = dict(meta.get("deployed_to") or {})
            if device_slug in deployed_to:
                deployed_to.pop(device_slug)
                meta["deployed_to"] = deployed_to
                row.metadata_json = meta
                row.updated_at = datetime.now(UTC)
                await session.commit()
