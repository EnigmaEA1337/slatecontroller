"""AP review endpoints — operator-curated trust status keyed by ap_root.

A review captures the operator's judgement on one *physical* AP (the
cluster of VAPs sharing the same lower-5-byte MAC + channel). One row
per (device_slug, ap_root) ; the latest values are returned.

Status semantics :

    trusted      Known-good AP (home, office). Suppresses evil-twin
                 and strong-neighbour alerts. Always shown.
    known        Acknowledged neighbour. Not suppressed but not flagged.
    ignored      Hidden from default tree view ; still kept in history.
    suspicious   Flagged for follow-up ; bumped to top of Air Watch.

The "unknown" state is the absence of a row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_device_connections
from app.auth import User, get_current_user
from app.db.models import ApReviewRow, BssidReviewRow
from app.devices.registry import DeviceConnections

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/wifi/reviews", tags=["wifi", "reviews"])
# Separate prefix for the per-BSSID override layer. Keeping them on
# distinct prefixes avoids the ``{ap_root:path}`` matcher swallowing
# requests destined for the BSSID endpoints.
bssid_router = APIRouter(prefix="/wifi/bssid-reviews", tags=["wifi", "reviews"])


ReviewStatus = Literal["trusted", "known", "ignored", "suspicious"]


class ApReviewView(BaseModel):
    ap_root: str
    status: ReviewStatus
    label: str
    note: str
    vendor: str
    sample_ssids: list[str]
    sample_bssid: str
    band: str
    channel: int
    reviewed_at: datetime
    reviewed_by: str


class ApReviewUpsert(BaseModel):
    status: ReviewStatus = Field(default="known")
    label: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=512)
    vendor: str = Field(default="", max_length=128)
    sample_ssids: list[str] = Field(default_factory=list)
    sample_bssid: str = Field(default="", max_length=17)
    band: str = Field(default="", max_length=2)
    channel: int = Field(default=0, ge=0, le=233)


@router.get("", response_model=list[ApReviewView])
async def list_reviews(
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[ApReviewView]:
    """Return all AP reviews for the active device."""
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        rows = (await s.scalars(
            select(ApReviewRow).where(ApReviewRow.device_slug == conn.slug),
        )).all()
    return [_row_to_view(r) for r in rows]


@router.put("/{ap_root:path}", response_model=ApReviewView)
async def upsert_review(
    ap_root: str,
    body: ApReviewUpsert,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> ApReviewView:
    """Create or update the review for one physical AP (by ap_root)."""
    if not ap_root:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="empty ap_root",
        )
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        existing = await s.scalar(
            select(ApReviewRow).where(
                ApReviewRow.device_slug == conn.slug,
                ApReviewRow.ap_root == ap_root,
            ),
        )
        ssids_csv = ",".join(s.strip() for s in body.sample_ssids if s.strip())
        now = datetime.now(UTC).replace(tzinfo=None)
        if existing is None:
            row = ApReviewRow(
                device_slug=conn.slug,
                ap_root=ap_root,
                status=body.status,
                label=body.label,
                note=body.note,
                vendor=body.vendor,
                sample_ssids=ssids_csv,
                sample_bssid=body.sample_bssid,
                band=body.band,
                channel=body.channel,
                reviewed_at=now,
                reviewed_by=user.username,
            )
            s.add(row)
        else:
            existing.status = body.status
            existing.label = body.label
            existing.note = body.note
            # Snapshot fields only refresh when caller provided them, so
            # an "edit status only" call doesn't blank the original
            # SSID / BSSID / vendor context.
            if body.vendor:
                existing.vendor = body.vendor
            if body.sample_ssids:
                existing.sample_ssids = ssids_csv
            if body.sample_bssid:
                existing.sample_bssid = body.sample_bssid
            if body.band:
                existing.band = body.band
            if body.channel:
                existing.channel = body.channel
            existing.reviewed_at = now
            existing.reviewed_by = user.username
            row = existing
        await s.commit()
        await s.refresh(row)
    logger.info(
        "wifi.ap_review.upsert",
        username=user.username, device=conn.slug,
        ap_root=ap_root, status=body.status,
    )
    return _row_to_view(row)


@router.delete("/{ap_root:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_review(
    ap_root: str,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Drop the review for one AP (returns to implicit 'unknown' state)."""
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        row = await s.scalar(
            select(ApReviewRow).where(
                ApReviewRow.device_slug == conn.slug,
                ApReviewRow.ap_root == ap_root,
            ),
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no review for ap_root={ap_root}",
            )
        await s.delete(row)
        await s.commit()
    logger.info(
        "wifi.ap_review.deleted",
        username=user.username, device=conn.slug, ap_root=ap_root,
    )


def _row_to_view(r: ApReviewRow) -> ApReviewView:
    ssids = [s for s in r.sample_ssids.split(",") if s] if r.sample_ssids else []
    return ApReviewView(
        ap_root=r.ap_root,
        status=r.status,  # type: ignore[arg-type]
        label=r.label,
        note=r.note,
        vendor=r.vendor,
        sample_ssids=ssids,
        sample_bssid=r.sample_bssid,
        band=r.band,
        channel=r.channel,
        reviewed_at=r.reviewed_at,
        reviewed_by=r.reviewed_by,
    )


# ----------------------------------------------------------------------
# BSSID-level reviews (override layer).
# ----------------------------------------------------------------------


class BssidReviewView(BaseModel):
    bssid: str
    status: ReviewStatus
    label: str
    note: str
    ssid: str
    vendor: str
    band: str
    channel: int
    reviewed_at: datetime
    reviewed_by: str


class BssidReviewUpsert(BaseModel):
    status: ReviewStatus = Field(default="known")
    label: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=512)
    ssid: str = Field(default="", max_length=64)
    vendor: str = Field(default="", max_length=128)
    band: str = Field(default="", max_length=2)
    channel: int = Field(default=0, ge=0, le=233)


def _bssid_row_to_view(r: BssidReviewRow) -> BssidReviewView:
    return BssidReviewView(
        bssid=r.bssid,
        status=r.status,  # type: ignore[arg-type]
        label=r.label,
        note=r.note,
        ssid=r.ssid,
        vendor=r.vendor,
        band=r.band,
        channel=r.channel,
        reviewed_at=r.reviewed_at,
        reviewed_by=r.reviewed_by,
    )


@bssid_router.get("", response_model=list[BssidReviewView])
async def list_bssid_reviews(
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    _user: Annotated[User, Depends(get_current_user)],
) -> list[BssidReviewView]:
    """Return all per-BSSID review overrides for the active device."""
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        rows = (await s.scalars(
            select(BssidReviewRow).where(
                BssidReviewRow.device_slug == conn.slug,
            ),
        )).all()
    return [_bssid_row_to_view(r) for r in rows]


@bssid_router.put("/{bssid}", response_model=BssidReviewView)
async def upsert_bssid_review(
    bssid: str,
    body: BssidReviewUpsert,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> BssidReviewView:
    """Create or update the per-BSSID override for one specific VAP.

    Acts on top of any group-level :class:`ApReviewRow` : the effective
    status of this BSSID becomes whatever is set here, regardless of the
    group's status.
    """
    if not bssid or len(bssid) != 17:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid bssid: {bssid!r} (expected 17-char MAC)",
        )
    bssid = bssid.lower()
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        existing = await s.scalar(
            select(BssidReviewRow).where(
                BssidReviewRow.device_slug == conn.slug,
                BssidReviewRow.bssid == bssid,
            ),
        )
        now = datetime.now(UTC).replace(tzinfo=None)
        if existing is None:
            row = BssidReviewRow(
                device_slug=conn.slug,
                bssid=bssid,
                status=body.status,
                label=body.label,
                note=body.note,
                ssid=body.ssid,
                vendor=body.vendor,
                band=body.band,
                channel=body.channel,
                reviewed_at=now,
                reviewed_by=user.username,
            )
            s.add(row)
        else:
            existing.status = body.status
            existing.label = body.label
            existing.note = body.note
            if body.ssid:
                existing.ssid = body.ssid
            if body.vendor:
                existing.vendor = body.vendor
            if body.band:
                existing.band = body.band
            if body.channel:
                existing.channel = body.channel
            existing.reviewed_at = now
            existing.reviewed_by = user.username
            row = existing
        await s.commit()
        await s.refresh(row)
    logger.info(
        "wifi.bssid_review.upsert",
        username=user.username, device=conn.slug,
        bssid=bssid, status=body.status,
    )
    return _bssid_row_to_view(row)


@bssid_router.delete("/{bssid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bssid_review(
    bssid: str,
    request: Request,
    conn: Annotated[DeviceConnections, Depends(get_device_connections)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Drop the BSSID-level override (group status takes over again)."""
    bssid = bssid.lower()
    sf: async_sessionmaker = request.app.state.db_session_factory
    async with sf() as s:
        row = await s.scalar(
            select(BssidReviewRow).where(
                BssidReviewRow.device_slug == conn.slug,
                BssidReviewRow.bssid == bssid,
            ),
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no review for bssid={bssid}",
            )
        await s.delete(row)
        await s.commit()
    logger.info(
        "wifi.bssid_review.deleted",
        username=user.username, device=conn.slug, bssid=bssid,
    )
