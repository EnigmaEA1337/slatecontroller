"""DB-backed CRUD for Fortinet SSL VPN configs + encrypted secrets.

Mirrors the device-credentials pattern (`app/devices/store.py`) :

  - public row in ``fortinet_configs``
  - encrypted blob(s) in ``fortinet_secrets``, FK-cascaded so deleting the
    config removes the password too. ``kind`` is reserved for future
    secret types (client cert / key).

Password lookups always go through :meth:`get_password` which decrypts
on demand ; the encrypted bytes never leak to callers.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import FortinetConfigRow, FortinetSecretRow
from app.exceptions import SlateError
from app.vpn.crypto import VPNCryptoError, decrypt, encrypt


SECRET_PASSWORD = "password"


class FortinetStoreError(SlateError):
    pass


class FortinetNotFoundError(FortinetStoreError):
    pass


class FortinetDuplicateError(FortinetStoreError):
    pass


class FortinetConfigStore:
    """CRUD over ``fortinet_configs`` + ``fortinet_secrets``."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # ---------------------------- queries ---------------------------- #

    async def list_all(self) -> list[FortinetConfigRow]:
        async with self._sf() as session:
            rows = await session.scalars(
                select(FortinetConfigRow).order_by(FortinetConfigRow.slug),
            )
            return list(rows.all())

    async def get_by_slug(self, slug: str) -> FortinetConfigRow:
        async with self._sf() as session:
            row = await session.scalar(
                select(FortinetConfigRow).where(FortinetConfigRow.slug == slug),
            )
            if row is None:
                raise FortinetNotFoundError(slug)
            return row

    async def has_password(self, slug: str) -> bool:
        async with self._sf() as session:
            config = await session.scalar(
                select(FortinetConfigRow).where(FortinetConfigRow.slug == slug),
            )
            if config is None:
                return False
            secret = await session.scalar(
                select(FortinetSecretRow).where(
                    FortinetSecretRow.config_id == config.id,
                    FortinetSecretRow.kind == SECRET_PASSWORD,
                ),
            )
            return secret is not None

    async def get_password(self, slug: str) -> str:
        async with self._sf() as session:
            config = await session.scalar(
                select(FortinetConfigRow).where(FortinetConfigRow.slug == slug),
            )
            if config is None:
                raise FortinetNotFoundError(slug)
            secret = await session.scalar(
                select(FortinetSecretRow).where(
                    FortinetSecretRow.config_id == config.id,
                    FortinetSecretRow.kind == SECRET_PASSWORD,
                ),
            )
            if secret is None:
                raise FortinetStoreError(
                    f"config {slug!r} has no password stored",
                )
            try:
                return decrypt(secret.encrypted_value)
            except VPNCryptoError as exc:
                raise FortinetStoreError(
                    f"cannot decrypt password for {slug!r}: {exc}",
                ) from exc

    # ---------------------------- create / update ---------------------------- #

    async def create(
        self,
        *,
        slug: str,
        display_name: str,
        gateway_host: str,
        gateway_port: int,
        username: str,
        password: str,
        trusted_cert_sha256: str = "",
        ca_cert_pem: str = "",
        notes: str = "",
    ) -> FortinetConfigRow:
        # Empty password = "no stored secret" : the mobile login flow asks
        # for it fresh on every connect. Skip the secret row insert so
        # ``has_password()`` returns False and the manager refuses to
        # connect without an override.
        encrypted = encrypt(password) if password else None
        async with self._sf() as session:
            existing = await session.scalar(
                select(FortinetConfigRow).where(FortinetConfigRow.slug == slug),
            )
            if existing is not None:
                raise FortinetDuplicateError(slug)
            row = FortinetConfigRow(
                slug=slug,
                display_name=display_name,
                gateway_host=gateway_host,
                gateway_port=gateway_port,
                username=username,
                trusted_cert_sha256=trusted_cert_sha256,
                ca_cert_pem=ca_cert_pem,
                notes=notes,
                last_status="unknown",
            )
            session.add(row)
            await session.flush()
            if encrypted is not None:
                session.add(
                    FortinetSecretRow(
                        config_id=row.id,
                        kind=SECRET_PASSWORD,
                        encrypted_value=encrypted,
                    ),
                )
            await session.commit()
            await session.refresh(row)
            return row

    async def update_fields(self, slug: str, **fields: object) -> FortinetConfigRow:
        """Edit any subset of public fields. Skips keys whose value is None."""
        async with self._sf() as session:
            row = await session.scalar(
                select(FortinetConfigRow).where(FortinetConfigRow.slug == slug),
            )
            if row is None:
                raise FortinetNotFoundError(slug)
            for k, v in fields.items():
                if v is None:
                    continue
                setattr(row, k, v)
            await session.commit()
            await session.refresh(row)
            return row

    async def set_password(self, slug: str, password: str) -> None:
        encrypted = encrypt(password)
        async with self._sf() as session:
            config = await session.scalar(
                select(FortinetConfigRow).where(FortinetConfigRow.slug == slug),
            )
            if config is None:
                raise FortinetNotFoundError(slug)
            secret = await session.scalar(
                select(FortinetSecretRow).where(
                    FortinetSecretRow.config_id == config.id,
                    FortinetSecretRow.kind == SECRET_PASSWORD,
                ),
            )
            if secret is None:
                session.add(
                    FortinetSecretRow(
                        config_id=config.id,
                        kind=SECRET_PASSWORD,
                        encrypted_value=encrypted,
                    ),
                )
            else:
                secret.encrypted_value = encrypted
            await session.commit()

    async def delete(self, slug: str) -> bool:
        async with self._sf() as session:
            row = await session.scalar(
                select(FortinetConfigRow).where(FortinetConfigRow.slug == slug),
            )
            if row is None:
                return False
            await session.delete(row)  # CASCADE removes the secret too
            await session.commit()
            return True

    # ---------------------------- status mirror ---------------------------- #

    async def mark_status(
        self,
        slug: str,
        *,
        status: str,
        last_error: str = "",
        bump_connected: bool = False,
        bump_disconnected: bool = False,
    ) -> None:
        """Mirror the agent-reported tunnel state. The UI reads this via
        the listing endpoint without polling the Slate every render."""
        async with self._sf() as session:
            row = await session.scalar(
                select(FortinetConfigRow).where(FortinetConfigRow.slug == slug),
            )
            if row is None:
                raise FortinetNotFoundError(slug)
            row.last_status = status
            row.last_error = last_error
            now = datetime.now(UTC)
            if bump_connected:
                row.last_connected_at = now
            if bump_disconnected:
                row.last_disconnected_at = now
            await session.commit()
