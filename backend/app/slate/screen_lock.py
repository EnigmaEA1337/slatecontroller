"""Slate touchscreen PIN lock — read/write + strength evaluation.

The Slate 7 Pro ships with a 2.4" touchscreen that can be locked with a
numeric PIN. The config lives in OpenWrt UCI under `gl_screen.generic.*`:

  PASSCODE         numeric string, in single-quoted UCI form
  ENABLE_PASSCODE  '1' = lock active, '0' = unlocked permanently
  AUTO_LOCK_TIME   seconds before auto-lock kicks in (typically 60..600)

This module wraps those reads/writes behind a tiny SSH-backed manager. The
PIN is treated as write-only at the API boundary — the controller reads it
internally (to compute strength + status), but never returns it.

Hardening interaction: `app.slate.hardening._ssh_check_screen_lock`
consumes `get_status()` and penalises weak PINs, missing locks, or
auto-lock windows so long they're effectively pointless.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from typing import Literal

import structlog

from app.exceptions import SlateError
from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)

PinStrength = Literal["none", "weak", "medium", "strong"]


# Common trivial PINs people pick when forced to set one. Order doesn't
# matter — set membership only. Add to this list when you spot another
# obvious bad PIN in the wild.
_WEAK_PINS: frozenset[str] = frozenset(
    {
        # 4-digit ascending / repeating
        "0000", "1111", "2222", "3333", "4444", "5555", "6666", "7777", "8888", "9999",
        "1234", "4321", "1212", "2121",
        # Famous bad PINs (HSBC studies + iPhone leak datasets)
        "1004", "1122", "2580", "5683", "0852", "1010", "0101",
        # Years that show up everywhere
        "1980", "1990", "2000", "2001", "2010", "2020", "2024", "2025", "2026",
        # 6-digit common
        "000000", "111111", "123456", "654321", "121212",
    }
)


class ScreenLockError(SlateError):
    """SSH / UCI failure setting screen lock options."""


@dataclass(frozen=True)
class ScreenLockStatus:
    """Public view of the screen lock state.

    `pin_strength` is computed server-side from the raw PIN read on the
    Slate; the PIN itself is *never* returned over the API.
    """

    enabled: bool
    has_pin: bool
    pin_length: int  # 0 if no PIN set
    pin_strength: PinStrength
    auto_lock_seconds: int  # 0 = never


def evaluate_pin_strength(pin: str) -> PinStrength:
    """Classify a numeric PIN into none/weak/medium/strong.

    Rules (simple but effective):
      - empty            → none
      - in _WEAK_PINS    → weak
      - <4 digits        → weak (too short to brute-force-resist)
      - 4 digits, not in weak list → medium (real-world default)
      - 5 digits         → medium
      - 6+ digits, not in weak list → strong
    """
    if not pin:
        return "none"
    if pin in _WEAK_PINS:
        return "weak"
    n = len(pin)
    if n < 4:
        return "weak"
    if n >= 6:
        return "strong"
    return "medium"


def _parse_pin_from_uci(raw: str | None) -> str:
    """The UCI option PASSCODE is stored with its OWN quotes inside the value
    (e.g. `PASSCODE='"9263"'`). Strip both layers — UCI's outer quotes and
    the literal inner double-quotes."""
    if raw is None:
        return ""
    s = raw.strip().rstrip("\n")
    # UCI's outer single quotes
    if s.startswith("'") and s.endswith("'"):
        s = s[1:-1]
    # GL.iNet's literal inner double quotes
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    return s


async def get_status(ssh: SlateSSH) -> ScreenLockStatus:
    """Read every screen-lock UCI option in one round-trip."""
    try:
        r = await ssh.run(
            "uci get gl_screen.generic.ENABLE_PASSCODE 2>/dev/null; echo '---';"
            "uci get gl_screen.generic.PASSCODE 2>/dev/null; echo '---';"
            "uci get gl_screen.generic.AUTO_LOCK_TIME 2>/dev/null; echo '---'",
            timeout=8,
        )
    except SlateSSHError as exc:
        raise ScreenLockError(f"SSH read failed: {exc}") from exc

    parts = r.stdout.split("---")
    enabled_raw = parts[0].strip() if len(parts) > 0 else ""
    pin_raw = parts[1] if len(parts) > 1 else ""
    auto_lock_raw = parts[2].strip() if len(parts) > 2 else ""

    pin = _parse_pin_from_uci(pin_raw)
    auto_lock = 0
    try:
        auto_lock = int(auto_lock_raw)
    except (ValueError, TypeError):
        auto_lock = 0

    return ScreenLockStatus(
        enabled=enabled_raw == "1",
        has_pin=bool(pin),
        pin_length=len(pin),
        pin_strength=evaluate_pin_strength(pin),
        auto_lock_seconds=auto_lock,
    )


# Validation patterns for write operations — fail loudly rather than push
# garbage to UCI.
_PIN_RE = re.compile(r"^\d{4,8}$")


async def set_pin(ssh: SlateSSH, pin: str) -> ScreenLockStatus:
    """Set the screen PIN. Must be 4-8 digits, numeric only.

    GL.iNet stores it as PASSCODE='"<digits>"' with literal inner double
    quotes — we replicate that exactly so the on-device UI parses it.
    """
    if not _PIN_RE.match(pin):
        raise ScreenLockError("PIN must be 4 to 8 digits")
    # Build the UCI value with literal inner double-quotes, escape for shell.
    # uci set already handles single-quote outer layer; we just need the
    # inner `"<digits>"` part.
    value = f'"{pin}"'
    cmd = (
        f"uci set gl_screen.generic.PASSCODE='{value}' && "
        f"uci set gl_screen.generic.ENABLE_PASSCODE='1' && "
        f"uci commit gl_screen && "
        f"/etc/init.d/gl_screen reload >/dev/null 2>&1 ; "
        f"echo OK"
    )
    try:
        r = await ssh.run(cmd, timeout=10)
    except SlateSSHError as exc:
        raise ScreenLockError(f"SSH set_pin failed: {exc}") from exc
    if "OK" not in r.stdout:
        raise ScreenLockError(
            f"set_pin did not return OK (stderr={r.stderr.strip()!r})",
        )
    logger.info("screen_lock.pin_set", length=len(pin))
    return await get_status(ssh)


async def set_enabled(ssh: SlateSSH, enabled: bool) -> ScreenLockStatus:
    """Toggle the lock screen on/off. Doesn't touch the stored PIN."""
    flag = "1" if enabled else "0"
    cmd = (
        f"uci set gl_screen.generic.ENABLE_PASSCODE='{flag}' && "
        f"uci commit gl_screen && "
        f"/etc/init.d/gl_screen reload >/dev/null 2>&1 ; "
        f"echo OK"
    )
    try:
        r = await ssh.run(cmd, timeout=10)
    except SlateSSHError as exc:
        raise ScreenLockError(f"SSH set_enabled failed: {exc}") from exc
    if "OK" not in r.stdout:
        raise ScreenLockError(
            f"set_enabled did not return OK (stderr={r.stderr.strip()!r})",
        )
    logger.info("screen_lock.toggled", enabled=enabled)
    return await get_status(ssh)


@dataclass(frozen=True)
class TouchscreenLockoutTelemetry:
    """On-device gl_screen lockout snapshot, polled via SSH.

    Observed gl_screen semantic (live discovery 2026-06-03) :

    - ``continuous_errors`` mirrors PASSWORD_CONTINOUS_ERRORS — current
      failure streak counter. When the streak hits the gl_screen-internal
      threshold (5 by default), gl_screen RESETS this back to 0 and
      MOVES the count into UNLOCK_ATTEMPT_EXCEED_LIMIT.
    - ``exceed_count`` mirrors UNLOCK_ATTEMPT_EXCEED_LIMIT — *count of
      failures that triggered the current lockout*, not a flag. ``0``
      means no lockout, anything > 0 means the touchscreen is locked.
      At lockout expiry gl_screen clears this back to 0.
    - ``exceed_limit`` is the boolean "is locked" semantic derived from
      ``exceed_count > 0`` — what the UI cares about.
    """

    continuous_errors: int
    exceed_count: int
    exceed_limit: bool


async def read_touchscreen_lockout(
    ssh: SlateSSH,
) -> TouchscreenLockoutTelemetry:
    """Read the on-device touchscreen lockout state from
    ``/etc/gl_screen/status``.

    Raises :class:`ScreenLockError` only on SSH failures. Missing file
    → return zeroes (the device may not have written the status yet).
    """
    try:
        r = await ssh.run(
            "cat /etc/gl_screen/status 2>/dev/null || true", timeout=5,
        )
    except SlateSSHError as exc:
        raise ScreenLockError(
            f"SSH read /etc/gl_screen/status failed: {exc}",
        ) from exc
    errors = 0
    exceed_count = 0
    for line in r.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) != 2:
            continue
        key, val = parts
        if key == "PASSWORD_CONTINOUS_ERRORS":
            try:
                errors = int(val)
            except ValueError:
                pass
        elif key == "UNLOCK_ATTEMPT_EXCEED_LIMIT":
            try:
                exceed_count = int(val)
            except ValueError:
                pass
    return TouchscreenLockoutTelemetry(
        continuous_errors=errors,
        exceed_count=exceed_count,
        exceed_limit=exceed_count > 0,
    )


async def _read_pin(ssh: SlateSSH) -> str:
    """Internal : read the stored PIN from UCI. Never returned over the API ;
    only used by :func:`verify_pin` for constant-time comparison."""
    try:
        r = await ssh.run(
            "uci get gl_screen.generic.PASSCODE 2>/dev/null", timeout=5,
        )
    except SlateSSHError as exc:
        raise ScreenLockError(f"SSH read PIN failed: {exc}") from exc
    return _parse_pin_from_uci(r.stdout)


async def verify_pin(ssh: SlateSSH, attempt: str) -> bool:
    """Verify a PIN attempt against the stored UCI value.

    Returns True on match, False on mismatch. Uses :func:`hmac.compare_digest`
    for constant-time comparison so a remote timing oracle can't leak
    digit-by-digit info — even though our attacker model is the operator,
    not a remote, leaks are leaks.

    Raises :class:`ScreenLockError` only on infrastructure problems
    (SSH down, UCI unreadable). Never raises on a wrong PIN — that's a
    legitimate path the caller wraps with the lockout service.
    """
    if not attempt:
        return False
    stored = await _read_pin(ssh)
    if not stored:
        # No PIN configured on the device — nothing to verify against.
        # Caller decides whether that's a hard error.
        return False
    return hmac.compare_digest(stored, attempt)


async def set_auto_lock(ssh: SlateSSH, seconds: int) -> ScreenLockStatus:
    """Set the auto-lock delay (in seconds). Reasonable range: 15s..1h."""
    if seconds < 15 or seconds > 3600:
        raise ScreenLockError("auto_lock_seconds must be between 15 and 3600")
    cmd = (
        f"uci set gl_screen.generic.AUTO_LOCK_TIME='{seconds}' && "
        f"uci commit gl_screen && "
        f"/etc/init.d/gl_screen reload >/dev/null 2>&1 ; "
        f"echo OK"
    )
    try:
        r = await ssh.run(cmd, timeout=10)
    except SlateSSHError as exc:
        raise ScreenLockError(f"SSH set_auto_lock failed: {exc}") from exc
    if "OK" not in r.stdout:
        raise ScreenLockError(
            f"set_auto_lock did not return OK (stderr={r.stderr.strip()!r})",
        )
    logger.info("screen_lock.auto_lock_set", seconds=seconds)
    return await get_status(ssh)
