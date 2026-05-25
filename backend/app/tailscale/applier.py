"""Apply a profile's Tailscale config to the running Slate.

Wired into POST /api/profiles/{name}/activate. The applier:

  1. Honours `enabled` — toggles tailscale up/down WITHOUT touching the
     stored auth key (so the device identity is preserved).
  2. Applies `connection` overrides (accept_routes, exit_node, …) via
     `tailscale set` — no re-auth needed.
  3. Updates the HA watchdog config from `ha` overrides.

Each step is best-effort and reported separately so the UI can surface
partial successes (e.g. "connection OK, HA watchdog write failed").

Note: this module is intentionally narrow — it ONLY handles Tailscale.
Other profile subsystems (Wi-Fi SSIDs, AdGuard, firewall…) get their
own appliers in Phase 2b. The activate endpoint stitches them together.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from app.models.profile import TailscaleConfig
from app.tailscale.client import TailscaleClient
from app.tailscale.ha_store import TailscaleHAStore

logger = structlog.get_logger(__name__)


@dataclass
class TailscaleApplyReport:
    """Per-profile-activation outcome for the Tailscale subsystem."""

    skipped: bool = False
    changes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "skipped": self.skipped,
            "changes": self.changes,
            "errors": self.errors,
        }


async def apply_tailscale_profile(
    cfg: TailscaleConfig,
    client: TailscaleClient,
    ha_store: TailscaleHAStore,
) -> TailscaleApplyReport:
    """Reconcile the running Slate's Tailscale state with `cfg`."""
    rep = TailscaleApplyReport()

    # 1. enabled = False → tailscale down (keep daemon + identity)
    if not cfg.enabled:
        # Only act if the daemon believes it's running — saves a roundtrip.
        try:
            state = await client.get_status()
        except Exception as exc:  # noqa: BLE001
            rep.errors.append(f"status check before disable: {exc}")
            return rep
        if state.daemon_running and state.backend_state == "Running":
            ok, note = await client.disconnect()
            if ok:
                rep.changes.append("tailscale down (left tailnet, daemon kept)")
            else:
                rep.errors.append(f"tailscale down failed: {note}")
        else:
            rep.changes.append("tailscale already down — noop")
        # We still honour the `ha` block when disabled: a profile that
        # disables Tailscale might also want to disable the watchdog.
        await _apply_ha(cfg, ha_store, rep)
        return rep

    # 2. enabled = True — apply connection overrides if any.
    conn = cfg.connection
    if conn is not None:
        ok, applied = await client.apply_overrides(
            accept_routes=conn.accept_routes,
            accept_dns=conn.accept_dns,
            advertise_routes=conn.advertise_routes,
            advertise_exit_node=conn.advertise_exit_node,
            exit_node=conn.exit_node,
            shields_up=conn.shields_up,
        )
        if ok:
            if applied:
                rep.changes.append(f"tailscale set: {', '.join(applied)}")
            else:
                rep.changes.append("connection overrides: nothing to apply")
        else:
            rep.errors.extend(applied)

    # 3. HA watchdog overrides.
    await _apply_ha(cfg, ha_store, rep)

    return rep


async def _apply_ha(
    cfg: TailscaleConfig,
    ha_store: TailscaleHAStore,
    rep: TailscaleApplyReport,
) -> None:
    """Patch the HA store from `cfg.ha`. No-op if no overrides set."""
    ha = cfg.ha
    if ha is None:
        return
    try:
        new_state = await ha_store.update_config(
            enabled=ha.enabled,
            candidates=ha.candidates,
            failsafe_mode=ha.failsafe_mode,
        )
    except (ValueError, Exception) as exc:  # noqa: BLE001
        rep.errors.append(f"HA store update: {exc}")
        return
    rep.changes.append(
        f"HA watchdog: enabled={new_state['enabled']}, "
        f"candidates={new_state['candidates']}, "
        f"failsafe={new_state['failsafe_mode']}"
    )
