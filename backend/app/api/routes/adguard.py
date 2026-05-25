"""AdGuard Home control endpoints (Protection > AdGuard)."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl

from app.adguard.feeds import CATALOG as FEED_CATALOG
from app.adguard.feeds import get_feed
from app.adguard.manager import (
    ADGUARD_HTTP_PORT,
    AdGuardError,
    AdGuardManager,
)
from app.api.deps import get_adguard_manager
from app.auth import User, get_current_user

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/adguard", tags=["adguard"])


# ---------------------------- Pydantic IO ---------------------------- #


class AdGuardStatusResponse(BaseModel):
    uci_enabled: bool
    init_running: bool
    web_ui_reachable: bool
    web_ui_url: str
    protection_enabled: bool | None
    dns_port: int | None
    version: str | None
    http_port: int = ADGUARD_HTTP_PORT
    error: str | None


class ToggleRequest(BaseModel):
    enabled: bool


class ProtectionRequest(BaseModel):
    enabled: bool


class DnssecRequest(BaseModel):
    enabled: bool


class DnssecStatusResponse(BaseModel):
    enabled: bool
    upstream_dns: list[str]
    fallback_dns: list[str]


class StatsResponse(BaseModel):
    num_dns_queries: int
    num_blocked_filtering: int
    num_replaced_safebrowsing: int
    num_replaced_parental: int
    avg_processing_time_ms: float
    top_queried_domains: list[dict[str, int]]
    top_blocked_domains: list[dict[str, int]]
    top_clients: list[dict[str, int]]


class FilterPublic(BaseModel):
    id: int
    name: str
    url: str
    enabled: bool
    rules_count: int
    last_updated: str | None


class AddFilterRequest(BaseModel):
    url: HttpUrl
    name: str = Field(min_length=1, max_length=120)


class ToggleFilterRequest(BaseModel):
    url: HttpUrl
    enabled: bool


class FeedEntryPublic(BaseModel):
    slug: str
    name: str
    description: str
    url: str
    category: str
    maintainer: str
    intensity: str
    recommended: bool
    # True if this feed is currently in AdGuard's active filter list.
    active: bool = False


class ApplyFeedsRequest(BaseModel):
    slugs: list[str] = Field(
        description="Feed slugs from the catalog to enable on AdGuard. Adds them"
        " if missing, enables them if already present. Other existing filters"
        " are left untouched.",
    )


# ---------------------------- endpoints ---------------------------- #


@router.get("/status", response_model=AdGuardStatusResponse)
async def get_status(
    _user: Annotated[User, Depends(get_current_user)],
    manager: Annotated[AdGuardManager, Depends(get_adguard_manager)],
) -> AdGuardStatusResponse:
    s = await manager.get_status()
    return AdGuardStatusResponse(
        uci_enabled=s.uci_enabled,
        init_running=s.init_running,
        web_ui_reachable=s.web_ui_reachable,
        web_ui_url=s.web_ui_url,
        protection_enabled=s.protection_enabled,
        dns_port=s.dns_port,
        version=s.version,
        error=s.error,
    )


@router.post("/toggle", response_model=AdGuardStatusResponse)
async def toggle(
    body: ToggleRequest,
    _user: Annotated[User, Depends(get_current_user)],
    manager: Annotated[AdGuardManager, Depends(get_adguard_manager)],
) -> AdGuardStatusResponse:
    """Enable or disable AdGuard Home (UCI + init.d enable/disable + start/stop).

    On enable, if AdGuard's REST API rejects our credentials (fresh install
    with empty `users:`), automatically inject the controller's admin user
    so the API becomes usable without a manual wizard.
    """
    try:
        await manager.set_enabled(body.enabled)
    except AdGuardError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    logger.info("adguard.toggle", enabled=body.enabled)

    if body.enabled:
        # Wait briefly for the daemon to bind, then bootstrap if needed.
        import asyncio as _asyncio

        for _ in range(10):
            await _asyncio.sleep(0.5)
            if await manager.is_admin_provisioned():
                break
        else:
            try:
                await manager.bootstrap_admin()
            except AdGuardError as exc:
                logger.warning("adguard.bootstrap_failed", error=str(exc))

    s = await manager.get_status()
    return AdGuardStatusResponse(
        uci_enabled=s.uci_enabled,
        init_running=s.init_running,
        web_ui_reachable=s.web_ui_reachable,
        web_ui_url=s.web_ui_url,
        protection_enabled=s.protection_enabled,
        dns_port=s.dns_port,
        version=s.version,
        error=s.error,
    )


@router.post("/bootstrap", response_model=AdGuardStatusResponse)
async def bootstrap(
    _user: Annotated[User, Depends(get_current_user)],
    manager: Annotated[AdGuardManager, Depends(get_adguard_manager)],
) -> AdGuardStatusResponse:
    """Force-inject our admin user into AdGuard's config.yaml.

    Idempotent. Useful when AdGuard rejects our credentials after firmware
    upgrade or when the user manually reset AdGuard's config.
    """
    try:
        await manager.bootstrap_admin()
    except AdGuardError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    s = await manager.get_status()
    return AdGuardStatusResponse(
        uci_enabled=s.uci_enabled,
        init_running=s.init_running,
        web_ui_reachable=s.web_ui_reachable,
        web_ui_url=s.web_ui_url,
        protection_enabled=s.protection_enabled,
        dns_port=s.dns_port,
        version=s.version,
        error=s.error,
    )


@router.post("/protection", response_model=AdGuardStatusResponse)
async def set_protection(
    body: ProtectionRequest,
    _user: Annotated[User, Depends(get_current_user)],
    manager: Annotated[AdGuardManager, Depends(get_adguard_manager)],
) -> AdGuardStatusResponse:
    """Flip AdGuard's runtime 'protection' switch without restarting the daemon."""
    try:
        await manager.set_protection(body.enabled)
    except AdGuardError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    s = await manager.get_status()
    return AdGuardStatusResponse(
        uci_enabled=s.uci_enabled,
        init_running=s.init_running,
        web_ui_reachable=s.web_ui_reachable,
        web_ui_url=s.web_ui_url,
        protection_enabled=s.protection_enabled,
        dns_port=s.dns_port,
        version=s.version,
        error=s.error,
    )


@router.get("/dnssec", response_model=DnssecStatusResponse)
async def get_dnssec(
    _user: Annotated[User, Depends(get_current_user)],
    manager: Annotated[AdGuardManager, Depends(get_adguard_manager)],
) -> DnssecStatusResponse:
    """Report whether AdGuard validates DNSSEC + which upstreams it uses."""
    try:
        cfg = await manager.get_dns_config()
    except AdGuardError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return DnssecStatusResponse(
        enabled=bool(cfg.get("enable_dnssec", False)),
        upstream_dns=list(cfg.get("upstream_dns", []) or []),
        fallback_dns=list(cfg.get("fallback_dns", []) or []),
    )


@router.post("/dnssec", response_model=DnssecStatusResponse)
async def set_dnssec(
    body: DnssecRequest,
    _user: Annotated[User, Depends(get_current_user)],
    manager: Annotated[AdGuardManager, Depends(get_adguard_manager)],
) -> DnssecStatusResponse:
    """Toggle AdGuard's local DNSSEC validation.

    With this on, the controller catches RRSIG mismatches itself instead
    of blindly trusting the upstream — closing the BGP-hijack-of-upstream
    + cache-poisoning gaps. ~0.5% of domains have broken DNSSEC and will
    SERVFAIL; that's by design.
    """
    try:
        cfg = await manager.set_dnssec_enabled(body.enabled)
    except AdGuardError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return DnssecStatusResponse(
        enabled=bool(cfg.get("enable_dnssec", False)),
        upstream_dns=list(cfg.get("upstream_dns", []) or []),
        fallback_dns=list(cfg.get("fallback_dns", []) or []),
    )


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    _user: Annotated[User, Depends(get_current_user)],
    manager: Annotated[AdGuardManager, Depends(get_adguard_manager)],
) -> StatsResponse:
    try:
        s = await manager.get_stats()
    except AdGuardError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return StatsResponse(
        num_dns_queries=s.num_dns_queries,
        num_blocked_filtering=s.num_blocked_filtering,
        num_replaced_safebrowsing=s.num_replaced_safebrowsing,
        num_replaced_parental=s.num_replaced_parental,
        avg_processing_time_ms=s.avg_processing_time_ms,
        top_queried_domains=s.top_queried_domains,
        top_blocked_domains=s.top_blocked_domains,
        top_clients=s.top_clients,
    )


@router.get("/filters", response_model=list[FilterPublic])
async def list_filters(
    _user: Annotated[User, Depends(get_current_user)],
    manager: Annotated[AdGuardManager, Depends(get_adguard_manager)],
) -> list[FilterPublic]:
    try:
        filters = await manager.list_filters()
    except AdGuardError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return [
        FilterPublic(
            id=f.id,
            name=f.name,
            url=f.url,
            enabled=f.enabled,
            rules_count=f.rules_count,
            last_updated=f.last_updated,
        )
        for f in filters
    ]


@router.post("/filters", response_model=list[FilterPublic], status_code=status.HTTP_201_CREATED)
async def add_filter(
    body: AddFilterRequest,
    _user: Annotated[User, Depends(get_current_user)],
    manager: Annotated[AdGuardManager, Depends(get_adguard_manager)],
) -> list[FilterPublic]:
    try:
        await manager.add_filter(url=str(body.url), name=body.name)
    except AdGuardError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    logger.info("adguard.filter.added", name=body.name, url=str(body.url))
    filters = await manager.list_filters()
    return [
        FilterPublic(
            id=f.id, name=f.name, url=f.url, enabled=f.enabled,
            rules_count=f.rules_count, last_updated=f.last_updated,
        )
        for f in filters
    ]


@router.patch("/filters", response_model=list[FilterPublic])
async def toggle_filter(
    body: ToggleFilterRequest,
    _user: Annotated[User, Depends(get_current_user)],
    manager: Annotated[AdGuardManager, Depends(get_adguard_manager)],
) -> list[FilterPublic]:
    try:
        await manager.set_filter_enabled(url=str(body.url), enabled=body.enabled)
    except AdGuardError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    filters = await manager.list_filters()
    return [
        FilterPublic(
            id=f.id, name=f.name, url=f.url, enabled=f.enabled,
            rules_count=f.rules_count, last_updated=f.last_updated,
        )
        for f in filters
    ]


@router.delete("/filters", response_model=list[FilterPublic])
async def remove_filter(
    url: str,
    _user: Annotated[User, Depends(get_current_user)],
    manager: Annotated[AdGuardManager, Depends(get_adguard_manager)],
) -> list[FilterPublic]:
    try:
        await manager.remove_filter(url=url)
    except AdGuardError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    logger.info("adguard.filter.removed", url=url)
    filters = await manager.list_filters()
    return [
        FilterPublic(
            id=f.id, name=f.name, url=f.url, enabled=f.enabled,
            rules_count=f.rules_count, last_updated=f.last_updated,
        )
        for f in filters
    ]


@router.get("/feeds/catalog", response_model=list[FeedEntryPublic])
async def feed_catalog(
    _user: Annotated[User, Depends(get_current_user)],
    manager: Annotated[AdGuardManager, Depends(get_adguard_manager)],
) -> list[FeedEntryPublic]:
    """Return the curated feed catalog with active-state per AdGuard's current filter list."""
    active_urls: set[str] = set()
    try:
        active = await manager.list_filters()
        active_urls = {f.url for f in active if f.enabled}
    except AdGuardError:
        # AdGuard down — return the catalog anyway with active=False.
        pass
    return [
        FeedEntryPublic(
            slug=f.slug,
            name=f.name,
            description=f.description,
            url=f.url,
            category=f.category,
            maintainer=f.maintainer,
            intensity=f.intensity,
            recommended=f.recommended,
            active=f.url in active_urls,
        )
        for f in FEED_CATALOG
    ]


@router.post("/feeds/apply", response_model=list[FilterPublic])
async def apply_feeds(
    body: ApplyFeedsRequest,
    _user: Annotated[User, Depends(get_current_user)],
    manager: Annotated[AdGuardManager, Depends(get_adguard_manager)],
) -> list[FilterPublic]:
    """Bulk-enable the requested catalog feeds on AdGuard.

    Adds missing ones, re-enables disabled-but-present ones. Idempotent.
    Does NOT remove other filters — call DELETE /filters for that.
    """
    try:
        existing = await manager.list_filters()
    except AdGuardError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    existing_by_url = {f.url: f for f in existing}

    for slug in body.slugs:
        feed = get_feed(slug)
        if feed is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown feed slug: {slug!r}",
            )
        try:
            if feed.url in existing_by_url:
                if not existing_by_url[feed.url].enabled:
                    await manager.set_filter_enabled(url=feed.url, enabled=True)
            else:
                await manager.add_filter(url=feed.url, name=feed.name)
        except AdGuardError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"apply {slug}: {exc}",
            ) from exc

    logger.info("adguard.feeds.applied", slugs=body.slugs)
    filters = await manager.list_filters()
    return [
        FilterPublic(
            id=f.id, name=f.name, url=f.url, enabled=f.enabled,
            rules_count=f.rules_count, last_updated=f.last_updated,
        )
        for f in filters
    ]


@router.post("/filters/refresh", status_code=status.HTTP_202_ACCEPTED)
async def refresh_filters(
    _user: Annotated[User, Depends(get_current_user)],
    manager: Annotated[AdGuardManager, Depends(get_adguard_manager)],
) -> dict[str, bool]:
    try:
        await manager.refresh_filters()
    except AdGuardError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    logger.info("adguard.filter.refresh")
    return {"refreshed": True}


@router.get("/blocked-services/catalog")
async def list_blocked_services_catalog(
    _user: Annotated[User, Depends(get_current_user)],
    manager: Annotated[AdGuardManager, Depends(get_adguard_manager)],
) -> dict[str, list[dict]]:
    """List the authoritative AdGuard `blocked_services` catalog (~118 IDs).

    Read-only proxy over AdGuard's `/control/blocked_services/all`. Used by
    the DNS security level editor so the UI offers a multi-select of valid
    service IDs — picking an unknown ID triggers HTTP 400 on AdGuard's side
    on client add/update, which is what we want to prevent upfront.

    Icon SVGs (1-5 KB each, 500 KB total) are stripped — the multi-select
    only needs id + name.
    """
    try:
        resp = await manager._http.get("/control/blocked_services/all")
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AdGuard /control/blocked_services/all failed: {exc}",
        ) from exc
    services = data.get("blocked_services") if isinstance(data, dict) else data
    if not isinstance(services, list):
        services = []
    slim = [
        {
            "id": s.get("id") if isinstance(s, dict) else s,
            "name": s.get("name") if isinstance(s, dict) else s,
        }
        for s in services
    ]
    return {"services": slim}
