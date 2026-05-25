"""Exit-node failover watchdog.

Why: Tailscale's `--exit-node` setting is single-valued — if the configured
peer goes offline, the Slate's default route still points at it and Internet
traffic blackholes. Tailscale has no built-in HA between multiple eligible
exit-nodes (see project memory). This loop fills that gap.

Algorithm (one tick):
  1. Fetch `tailscale status --json`.
  2. Find the currently-routing exit-node (peer.ExitNode = true).
  3. Walk the user's ordered candidate list. Pick the first one that is
     online AND advertises exit-node capability.
  4. If picked = current → noop.
  5. If picked != current → `tailscale set --exit-node=<picked>`. Record.
  6. If no candidate is online → record "down" but DON'T unset the
     current exit-node (better to have a stale route than the Slate
     silently falling back to its raw WAN).

Resilience: every step is best-effort with try/except; the watchdog logs
and continues, no exception ever leaves the loop. Cadence is driven by the
HA store's `check_interval_seconds`, re-read each tick — the user can
change it from the UI and the next tick honours it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from app.tailscale.client import TailscaleClient
from app.tailscale.ha_store import DEFAULT_CHECK_INTERVAL, TailscaleHAStore

logger = structlog.get_logger(__name__)


def _peer_matches(peer: dict[str, Any], identifier: str) -> bool:
    """Match a peer record against a hostname or Tailscale IP."""
    ident = identifier.strip().lower()
    if not ident:
        return False
    hostname = (peer.get("HostName") or "").lower()
    dns = (peer.get("DNSName") or "").lower()
    if ident == hostname:
        return True
    # DNSName has trailing dot: "host.tailnet.ts.net."
    if dns.rstrip(".") == ident or dns.rstrip(".").startswith(ident + "."):
        return True
    if ident in [str(ip) for ip in (peer.get("TailscaleIPs") or [])]:
        return True
    return False


def _same_node(a: str | None, b: str | None) -> bool:
    """Case-insensitive identity match between two node identifiers.

    Tailscale reports HostName in mixed case (e.g. "UI-ETR-UDM01-P") but
    users typically configure candidates lowercased. Comparing raw strings
    causes the watchdog to thrash — switching from "UI-…-P" to "ui-…-p"
    every tick. Normalising both sides fixes it.
    """
    if not a or not b:
        return False
    return a.strip().rstrip(".").lower() == b.strip().rstrip(".").lower()


async def _select_target(
    status_raw: dict[str, Any], candidates: list[str]
) -> tuple[str | None, str | None]:
    """Return (chosen_target, current_target). Either may be None."""
    peers = status_raw.get("Peer") or {}
    current = None
    for p in peers.values():
        if isinstance(p, dict) and p.get("ExitNode"):
            current = (
                p.get("HostName") or p.get("DNSName") or
                (p.get("TailscaleIPs") or [None])[0]
            )
            break
    chosen = None
    for cand in candidates:
        for p in peers.values():
            if not isinstance(p, dict):
                continue
            if _peer_matches(p, cand) and p.get("Online") and p.get("ExitNodeOption"):
                chosen = cand
                break
        if chosen:
            break
    return chosen, current


async def _tick(
    client: TailscaleClient, store: TailscaleHAStore, cfg: dict[str, Any]
) -> None:
    candidates: list[str] = cfg.get("candidates") or []
    if not candidates:
        await store.record_tick(
            action="noop", detail="no candidates configured", target=None,
        )
        return

    # Use the raw JSON status so we get fields not modelled in TailscaleStatus.
    import json as _json
    try:
        r = await client._ssh.run("tailscale status --json 2>&1")  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        await store.record_tick(action="error", detail=f"status fetch: {exc}")
        return
    try:
        status_raw = _json.loads(r.stdout)
    except Exception:  # noqa: BLE001
        await store.record_tick(action="error", detail="status not JSON")
        return

    chosen, current = await _select_target(status_raw, candidates)
    failsafe = cfg.get("failsafe_mode", "fail_open")

    if not chosen:
        # All preferred candidates are offline. Two policies:
        #   fail_open: drop --exit-node so default route falls back to the
        #              raw WAN and Internet is restored (recommended — avoids
        #              "no Internet because the route points at a dead peer").
        #   keep:      preserve the stale exit-node assignment (no killswitch);
        #              user accepts losing Internet to prevent leakage.
        if failsafe == "fail_open" and current:
            ok, out = await client.set_exit_node("")
            if ok:
                await store.record_tick(
                    action="killswitch_open",
                    detail=(
                        f"all {len(candidates)} candidate(s) offline; "
                        f"dropped exit-node {current!r} → WAN fallback"
                    ),
                    target="",
                    switched=True,
                )
                logger.warning(
                    "tailscale.ha.killswitch_open",
                    dropped=current, candidates=candidates,
                )
            else:
                await store.record_tick(
                    action="error",
                    detail=f"failsafe: set --exit-node='' failed: {out[:200]}",
                    target=current,
                )
                logger.error(
                    "tailscale.ha.killswitch_failed",
                    output=out[:300],
                )
            return
        # keep mode (or already no exit-node set): record + move on.
        await store.record_tick(
            action="down",
            detail=f"all {len(candidates)} candidate(s) offline; kept current={current!r}",
            target=current,
        )
        logger.warning(
            "tailscale.ha.all_offline",
            candidates=candidates, current=current, failsafe=failsafe,
        )
        return

    if _same_node(chosen, current):
        await store.record_tick(action="noop", detail=f"current={current!r}", target=current)
        return

    ok, out = await client.set_exit_node(chosen)
    if ok:
        await store.record_tick(
            action="set",
            detail=f"switched {current!r} → {chosen!r}",
            target=chosen,
            switched=True,
        )
        logger.warning(
            "tailscale.ha.switched",
            from_node=current, to_node=chosen, output=out[:200],
        )
    else:
        await store.record_tick(
            action="error",
            detail=f"set --exit-node failed: {out[:200]}",
            target=current,
        )
        logger.error(
            "tailscale.ha.set_failed",
            target=chosen, output=out[:300],
        )


async def run_watchdog(
    client: TailscaleClient, store: TailscaleHAStore
) -> None:
    """Infinite loop. Cancellable via the task. Re-reads config each tick
    so interval + enabled flag changes take effect immediately."""
    logger.info("tailscale.ha.watchdog.start")
    while True:
        try:
            cfg = await store.get()
            interval = int(cfg.get("check_interval_seconds") or DEFAULT_CHECK_INTERVAL)
            if cfg.get("enabled"):
                await _tick(client, store, cfg)
        except asyncio.CancelledError:
            logger.info("tailscale.ha.watchdog.stop")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("tailscale.ha.watchdog.tick_failed", error=str(exc))
            interval = DEFAULT_CHECK_INTERVAL
        # Let CancelledError propagate from sleep so shutdown can finish.
        await asyncio.sleep(interval)
