"""OUI (Organizationally Unique Identifier) lookup → manufacturer name.

The first three bytes of a 48-bit MAC address identify the OUI. IEEE
maintains the authoritative public registry at
https://standards-oui.ieee.org/oui/oui.csv (~4 MB, ~35 000 entries).

This module :
  - Maintains a local on-disk CSV cache of the IEEE OUI registry so we
    don't hit the network on every scan.
  - Refreshes the cache periodically (default: weekly) when it's stale.
  - Exposes ``lookup(bssid)`` → ``OuiInfo`` with the resolved vendor
    name + a flag telling whether the MAC is randomised (locally
    administered, U/L bit set).
  - Provides a curated short-name → display-name mapping so the UI
    can match a vendor against a logo without doing the heavy lifting.

The lookup is process-local. No DB round-trip in the hot path.
"""

from __future__ import annotations

import csv
import io
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import structlog

# Note: use structlog (not stdlib `logging`) so we can pass arbitrary
# keyword arguments to `logger.info(...)`. Stdlib Logger._log() rejects
# anything beyond `extra=...`, and a stray `count=N` raises TypeError
# from inside a background OUI refresh — which silently breaks the
# downstream consumer (we saw this kill 6 GHz scans live).
logger = structlog.get_logger(__name__)

# Where we keep the local CSV cache. Lives in the controller's data
# directory so it survives container restarts but is volatile-by-design
# (deletable, regenerable).
CACHE_PATH = Path("/app/data/oui_cache.csv")

# IEEE official feed (no auth required). Mirror URLs that also work :
#   https://www.wireshark.org/download/automated/data/manuf  (different format)
#   https://gitlab.com/wireshark/wireshark/-/raw/master/manuf
IEEE_OUI_URL = "https://standards-oui.ieee.org/oui/oui.csv"
# Wireshark mirror — actively maintained, fewer access restrictions
# than the IEEE feed (which 418s default httpx user-agents). Used as a
# fallback when the IEEE URL refuses our request.
WIRESHARK_MANUF_URL = "https://www.wireshark.org/download/automated/data/manuf"

# Refresh interval : weekly is fine — the IEEE registry changes slowly.
REFRESH_INTERVAL_S = 7 * 24 * 3600


@dataclass(frozen=True)
class OuiInfo:
    """One MAC address's vendor lookup result."""

    bssid: str            # normalised lowercase with colons
    oui: str              # uppercase, no separators (e.g. "9483C4")
    vendor: str           # "Apple, Inc." or "" if unknown
    vendor_slug: str      # "apple" / "ubiquiti" / "cisco" / ""
    is_randomized: bool   # locally administered MAC (U/L bit = 1)


# Curated slug map. Names are normalised (lowercase, no punctuation) and
# matched against a substring of the vendor field for resilience to
# IEEE renames ("Cisco Meraki" should still slug to "cisco").
_VENDOR_SLUGS: tuple[tuple[str, str], ...] = (
    ("apple", "apple"),
    ("ubiquiti", "ubiquiti"),
    ("cisco", "cisco"),
    ("aruba", "aruba"),
    ("hewlett packard", "hp"),
    ("hewlett-packard", "hp"),
    ("aruba networks", "aruba"),
    ("intel", "intel"),
    ("samsung", "samsung"),
    ("huawei", "huawei"),
    ("xiaomi", "xiaomi"),
    ("oneplus", "oneplus"),
    ("oppo", "oppo"),
    ("google", "google"),
    ("microsoft", "microsoft"),
    ("mediatek", "mediatek"),
    ("qualcomm", "qualcomm"),
    ("broadcom", "broadcom"),
    ("realtek", "realtek"),
    ("netgear", "netgear"),
    ("tp-link", "tplink"),
    ("tplink", "tplink"),
    ("asus", "asus"),
    ("d-link", "dlink"),
    ("dlink", "dlink"),
    ("mikrotik", "mikrotik"),
    ("ruckus", "ruckus"),
    ("zte", "zte"),
    ("nokia", "nokia"),
    ("amazon", "amazon"),
    ("raspberry pi", "raspberrypi"),
    ("sonos", "sonos"),
    ("nintendo", "nintendo"),
    ("sony", "sony"),
    ("lg electronics", "lg"),
    ("philips", "philips"),
    ("freebox", "freebox"),
    ("free sas", "freebox"),
    ("bouygues", "bouygues"),
    ("orange", "orange"),
    ("livebox", "orange"),
    ("sfr", "sfr"),
    ("gl technologies", "glinet"),
    ("gl.inet", "glinet"),
    ("mediatek inc.", "mediatek"),
    ("espressif", "espressif"),
)


def _vendor_slug(vendor: str) -> str:
    if not vendor:
        return ""
    lower = vendor.lower()
    for needle, slug in _VENDOR_SLUGS:
        if needle in lower:
            return slug
    return ""


class OuiRegistry:
    """In-memory lookup table loaded from the local CSV cache."""

    def __init__(self) -> None:
        self._table: dict[str, str] = {}
        self._lock = threading.RLock()
        self._loaded_at: float = 0.0

    @property
    def size(self) -> int:
        return len(self._table)

    @property
    def loaded_at(self) -> float:
        return self._loaded_at

    def is_loaded(self) -> bool:
        return bool(self._table)

    def is_stale(self) -> bool:
        return time.time() - self._loaded_at > REFRESH_INTERVAL_S

    def load_from_cache(self) -> None:
        """Read CACHE_PATH into memory. Silent fallback to empty on miss."""
        with self._lock:
            if not CACHE_PATH.exists():
                return
            try:
                with CACHE_PATH.open("r", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    self._table = {row[0]: row[1] for row in reader if len(row) >= 2}
                self._loaded_at = CACHE_PATH.stat().st_mtime
                logger.info(
                    "wifi.oui.loaded", count=len(self._table),
                    cache_path=str(CACHE_PATH),
                )
            except (OSError, csv.Error) as exc:
                logger.warning("wifi.oui.cache_read_failed", error=str(exc))
                self._table = {}

    async def refresh_if_stale(self) -> None:
        """Re-download the OUI feed if cache is missing or older
        than ``REFRESH_INTERVAL_S``.

        Tries IEEE first (authoritative) ; falls back to Wireshark's
        ``manuf`` file when IEEE refuses our request — which is the
        common case for a default httpx User-Agent (HTTP 418 from their
        WAF). Either source produces the same vendor strings, just in
        different formats."""
        if self.is_loaded() and not self.is_stale():
            return
        headers = {
            # IEEE's WAF rejects empty / default UAs — set a realistic one.
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept": "text/csv, text/plain, */*",
        }
        parsed: dict[str, str] = {}
        # Attempt 1 : IEEE official CSV.
        try:
            async with httpx.AsyncClient(timeout=60, headers=headers) as client:
                logger.info("wifi.oui.fetching", url=IEEE_OUI_URL)
                resp = await client.get(IEEE_OUI_URL)
                if resp.status_code == 200:
                    parsed = _parse_ieee_csv(resp.text)
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("wifi.oui.ieee_fetch_failed", error=str(exc))
        # Attempt 2 : Wireshark manuf mirror.
        if not parsed:
            try:
                async with httpx.AsyncClient(timeout=60, headers=headers) as client:
                    logger.info("wifi.oui.fetching", url=WIRESHARK_MANUF_URL)
                    resp = await client.get(WIRESHARK_MANUF_URL)
                    resp.raise_for_status()
                    parsed = _parse_wireshark_manuf(resp.text)
            except (httpx.HTTPError, OSError) as exc:
                logger.warning("wifi.oui.wireshark_fetch_failed", error=str(exc))
        if not parsed:
            logger.warning("wifi.oui.all_sources_failed")
            return
        # Persist + load.
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with CACHE_PATH.open("w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                for oui, name in parsed.items():
                    writer.writerow([oui, name])
        except OSError as exc:
            logger.warning("wifi.oui.cache_write_failed", error=str(exc))
        with self._lock:
            self._table = parsed
            self._loaded_at = time.time()
        logger.info("wifi.oui.refreshed", count=len(parsed))

    def lookup(self, bssid: str) -> OuiInfo:
        """Resolve one BSSID. Always returns an OuiInfo — vendor is ""
        when the OUI isn't in the table."""
        normalised = bssid.lower().strip()
        oui_key = normalised.replace(":", "").replace("-", "")[:6].upper()
        vendor = self._table.get(oui_key, "")
        # Locally administered bit = bit 1 of the first byte.
        is_rand = False
        try:
            first_byte = int(oui_key[:2], 16)
            is_rand = bool(first_byte & 0b10)
        except ValueError:
            pass
        return OuiInfo(
            bssid=normalised,
            oui=oui_key,
            vendor=vendor,
            vendor_slug=_vendor_slug(vendor),
            is_randomized=is_rand,
        )


def _parse_ieee_csv(raw: str) -> dict[str, str]:
    """Parse the IEEE OUI CSV. Columns are :
        Registry, Assignment, Organization Name, Organization Address
    Where Assignment is the OUI in the form ``9483C4``."""
    out: dict[str, str] = {}
    reader = csv.reader(io.StringIO(raw))
    for i, row in enumerate(reader):
        if i == 0:  # header
            continue
        if len(row) < 3:
            continue
        oui = row[1].strip().upper().replace("-", "").replace(":", "")
        name = row[2].strip()
        if len(oui) == 6 and name:
            out[oui] = name
    return out


def _parse_wireshark_manuf(raw: str) -> dict[str, str]:
    """Parse the Wireshark ``manuf`` file. Each non-comment line is :
        ``00:00:00 short_name # OPTIONAL long name``
    or
        ``00:00:00 short_name long name``
    The long name (after ``#`` or the second tab) is preferred when
    present, falling back to the short name."""
    out: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip inline comments using `#` as a separator for the friendly
        # long name : "00:00:00\tShort\t# Long name"
        head, sep, tail = line.partition("#")
        long_name = tail.strip() if sep else ""
        parts = head.split()
        if not parts:
            continue
        oui_field = parts[0]
        # Wireshark allows masked OUIs like "00:00:00/24" — strip the
        # /XX prefix bits (we only resolve the 24-bit OUI portion here).
        oui_field = oui_field.split("/")[0]
        oui = oui_field.upper().replace("-", "").replace(":", "")[:6]
        if len(oui) != 6:
            continue
        # Prefer the long name when present ; else assemble from the
        # remaining tokens (short name + any extras).
        if long_name:
            name = long_name
        elif len(parts) >= 3:
            name = " ".join(parts[2:])
        elif len(parts) == 2:
            name = parts[1]
        else:
            continue
        if name:
            out[oui] = name
    return out


# ---- module-level singleton ---- #

_REGISTRY: OuiRegistry | None = None


def get_registry() -> OuiRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = OuiRegistry()
        _REGISTRY.load_from_cache()
    return _REGISTRY


def lookup(bssid: str) -> OuiInfo:
    """Convenience wrapper around the module singleton."""
    return get_registry().lookup(bssid)


async def refresh_async() -> None:
    """Trigger an async refresh of the OUI registry. Safe to call from
    background tasks ; cheap when the cache is fresh."""
    await get_registry().refresh_if_stale()
