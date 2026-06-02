"""Sync profile definitions controller → Slate.

Serializes Pydantic `Profile` models to JSON and pushes them to
`/etc/slate-controller/profiles/<name>.json` on the Slate. The agent's
slate-ctrl picks them up at apply time. Idempotent — running sync again
overwrites the JSON with whatever the controller currently holds.

The JSON shape is just `Profile.model_dump()` — same schema the controller
uses internally. The shell handlers parse it with jsonfilter (OpenWrt's
JSON tool).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.models.profile import Profile
from app.networks.models import NetworkPublic
from app.profiles.wallpapers import WallpaperStore
from app.settings.tailnet_admin import TailnetAdminStore
from app.slate.ssh import SlateSSH, SlateSSHError
from app.slate_agent.deploy import REMOTE_PROFILES_DIR, REMOTE_SCREENS_DIR
from app.wifi.models import WifiSsidPublic

# Slate-side admin surface — TCP ports the tailnet-admin firewall guards
# when the whitelist is non-empty. Single source of truth so the bash
# handler reads the same list (passed in JSON), and so the listening-
# surface audit knows what's expected to be filtered.
#   22   dropbear (SSH)
#   80   LuCI HTTP + GL.iNet UI redirect (nginx)
#   443  LuCI HTTPS + GL.iNet UI (nginx)
#   3000 AdGuard Home UI (HTTP)
#   3443 AdGuard Home UI (HTTPS — planned, currently inactive until the
#        AdGuard TLS toggle is wired up ; harmless to include early)
#   8000 slate-ctrl API (controller's own listener)
#   8080 uhttpd HTTP (GL.iNet ships uhttpd serving LuCI on :8080 in
#        parallel to nginx :80 — same surface, just a different port)
#   8443 uhttpd HTTPS (LuCI HTTPS port, also exposed on tailnet)
#
# NOT admin (intentionally left open on the tailnet — these are SERVICES
# that tailnet peers consume, not admin surface) :
#   53   DNS (dnsmasq / AdGuard) — tailnet peers may use the Slate as
#        resolver, blocking would break that
#   853  DoT — same rationale, encrypted DNS for tailnet peers
#   3053 AdGuard's actual DNS port when forced (cf. quirk : default 53
#        clashes with dnsmasq) — also a client-facing service
#   34641 Tailscale peerapi — internal, managed by Tailscale itself
ADMIN_PORTS_TCP: tuple[int, ...] = (22, 80, 443, 3000, 3443, 8000, 8080, 8443)

# Where pre-rendered wallpaper PNGs live on the Slate (one per profile×kind).
# The wallpaper.sh handler copies the matching file into the gl_screen paths
# at apply time. Same layout idea as screens/loading_<name>.raw.
REMOTE_WALLPAPERS_DIR = "/etc/slate-controller/wallpapers"

logger = structlog.get_logger(__name__)


def _profile_to_agent_payload(
    profile: Profile,
    wifi_catalog: list[WifiSsidPublic],
    network_catalog: list[NetworkPublic],
    admin_ips: list[str] | None = None,
    tor_daemon_enabled: bool = False,
    tor_use_bridges: bool = False,
    tor_bridge_lines: list[str] | None = None,
    tor_exit_country_code: str = "",
    radio_configs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Transform a Pydantic Profile into the on-Slate JSON shape.

    The on-disk JSON is a *deployment artifact*, not a raw Pydantic dump.
    The handlers want flat, self-sufficient blocks (no need to consult a
    separate catalog), so we resolve cross-references here:

      - Wi-Fi: the Pydantic profile has `ssids: [{slug, enabled}]`. The
        handler needs the broadcast SSID name to find the uci section, so
        we hoist that under `wifi.ssids: [{slug, name, bands, mlo,
        security, network_slug, enabled}]`. The original `ssids` field
        is preserved so we don't break introspection but the agent reads
        `wifi.*`. ``bands`` is a list of compact tokens ("2"/"5"/"6") —
        the handler creates one wifi-iface per band when MLO is off.
      - AdGuard: REMOVED from profiles. The controller drives AdGuard
        directly via the per-network DNS protection manager (uses
        AdGuard's persistent-clients REST API). The agent handler for
        adguard now only deals with the global daemon (start/stop) ;
        no per-profile filterlists payload is shipped anymore.
    """
    payload = profile.model_dump(mode="json")

    # Catalog-driven layout : ship the FULL wifi_ssids catalog in every
    # apply, not just the SSIDs the active profile references. The
    # handler then materializes one UCI section per (slug, band) at
    # deploy time (or reuses the existing one) and only flips `disabled`
    # to reflect the profile's activation choices. Result : profile
    # switches stay layout-stable → no reboot, only `wifi reload`.
    # Layout-pending now means "the CATALOG changed" (SSID added/
    # removed / band list edited / MLO toggled), not "this profile
    # selected a different subset of SSIDs".
    profile_refs_by_slug = {ref.slug: ref for ref in profile.ssids}
    resolved_ssids: list[dict[str, Any]] = []
    for catalog in wifi_catalog:
        ref = profile_refs_by_slug.get(catalog.slug)
        # network_slug : the profile decides L2→L3 binding. When the
        # SSID isn't referenced in the profile, we still need a syntactic
        # default so the section is provisioned validly ; "lan" is the
        # universal fallback (every Slate has a `lan` network). The slot
        # will be disabled so the binding is dormant anyway.
        network_slug = ref.network_slug if ref is not None else "lan"
        enabled = ref.enabled if ref is not None else False
        resolved_ssids.append({
            "slug": catalog.slug,
            "name": catalog.ssid_name,
            "bands": list(catalog.bands),
            "mlo": catalog.mlo,
            "security": catalog.security,
            "network_slug": network_slug,
            "client_isolation": catalog.client_isolation,
            "hidden": catalog.hidden,
            "enabled": enabled,
        })
    # Processing-order policy for slot-row exclusivity in the panel :
    #   1. multi-band non-MLO  → claim aligned indexes across their bands
    #      before single-band SSIDs take the low free slots
    #   2. MLO                 → uses fixed mld0/wlanmld* sections, slot
    #      2 is mechanical on this firmware
    #   3. single-band 5 GHz   → most contended band, eat the next free
    #      slot first
    #   4. single-band 6 GHz   → medium contention
    #   5. single-band 2.4 GHz → least contended, fall to the high
    #      indexes so the visual row exclusivity holds (e.g. a lone
    #      2.4 GHz SSID never piggy-backs on a row already owned by an
    #      MLO group on 5/6 GHz)
    # Within each group : alphabetical slug for determinism.
    def _sort_key(s: dict[str, Any]) -> tuple[int, str]:
        bands = s["bands"]
        if s["mlo"]:
            return (1, s["slug"])
        if len(bands) > 1:
            return (0, s["slug"])
        contention = {"5": 2, "6": 3, "2": 4}.get(bands[0] if bands else "", 5)
        return (contention, s["slug"])
    resolved_ssids.sort(key=_sort_key)
    payload["wifi"] = {"ssids": resolved_ssids}

    # Network block — materialize the catalog so the `network.sh`
    # handler can reconcile UCI bridges / interfaces / DHCP / firewall
    # zones / forwardings to the user's intent. Networks are GLOBAL
    # state (not per-profile) but we embed the same block in every
    # profile JSON so apply = reconcile : if the user adds a network
    # and then activates ANY profile, the new network materializes on
    # the Slate. Same idempotency contract as wifi.ssids.
    #
    # Top-level key is singular `network` (not `networks`) so the
    # slate-ctrl dispatcher's `run_handler "network" ...` finds the
    # block via ``@.network`` — convention is "subsystem name == JSON
    # key", same as for ``wifi``/``firewall``/``tailscale``. The list
    # lives under ``items`` to keep room for future per-subsystem flags
    # at the same level.
    resolved_nets: list[dict[str, Any]] = []
    for net in network_catalog:
        resolved_nets.append({
            "slug": net.slug,
            "display_name": net.display_name,
            "bridge_name": net.bridge_name,
            "subnet_cidr": net.subnet_cidr,
            "gateway_ip": net.gateway_ip,
            "dhcp_enabled": net.dhcp_enabled,
            "vlan_tag": net.vlan_tag,
            "ipv6_enabled": net.ipv6_enabled,
            "ipv6_subnet_cidr": net.ipv6_subnet_cidr,
            "intra_bridge_isolation": net.intra_bridge_isolation,
            "reach_internet": net.reach_internet,
            "reachable_networks": list(net.reachable_networks),
            "services_access": net.services_access,
            "admin_ui_access": net.admin_ui_access,
            "ssh_access": net.ssh_access,
            # Per-network Tor routing — tor.sh reads these to decide
            # whether to install NAT rules / kill-switch / DNSPort
            # redirect for this bridge.
            "tor_route_mode": net.tor_route_mode,
            "tor_dns_over_tor": net.tor_dns_over_tor,
            "tor_kill_switch": net.tor_kill_switch,
        })
    payload["network"] = {"items": resolved_nets}

    # Radio (layer-1) block — per-band channel/htmode/txpower/country.
    # Optional : when omitted, the radio.sh handler is a no-op and the
    # MTK driver keeps whatever ACS/EHT defaults it had. We pass the
    # 3 standard bands (2/5/6) every time so any drift between profile
    # applies converges to the stored config.
    if radio_configs is not None:
        payload["radio"] = {"bands": list(radio_configs)}

    # AdGuard enrichment removed — there is no per-profile AdGuard
    # block anymore. All filtering / blocklists are driven by the
    # per-network DNS protection manager (which talks to AdGuard via
    # its persistent-clients REST API directly, not through this
    # agent JSON). Strip any legacy `adguard` block that lingers on
    # old profile payloads so the agent handler doesn't see it.
    payload.pop("adguard", None)

    # Tailscale subnet routing : the source of truth is the per-network
    # `expose_to_tailnet` flag — NOT a per-profile override. Whatever
    # the profile carries in `tailscale.connection.advertise_routes` is
    # ignored ; the network catalog wins. Rationale : routing is a
    # subnet property, not a profile property — a network is either
    # reachable from the tailnet or it isn't, regardless of which
    # profile is active. The user enforced this separation explicitly.
    advertised: list[str] = []
    for net in network_catalog:
        if not net.expose_to_tailnet:
            continue
        if net.subnet_cidr:
            advertised.append(net.subnet_cidr)
        if net.ipv6_enabled and net.ipv6_subnet_cidr:
            advertised.append(net.ipv6_subnet_cidr)
    ts_block = payload.get("tailscale") or {}
    conn = ts_block.get("connection") or {}
    conn["advertise_routes"] = advertised  # always overwrite
    ts_block["connection"] = conn

    # Admin-only enforcement payload. Design simplification (2026-06-01) :
    # the per-profile `admin_only` flag is dropped — the whitelist itself
    # IS the switch :
    #
    #   - whitelist empty → no enforcement (anti-self-DoS safety, same as
    #     before, prevents the operator from accidentally locking themself
    #     out of the Slate from the tailnet)
    #   - whitelist non-empty → enforcement active across ALL profiles
    #
    # Rationale : Tailscale is always-on on the Slate (not a profile-
    # scoped feature). Filtering the admin surface profile-by-profile led
    # to the foot-gun where Mission was strict but switching to Vacances
    # silently opened the admin to every tailnet peer. The legacy flag
    # in profile YAMLs is parsed but ignored at sync time ; the handler
    # decides purely from the (admin_ips, admin_ports_tcp) tuple. Old
    # profiles continue to work, no schema migration needed.
    flag_on = bool(admin_ips)
    ts_block["admin_only"] = flag_on  # kept in payload for back-compat handler reads
    ts_block["admin_ips"] = list(admin_ips or []) if flag_on else []
    ts_block["admin_ports_tcp"] = list(ADMIN_PORTS_TCP) if flag_on else []
    payload["tailscale"] = ts_block

    # Override Profile.tor (TorConfig = enabled+bridge) with the new
    # structured block. The Tor model moved to "global daemon switch +
    # per-network routing modes" (cf TorSettings store + NetworkRow
    # tor_*) — the legacy per-profile fields are no longer the source of
    # truth. The handler reads only the new structure (ports, bridges,
    # global enable); per-network routing comes from `@.network.items[*]`.
    payload["tor"] = {
        "enabled": tor_daemon_enabled,
        "use_bridges": tor_use_bridges,
        "bridges": list(tor_bridge_lines or []),
        # ISO-3166-1 alpha-2 lowercase ("ch", "de"…). Empty = let Tor pick.
        "exit_country_code": (tor_exit_country_code or "").lower(),
        # Standard ports — we expose them here so the handler can write a
        # consistent torrc and the controller-side status check looks at
        # the same numbers. Override per-deployment by editing this dict.
        "socks_port": 9050,
        "control_port": 9051,
        "trans_port": 9040,
        "dns_port": 5353,
    }
    return payload


def add_wallpaper_block(
    payload: dict[str, Any],
    wallpaper_kinds_present: set[str],
) -> dict[str, Any]:
    """Mutate `payload` in place to add a `wallpaper: {home, lock}` block.

    The block tells the wallpaper.sh handler which kind to copy from the
    pre-rendered cache on the Slate. Without it, the handler can't know
    which file to look for (a profile might have a `home` wallpaper but
    no `lock`, etc.). Empty values are still included so the handler
    knows to clear stale state.
    """
    payload["wallpaper"] = {
        "home": "home" in wallpaper_kinds_present,
        "lock": "lock" in wallpaper_kinds_present,
    }
    return payload


@dataclass
class SyncReport:
    pushed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"ok": self.ok, "pushed": self.pushed, "errors": self.errors}


async def sync_profiles(
    ssh: SlateSSH, profiles: list[Profile],
    *,
    wifi_catalog: list[WifiSsidPublic],
    network_catalog: list[NetworkPublic] | None = None,
    tailnet_admin_store: TailnetAdminStore | None = None,
    wallpaper_store: WallpaperStore | None = None,
    tor_settings_store: Any | None = None,
    tor_bridge_store: Any | None = None,
    radio_configs: list[dict[str, Any]] | None = None,
) -> SyncReport:
    """Push every profile's JSON to the Slate.

    `wifi_catalog` lets us resolve SSID slugs → broadcast names so the
    wifi.sh handler can find the uci section directly. Pass None if you
    don't care about Wi-Fi (handler will skip with a warning).

    `network_catalog` provides the per-network `expose_to_tailnet` flag,
    from which we compute `tailscale.connection.advertise_routes`. Pass
    None and no subnet routes will be advertised (no breakage — the
    handler just clears advertised routes).

    `wallpaper_store` lets us add a `wallpaper: {home, lock}` block to each
    profile JSON indicating which wallpaper kinds exist for that profile.
    The wallpaper.sh handler reads that block to know which pre-rendered
    PNG to copy from /etc/slate-controller/wallpapers/. Pass None and
    every profile gets `wallpaper: {home: false, lock: false}` → the
    handler is a no-op.

    Existing JSONs are overwritten. Old profiles that are no longer present
    are NOT pruned — `slate-ctrl list` will still see them.
    """
    rep = SyncReport()
    # Defensive guard : `wifi_catalog` USED to default to None, which
    # silently degraded each SSID to `{slug, enabled, missing: true}` in
    # the pushed JSON. The wifi.sh handler then skipped every SSID
    # (`[ -z "$name" ] && continue` line ~302) and the Slate's wireless
    # state drifted across applies — a previous profile's SSID could
    # remain UP because nothing was ever rewritten. Fixed 2026-06-02 :
    # the param is now required (`*` keyword-only above) so the caller
    # can no longer forget to fetch the catalog.
    catalog = wifi_catalog
    networks = network_catalog or []

    # Resolve the global tailnet admin whitelist once per sync. Cheaper
    # than per-profile and the value is unlikely to change mid-loop.
    admin_ips: list[str] = []
    if tailnet_admin_store is not None:
        try:
            data = await tailnet_admin_store.get()
            admin_ips = list(data.get("admin_ips") or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("sync.tailnet_admin_load_failed", error=str(exc))

    # Pull the wallpaper index ONCE rather than N queries (N profiles).
    wallpaper_index: dict[tuple[str, str], object] = {}
    if wallpaper_store is not None:
        try:
            wallpaper_index = await wallpaper_store.list_existing()  # type: ignore[assignment]
        except Exception as exc:  # noqa: BLE001
            logger.warning("sync.wallpaper_index_failed", error=str(exc))

    # Resolve the global Tor settings + enabled bridges once per sync — the
    # same block is embedded in every profile JSON (Tor config is global,
    # not per-profile ; per-network routing modes live on the network
    # catalog itself).
    tor_daemon_enabled = False
    tor_use_bridges = False
    tor_bridge_lines: list[str] = []
    tor_exit_country_code = ""
    if tor_settings_store is not None:
        try:
            ts = await tor_settings_store.get()
            tor_daemon_enabled = bool(getattr(ts, "daemon_enabled", False))
            tor_use_bridges = bool(getattr(ts, "use_bridges", False))
            tor_exit_country_code = str(getattr(ts, "exit_country_code", "") or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("sync.tor_settings_load_failed", error=str(exc))
    if tor_bridge_store is not None:
        try:
            tor_bridge_lines = await tor_bridge_store.list_enabled_lines()
        except Exception as exc:  # noqa: BLE001
            logger.warning("sync.tor_bridges_load_failed", error=str(exc))

    # Ensure the destination dir exists (deploy_agent already creates it,
    # but sync can be called independently).
    try:
        await ssh.run(f"mkdir -p {REMOTE_PROFILES_DIR}", timeout=5)
    except SlateSSHError as exc:
        rep.errors.append(f"mkdir profiles dir: {exc}")
        return rep

    for profile in profiles:
        try:
            data = _profile_to_agent_payload(
                profile, catalog, networks, admin_ips=admin_ips,
                tor_daemon_enabled=tor_daemon_enabled,
                tor_use_bridges=tor_use_bridges,
                tor_bridge_lines=tor_bridge_lines,
                tor_exit_country_code=tor_exit_country_code,
                radio_configs=radio_configs,
            )
            kinds_present = {
                kind for (pname, kind) in wallpaper_index
                if pname == profile.name
            }
            add_wallpaper_block(data, kinds_present)
            payload = json.dumps(data, indent=2).encode()
            target = f"{REMOTE_PROFILES_DIR}/{profile.name}.json"
            await ssh.put_bytes(payload, target, mode=0o644)
            rep.pushed.append(f"{profile.name} ({len(payload)}B)")
        except (SlateSSHError, ValueError) as exc:
            rep.errors.append(f"sync {profile.name}: {exc}")

    logger.info(
        "slate_agent.sync",
        ok=rep.ok, pushed=len(rep.pushed), errors=len(rep.errors),
    )
    return rep


async def sync_loading_screens(ssh: SlateSSH, profiles: list[Profile]) -> SyncReport:
    """Pre-render a "loading profile X" status PNG per profile, convert to
    RGB565 raw, and push to /etc/slate-controller/screens/loading_<name>.raw
    on the Slate.

    Rendering happens controller-side (Pillow + Slate's TTF cached locally)
    so the agent's `screen.sh` handler can show the message via a plain
    `cat raw > /dev/fb0` — no fonts, no PIL, no per-frame cost on the Slate.

    Same shape as sync_profiles. Failures are per-profile.
    """
    from app.profiles.fb_takeover import _png_to_rgb565_portrait
    from app.profiles.status_screen import render_status_image

    rep = SyncReport()
    try:
        await ssh.run(f"mkdir -p {REMOTE_SCREENS_DIR}", timeout=5)
    except SlateSSHError as exc:
        rep.errors.append(f"mkdir screens dir: {exc}")
        return rep

    for profile in profiles:
        try:
            png = await render_status_image(
                ssh,
                title=f"loading profile {profile.name}",
                subtitle="from slate-controller",
                target=profile.name,
                kind="status",
            )
            raw = _png_to_rgb565_portrait(png)
            target = f"{REMOTE_SCREENS_DIR}/loading_{profile.name}.raw"
            await ssh.put_bytes(raw, target, mode=0o644)
            rep.pushed.append(f"loading_{profile.name} ({len(raw)}B raw)")
        except (SlateSSHError, ValueError, OSError) as exc:
            rep.errors.append(f"sync screen {profile.name}: {exc}")

    logger.info(
        "slate_agent.sync_screens",
        ok=rep.ok, pushed=len(rep.pushed), errors=len(rep.errors),
    )
    return rep


async def sync_profile_wallpapers(
    ssh: SlateSSH,
    profiles: list[Profile],
    wallpaper_store: WallpaperStore,
) -> SyncReport:
    """Pre-render the home + lock wallpaper PNGs for every profile that has
    one, and push them to /etc/slate-controller/wallpapers/<profile>_<kind>.png.

    Why pre-render here instead of letting the agent do it on the Slate :
      - PIL/Pillow is not on the stock GL.iNet firmware (and pulling it via
        opkg would add ~15 MB).
      - Resize + alpha-flatten + LANCZOS happens in microseconds on the
        controller's CPU; on the Slate's MT7986A it would be seconds.
      - We can re-use the SAME `_resize_to_screen` helper the controller
        already uses for direct (non-agent) apply paths — single source
        of truth for the rendering rules.

    Returns per-profile push entries. Skips profiles with no wallpaper at
    all (the handler will clear gl_screen back to OEM in that case).
    """
    from app.profiles.screen_applier import _resize_to_screen

    rep = SyncReport()

    try:
        await ssh.run(f"mkdir -p {REMOTE_WALLPAPERS_DIR}", timeout=5)
    except SlateSSHError as exc:
        rep.errors.append(f"mkdir wallpapers dir: {exc}")
        return rep

    for profile in profiles:
        for kind in ("home", "lock"):
            try:
                blob = await wallpaper_store.get_blob(profile.name, kind=kind)
            except Exception as exc:  # noqa: BLE001
                rep.errors.append(
                    f"read {profile.name}/{kind}: {exc}"
                )
                continue
            if blob is None:
                continue  # no custom wallpaper for this (profile, kind)
            try:
                resized_png = _resize_to_screen(blob.content, fit_mode=blob.fit_mode)
            except Exception as exc:  # noqa: BLE001
                rep.errors.append(
                    f"resize {profile.name}/{kind}: {exc}"
                )
                continue
            target = f"{REMOTE_WALLPAPERS_DIR}/{profile.name}_{kind}.png"
            try:
                await ssh.put_bytes(resized_png, target, mode=0o644)
                rep.pushed.append(
                    f"{profile.name}/{kind} ({len(resized_png)}B "
                    f"fit={blob.fit_mode})"
                )
            except SlateSSHError as exc:
                rep.errors.append(
                    f"push {profile.name}/{kind}: {exc}"
                )

    logger.info(
        "slate_agent.sync_wallpapers",
        ok=rep.ok, pushed=len(rep.pushed), errors=len(rep.errors),
    )
    return rep


REMOTE_MENUS_DIR = "/etc/slate-controller/menus"


async def sync_button_cycle(
    ssh: SlateSSH, steps: list,
    *,
    active_name: str | None = None,
) -> SyncReport:
    """Push the reset-button cycle list + pre-rendered menu frames.

    `active_name` is folded into the frames so the row matching the
    currently-loaded profile gets an "ACTIVE" pill. Pass None to skip
    the badge (initial sync where we don't know yet, or external
    callers that don't care).

    Two artifacts go to the Slate :
      1. `/etc/slate-controller/cycle.json` — the ordered step list,
         consumed by `cycle-profile.sh` at button-press time.
      2. `/etc/slate-controller/menus/cycle_<N>.raw` — one 153 600 B
         RGB565 frame per cursor position, painted on the panel while
         the user keeps pressing (select-then-commit UX).
         Stale frames from a longer previous cycle are pruned so the
         menus dir mirrors the current cycle exactly.

    Idempotent — overwrites everything each call. Empty `steps` writes
    `{"steps": []}` and prunes all menu frames so the agent has an
    explicit "cycle disabled" signal.
    """
    from app.profiles.cycle_menu_renderer import render_menu_frames_async
    from app.profiles.fb_takeover import _png_to_rgb565_portrait
    from app.settings.button_cycle import remote_path, to_agent_payload

    rep = SyncReport()

    # 1. cycle.json
    payload = to_agent_payload(steps)
    try:
        await ssh.put_bytes(payload, remote_path(), mode=0o644)
        rep.pushed.append(f"cycle.json ({len(steps)} steps, {len(payload)}B)")
    except SlateSSHError as exc:
        rep.errors.append(f"sync cycle.json: {exc}")
        # If we can't even write cycle.json, no point trying frames.
        return rep

    # 2. menu frames. Render every cursor position, convert to RGB565,
    # push. The rendering happens here (controller-side, Pillow) so the
    # agent has nothing to compute at button-press time — `cat raw > fb0`
    # and it's painted.
    try:
        await ssh.run(f"mkdir -p {REMOTE_MENUS_DIR}", timeout=5)
    except SlateSSHError as exc:
        rep.errors.append(f"mkdir menus dir: {exc}")
        return rep

    try:
        # Rendering needs SSH to (lazily) fetch the Slate's TTF fonts on
        # first run. Subsequent calls hit the local cache → ~20ms per
        # frame.
        png_frames = await render_menu_frames_async(
            ssh, steps, active_name=active_name,
        )
    except Exception as exc:  # noqa: BLE001
        rep.errors.append(f"render menu frames: {exc}")
        return rep

    for idx, png in enumerate(png_frames):
        try:
            raw = await asyncio.to_thread(_png_to_rgb565_portrait, png)
        except Exception as exc:  # noqa: BLE001
            rep.errors.append(f"rgb565 frame #{idx}: {exc}")
            continue
        target = f"{REMOTE_MENUS_DIR}/cycle_{idx}.raw"
        try:
            await ssh.put_bytes(raw, target, mode=0o644)
            rep.pushed.append(f"cycle_{idx}.raw ({len(raw)}B)")
        except SlateSSHError as exc:
            rep.errors.append(f"push frame #{idx}: {exc}")

    # 3. Prune stale frames from previous syncs. If the cycle used to
    # have 6 steps and now has 3, frames 3-5 are stale and would
    # mislead the agent if cycle.json is briefly inconsistent.
    try:
        await ssh.run(
            f"for f in {REMOTE_MENUS_DIR}/cycle_*.raw; do "
            f"  idx=$(basename \"$f\" .raw | sed s/cycle_//); "
            f"  case \"$idx\" in ''|*[!0-9]*) continue ;; esac; "
            f"  [ \"$idx\" -ge {len(steps)} ] && rm -f \"$f\"; "
            f"done 2>/dev/null || true",
            timeout=10,
        )
    except SlateSSHError as exc:
        rep.errors.append(f"prune stale menu frames: {exc}")

    logger.info(
        "slate_agent.sync_button_cycle",
        ok=rep.ok, steps=len(steps), frames=len(png_frames),
    )
    return rep


async def refresh_button_cycle_active(
    ssh: SlateSSH,
    cycle_steps: list,
    active_name: str | None,
) -> SyncReport | None:
    """Re-render + push menu frames if the cycle has at least one
    matching profile slot. Caller is responsible for reading the
    current cycle config and active name.

    Returns the sync report on success, or None when nothing needed
    re-rendering (empty cycle, or active doesn't appear in any slot).
    The latter optimization saves a full 4×150KB SSH push when the
    user activates a profile that's not in their cycle.
    """
    if not cycle_steps:
        return None
    if active_name is None:
        # Without an active to highlight, the badge layer wouldn't
        # change. Skip — callers can still call sync_button_cycle
        # directly when they want the un-badged version.
        return None
    matches = any(
        s.kind == "profile" and s.name == active_name for s in cycle_steps
    )
    if not matches:
        return None
    return await sync_button_cycle(ssh, cycle_steps, active_name=active_name)


async def list_remote_profiles(ssh: SlateSSH) -> list[str]:
    """Return the profile names currently present on the Slate."""
    try:
        r = await ssh.run("/usr/local/bin/slate-ctrl list 2>/dev/null", timeout=5)
        if r.exit_status != 0:
            return []
        return [line.strip() for line in r.stdout.splitlines() if line.strip()]
    except SlateSSHError:
        return []


async def get_active_remote_profile(ssh: SlateSSH) -> str | None:
    """Return the agent's active profile (state/active file), or None."""
    try:
        r = await ssh.run("/usr/local/bin/slate-ctrl status 2>/dev/null", timeout=5)
        if r.exit_status == 0:
            name = r.stdout.strip()
            return name or None
    except SlateSSHError:
        pass
    return None


async def resolve_active_name(ssh: SlateSSH, store: Any) -> str | None:
    """The Slate is the source of truth for which profile is active.

    Queries `slate-ctrl status` first ; if it disagrees with the controller
    DB (which happens when an apply ran via the physical button, a direct
    SSH call, or any path that bypassed `ProfileStore.set_active`), auto-
    reconciles the DB to match. Falls back to the DB value when the Slate
    is unreachable so the UI keeps working offline.

    `store` is typed `Any` so this module stays a leaf — its only job is to
    talk to the device. The caller passes its ProfileStore.
    """
    device = await get_active_remote_profile(ssh)
    try:
        db = await store.get_active_name()
    except Exception as exc:  # noqa: BLE001
        logger.warning("active.db_read_failed", error=str(exc))
        db = None
    if device is None:
        return db
    if device != db:
        try:
            await store.set_active(device)
            logger.info("active.reconciled", from_=db, to=device)
        except Exception as exc:  # noqa: BLE001
            # Device names a profile we don't know (or DB write failed) —
            # surface the device value anyway, just don't sync the DB.
            logger.warning(
                "active.reconcile_failed", device=device, db=db, error=str(exc),
            )
    return device


# Emitted by slate-ctrl when a handler (wifi.sh) signalled a radio change
# that only applies after a full reboot. The agent schedules the reboot
# itself ~8s later; the controller watches for this line to know it should
# poll for the Slate to come back. Single source of truth — keep in sync
# with the echo in scripts/slate-ctrl.
REBOOT_SENTINEL = "REBOOT SCHEDULED"


async def apply_single_handler(
    ssh: SlateSSH, subsystem: str, *, timeout: float = 45.0,
) -> tuple[bool, str]:
    """Run ONE handler (network / wifi / tor / ...) against the currently
    active profile on the device. Lighter than the full
    ``apply_remote_profile`` flow : used by the per-area Save+Apply
    endpoints so the operator's "I changed the Tor exit country" PUT
    request finishes in a couple seconds rather than running every other
    handler too.

    The caller is expected to have just synced the profile JSON (so the
    handler sees the user's latest intent). On busybox/dropbear this is
    ~1-3 s per call.
    """
    cmd = f"/usr/local/bin/slate-ctrl apply-only {subsystem} 2>&1"
    try:
        r = await ssh.run(cmd, timeout=timeout)
    except SlateSSHError as exc:
        return False, f"SSH error: {exc}"
    return r.exit_status == 0, r.stdout


async def apply_remote_profile(ssh: SlateSSH, name: str) -> tuple[bool, str]:
    """Invoke `slate-ctrl apply <name>` on the Slate.

    Returns (ok, output). On success, the agent's local handlers have
    applied the profile — this replaces (when used) the per-subsystem
    appliers the controller runs over SSH today.

    If the output contains `REBOOT_SENTINEL`, the agent has scheduled a
    deferred reboot (radio changes). Callers should detect that and run
    `finalize_after_reboot` as a background task rather than continuing to
    SSH the (about-to-reboot) Slate inline.
    """
    try:
        r = await ssh.run(
            f"/usr/local/bin/slate-ctrl apply {name} 2>&1", timeout=60,
        )
        return r.exit_status == 0, r.stdout
    except SlateSSHError as exc:
        return False, f"SSH error: {exc}"


async def wait_for_slate_recovery(
    ssh: SlateSSH,
    *,
    label: str = "",
    settle_seconds: float = 20.0,
    timeout_seconds: float = 240.0,
    poll_seconds: float = 5.0,
) -> tuple[bool, str]:
    """Poll the Slate over SSH until it answers again after a reboot.

    The agent schedules the reboot ~8s after `slate-ctrl apply` returns, so
    we sleep `settle_seconds` first (let it actually go down) to avoid
    matching the pre-reboot sshd, then poll `/proc/uptime` until SSH is back.
    Returns (recovered, human_message) and logs the outcome.
    """
    await asyncio.sleep(settle_seconds)
    loop = asyncio.get_event_loop()
    start = loop.time()
    while loop.time() - start < timeout_seconds:
        try:
            r = await ssh.run("cat /proc/uptime", timeout=8)
            if r.exit_status == 0 and r.stdout.strip():
                elapsed = loop.time() - start + settle_seconds
                uptime = r.stdout.split(".", 1)[0]
                msg = f"Slate back after ~{elapsed:.0f}s (uptime={uptime}s)"
                logger.info("agent.reboot.recovered", profile=label, detail=msg)
                return True, msg
        except SlateSSHError:
            pass
        await asyncio.sleep(poll_seconds)
    msg = f"Slate did not answer within {timeout_seconds:.0f}s after reboot"
    logger.warning("agent.reboot.timeout", profile=label, detail=msg)
    return False, msg


async def finalize_after_reboot(ssh: SlateSSH, name: str, db_engine: Any) -> None:
    """Background task: wait for the agent-scheduled reboot to complete,
    then re-run the button-cycle menu refresh.

    The inline cycle refresh that both activate paths normally do needs SSH,
    which is dead while the Slate reboots — so when a reboot is pending we
    defer that refresh here instead of racing the box on its way down.
    """
    recovered, _ = await wait_for_slate_recovery(ssh, label=name)
    if not recovered:
        return
    try:
        from app.db.database import make_session_factory
        from app.settings.button_cycle import ButtonCycleStore

        steps = await ButtonCycleStore(make_session_factory(db_engine)).get()
        await refresh_button_cycle_active(ssh, steps, name)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "agent.reboot.cycle_refresh_failed", name=name, error=str(exc),
        )
