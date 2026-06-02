"""Per-band radio configuration : channel, htmode, txpower, country.

The catalog SSID model (``wifi/models.py``) is layer-2 — what we
broadcast. This module is layer-1 — how the **radio** is configured :
which frequency, how wide the channel is, how much power we radiate,
and which regulatory domain we declare.

Storage : one row per (device_slug, band) in the ``radio_configs``
table. Defaults are the firmware factory values when a row is missing
so an unconfigured device behaves like the OEM out-of-box.

The handler ``radio.sh`` reads these settings from the apply payload
and writes them onto the matching ``wireless.MT7990_1_X`` wifi-device
section. Channel / htmode / country changes trigger a radio restart
(``wifi reload``) ; they do NOT need a full reboot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import RadioConfigRow
from app.wifi.models import WifiBand

# Allowed htmode values per band. The MTK driver supports more (EHT320)
# but we expose only the sane production set to avoid driver crashes
# on edge widths.
HtMode = Literal[
    "HT20", "HT40",
    "VHT20", "VHT40", "VHT80", "VHT160",
    "HE20", "HE40", "HE80", "HE160",
    "EHT20", "EHT40", "EHT80", "EHT160", "EHT320",
]

HTMODE_BY_BAND: dict[WifiBand, tuple[HtMode, ...]] = {
    "2": ("HT20", "HT40"),
    "5": ("HT20", "HT40", "VHT80", "VHT160", "HE80", "HE160", "EHT80", "EHT160"),
    "6": ("HE80", "HE160", "EHT80", "EHT160", "EHT320"),
}

DEFAULT_HTMODE: dict[WifiBand, HtMode] = {
    "2": "HT40",
    "5": "EHT160",
    "6": "EHT320",
}

DEFAULT_COUNTRY = "FR"
DEFAULT_TXPOWER_PERCENT = 100


class RadioConfig(BaseModel):
    """Per-band radio configuration, controller-side view."""

    band: WifiBand
    channel: int = Field(
        default=0,
        ge=0, le=233,
        description="0 = auto / ACS ; non-zero = forced channel number.",
    )
    htmode: str = Field(
        default="",
        description="EHT320 / EHT160 / HT40 / ... ; empty = use band default.",
    )
    txpower_percent: int = Field(default=100, ge=10, le=100)
    country: str = Field(
        default=DEFAULT_COUNTRY,
        min_length=2, max_length=2,
        description="ISO 3166 alpha-2 regulatory domain (FR / US / DE / JP / ...).",
    )
    updated_at: datetime | None = None

    @classmethod
    def default_for(cls, band: WifiBand) -> "RadioConfig":
        return cls(
            band=band,
            channel=0,
            htmode=DEFAULT_HTMODE[band],
            txpower_percent=DEFAULT_TXPOWER_PERCENT,
            country=DEFAULT_COUNTRY,
        )

    def to_uci_dict(self) -> dict[str, Any]:
        """Render as the kwargs the handler will set on the wifi-device
        section. `channel` 0 becomes ``'auto'`` (MTK's ACS sentinel)."""
        return {
            "channel": "auto" if self.channel == 0 else str(self.channel),
            "htmode": self.htmode or DEFAULT_HTMODE[self.band],
            "txpower": str(self.txpower_percent),
            "country": self.country,
        }


class RadioConfigStore:
    """CRUD over ``radio_configs`` keyed by (device_slug, band)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self, device_slug: str, band: WifiBand) -> RadioConfig:
        async with self._sf() as s:
            row = await s.scalar(
                select(RadioConfigRow).where(
                    RadioConfigRow.device_slug == device_slug,
                    RadioConfigRow.band == band,
                ),
            )
        if row is None:
            return RadioConfig.default_for(band)
        return RadioConfig(
            band=band,
            channel=row.channel,
            htmode=row.htmode,
            txpower_percent=row.txpower_percent,
            country=row.country,
            updated_at=row.updated_at,
        )

    async def get_all_for_device(self, device_slug: str) -> dict[WifiBand, RadioConfig]:
        """Return the 3 band configs, defaulting any missing."""
        out: dict[WifiBand, RadioConfig] = {}
        for band in ("2", "5", "6"):
            out[band] = await self.get(device_slug, band)  # type: ignore[index]
        return out

    async def upsert(
        self,
        device_slug: str,
        band: WifiBand,
        *,
        channel: int | None = None,
        htmode: str | None = None,
        txpower_percent: int | None = None,
        country: str | None = None,
    ) -> RadioConfig:
        """Partial update — fields left None keep their stored value."""
        async with self._sf() as s:
            row = await s.scalar(
                select(RadioConfigRow).where(
                    RadioConfigRow.device_slug == device_slug,
                    RadioConfigRow.band == band,
                ),
            )
            now = datetime.now(UTC)
            if row is None:
                row = RadioConfigRow(
                    device_slug=device_slug,
                    band=band,
                    channel=channel if channel is not None else 0,
                    htmode=htmode if htmode is not None else DEFAULT_HTMODE[band],
                    txpower_percent=(
                        txpower_percent if txpower_percent is not None
                        else DEFAULT_TXPOWER_PERCENT
                    ),
                    country=country if country is not None else DEFAULT_COUNTRY,
                    created_at=now,
                    updated_at=now,
                )
                s.add(row)
            else:
                if channel is not None:
                    row.channel = channel
                if htmode is not None:
                    row.htmode = htmode
                if txpower_percent is not None:
                    row.txpower_percent = txpower_percent
                if country is not None:
                    row.country = country
                row.updated_at = now
            await s.commit()
        return await self.get(device_slug, band)
