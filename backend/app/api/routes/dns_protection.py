"""REST endpoints for DNS protection (Protection > DNS in the UI).

Exposes:
  GET  /api/dns/catalog                    — list curated DNS providers
  GET  /api/dns/security-levels            — list security level presets
  GET  /api/dns/protections                — list active mappings (network → level)
  GET  /api/dns/protections/{network}      — get one mapping
  PUT  /api/dns/protections/{network}      — set/replace mapping (apply on AdGuard)
  DELETE /api/dns/protections/{network}    — remove mapping + AdGuard client
  POST /api/dns/protections/reapply        — re-push all to AdGuard (post-restart)
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import (
    get_dns_protection_manager,
    get_dns_security_level_store,
    get_slate_ssh,
)
from app.auth import User, get_current_user
from app.dns.catalog import CATALOG, filter_providers
from app.adguard.manager import AdGuardError
from app.dns.manager import (
    DnsProtectionError,
    DnsProtectionManager,
    NetworkProtection,
    reapply_protections_using_level,
)
from app.dns.security_levels import (
    SecurityLevel,
    validate_provider_for_level,
)
from app.dns.store import EDITABLE_FIELDS, DnsSecurityLevelStore
from app.firewall.dns_anti_bypass import (
    AntiBypassError,
    disable as anti_bypass_disable,
    enable as anti_bypass_enable,
    get_status as anti_bypass_status,
)
from app.networks.store import NetworkNotFoundError
from app.slate.ssh import SlateSSH

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/dns", tags=["dns"])


class ProtectionRequest(BaseModel):
    """Body for PUT /api/dns/protections/{network}."""

    model_config = ConfigDict(extra="forbid")

    level_slug: str = Field(min_length=1, max_length=32)
    provider_slug: str | None = Field(
        default=None,
        description="Override the level's default provider. Must be in the level's allowed list.",
        max_length=64,
    )


@router.get("/catalog")
async def get_catalog(
    _user: Annotated[User, Depends(get_current_user)],
    eu_only: bool = False,
    filter_profile: str | None = None,
) -> dict:
    """List curated DNS providers, optionally filtered."""
    providers = filter_providers(
        eu_only=eu_only if eu_only else None,
        filter_profile=filter_profile,  # type: ignore[arg-type]
    ) if (eu_only or filter_profile) else CATALOG
    return {
        "providers": [_provider_to_dict(p) for p in providers],
        "total": len(providers),
    }


@router.get("/security-levels")
async def get_security_levels(
    levels: Annotated[DnsSecurityLevelStore, Depends(get_dns_security_level_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """List all security levels (factory seed + any user edits)."""
    items = await levels.list_all()
    return {"levels": [_level_to_dict(level) for level in items]}


class LevelPatch(BaseModel):
    """PATCH body for /api/dns/security-levels/{slug}.

    Every field is optional — only what's present is mutated. Validation
    (provider exists, allowed in level, etc.) runs after merge.
    """

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=512)
    default_provider_slug: str | None = Field(default=None, max_length=64)
    allowed_provider_slugs: list[str] | None = Field(default=None)
    adguard_filtering: bool | None = None
    safe_browsing: bool | None = None
    parental_control: bool | None = None
    safe_search: bool | None = None
    blocked_services: list[str] | None = Field(default=None)
    adguard_blocklist_slugs: list[str] | None = Field(default=None)
    require_dot: bool | None = None
    require_dnssec: bool | None = None
    eu_only: bool | None = None


@router.patch("/security-levels/{slug}")
async def patch_security_level(
    slug: str,
    body: LevelPatch,
    levels: Annotated[DnsSecurityLevelStore, Depends(get_dns_security_level_store)],
    manager: Annotated[DnsProtectionManager, Depends(get_dns_protection_manager)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Edit one or more fields of a security level. Triggers a re-apply
    to AdGuard for every network using this level — so the new config
    propagates instantly without manual reapply.

    Validates that:
      - `default_provider_slug`, if set, exists in the catalog AND is in
        the (potentially also-being-updated) allowed list
      - the level's constraints (`require_dot`, `eu_only`, `require_dnssec`)
        are satisfied by the chosen default provider
    """
    current = await levels.get(slug)
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"level '{slug}' not found",
        )

    # Build the patch dict, dropping unset fields.
    patch = {k: v for k, v in body.model_dump().items() if v is not None}

    # Validate the resulting state (after applying patch on top of current).
    merged = SecurityLevel(
        slug=current.slug,  # type: ignore[arg-type]
        name=current.name,
        description=patch.get("description", current.description),
        icon=current.icon,
        color=current.color,
        default_provider_slug=patch.get("default_provider_slug", current.default_provider_slug),
        allowed_provider_slugs=patch.get("allowed_provider_slugs", current.allowed_provider_slugs),
        adguard_filtering=patch.get("adguard_filtering", current.adguard_filtering),
        safe_browsing=patch.get("safe_browsing", current.safe_browsing),
        parental_control=patch.get("parental_control", current.parental_control),
        safe_search=patch.get("safe_search", current.safe_search),
        blocked_services=patch.get("blocked_services", current.blocked_services),
        adguard_blocklist_slugs=patch.get("adguard_blocklist_slugs", current.adguard_blocklist_slugs),
        require_dot=patch.get("require_dot", current.require_dot),
        require_dnssec=patch.get("require_dnssec", current.require_dnssec),
        eu_only=patch.get("eu_only", current.eu_only),
        intensity=current.intensity,  # type: ignore[arg-type]
    )
    err = validate_provider_for_level(merged, merged.default_provider_slug)
    if err is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"default_provider invalid after edit: {err}",
        )

    updated = await levels.update(slug, patch)
    # Propagate to AdGuard so live networks pick up the new config.
    rep = await reapply_protections_using_level(manager=manager, level_slug=slug)
    logger.info(
        "dns.security_level.patched",
        username=user.username, slug=slug,
        fields=list(patch.keys()), reapplied=len(rep.applied), errors=len(rep.errors),
    )
    return {
        "level": _level_to_dict(updated),
        "reapply": {
            "ok": rep.ok,
            "applied": rep.applied,
            "errors": rep.errors,
        },
    }


@router.post("/security-levels/{slug}/reset")
async def reset_security_level(
    slug: str,
    levels: Annotated[DnsSecurityLevelStore, Depends(get_dns_security_level_store)],
    manager: Annotated[DnsProtectionManager, Depends(get_dns_protection_manager)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Restore a level to its factory defaults. Also re-applies to AdGuard."""
    try:
        restored = await levels.reset_to_factory(slug)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"level '{slug}' is not a factory preset",
        ) from exc
    rep = await reapply_protections_using_level(manager=manager, level_slug=slug)
    logger.info(
        "dns.security_level.reset",
        username=user.username, slug=slug, reapplied=len(rep.applied),
    )
    return {
        "level": _level_to_dict(restored),
        "reapply": {
            "ok": rep.ok,
            "applied": rep.applied,
            "errors": rep.errors,
        },
    }


@router.get("/protections")
async def list_protections(
    manager: Annotated[DnsProtectionManager, Depends(get_dns_protection_manager)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """List every persisted network → level mapping."""
    items = await manager.list_protections()
    return {"protections": [_protection_to_dict(p) for p in items]}


@router.get("/protections/{network_slug}")
async def get_protection(
    network_slug: str,
    manager: Annotated[DnsProtectionManager, Depends(get_dns_protection_manager)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        item = await manager.get_protection(network_slug)
    except NetworkNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"network '{network_slug}' not found",
        ) from exc
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no protection configured for network '{network_slug}'",
        )
    return _protection_to_dict(item)


@router.put("/protections/{network_slug}")
async def set_protection(
    network_slug: str,
    body: ProtectionRequest,
    manager: Annotated[DnsProtectionManager, Depends(get_dns_protection_manager)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Persist + apply the mapping. Idempotent."""
    try:
        item = await manager.set_protection(
            network_slug,
            level_slug=body.level_slug,
            provider_slug=body.provider_slug,
        )
    except NetworkNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"network '{network_slug}' not found",
        ) from exc
    except DnsProtectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc),
        ) from exc
    except AdGuardError as exc:
        # AdGuard rejected the apply (typical : CIDR collision against an
        # operator-added client, AdGuard down, auth wrong). Surface as 502
        # so the UI can render the actual cause instead of a generic 500.
        logger.warning(
            "dns_protection.adguard_apply_failed",
            network=network_slug, level=body.level_slug, error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc),
        ) from exc
    logger.info(
        "dns_protection.set",
        username=user.username,
        network=network_slug,
        level=body.level_slug,
        provider=body.provider_slug,
    )
    return _protection_to_dict(item)


@router.delete("/protections/{network_slug}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_protection(
    network_slug: str,
    manager: Annotated[DnsProtectionManager, Depends(get_dns_protection_manager)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await manager.remove_protection(network_slug)
    logger.info(
        "dns_protection.removed",
        username=user.username,
        network=network_slug,
    )


@router.post("/protections/reapply")
async def reapply_all(
    manager: Annotated[DnsProtectionManager, Depends(get_dns_protection_manager)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Push every persisted protection to AdGuard.

    Useful after an AdGuard restart, fresh bootstrap, or state restore —
    AdGuard's clients may have been lost. Idempotent.
    """
    rep = await manager.reapply_all()
    logger.info(
        "dns_protection.reapply",
        username=user.username, ok=rep.ok,
        applied=len(rep.applied), errors=len(rep.errors),
    )
    return {
        "ok": rep.ok,
        "applied": rep.applied,
        "skipped": rep.skipped,
        "errors": rep.errors,
    }


# ---------------------------- serializers ---------------------------- #


def _provider_to_dict(p) -> dict:
    return {
        "slug": p.slug,
        "name": p.name,
        "organization": p.organization,
        "country": p.country,
        "is_eu_based": p.is_eu_based,
        "ipv4_primary": p.ipv4_primary,
        "ipv4_secondary": p.ipv4_secondary,
        "ipv6_primary": p.ipv6_primary,
        "doh_url": p.doh_url,
        "dot_hostname": p.dot_hostname,
        "filter_profile": p.filter_profile,
        "log_policy": p.log_policy,
        "supports_dnssec": p.supports_dnssec,
        "recommended": p.recommended,
        "intensity": p.intensity,
        "description": p.description,
    }


def _level_to_dict(level) -> dict:
    return {
        "slug": level.slug,
        "name": level.name,
        "description": level.description,
        "icon": level.icon,
        "color": level.color,
        "default_provider_slug": level.default_provider_slug,
        "allowed_provider_slugs": level.allowed_provider_slugs,
        "adguard_filtering": level.adguard_filtering,
        "safe_browsing": level.safe_browsing,
        "parental_control": level.parental_control,
        "safe_search": level.safe_search,
        "blocked_services": level.blocked_services,
        "adguard_blocklist_slugs": level.adguard_blocklist_slugs,
        "require_dot": level.require_dot,
        "require_dnssec": level.require_dnssec,
        "eu_only": level.eu_only,
        "intensity": level.intensity,
    }


def _protection_to_dict(p: NetworkProtection) -> dict:
    d = asdict(p)
    d["created_at"] = p.created_at.isoformat()
    d["updated_at"] = p.updated_at.isoformat()
    return d


# ---------------------------- anti-bypass ---------------------------- #


@router.get("/anti-bypass/status")
async def get_anti_bypass_status(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Snapshot of every anti-bypass mechanism (custom + GL.iNet ones)."""
    try:
        status_ = await anti_bypass_status(ssh)
    except AntiBypassError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc),
        ) from exc
    return {
        "custom_block_dot_active": status_.custom_block_dot_active,
        "gl_rules_enabled": status_.gl_rules_enabled,
        "all_active": status_.all_active,
        "any_active": status_.any_active,
    }


@router.post("/anti-bypass/enable")
async def enable_anti_bypass(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Activate: block TCP/853 LAN→WAN + flip every GL.iNet leak rule on."""
    try:
        status_ = await anti_bypass_enable(ssh)
    except AntiBypassError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc),
        ) from exc
    logger.info("dns.anti_bypass.enable", username=user.username, all_active=status_.all_active)
    return {
        "custom_block_dot_active": status_.custom_block_dot_active,
        "gl_rules_enabled": status_.gl_rules_enabled,
        "all_active": status_.all_active,
        "any_active": status_.any_active,
    }


@router.post("/anti-bypass/disable")
async def disable_anti_bypass(
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """Deactivate: delete the custom rule + flip every GL.iNet leak rule off."""
    try:
        status_ = await anti_bypass_disable(ssh)
    except AntiBypassError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc),
        ) from exc
    logger.info("dns.anti_bypass.disable", username=user.username, any_active=status_.any_active)
    return {
        "custom_block_dot_active": status_.custom_block_dot_active,
        "gl_rules_enabled": status_.gl_rules_enabled,
        "all_active": status_.all_active,
        "any_active": status_.any_active,
    }
