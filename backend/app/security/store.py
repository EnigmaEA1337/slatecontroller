"""DB persistence for inventory snapshots, findings, and acknowledgements."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    DeviceInventorySnapshotRow,
    RiskAcceptanceRow,
    VulnerabilityAcknowledgementRow,
    VulnerabilityFindingRow,
)
from app.security.models import Finding, Inventory, Package


# Rolling retention. Configurable via env later if needed.
MAX_SNAPSHOTS_PER_DEVICE = 30


class SecurityStore:
    """High-level DB API for the security feature."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # ------------------------- snapshots ------------------------- #

    async def create_snapshot(self, device_id: int, inv: Inventory) -> int:
        """Insert a new snapshot row, return its id. Also prunes old ones."""
        async with self._sf() as s:
            row = DeviceInventorySnapshotRow(
                device_id=device_id,
                taken_at=inv.taken_at,
                openwrt_distrib_id=inv.openwrt_distrib_id,
                openwrt_release=inv.openwrt_release,
                openwrt_target=inv.openwrt_target,
                openwrt_arch=inv.openwrt_arch,
                openwrt_taints=inv.openwrt_taints,
                firmware_version=inv.firmware_version,
                kernel=inv.kernel,
                board_name=inv.board_name,
                hostname=inv.hostname,
                model=inv.model,
                packages_json=[p.model_dump() for p in inv.packages],
                package_count=inv.package_count,
                scan_status="pending",
                scan_error="",
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            snapshot_id = row.id

        await self._prune(device_id)
        return snapshot_id

    async def mark_scan_complete(
        self,
        snapshot_id: int,
        status: str,
        error: str = "",
    ) -> None:
        async with self._sf() as s:
            row = await s.get(DeviceInventorySnapshotRow, snapshot_id)
            if row is None:
                return
            row.scan_status = status
            row.scan_error = error[:512]
            await s.commit()

    async def list_snapshots(
        self, device_id: int, limit: int = 50
    ) -> list[DeviceInventorySnapshotRow]:
        async with self._sf() as s:
            stmt = (
                select(DeviceInventorySnapshotRow)
                .where(DeviceInventorySnapshotRow.device_id == device_id)
                .order_by(DeviceInventorySnapshotRow.taken_at.desc())
                .limit(limit)
            )
            return list((await s.scalars(stmt)).all())

    async def get_snapshot(
        self, snapshot_id: int
    ) -> DeviceInventorySnapshotRow | None:
        async with self._sf() as s:
            return await s.get(DeviceInventorySnapshotRow, snapshot_id)

    async def latest_snapshot(
        self, device_id: int
    ) -> DeviceInventorySnapshotRow | None:
        async with self._sf() as s:
            stmt = (
                select(DeviceInventorySnapshotRow)
                .where(DeviceInventorySnapshotRow.device_id == device_id)
                .order_by(DeviceInventorySnapshotRow.taken_at.desc())
                .limit(1)
            )
            return await s.scalar(stmt)

    async def snapshot_packages(self, snapshot_id: int) -> list[Package]:
        snap = await self.get_snapshot(snapshot_id)
        if not snap:
            return []
        return [Package(**p) for p in (snap.packages_json or [])]

    async def _prune(self, device_id: int) -> None:
        async with self._sf() as s:
            stmt = (
                select(DeviceInventorySnapshotRow.id)
                .where(DeviceInventorySnapshotRow.device_id == device_id)
                .order_by(DeviceInventorySnapshotRow.taken_at.desc())
                .offset(MAX_SNAPSHOTS_PER_DEVICE)
            )
            stale_ids = list((await s.scalars(stmt)).all())
            if stale_ids:
                await s.execute(
                    delete(DeviceInventorySnapshotRow).where(
                        DeviceInventorySnapshotRow.id.in_(stale_ids)
                    )
                )
                await s.commit()

    # ------------------------- findings ------------------------- #

    async def replace_findings(
        self, snapshot_id: int, findings: Sequence[Finding]
    ) -> None:
        """Drop any existing findings for the snapshot and insert fresh ones.

        Idempotent re-scans use this so old data doesn't accumulate.
        """
        async with self._sf() as s:
            await s.execute(
                delete(VulnerabilityFindingRow).where(
                    VulnerabilityFindingRow.snapshot_id == snapshot_id
                )
            )
            now = datetime.now(UTC)
            for f in findings:
                s.add(
                    VulnerabilityFindingRow(
                        snapshot_id=snapshot_id,
                        cve_id=f.cve_id,
                        package_name=f.package_name,
                        package_version=f.package_version,
                        severity=f.severity,
                        source=f.source,
                        fixed_in=f.fixed_in,
                        url=f.url,
                        summary=f.summary,
                        cvss_score=f.cvss_score,
                        cvss_vector=f.cvss_vector,
                        aliases_json=f.aliases,
                        attack_path_json=(
                            f.attack_path.model_dump() if f.attack_path else None
                        ),
                        created_at=now,
                    )
                )
            await s.commit()

    async def list_findings(
        self, snapshot_id: int
    ) -> list[VulnerabilityFindingRow]:
        async with self._sf() as s:
            stmt = (
                select(VulnerabilityFindingRow)
                .where(VulnerabilityFindingRow.snapshot_id == snapshot_id)
                .order_by(
                    VulnerabilityFindingRow.cvss_score.desc().nullslast(),
                    VulnerabilityFindingRow.package_name,
                )
            )
            return list((await s.scalars(stmt)).all())

    # ----------------------- acknowledgements ----------------------- #

    async def acknowledge(
        self, cve_id: str, package_name: str, note: str = ""
    ) -> None:
        async with self._sf() as s:
            stmt = select(VulnerabilityAcknowledgementRow).where(
                and_(
                    VulnerabilityAcknowledgementRow.cve_id == cve_id,
                    VulnerabilityAcknowledgementRow.package_name == package_name,
                )
            )
            existing = await s.scalar(stmt)
            if existing:
                existing.note = note[:512]
                existing.acknowledged_at = datetime.now(UTC)
            else:
                s.add(
                    VulnerabilityAcknowledgementRow(
                        cve_id=cve_id,
                        package_name=package_name,
                        note=note[:512],
                    )
                )
            await s.commit()

    async def unacknowledge(self, cve_id: str, package_name: str) -> None:
        async with self._sf() as s:
            await s.execute(
                delete(VulnerabilityAcknowledgementRow).where(
                    and_(
                        VulnerabilityAcknowledgementRow.cve_id == cve_id,
                        VulnerabilityAcknowledgementRow.package_name == package_name,
                    )
                )
            )
            await s.commit()

    async def acked_keys(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Return {(cve_id, package_name): {note, acknowledged_at}}."""
        async with self._sf() as s:
            rows = list(
                (await s.scalars(select(VulnerabilityAcknowledgementRow))).all()
            )
            return {
                (r.cve_id, r.package_name): {
                    "note": r.note,
                    "acknowledged_at": r.acknowledged_at,
                }
                for r in rows
            }

    # ----------------------- risk acceptances ----------------------- #

    async def accept_risk(
        self,
        cve_id: str,
        package_name: str,
        accepted_by: str,
        reason: str,
        expires_at: datetime | None = None,
    ) -> None:
        async with self._sf() as s:
            stmt = select(RiskAcceptanceRow).where(
                and_(
                    RiskAcceptanceRow.cve_id == cve_id,
                    RiskAcceptanceRow.package_name == package_name,
                )
            )
            existing = await s.scalar(stmt)
            if existing:
                existing.reason = reason[:1024]
                existing.accepted_at = datetime.now(UTC)
                existing.accepted_by = accepted_by[:64]
                existing.expires_at = expires_at
            else:
                s.add(
                    RiskAcceptanceRow(
                        cve_id=cve_id,
                        package_name=package_name,
                        accepted_by=accepted_by[:64],
                        reason=reason[:1024],
                        expires_at=expires_at,
                    )
                )
            await s.commit()

    async def revoke_risk(self, cve_id: str, package_name: str) -> None:
        async with self._sf() as s:
            await s.execute(
                delete(RiskAcceptanceRow).where(
                    and_(
                        RiskAcceptanceRow.cve_id == cve_id,
                        RiskAcceptanceRow.package_name == package_name,
                    )
                )
            )
            await s.commit()

    async def risk_accepted_keys(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Return {(cve_id, package_name): {reason, accepted_by, accepted_at, expires_at, expired}}.

        Expired entries are still returned — UI shows them as "expired" and
        applies the same visual as "not accepted" in the default view.
        """
        now = datetime.now(UTC)
        async with self._sf() as s:
            rows = list((await s.scalars(select(RiskAcceptanceRow))).all())
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rows:
            expires = r.expires_at
            if expires is not None and expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            out[(r.cve_id, r.package_name)] = {
                "reason": r.reason,
                "accepted_by": r.accepted_by,
                "accepted_at": r.accepted_at,
                "expires_at": expires,
                "expired": expires is not None and expires < now,
            }
        return out
