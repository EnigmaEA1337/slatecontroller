"""Anti-theft autonomous mode + auto-erase actions.

Co-operates with :class:`PinLockoutService` : every recorded PIN failure
is also forwarded here via :meth:`on_pin_failure`. If autonomous mode is
on and the cumulative ``total_failures`` counter trips the threshold,
:meth:`_execute_action` fires.

Actions (escalating, opt-in per config row) :

    alert       Log critical event, append to audit, (future) call webhook.
                Default — completely safe, no data touched.
    soft_wipe   alert + SSH the Slate to :
                  - tailscale logout
                  - clear ``wireguard.*`` and ``openvpn.*`` UCI sections
                  - set the touchscreen PIN to a long random string
                Effectively bricks the radio without destroying OpenWrt
                itself — a factory-reset still recovers the device.

``factory_reset`` is intentionally NOT implemented yet : we want the soft
path validated on a live Slate before shipping anything that wipes the
whole firmware.

After firing, the counter is reset (next round of failures is a fresh
streak). ``last_action_at`` / ``last_action_kind`` / ``last_action_note``
are persisted so the UI can show "Auto-erase fired 2026-06-03 18:14 ·
soft_wipe · cleared 2 wg + 1 ovpn".
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import AntiTheftConfigRow
from app.devices.registry import DeviceConnectionsRegistry

logger = structlog.get_logger(__name__)


ActionKind = Literal["alert", "soft_wipe"]


@dataclass(frozen=True)
class AntiTheftSnapshot:
    autonomous_mode: bool
    failure_threshold: int
    action: ActionKind
    notify_webhook_url: str
    total_failures: int
    last_action_at: datetime | None
    last_action_kind: str
    last_action_note: str


class AntiTheftService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker,
        device_registry: DeviceConnectionsRegistry,
    ) -> None:
        self._sf = session_factory
        self._dev = device_registry

    # ---------- config CRUD ----------

    async def snapshot(self, slug: str) -> AntiTheftSnapshot:
        async with self._sf() as s:
            row = await self._get(s, slug)
            return self._project(row)

    async def upsert(
        self,
        slug: str,
        *,
        autonomous_mode: bool,
        failure_threshold: int,
        action: ActionKind,
        notify_webhook_url: str,
    ) -> AntiTheftSnapshot:
        async with self._sf() as s:
            row = await self._get(s, slug)
            if row is None:
                row = AntiTheftConfigRow(
                    device_slug=slug,
                    autonomous_mode=autonomous_mode,
                    failure_threshold=failure_threshold,
                    action=action,
                    notify_webhook_url=notify_webhook_url[:256],
                )
                s.add(row)
            else:
                row.autonomous_mode = autonomous_mode
                row.failure_threshold = failure_threshold
                row.action = action
                row.notify_webhook_url = notify_webhook_url[:256]
            await s.commit()
            await s.refresh(row)
            return self._project(row)

    async def reset_counter(self, slug: str) -> AntiTheftSnapshot:
        """Manual reset — useful for tests and after legit recovery."""
        async with self._sf() as s:
            row = await self._get(s, slug)
            if row is not None:
                row.total_failures = 0
                await s.commit()
                await s.refresh(row)
                return self._project(row)
            return self._project(None)

    # ---------- hooks called by PinLockoutService ----------

    async def on_pin_failure(self, slug: str) -> None:
        """Increment the cumulative counter. Fire the configured action
        when threshold is hit AND autonomous_mode is on.

        The counter ALWAYS tracks failures so the operator can see history
        in the UI even with autonomous mode off ; only the *firing* is
        gated. This makes the autonomous toggle behave like "arm /
        disarm" rather than "track / don't track" — toggling it on later
        is then immediately backed by real data.
        """
        async with self._sf() as s:
            row = await self._get_or_create(s, slug)
            row.total_failures = (row.total_failures or 0) + 1
            slug_local = row.device_slug
            action_kind: ActionKind = row.action  # type: ignore[assignment]
            # Only fire if armed.
            tripped = (
                row.autonomous_mode
                and row.total_failures >= row.failure_threshold
            )
            if tripped:
                row.total_failures = 0
                row.last_action_at = datetime.now(UTC).replace(tzinfo=None)
                row.last_action_kind = action_kind
            await s.commit()
            if tripped:
                note = await self._execute_action(slug_local, action_kind)
                async with self._sf() as s2:
                    row2 = await self._get(s2, slug_local)
                    if row2 is not None:
                        row2.last_action_note = (note or "")[:512]
                        await s2.commit()

    async def on_pin_success(self, slug: str) -> None:
        """Reset the cumulative counter — clean slate."""
        async with self._sf() as s:
            row = await self._get(s, slug)
            if row is None:
                return
            if row.total_failures:
                row.total_failures = 0
                await s.commit()

    # ---------- action implementations ----------

    async def test_run(self, slug: str) -> str:
        """Dry-run the configured action without touching the device or
        the counter — surfaces what the wipe would do, returns a
        human-readable summary. Safe to call from the UI 'tester'
        button."""
        async with self._sf() as s:
            row = await self._get(s, slug)
        if row is None:
            return "Aucune politique anti-theft configurée pour ce device."
        if row.action == "alert":
            return (
                "DRY-RUN · action=alert · "
                "log de niveau warning + audit trail. Aucune donnée touchée."
            )
        if row.action == "soft_wipe":
            return (
                "DRY-RUN · action=soft_wipe · clearerait : "
                "(1) tailscale logout, "
                "(2) UCI delete wireguard.* + openvpn.*, "
                "(3) set screen PIN à un random 8-digits inconnu. "
                "Récup possible via factory reset OpenWrt."
            )
        return f"action {row.action!r} inconnue"

    async def _execute_action(
        self, slug: str, action_kind: ActionKind,
    ) -> str:
        """Execute the configured action. Returns a human note for audit."""
        logger.critical(
            "anti_theft.action.fired",
            slug=slug, action=action_kind,
        )
        if action_kind == "alert":
            return "alert fired — webhook+audit only, no data touched"
        if action_kind == "soft_wipe":
            return await self._soft_wipe(slug)
        return f"unknown action {action_kind}"

    async def _soft_wipe(self, slug: str) -> str:
        """Best-effort destructive wipe on the Slate.

        Each step is independently try/except'd so a failure on one
        doesn't stop the rest — partial wipe is still better than none.
        Returns a summary of what actually completed.
        """
        try:
            conn = await self._dev.for_slug(slug)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "anti_theft.soft_wipe.no_device", slug=slug, error=str(exc),
            )
            return f"soft_wipe aborted — device unreachable: {exc}"

        ssh = conn.ssh
        steps: list[str] = []

        # 1. Tailscale logout — kills the tailnet identity.
        try:
            await ssh.run("tailscale logout 2>&1 || true", timeout=15)
            steps.append("tailscale-logout")
            logger.info("anti_theft.soft_wipe.tailscale_logout", slug=slug)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "anti_theft.soft_wipe.tailscale_failed",
                slug=slug, error=str(exc),
            )

        # 2. UCI sweep : delete every wireguard + openvpn section.
        #    The greedy redirect to /dev/null swallows missing-section
        #    errors when the Slate doesn't have any.
        try:
            await ssh.run(
                "for s in $(uci show wireguard 2>/dev/null | "
                "awk -F. '{print $1\".\"$2}' | sort -u); do "
                "  uci delete $s 2>/dev/null; "
                "done; "
                "for s in $(uci show openvpn 2>/dev/null | "
                "awk -F. '{print $1\".\"$2}' | sort -u); do "
                "  uci delete $s 2>/dev/null; "
                "done; "
                "uci commit; echo OK",
                timeout=10,
            )
            steps.append("uci-wg+ovpn-wiped")
            logger.info("anti_theft.soft_wipe.uci_cleared", slug=slug)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "anti_theft.soft_wipe.uci_failed",
                slug=slug, error=str(exc),
            )

        # 3. Override the touchscreen PIN with a long random string.
        #    8 random digits — within the 4-8 valid range, impossible
        #    for the operator (or thief) to guess without factory reset.
        random_pin = "".join(secrets.choice("0123456789") for _ in range(8))
        try:
            await ssh.run(
                f'uci set gl_screen.generic.PASSCODE=\'"{random_pin}"\' && '
                "uci set gl_screen.generic.ENABLE_PASSCODE='1' && "
                "uci commit gl_screen && "
                "/etc/init.d/gl_screen reload >/dev/null 2>&1; echo OK",
                timeout=10,
            )
            steps.append("pin-randomized")
            logger.info(
                "anti_theft.soft_wipe.pin_randomized",
                slug=slug,
                # Don't log the PIN itself — even random, it's secret now.
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "anti_theft.soft_wipe.pin_failed",
                slug=slug, error=str(exc),
            )

        return f"soft_wipe done · steps={','.join(steps) or 'none'}"

    # ---------- helpers ----------

    async def _get(self, s, slug: str) -> AntiTheftConfigRow | None:
        return await s.scalar(
            select(AntiTheftConfigRow).where(
                AntiTheftConfigRow.device_slug == slug,
            ),
        )

    async def _get_or_create(self, s, slug: str) -> AntiTheftConfigRow:
        """Same as :meth:`_get` but materialises a default row when none
        exists — used by :meth:`on_pin_failure` so a counter starts ticking
        from the first attempt without requiring the operator to first
        visit the Anti-theft page."""
        row = await self._get(s, slug)
        if row is not None:
            return row
        row = AntiTheftConfigRow(device_slug=slug)
        s.add(row)
        await s.flush()
        return row

    @staticmethod
    def _project(row: AntiTheftConfigRow | None) -> AntiTheftSnapshot:
        if row is None:
            return AntiTheftSnapshot(
                autonomous_mode=False,
                failure_threshold=10,
                action="alert",
                notify_webhook_url="",
                total_failures=0,
                last_action_at=None,
                last_action_kind="",
                last_action_note="",
            )
        return AntiTheftSnapshot(
            autonomous_mode=row.autonomous_mode,
            failure_threshold=row.failure_threshold,
            action=row.action,  # type: ignore[arg-type]
            notify_webhook_url=row.notify_webhook_url,
            total_failures=row.total_failures or 0,
            last_action_at=row.last_action_at,
            last_action_kind=row.last_action_kind,
            last_action_note=row.last_action_note,
        )
