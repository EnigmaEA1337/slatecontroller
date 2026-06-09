"""Tailscale management endpoints.

Flow:
  1. User pastes a Tailscale auth key (one-shot reusable+tagged from admin.tailscale.com)
     and the connection options (accept/advertise routes, exit-node).
  2. We persist the key encrypted in app_secrets and run `tailscale up` via SSH.
  3. Status endpoint reads `tailscale status --json` and returns a clean payload.

The auth key is never returned in any response — only a "configured" boolean.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.api.deps import get_slate_ssh
from app.auth import User, get_current_user
from app.db.database import make_session_factory
from app.networks.store import NetworkStore
from app.slate.ssh import SlateSSH
from app.tailscale.admin_api import TailscaleAdminAPI, TailscaleAdminAPIError
from app.tailscale.admin_store import TailscaleAdminStore
from app.tailscale.audit import TailscaleAuditor
from app.tailscale.client import TailscaleClient
from app.tailscale.dns_routing import (
    DesiredDomainRule,
    apply_state as apply_dns_routing_state,
    discover_state as discover_dns_routing_state,
)
from app.tailscale.forwarding import (
    DesiredRule as TailnetDesiredRule,
    apply_state as apply_forwarding_state,
    discover_state as discover_forwarding_state,
)
from app.tailscale.ha_store import (
    FAILSAFE_MODES,
    MAX_CHECK_INTERVAL,
    MIN_CHECK_INTERVAL,
    TailscaleHAStore,
)
from app.tailscale.models import (
    TailscaleConfigInput,
    TailscaleConnectResponse,
    TailscaleStatus,
)
from app.tailscale.store import TailscaleStore

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/tailscale", tags=["tailscale"])


def _store(request: Request) -> TailscaleStore:
    sf = make_session_factory(request.app.state.db_engine)
    return TailscaleStore(sf)


def _admin_store(request: Request) -> TailscaleAdminStore:
    sf = make_session_factory(request.app.state.db_engine)
    return TailscaleAdminStore(sf)


def _ha_store(request: Request) -> TailscaleHAStore:
    # Reuse the singleton installed in lifespan — the watchdog and the
    # API share the same instance so writes are observed within one tick.
    return request.app.state.tailscale_ha_store


@router.get("/status", response_model=TailscaleStatus)
async def get_status(
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> TailscaleStatus:
    """Live status from the Slate (tailscale status --json).

    Augments the daemon-reported status with the user-intent fields from our
    last-applied config (accept_routes, advertised_routes target list, etc.)
    — those aren't directly exposed in `tailscale status --json`.
    """
    state = await TailscaleClient(ssh).get_status()
    # Overlay user-intent from the stored config so the UI doesn't lie
    # ("Routes acceptées: non" while we actually passed --accept-routes).
    try:
        cfg = await _store(request).get_metadata()
        applied = (cfg or {}).get("config") or {}
        if applied:
            state.accept_routes = bool(applied.get("accept_routes"))
            # advertised_routes from daemon shows APPROVED routes only.
            # If empty but we asked to advertise some, keep showing what we asked.
            asked = applied.get("advertise_routes") or []
            if not state.advertised_routes and asked:
                state.advertised_routes = list(asked)
            state.exit_node_enabled = bool(applied.get("advertise_exit_node"))
            if not state.use_exit_node and applied.get("exit_node"):
                state.use_exit_node = str(applied.get("exit_node"))
    except Exception:  # noqa: BLE001
        pass  # status without overlay is still useful
    return state


@router.get("/config")
async def get_config(
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Last-applied config + has-auth-key flag. No secrets returned."""
    return await _store(request).get_metadata()


@router.get("/sync-routes/preview")
async def preview_sync_routes(
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Diff between expected (from network catalog) and currently
    advertised routes on the Slate. Used by the UI to decide whether to
    show the « Re-pousser routes » button as actionable or as a no-op.

    Read-only — does not modify any state.
    """
    expected = await _expected_routes(request)
    client = TailscaleClient(ssh)
    state = await client.get_status()
    current = list(state.advertised_routes or [])
    # `enabled` are the routes the tailnet admin has approved; `expected
    # but not enabled` means « advertised but waiting for approval ».
    enabled = await _enabled_routes_from_pat(request, _ipv4(state.tailscale_ips))
    to_add = [r for r in expected if r not in current]
    to_remove = [r for r in current if r not in expected]
    not_yet_approved = (
        [r for r in expected if enabled is not None and r not in enabled]
        if enabled is not None else None
    )
    return {
        "expected": expected,
        "current_advertised": current,
        "current_approved": enabled,  # null when no PAT configured
        "to_add": to_add,
        "to_remove": to_remove,
        "not_yet_approved": not_yet_approved,
        "in_sync": not to_add and not to_remove,
    }


@router.post("/sync-routes")
async def sync_routes(
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Push the canonical advertise-routes list to the Slate.

    Computes the expected list from the network catalog (any network
    with `expose_to_tailnet=True`), calls `tailscale set --advertise-
    routes=...` over SSH, and — if a PAT is configured — also approves
    the routes via the tailnet admin API so peers immediately receive
    them. Idempotent : running twice with no catalog change is a no-op.
    """
    expected = await _expected_routes(request)
    client = TailscaleClient(ssh)

    # 1. Push the advertise list to the Slate. apply_overrides uses
    # `tailscale set` (no session reset) — safe over a live link.
    ok, applied = await client.apply_overrides(advertise_routes=expected)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"tailscale set failed: {' '.join(applied)}",
        )

    # 2. Persist intent in the store so /status overlay reflects the change.
    try:
        meta = await _store(request).get_metadata()
        cfg = ((meta or {}).get("config") or {}).copy()
        cfg["advertise_routes"] = expected
        await _store(request).save(auth_key=None, last_applied_config=cfg)
    except Exception as exc:  # noqa: BLE001 - non-fatal persistence
        logger.warning("tailscale.sync_routes.persist_failed", error=str(exc))

    # 3. Auto-approve via PAT if available. Best-effort : if the PAT is
    # missing or scoped without routes:write, we surface the failure in
    # the response but don't fail the whole call.
    state = await client.get_status()
    approval = await _approve_routes_via_pat(
        request, _ipv4(state.tailscale_ips), expected,
    )

    logger.info(
        "tailscale.sync_routes",
        username=user.username,
        expected=expected,
        applied=applied,
        approval=approval,
    )
    return {
        "ok": True,
        "expected": expected,
        "applied": applied,
        "approval": approval,
        "status": state.model_dump(),
    }


async def _expected_routes(request: Request) -> list[str]:
    """Aggregate IPv4 + IPv6 subnets of networks marked expose_to_tailnet."""
    sf = make_session_factory(request.app.state.db_engine)
    nets = await NetworkStore(sf).list_all()
    out: list[str] = []
    for n in nets:
        if not n.expose_to_tailnet:
            continue
        if n.subnet_cidr:
            out.append(n.subnet_cidr)
        if getattr(n, "ipv6_enabled", False) and getattr(n, "ipv6_subnet_cidr", None):
            out.append(n.ipv6_subnet_cidr)
    return out


async def _enabled_routes_from_pat(
    request: Request, slate_ts_ip: str | None,
) -> list[str] | None:
    """Read tailnet-side approval state for the Slate. Returns None when
    no PAT is configured (the UI then shows « approval unknown »)."""
    if not slate_ts_ip:
        return None
    admin_store = _admin_store(request)
    pat = await admin_store.get_pat()
    if not pat:
        return None
    meta = await admin_store.get_metadata()
    tailnet = (meta or {}).get("tailnet") or "-"
    try:
        async with TailscaleAdminAPI(pat, tailnet) as api:
            device_id = await _find_device_id_by_ip(api, slate_ts_ip)
            if not device_id:
                return None
            data = await api.device_routes(device_id)
            return list(data.get("enabledRoutes") or [])
    except TailscaleAdminAPIError:
        return None


async def _approve_routes_via_pat(
    request: Request, slate_ts_ip: str | None, routes: list[str],
) -> dict:
    """Approve `routes` on the Slate device via the tailnet admin API.

    Returns a dict describing the outcome — used by the response and
    the audit log. Never raises ; reports failure in the payload.
    """
    if not slate_ts_ip:
        return {"attempted": False, "reason": "slate tailscale ip unknown"}
    admin_store = _admin_store(request)
    pat = await admin_store.get_pat()
    if not pat:
        return {"attempted": False, "reason": "no PAT configured"}
    meta = await admin_store.get_metadata()
    tailnet = (meta or {}).get("tailnet") or "-"
    try:
        async with TailscaleAdminAPI(pat, tailnet) as api:
            device_id = await _find_device_id_by_ip(api, slate_ts_ip)
            if not device_id:
                return {"attempted": False, "reason": "Slate device not found in tailnet"}
            await api.set_device_routes(device_id, routes)
        return {"attempted": True, "approved": routes}
    except TailscaleAdminAPIError as exc:
        return {"attempted": True, "error": str(exc)}


async def _find_device_id_by_ip(
    api: TailscaleAdminAPI, ts_ip: str,
) -> str | None:
    """Match a Tailscale IP back to its tailnet device ID."""
    for d in await api.devices():
        addrs = d.get("addresses") or []
        if ts_ip in addrs:
            return str(d.get("id") or "") or None
    return None


def _ipv4(ips: list[str]) -> str | None:
    """Pick the IPv4 entry out of a TailscaleIPs list (skip ipv6 fd7a:...)."""
    for ip in ips or []:
        if "." in ip and ":" not in ip:
            return ip
    return None


# ---- Subnet routing inverse — LAN clients → tailnet peers --------------


@router.get("/app-presets")
async def get_app_presets(
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Catalogue of well-known application IP ranges (Netflix, Plex…).

    Each entry is a labelled bundle of CIDRs. The NetworkForm UI shows
    them as importable blocks ; on selection it expands the bundle into
    individual TailnetDestination rows tagged with the preset's id as
    `label`. Stateless read-only endpoint — the catalogue is hardcoded
    in `app.tailscale.app_presets` for the MVP and refreshed manually.
    """
    from app.tailscale.app_presets import to_api_payload  # noqa: PLC0415
    return {"presets": to_api_payload()}


@router.get("/destinations")
async def get_tailnet_destinations(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """List the tailnet subnets reachable from the Slate.

    Read `tailscale status --json`, walk every peer's `PrimaryRoutes`
    (subnets the peer advertises and that the tailnet admin has approved)
    and return them as flat entries. Used by the NetworkForm to populate
    its « destinations atteignables » list — the operator then ticks
    which ones THIS network should be allowed to reach.

    Peer-own IPs (the /32 and /128 entries that every peer carries for
    itself) are excluded so the UI doesn't display noise. CGNAT
    (100.64.0.0/10) entries are also filtered out — they're the peers'
    Tailscale own IPs, never useful as routed destinations.
    """
    import json as _json
    try:
        r = await ssh.run("tailscale status --json", timeout=15)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"tailscale status failed: {exc}",
        ) from exc
    try:
        data = _json.loads(r.stdout)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"tailscale status returned invalid JSON: {exc}",
        ) from exc

    out: list[dict] = []
    seen: set[str] = set()
    for _pid, p in (data.get("Peer") or {}).items():
        host = p.get("HostName") or p.get("DNSName") or "?"
        for cidr in p.get("PrimaryRoutes") or []:
            if cidr.endswith("/32") or cidr.endswith("/128"):
                continue
            if cidr.startswith("100."):
                continue
            if cidr in seen:
                for entry in out:
                    if entry["cidr"] == cidr and host not in entry["peers"]:
                        entry["peers"].append(host)
                continue
            seen.add(cidr)
            out.append({"cidr": cidr, "peers": [host]})
    out.sort(key=lambda e: e["cidr"])
    return {"destinations": out}


@router.get("/forwarding")
async def get_forwarding(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Live snapshot of the reverse-routing posture on the Slate.

    Lists every L3-managed local subnet and the (zone, dest_cidr) pairs
    currently active in iptables — both as ACCEPT forward rules and as
    SNAT rules. The fine-grained editor lives in the NetworkForm and
    persists into `NetworkRow.tailnet_destinations` ; this endpoint is
    read-only and used for diff/preview displays.
    """
    state = await discover_forwarding_state(ssh)
    return {
        "tailscale_zone_exists": state.tailscale_zone_exists,
        "tailscale_self_ip": state.tailscale_self_ip,
        "wan_iface": state.wan_iface,
        "proton_iface": state.proton_iface,
        "tor_active": state.tor_active,
        "subnets": [
            {
                "slug": s.slug,
                "zone": s.zone,
                "iface": s.iface,
                "cidr": s.cidr,
                "ipaddr": s.ipaddr,
            }
            for s in state.subnets
        ],
        "active_fwd": [
            {"zone": z, "dest_cidr": c} for z, c in sorted(state.active_fwd)
        ],
        "active_snat": [
            {"zone": z, "dest_cidr": c} for z, c in sorted(state.active_snat)
        ],
        "active_tor": [
            {"zone": z, "dest_cidr": c} for z, c in sorted(state.active_tor)
        ],
    }


@router.post("/forwarding/reconcile")
async def reconcile_forwarding_from_catalog(
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Recompute the reverse routing from the Network catalog and apply.

    Walks every Network row, reads its `tailnet_destinations` list, and
    builds the canonical (src_zone, src_cidr, dest_cidr, mode) rule set.
    Then calls the forwarding reconciler to align the live firewall.
    Idempotent. Called after a NetworkForm save and from any « re-pousser
    le routage LAN » button.
    """
    sf = make_session_factory(request.app.state.db_engine)
    from app.networks.store import NetworkStore  # noqa: PLC0415 - lazy
    nets = await NetworkStore(sf).list_all()
    state = await discover_forwarding_state(ssh)
    zone_by_slug = {s.slug: s.zone for s in state.subnets}
    cidr_by_slug = {s.slug: s.cidr for s in state.subnets}

    rules: list[TailnetDesiredRule] = []
    for n in nets:
        src_zone = zone_by_slug.get(n.slug)
        src_cidr = cidr_by_slug.get(n.slug) or n.subnet_cidr
        if not src_zone:
            continue
        for dest in n.tailnet_destinations or []:
            cidr = dest.cidr if hasattr(dest, "cidr") else dest.get("cidr")
            mode = dest.mode if hasattr(dest, "mode") else dest.get("mode")
            via = (
                dest.via if hasattr(dest, "via") else dest.get("via", "tailnet")
            )
            if not cidr or mode not in ("routed", "snat"):
                continue
            if via not in ("tailnet", "wan", "proton", "tor"):
                via = "tailnet"
            rules.append(
                TailnetDesiredRule(
                    src_zone=src_zone,
                    src_cidr=src_cidr,
                    dest_cidr=cidr,
                    mode=mode,
                    via=via,
                )
            )

    try:
        report = await apply_forwarding_state(ssh, desired_rules=rules)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc),
        ) from exc
    logger.info(
        "tailscale.forwarding.reconciled",
        username=user.username,
        rules=len(rules),
        snat=sum(1 for r in rules if r.mode == "snat"),
        routed=sum(1 for r in rules if r.mode == "routed"),
    )
    return report


@router.get("/dns-routing")
async def get_dns_routing(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Live snapshot of the DNS-based routing posture on the Slate.

    Reports whether ipset is installed, where dnsmasq lives, the list of
    active `slate_*` ipsets, and the (zone, label) pairs currently
    matched by a mangle MARK rule. Used by the UI to flag config drift
    and by the reconciler as the starting point of a diff/apply cycle.
    """
    state = await discover_dns_routing_state(ssh)
    return {
        "ipset_installed": state.ipset_installed,
        "dnsmasq_path": state.dnsmasq_path,
        "active_ipsets": sorted(state.active_ipsets),
        "active_marks": [
            {"zone": z, "label": l} for z, l in sorted(state.active_marks)
        ],
    }


@router.post("/dns-routing/reconcile")
async def reconcile_dns_routing_from_catalog(
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Recompute every DNS routing rule from the Network catalog and apply.

    Walks every Network row, reads its `domain_routing_rules` list,
    resolves the egress iface + SNAT source from the Slate's live
    topology (Tailscale daemon, WAN default route, etc.) and builds the
    canonical (zone, label, domains, via, mode, …) rule set. Then calls
    the dns_routing reconciler. Idempotent.
    """
    sf = make_session_factory(request.app.state.db_engine)
    from app.networks.store import NetworkStore  # noqa: PLC0415 - lazy
    nets = await NetworkStore(sf).list_all()
    fwd_state = await discover_forwarding_state(ssh)
    zone_by_slug = {s.slug: s.zone for s in fwd_state.subnets}
    iface_by_slug = {s.slug: s.iface for s in fwd_state.subnets}

    rules: list[DesiredDomainRule] = []
    for n in nets:
        zone = zone_by_slug.get(n.slug)
        iface = iface_by_slug.get(n.slug)
        if not zone or not iface:
            continue
        for raw in n.domain_routing_rules or []:
            r = raw if hasattr(raw, "label") else type(
                "_R", (object,), {
                    "label": raw.get("label"),
                    "domains": raw.get("domains") or [],
                    "mode": raw.get("mode") or "snat",
                    "via": raw.get("via") or "tailnet",
                },
            )
            if not r.label or not r.domains:
                continue
            mode = r.mode if r.mode in ("routed", "snat") else "snat"
            via = r.via if r.via in ("tailnet", "wan", "proton", "tor") else "tailnet"
            # Resolve the egress runtime info from the snapshot.
            if via == "tailnet":
                if not fwd_state.tailscale_zone_exists or not fwd_state.tailscale_self_ip:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=(
                            f"rule '{r.label}' asks for via=tailnet but "
                            "Tailscale isn't ready (no zone or no self IP)."
                        ),
                    )
                egress = "tailscale0"
                snat_ip = fwd_state.tailscale_self_ip or ""
            elif via == "wan":
                if not fwd_state.wan_iface:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"rule '{r.label}' asks for via=wan but no WAN iface detected.",
                    )
                egress = fwd_state.wan_iface
                snat_ip = ""
            elif via == "proton":
                if not fwd_state.proton_iface:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"rule '{r.label}' asks for via=proton but no Proton tunnel found.",
                    )
                egress = fwd_state.proton_iface
                snat_ip = ""
            else:
                # via=tor : DNS-based Tor routing not supported in this MVP.
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"rule '{r.label}' uses via=tor — not supported "
                        "via DNS routing yet. Use the per-network Tor "
                        "switch (tor_route_mode) instead."
                    ),
                )
            rules.append(
                DesiredDomainRule(
                    zone=zone,
                    src_iface=iface,
                    label=r.label,
                    domains=list(r.domains),
                    mode=mode,
                    via=via,
                    egress_iface=egress,
                    egress_snat_ip=snat_ip,
                )
            )

    try:
        report = await apply_dns_routing_state(ssh, desired=rules)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc),
        ) from exc
    logger.info(
        "tailscale.dns_routing.reconciled",
        username=user.username,
        rules=len(rules),
    )
    return report


@router.post("/connect", response_model=TailscaleConnectResponse)
async def connect(
    body: TailscaleConfigInput,
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> TailscaleConnectResponse:
    """Run `tailscale up` with the supplied (or stored) auth key + options.

    If `auth_key` is omitted in the body, we reuse the one previously stored.
    Returns the resulting status + an `auth_url` if Tailscale prompted for
    browser-based login (happens when the key is invalid/expired and no
    other auth is set).
    """
    store = _store(request)
    auth_key = body.auth_key
    if not auth_key:
        auth_key = await store.get_auth_key()
    if not auth_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="auth_key required (none stored yet)",
        )

    cfg = body.model_copy(update={"auth_key": auth_key})
    client = TailscaleClient(ssh)
    success, note, auth_url = await client.connect(cfg)

    # Persist config + key only if at least the daemon started; we accept a
    # browser-auth required state as "partially configured" too.
    cfg_to_store = body.model_dump(exclude={"auth_key"})
    await store.save(
        auth_key=body.auth_key,  # only store new key if user provided one
        last_applied_config=cfg_to_store,
    )
    logger.info(
        "tailscale.connect",
        username=user.username,
        success=success,
        has_auth_url=bool(auth_url),
        advertise_routes=cfg.advertise_routes,
        advertise_exit_node=cfg.advertise_exit_node,
        exit_node=cfg.exit_node,
    )

    return TailscaleConnectResponse(
        success=success,
        status=await client.get_status(),
        note=note,
        auth_url=auth_url,
    )


@router.post("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """`tailscale down` — keep daemon running, just leave the tailnet."""
    ok, note = await TailscaleClient(ssh).disconnect()
    logger.info("tailscale.disconnect", username=user.username, ok=ok, note=note)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Wipe device identity on the Slate AND clear stored auth key."""
    ok, note = await TailscaleClient(ssh).logout()
    await _store(request).clear()
    logger.warning(
        "tailscale.logout", username=user.username, ok=ok, note=note
    )


@router.get("/audit")
async def audit(
    request: Request,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Combined Tailscale security audit (local + cloud if PAT configured).

    Returns a score 0-100, grade A-F, and a list of findings with
    severity, evidence, and remediation. Cloud checks (ACL, key hygiene,
    device approval, etc.) are added automatically when a PAT is saved
    via POST /api/tailscale/admin/pat. See app.tailscale.audit for the
    check catalog.
    """
    report = await TailscaleAuditor(
        ssh, _store(request), _admin_store(request),
    ).run()
    logger.info(
        "tailscale.audit",
        username=user.username,
        score=report.score, grade=report.grade,
        fail=report.fail_count, warn=report.warn_count, pass_=report.pass_count,
    )
    return {
        "score": report.score,
        "grade": report.grade,
        "pass_count": report.pass_count,
        "fail_count": report.fail_count,
        "warn_count": report.warn_count,
        "generated_at": report.generated_at.isoformat(),
        "raw_summary": report.raw_summary,
        "findings": [
            {
                "id": f.id, "label": f.label,
                "status": f.status, "severity": f.severity,
                "evidence": f.evidence,
                "recommendation": f.recommendation,
                "fix_available": f.fix_available,
            }
            for f in report.findings
        ],
    }


@router.post("/audit/fix")
async def fix_audit_finding(
    finding_id: str,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Apply the controller's auto-fix for a specific Tailscale audit
    finding. Only a small set of finding ids are auto-fixable (see
    ``TAILSCALE_FIXABLE_IDS`` in the audit module) — the rest require
    admin-console actions (ACL edits, device approval, MagicDNS toggle).
    """
    from app.tailscale.audit import TAILSCALE_FIXABLE_IDS
    from app.tailscale.client import TailscaleClient

    if finding_id not in TAILSCALE_FIXABLE_IDS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Pas de fix automatique pour « {finding_id} » — voir la note "
                "de remediation pour l'action requise."
            ),
        )

    client = TailscaleClient(ssh)
    ok = False
    message = ""

    if finding_id in {"daemon_running", "uci_enable"}:
        # Both fixes converge on the same action : ensure UCI says enable
        # AND the procd service is up. Idempotent.
        try:
            r = await ssh.run(
                "uci -q set tailscale.@tailscale[0].enabled=1 && "
                "uci commit tailscale && "
                "/etc/init.d/tailscale enable >/dev/null 2>&1; "
                "/etc/init.d/tailscale start 2>&1 && echo OK",
                timeout=20,
            )
            ok = r.exit_status == 0 and "OK" in (r.stdout or "")
            message = (r.stdout or "")[-300:] or "Daemon démarré"
        except Exception as exc:  # noqa: BLE001
            ok = False
            message = f"SSH error: {exc}"
    elif finding_id == "shields_up":
        # tailscale set --shields-up=true via the existing client helper.
        ok, errors = await client.apply_overrides(shields_up=True)
        message = "shields_up activé" if ok else "; ".join(errors)

    logger.info(
        "tailscale.audit.fix",
        username=user.username, finding=finding_id, ok=ok,
    )
    return {"ok": ok, "finding_id": finding_id, "message": message}


class HAConfigPatch(BaseModel):
    enabled: bool | None = None
    candidates: list[str] | None = None
    check_interval_seconds: int | None = None
    failsafe_mode: str | None = None  # "fail_open" | "keep"


@router.get("/ha")
async def get_ha(
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Full HA state (config + last-tick runtime)."""
    return await _ha_store(request).get()


@router.post("/ha")
async def set_ha(
    body: HAConfigPatch,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Patch enabled / candidates / interval. Watchdog picks it up next tick."""
    if body.check_interval_seconds is not None and not (
        MIN_CHECK_INTERVAL <= body.check_interval_seconds <= MAX_CHECK_INTERVAL
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"check_interval_seconds must be in [{MIN_CHECK_INTERVAL}, {MAX_CHECK_INTERVAL}]",
        )
    if body.failsafe_mode is not None and body.failsafe_mode not in FAILSAFE_MODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"failsafe_mode must be one of {list(FAILSAFE_MODES)}",
        )
    new_state = await _ha_store(request).update_config(
        enabled=body.enabled,
        candidates=body.candidates,
        check_interval_seconds=body.check_interval_seconds,
        failsafe_mode=body.failsafe_mode,
    )
    logger.info(
        "tailscale.ha.config_updated",
        username=user.username,
        enabled=new_state["enabled"],
        candidates=new_state["candidates"],
        interval=new_state["check_interval_seconds"],
    )
    return new_state


class AdminPatRequest(BaseModel):
    pat: str
    tailnet: str | None = None  # "-" by default ⇒ token's home tailnet


@router.get("/admin/pat")
async def get_admin_pat_status(
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Return PAT configuration metadata (NEVER the PAT itself)."""
    return await _admin_store(request).get_metadata()


@router.post("/admin/pat")
async def set_admin_pat(
    body: AdminPatRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Store a Tailscale admin PAT after verifying it against the API.

    The PAT is encrypted at rest. We refuse to store an invalid token —
    a 401 from /devices means the user typed something wrong (or used
    an OAuth client secret instead of an API access token).
    """
    pat = body.pat.strip()
    if not pat:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="pat required",
        )
    # Tailscale credentials all start with `tskey-` but only `-api-` works as
    # a Bearer token. Detect mismatch BEFORE round-tripping to api.tailscale.com
    # so the user gets a useful message instead of a generic 401.
    if pat.startswith("tskey-auth-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Ce token est une AUTH KEY (sert à enrôler des devices). "
                "Il faut un API access token: admin.tailscale.com → "
                "Settings → Keys → onglet 'API access tokens' → "
                "Generate access token. Le préfixe sera tskey-api-..."
            ),
        )
    if pat.startswith("tskey-client-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Ce token est un OAUTH CLIENT SECRET. Il nécessite un "
                "client_credentials exchange avant utilisation (pas géré "
                "pour l'instant). Génère plutôt un 'API access token' "
                "(tskey-api-...) dans admin.tailscale.com → Settings → Keys."
            ),
        )
    tailnet = (body.tailnet or "-").strip() or "-"
    try:
        async with TailscaleAdminAPI(pat, tailnet) as api:
            who = await api.whoami()
    except TailscaleAdminAPIError as exc:
        hint = ""
        if exc.status_code == 401:
            hint = (
                " — Token rejeté. Causes fréquentes: (1) token expiré "
                "(régénérer dans admin.tailscale.com → Keys), "
                "(2) copié avec espace/retour de ligne en début/fin, "
                "(3) scopes insuffisants si scoped token (cocher devices:read)."
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"PAT validation failed: {exc}{hint}",
        ) from exc
    detected_tailnet = who.get("tailnet") or tailnet
    await _admin_store(request).save(pat, detected_tailnet)
    logger.info(
        "tailscale.admin_pat.saved",
        username=user.username,
        tailnet=detected_tailnet,
        devices=who.get("device_count", 0),
    )
    return {
        "configured": True,
        "tailnet": detected_tailnet,
        "device_count": who.get("device_count", 0),
    }


@router.delete("/admin/pat", status_code=status.HTTP_204_NO_CONTENT)
async def delete_admin_pat(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await _admin_store(request).clear()
    logger.warning("tailscale.admin_pat.cleared", username=user.username)


class PingRequest(BaseModel):
    target: str
    mode: str = "icmp"   # "icmp" or "tailscale"
    count: int = 3


class PingResponse(BaseModel):
    ok: bool
    output: str
    target: str
    mode: str


class TracerouteRequest(BaseModel):
    target: str
    max_hops: int = 15


class TracerouteResponse(BaseModel):
    ok: bool
    output: str
    target: str
    max_hops: int


@router.post("/traceroute", response_model=TracerouteResponse)
async def traceroute(
    body: TracerouteRequest,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> TracerouteResponse:
    """Trace L3 path from the Slate to a target (busybox-style traceroute).

    Useful complement to ping when reachability fails — shows where the
    packet gets dropped (e.g. did it leave the Slate? did it reach the
    Tailscale exit? did it die in the home firewall?).
    """
    target = (body.target or "").strip()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="target required",
        )
    ok, output = await TailscaleClient(ssh).traceroute(target, max_hops=body.max_hops)
    logger.info(
        "tailscale.traceroute",
        username=user.username, target=target, max_hops=body.max_hops, ok=ok,
    )
    return TracerouteResponse(
        ok=ok, output=output, target=target, max_hops=body.max_hops,
    )


@router.post("/ping", response_model=PingResponse)
async def ping(
    body: PingRequest,
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> PingResponse:
    """Run an ICMP or Tailscale-overlay ping from the Slate to a target.

    Use case: after configuring Tailscale, verify that a peer / subnet route
    is actually reachable. ICMP mode works for any host; Tailscale mode
    reports the direct-vs-DERP relay path and is tailnet-specific.
    """
    target = (body.target or "").strip()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="target required",
        )
    mode = body.mode if body.mode in ("icmp", "tailscale") else "icmp"
    ok, output = await TailscaleClient(ssh).ping(target, mode=mode, count=body.count)
    logger.info(
        "tailscale.ping",
        username=user.username, target=target, mode=mode, ok=ok,
    )
    return PingResponse(ok=ok, output=output, target=target, mode=mode)
