"""DB-backed CRUD for devices + their encrypted secrets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import DeviceRow, DeviceSecretRow
from app.exceptions import SlateError
from app.vpn.crypto import VPNCryptoError, decrypt, encrypt


class DeviceStoreError(SlateError):
    pass


@dataclass(frozen=True)
class DeviceRecord:
    """Tuple of (row, decrypted rpc_password). Internal helper."""

    row: DeviceRow
    rpc_password: str


# Secret kinds — keep in sync with adoption code.
SECRET_RPC_PASSWORD = "rpc_password"
SECRET_SSH_KEYPAIR = "ssh_keypair"


class DeviceStore:
    """CRUD over `devices` + `device_secrets`. All writes are atomic."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # ---------------------------- queries ---------------------------- #

    async def list_all(self) -> list[DeviceRow]:
        async with self._sf() as session:
            result = await session.scalars(select(DeviceRow).order_by(DeviceRow.id))
            return list(result.all())

    async def get_by_slug(self, slug: str) -> DeviceRow | None:
        async with self._sf() as session:
            return await session.scalar(
                select(DeviceRow).where(DeviceRow.slug == slug),
            )

    async def get_default(self) -> DeviceRow | None:
        async with self._sf() as session:
            return await session.scalar(
                select(DeviceRow).where(DeviceRow.is_default.is_(True)),
            )

    # ---------------------------- create / update ---------------------------- #

    async def create(
        self,
        *,
        slug: str,
        label: str,
        model: str,
        host: str,
        rpc_port: int,
        rpc_scheme: str,
        ssh_port: int,
        rpc_username: str,
        rpc_password: str,
        notes: str,
        security_label: str = "",
        is_default: bool = False,
    ) -> DeviceRow:
        encrypted = encrypt(rpc_password)
        async with self._sf() as session:
            existing = await session.scalar(
                select(DeviceRow).where(DeviceRow.slug == slug),
            )
            if existing is not None:
                raise DeviceStoreError(f"device with slug={slug!r} already exists")
            # If this device is being marked default, unset others.
            if is_default:
                await self._clear_default_locked(session)
            row = DeviceRow(
                slug=slug,
                label=label,
                model=model,
                host=host,
                rpc_port=rpc_port,
                rpc_scheme=rpc_scheme,
                ssh_port=ssh_port,
                notes=notes,
                security_label=security_label,
                is_default=is_default,
                status="pending",
            )
            session.add(row)
            await session.flush()  # populate row.id
            session.add(
                DeviceSecretRow(
                    device_id=row.id,
                    kind=SECRET_RPC_PASSWORD,
                    encrypted_value=encrypted,
                    metadata_json={"username": rpc_username},
                ),
            )
            await session.commit()
            await session.refresh(row)
            return row

    async def update_fields(self, slug: str, **fields: object) -> DeviceRow:
        async with self._sf() as session:
            row = await session.scalar(
                select(DeviceRow).where(DeviceRow.slug == slug),
            )
            if row is None:
                raise DeviceStoreError(f"device {slug!r} not found")
            for k, v in fields.items():
                if v is None:
                    continue
                setattr(row, k, v)
            await session.commit()
            await session.refresh(row)
            return row

    async def update_credentials(
        self,
        slug: str,
        *,
        rpc_username: str | None = None,
        rpc_password: str | None = None,
    ) -> None:
        if rpc_username is None and rpc_password is None:
            return
        async with self._sf() as session:
            device = await session.scalar(
                select(DeviceRow).where(DeviceRow.slug == slug),
            )
            if device is None:
                raise DeviceStoreError(f"device {slug!r} not found")
            secret = await session.scalar(
                select(DeviceSecretRow).where(
                    DeviceSecretRow.device_id == device.id,
                    DeviceSecretRow.kind == SECRET_RPC_PASSWORD,
                ),
            )
            if secret is None:
                # Should not happen if create() ran — defensive.
                if rpc_password is None:
                    raise DeviceStoreError(
                        f"device {slug!r} has no RPC creds and no password provided",
                    )
                secret = DeviceSecretRow(
                    device_id=device.id,
                    kind=SECRET_RPC_PASSWORD,
                    encrypted_value=encrypt(rpc_password),
                    metadata_json={"username": rpc_username or ""},
                )
                session.add(secret)
            else:
                if rpc_password is not None:
                    secret.encrypted_value = encrypt(rpc_password)
                if rpc_username is not None:
                    meta = dict(secret.metadata_json or {})
                    meta["username"] = rpc_username
                    secret.metadata_json = meta
            await session.commit()

    async def set_default(self, slug: str) -> None:
        async with self._sf() as session:
            row = await session.scalar(
                select(DeviceRow).where(DeviceRow.slug == slug),
            )
            if row is None:
                raise DeviceStoreError(f"device {slug!r} not found")
            await self._clear_default_locked(session)
            row.is_default = True
            await session.commit()

    async def _clear_default_locked(self, session: AsyncSession) -> None:
        result = await session.scalars(
            select(DeviceRow).where(DeviceRow.is_default.is_(True)),
        )
        for row in result.all():
            row.is_default = False

    async def delete(self, slug: str) -> bool:
        async with self._sf() as session:
            row = await session.scalar(
                select(DeviceRow).where(DeviceRow.slug == slug),
            )
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def mark_probed(
        self,
        slug: str,
        *,
        status: str,
        tls_fingerprint_sha256: str | None = None,
    ) -> None:
        async with self._sf() as session:
            row = await session.scalar(
                select(DeviceRow).where(DeviceRow.slug == slug),
            )
            if row is None:
                raise DeviceStoreError(f"device {slug!r} not found")
            row.status = status
            row.last_probe_at = datetime.now(UTC)
            if tls_fingerprint_sha256 is not None:
                row.tls_fingerprint_sha256 = tls_fingerprint_sha256
            await session.commit()

    async def mark_adopted(self, slug: str) -> None:
        async with self._sf() as session:
            row = await session.scalar(
                select(DeviceRow).where(DeviceRow.slug == slug),
            )
            if row is None:
                raise DeviceStoreError(f"device {slug!r} not found")
            row.status = "adopted"
            row.adopted_at = datetime.now(UTC)
            await session.commit()

    async def mark_forgotten(self, slug: str) -> None:
        """Rollback a device to the "pending" state in the controller's DB.

        Does NOT touch the Slate itself — just resets `status` and clears
        `adopted_at` so the UI presents the adoption flow again. Used by the
        "Oublier" button when the operator wants to re-run hardening from
        scratch without factory-resetting the hardware. Host, RPC port,
        admin_urls, credentials, TLS fingerprint and SSH keypair are all
        preserved so the next adoption re-uses the same identity.
        """
        async with self._sf() as session:
            row = await session.scalar(
                select(DeviceRow).where(DeviceRow.slug == slug),
            )
            if row is None:
                raise DeviceStoreError(f"device {slug!r} not found")
            row.status = "pending"
            row.adopted_at = None
            await session.commit()

    # ---------------------------- secret access ---------------------------- #

    async def get_rpc_credentials(self, slug: str) -> tuple[str, str] | None:
        """Return (username, password) or None if missing."""
        async with self._sf() as session:
            device = await session.scalar(
                select(DeviceRow).where(DeviceRow.slug == slug),
            )
            if device is None:
                return None
            secret = await session.scalar(
                select(DeviceSecretRow).where(
                    DeviceSecretRow.device_id == device.id,
                    DeviceSecretRow.kind == SECRET_RPC_PASSWORD,
                ),
            )
            if secret is None:
                return None
            username = (secret.metadata_json or {}).get("username", "root")
            try:
                password = decrypt(secret.encrypted_value)
            except VPNCryptoError as exc:
                raise DeviceStoreError(
                    f"could not decrypt RPC password for {slug!r}: {exc}",
                ) from exc
            return username, password
