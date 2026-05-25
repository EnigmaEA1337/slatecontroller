"""Security Device Status: SBOM + vulnerability findings API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import (
    get_exploit_enricher,
    get_security_scanner,
    get_security_store,
    get_slate_client,
    get_slate_ssh,
)
from app.auth import User, get_current_user
from app.db.database import make_session_factory
from app.db.models import DeviceRow
from app.exceptions import SlateRpcError, SlateUnreachableError
from app.security.exploit_enricher import ExploitEnricher
from app.security.models import parse_attack_vector
from app.security.scanner import SecurityScanner
from app.security.store import SecurityStore
from app.slate.client import SlateClient
from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/security", tags=["security"])


# ---------------------------- models ---------------------------- #


class PackageOut(BaseModel):
    name: str
    version: str
    upstream_version: str
    vendor_specific: bool


class AttackPathOut(BaseModel):
    cwe: list[str] = Field(default_factory=list)
    capec: list[str] = Field(default_factory=list)
    techniques: list[str] = Field(default_factory=list)
    atlas: list[str] = Field(default_factory=list)


class KEVOut(BaseModel):
    date_added: datetime
    due_date: datetime | None = None
    vendor: str | None = None
    product: str | None = None
    vulnerability_name: str | None = None
    short_description: str | None = None
    required_action: str | None = None
    known_ransomware_use: bool = False


class EPSSOut(BaseModel):
    score: float
    percentile: float
    date: datetime


class ExploitSourceOut(BaseModel):
    source: str
    url: str
    title: str | None = None
    author: str | None = None
    date_published: datetime | None = None
    verified: bool = False
    stars: int | None = None


class CertFrBulletinOut(BaseModel):
    ref: str
    kind: str  # "alerte" | "avis"
    title: str
    url: str
    pub_date: datetime | None = None
    actively_exploited: bool = False
    ransomware_mentioned: bool = False


class ExploitEnrichmentOut(BaseModel):
    kev: KEVOut | None = None
    epss: EPSSOut | None = None
    exploit_db: list[ExploitSourceOut] = Field(default_factory=list)
    github_pocs: list[ExploitSourceOut] = Field(default_factory=list)
    metasploit_modules: list[ExploitSourceOut] = Field(default_factory=list)
    cert_fr: list[CertFrBulletinOut] = Field(default_factory=list)
    exploit_maturity: str = "none"
    priority_score: float = 0.0
    priority_level: str = "info"
    last_refreshed_at: datetime | None = None


class RiskAcceptanceOut(BaseModel):
    accepted_by: str = ""
    accepted_at: datetime
    reason: str = ""
    expires_at: datetime | None = None
    expired: bool = False


class FindingOut(BaseModel):
    cve_id: str
    package_name: str
    package_version: str
    severity: str
    source: str
    fixed_in: str | None = None
    url: str | None = None
    summary: str
    cvss_score: float | None = None
    cvss_vector: str | None = None
    # Derived from cvss_vector (AV: field). One of network|adjacent|local|physical|unknown.
    attack_vector: str = "unknown"
    aliases: list[str] = Field(default_factory=list)
    attack_path: AttackPathOut | None = None
    exploit: ExploitEnrichmentOut | None = None
    acknowledged: bool = False
    ack_note: str = ""
    risk_acceptance: RiskAcceptanceOut | None = None


class SnapshotSummary(BaseModel):
    id: int
    taken_at: datetime
    openwrt_release: str
    firmware_version: str
    kernel: str
    package_count: int
    scan_status: str
    scan_error: str = ""


class SnapshotDetail(SnapshotSummary):
    openwrt_distrib_id: str
    openwrt_target: str
    openwrt_arch: str
    openwrt_taints: str
    board_name: str
    hostname: str
    model: str
    packages: list[PackageOut] = Field(default_factory=list)


class FindingsResponse(BaseModel):
    snapshot: SnapshotSummary | None
    severity_counts: dict[str, int] = Field(default_factory=dict)
    findings: list[FindingOut] = Field(default_factory=list)
    vendor_packages: int = 0  # not scanned
    scanned_packages: int = 0


class ScanResponse(BaseModel):
    snapshot_id: int
    findings_count: int
    status: str
    error: str = ""


class AckRequest(BaseModel):
    cve_id: str
    package_name: str
    note: str = ""


class RiskAcceptRequest(BaseModel):
    cve_id: str
    package_name: str
    reason: str
    expires_at: datetime | None = None


# ---------------------------- helpers ---------------------------- #


async def _default_device_id(request: Request) -> int:
    """Resolve the default device id (V1 binds to a single device)."""
    sf = make_session_factory(request.app.state.db_engine)
    async with sf() as s:
        row = await s.scalar(
            select(DeviceRow).where(DeviceRow.is_default.is_(True))
        )
        if row is None:
            row = await s.scalar(select(DeviceRow).order_by(DeviceRow.id))
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="no device adopted",
            )
        return row.id


def _snap_to_summary(row) -> SnapshotSummary:
    return SnapshotSummary(
        id=row.id,
        taken_at=row.taken_at,
        openwrt_release=row.openwrt_release,
        firmware_version=row.firmware_version,
        kernel=row.kernel,
        package_count=row.package_count,
        scan_status=row.scan_status,
        scan_error=row.scan_error,
    )


def _snap_to_detail(row) -> SnapshotDetail:
    pkgs = [PackageOut(**p) for p in (row.packages_json or [])]
    return SnapshotDetail(
        id=row.id,
        taken_at=row.taken_at,
        openwrt_distrib_id=row.openwrt_distrib_id,
        openwrt_release=row.openwrt_release,
        openwrt_target=row.openwrt_target,
        openwrt_arch=row.openwrt_arch,
        openwrt_taints=row.openwrt_taints,
        firmware_version=row.firmware_version,
        kernel=row.kernel,
        board_name=row.board_name,
        hostname=row.hostname,
        model=row.model,
        packages=pkgs,
        package_count=row.package_count,
        scan_status=row.scan_status,
        scan_error=row.scan_error,
    )


# ---------------------------- endpoints ---------------------------- #


@router.get("/snapshots", response_model=list[SnapshotSummary])
async def list_snapshots(
    request: Request,
    store: Annotated[SecurityStore, Depends(get_security_store)],
    _user: Annotated[User, Depends(get_current_user)],
    limit: int = 50,
) -> list[SnapshotSummary]:
    device_id = await _default_device_id(request)
    rows = await store.list_snapshots(device_id, limit=limit)
    return [_snap_to_summary(r) for r in rows]


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotDetail)
async def get_snapshot(
    snapshot_id: int,
    store: Annotated[SecurityStore, Depends(get_security_store)],
    _user: Annotated[User, Depends(get_current_user)],
) -> SnapshotDetail:
    row = await store.get_snapshot(snapshot_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="snapshot not found"
        )
    return _snap_to_detail(row)


@router.get("/findings", response_model=FindingsResponse)
async def get_findings(
    request: Request,
    store: Annotated[SecurityStore, Depends(get_security_store)],
    enricher: Annotated[ExploitEnricher, Depends(get_exploit_enricher)],
    _user: Annotated[User, Depends(get_current_user)],
    snapshot_id: int | None = None,
) -> FindingsResponse:
    """Return findings for a given snapshot (defaults to the latest).

    Joins the per-CVE exploit cache at view time so KEV/EPSS updates show
    up the same day they're refreshed, without re-scanning the device.
    """
    device_id = await _default_device_id(request)
    if snapshot_id is None:
        snap = await store.latest_snapshot(device_id)
    else:
        snap = await store.get_snapshot(snapshot_id)
    if snap is None:
        return FindingsResponse(snapshot=None)

    rows = await store.list_findings(snap.id)
    acks = await store.acked_keys()
    risks = await store.risk_accepted_keys()

    # Cheap view-time join: one cache lookup for all CVE ids in this snapshot.
    cve_ids = [r.cve_id for r in rows if r.cve_id]
    exploit_cache = await enricher.load_for_cves(cve_ids)

    findings: list[FindingOut] = []
    sev_counts: dict[str, int] = {}
    for r in rows:
        sev_counts[r.severity] = sev_counts.get(r.severity, 0) + 1
        ack = acks.get((r.cve_id, r.package_name))
        ap_raw = r.attack_path_json or None
        ap = (
            AttackPathOut(
                cwe=ap_raw.get("cwe", []) or [],
                capec=ap_raw.get("capec", []) or [],
                techniques=ap_raw.get("techniques", []) or [],
                atlas=ap_raw.get("atlas", []) or [],
            )
            if ap_raw
            else None
        )
        enr = exploit_cache.get(r.cve_id)
        exploit_out = (
            ExploitEnrichmentOut(
                kev=KEVOut(**enr.kev.model_dump()) if enr.kev else None,
                epss=EPSSOut(**enr.epss.model_dump()) if enr.epss else None,
                exploit_db=[ExploitSourceOut(**e.model_dump()) for e in enr.exploit_db],
                github_pocs=[ExploitSourceOut(**e.model_dump()) for e in enr.github_pocs],
                metasploit_modules=[ExploitSourceOut(**e.model_dump()) for e in enr.metasploit_modules],
                cert_fr=[CertFrBulletinOut(**b.model_dump()) for b in enr.cert_fr],
                exploit_maturity=enr.exploit_maturity,
                priority_score=enr.priority_score,
                priority_level=enr.priority_level,
                last_refreshed_at=enr.last_refreshed_at,
            )
            if enr is not None
            else None
        )
        risk = risks.get((r.cve_id, r.package_name))
        risk_out = (
            RiskAcceptanceOut(
                accepted_by=risk["accepted_by"],
                accepted_at=risk["accepted_at"],
                reason=risk["reason"],
                expires_at=risk["expires_at"],
                expired=risk["expired"],
            )
            if risk is not None
            else None
        )
        findings.append(
            FindingOut(
                cve_id=r.cve_id,
                package_name=r.package_name,
                package_version=r.package_version,
                severity=r.severity,
                source=r.source,
                fixed_in=r.fixed_in,
                url=r.url,
                summary=r.summary,
                cvss_score=r.cvss_score,
                cvss_vector=r.cvss_vector,
                attack_vector=parse_attack_vector(r.cvss_vector),
                aliases=r.aliases_json or [],
                attack_path=ap,
                exploit=exploit_out,
                acknowledged=ack is not None,
                ack_note=ack.get("note", "") if ack else "",
                risk_acceptance=risk_out,
            )
        )

    vendor = sum(1 for p in (snap.packages_json or []) if p.get("vendor_specific"))
    scanned = snap.package_count - vendor

    return FindingsResponse(
        snapshot=_snap_to_summary(snap),
        severity_counts=sev_counts,
        findings=findings,
        vendor_packages=vendor,
        scanned_packages=scanned,
    )


@router.post("/scan", response_model=ScanResponse)
async def trigger_scan(
    scanner: Annotated[SecurityScanner, Depends(get_security_scanner)],
    store: Annotated[SecurityStore, Depends(get_security_store)],
    ssh: Annotated[SlateSSH, Depends(get_slate_ssh)],
    slate: Annotated[SlateClient, Depends(get_slate_client)],
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
) -> ScanResponse:
    """Collect a fresh SBOM and run all configured sources."""
    device_id = await _default_device_id(request)
    # Best-effort firmware version from RPC; not fatal if it fails.
    firmware = ""
    try:
        info = await slate.call("system", "get_info")
        if hasattr(info, "result"):
            info = info.result
        if isinstance(info, dict):
            inner = info.get("result", info)
            if isinstance(inner, dict):
                firmware = str(inner.get("version") or inner.get("firmware_version") or "")
    except (SlateRpcError, SlateUnreachableError) as exc:
        logger.info("security.scan.firmware_lookup_failed", error=str(exc))

    try:
        snapshot_id, findings = await scanner.run(
            ssh=ssh,
            store=store,
            device_id=device_id,
            firmware_version=firmware,
        )
    except SlateSSHError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SSH collection failed: {exc}",
        ) from exc

    snap = await store.get_snapshot(snapshot_id)
    return ScanResponse(
        snapshot_id=snapshot_id,
        findings_count=len(findings),
        status=snap.scan_status if snap else "unknown",
        error=snap.scan_error if snap else "",
    )


@router.post("/findings/acknowledge", status_code=status.HTTP_204_NO_CONTENT)
async def acknowledge(
    body: AckRequest,
    store: Annotated[SecurityStore, Depends(get_security_store)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await store.acknowledge(body.cve_id, body.package_name, body.note)
    logger.info(
        "security.finding.acknowledged",
        cve=body.cve_id,
        pkg=body.package_name,
        username=user.username,
    )


@router.delete("/findings/acknowledge", status_code=status.HTTP_204_NO_CONTENT)
async def unacknowledge(
    cve_id: str,
    package_name: str,
    store: Annotated[SecurityStore, Depends(get_security_store)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await store.unacknowledge(cve_id, package_name)
    logger.info(
        "security.finding.unacknowledged",
        cve=cve_id,
        pkg=package_name,
        username=user.username,
    )


@router.post("/findings/accept-risk", status_code=status.HTTP_204_NO_CONTENT)
async def accept_risk(
    body: RiskAcceptRequest,
    store: Annotated[SecurityStore, Depends(get_security_store)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Record an explicit risk acceptance decision (with mandatory reason)."""
    if not body.reason.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="reason is required to accept risk",
        )
    await store.accept_risk(
        body.cve_id,
        body.package_name,
        accepted_by=user.username,
        reason=body.reason.strip(),
        expires_at=body.expires_at,
    )
    logger.warning(
        "security.finding.risk_accepted",
        cve=body.cve_id,
        pkg=body.package_name,
        username=user.username,
        expires_at=body.expires_at.isoformat() if body.expires_at else None,
    )


@router.delete("/findings/accept-risk", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_risk(
    cve_id: str,
    package_name: str,
    store: Annotated[SecurityStore, Depends(get_security_store)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await store.revoke_risk(cve_id, package_name)
    logger.info(
        "security.finding.risk_revoked",
        cve=cve_id,
        pkg=package_name,
        username=user.username,
    )


@router.get("/sources/status")
async def sources_status(
    enricher: Annotated[ExploitEnricher, Depends(get_exploit_enricher)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Return load state and last-refresh time for each exploit source."""
    return enricher.sources_status()


class RiskScoreComponentOut(BaseModel):
    # Stable id so the frontend can target a component without parsing the label.
    id: str
    label: str
    value: float            # raw metric (count or average)
    weight: int             # max points this component can contribute
    contribution: float     # actual points this component contributes
    detail: str = ""
    # CVE ids that contributed to this component. Empty list means the
    # component is informational (e.g. averages) — UI hides "Voir" buttons.
    cve_ids: list[str] = Field(default_factory=list)


class RiskScoreResponse(BaseModel):
    score: float                                # 0-100
    level: str                                  # critical|high|medium|low|info
    snapshot_id: int | None = None
    snapshot_taken_at: datetime | None = None
    components: list[RiskScoreComponentOut] = Field(default_factory=list)
    risk_accepted_count: int = 0
    risk_accepted_unlimited: int = 0   # permanent acceptances (no expiry)
    risk_accepted_limited: int = 0     # acceptances with an expiry date
    findings_total: int = 0
    explanation: str = ""


class RiskScoreHistoryPoint(BaseModel):
    snapshot_id: int
    taken_at: datetime
    score: float
    level: str
    critical_exploitable: int
    kev_count: int
    weaponized_count: int
    remote_critical: int
    cert_fr_alertes: int


class RiskScoreHistoryResponse(BaseModel):
    points: list[RiskScoreHistoryPoint] = Field(default_factory=list)


async def _compute_risk_score(
    snap,
    store: SecurityStore,
    enricher: ExploitEnricher,
) -> "RiskScoreResponse":
    """Score a single snapshot. Shared by /risk-score and /risk-score/history."""
    rows = await store.list_findings(snap.id)
    acks = await store.acked_keys()
    risks = await store.risk_accepted_keys()

    open_rows = []
    for r in rows:
        key = (r.cve_id, r.package_name)
        if key in acks:
            continue
        risk = risks.get(key)
        if risk is not None and not risk["expired"]:
            continue
        open_rows.append(r)

    cve_ids = [r.cve_id for r in open_rows if r.cve_id]
    enrichments = await enricher.load_for_cves(cve_ids)

    crit_exp_ids: set[str] = set()
    kev_ids: set[str] = set()
    in_wild_ids: set[str] = set()
    weaponized_ids: set[str] = set()
    remote_critical_ids: set[str] = set()
    cert_fr_alerte_ids: set[str] = set()
    priority_high_ids: set[str] = set()
    priority_high_sum = 0.0
    for r in open_rows:
        enr = enrichments.get(r.cve_id)
        if not enr:
            continue
        if enr.priority_score >= 80:
            crit_exp_ids.add(r.cve_id)
        if enr.kev is not None:
            kev_ids.add(r.cve_id)
        if enr.exploit_maturity == "weaponized":
            weaponized_ids.add(r.cve_id)
        if enr.exploit_maturity == "in_the_wild":
            in_wild_ids.add(r.cve_id)
        if any(b.kind == "alerte" for b in enr.cert_fr):
            cert_fr_alerte_ids.add(r.cve_id)
        if enr.priority_score >= 60:
            priority_high_ids.add(r.cve_id)
            priority_high_sum += enr.priority_score
        vector = r.cvss_vector or ""
        if enr.priority_score >= 60 and "AV:N" in vector:
            remote_critical_ids.add(r.cve_id)

    crit_exp = len(crit_exp_ids)
    kev_count = len(kev_ids)
    weaponized_count = len(weaponized_ids)
    in_the_wild_count = len(in_wild_ids)
    remote_critical = len(remote_critical_ids)
    cert_fr_alertes = len(cert_fr_alerte_ids)
    priority_high_n = len(priority_high_ids)
    in_wild_or_kev_ids = in_wild_ids | kev_ids

    def _scale(count: int, cap: int, weight: int) -> float:
        if cap == 0:
            return 0
        return min(count, cap) / cap * weight

    components: list[RiskScoreComponentOut] = [
        RiskScoreComponentOut(
            id="critical_exploitable",
            label="Critiques exploitables (priority ≥ 80)",
            value=crit_exp,
            weight=30,
            contribution=_scale(crit_exp, 5, 30),
            detail="-30 pts si ≥ 5 CVE en priority critical",
            cve_ids=sorted(crit_exp_ids),
        ),
        RiskScoreComponentOut(
            id="in_the_wild",
            label="In the wild (KEV ou observé)",
            value=in_the_wild_count or kev_count,
            weight=20,
            contribution=_scale(in_the_wild_count or kev_count, 5, 20),
            detail="-20 pts si ≥ 5 CVE activement exploités",
            cve_ids=sorted(in_wild_or_kev_ids),
        ),
        RiskScoreComponentOut(
            id="weaponized",
            label="Weaponized (Metasploit prêt)",
            value=weaponized_count,
            weight=15,
            contribution=_scale(weaponized_count, 8, 15),
            detail="-15 pts si ≥ 8 modules Metasploit disponibles",
            cve_ids=sorted(weaponized_ids),
        ),
        RiskScoreComponentOut(
            id="remote_critical",
            label="Remote-exploitables critiques",
            value=remote_critical,
            weight=15,
            contribution=_scale(remote_critical, 10, 15),
            detail="-15 pts si ≥ 10 CVE AV:N en priority high/critical",
            cve_ids=sorted(remote_critical_ids),
        ),
        RiskScoreComponentOut(
            id="cert_fr_alerte",
            label="Couvert par alerte CERT-FR",
            value=cert_fr_alertes,
            weight=10,
            contribution=_scale(cert_fr_alertes, 3, 10),
            detail="-10 pts si ≥ 3 CVE en alerte ANSSI",
            cve_ids=sorted(cert_fr_alerte_ids),
        ),
        RiskScoreComponentOut(
            id="priority_avg",
            label="Priority moyenne des high/critical",
            value=(priority_high_sum / priority_high_n) if priority_high_n else 0.0,
            weight=10,
            contribution=(
                ((priority_high_sum / priority_high_n) / 100 * 10)
                if priority_high_n
                else 0.0
            ),
            detail="moyenne pondère le profil global",
            cve_ids=sorted(priority_high_ids),
        ),
    ]

    score = round(sum(c.contribution for c in components), 1)
    if score >= 80:
        level = "critical"
    elif score >= 60:
        level = "high"
    elif score >= 40:
        level = "medium"
    elif score >= 20:
        level = "low"
    else:
        level = "info"

    accepted_count = sum(1 for v in risks.values() if not v["expired"])
    accepted_unlimited = sum(
        1 for v in risks.values() if not v["expired"] and v["expires_at"] is None
    )
    accepted_limited = accepted_count - accepted_unlimited

    explanation = (
        f"{crit_exp} critiques exploitables · {kev_count} dans KEV · "
        f"{weaponized_count} weaponized · {remote_critical} remote AV:N · "
        f"{cert_fr_alertes} alertes CERT-FR · {accepted_count} risques acceptés"
    )

    return RiskScoreResponse(
        score=score,
        level=level,
        snapshot_id=snap.id,
        snapshot_taken_at=snap.taken_at,
        components=components,
        risk_accepted_count=accepted_count,
        risk_accepted_unlimited=accepted_unlimited,
        risk_accepted_limited=accepted_limited,
        findings_total=len(rows),
        explanation=explanation,
    )


@router.get("/risk-score", response_model=RiskScoreResponse)
async def get_risk_score(
    request: Request,
    store: Annotated[SecurityStore, Depends(get_security_store)],
    enricher: Annotated[ExploitEnricher, Depends(get_exploit_enricher)],
    _user: Annotated[User, Depends(get_current_user)],
) -> RiskScoreResponse:
    """Composite device risk score (0-100, higher = more at-risk).

    Aggregates the strongest signals from the latest snapshot: count of
    high-priority CVE, KEV presence, weaponized exploits, remote-exploitable
    criticals, CERT-FR alertes, and the average priority of high+critical
    findings. Risk-accepted findings (active, non-expired) are excluded.
    """
    device_id = await _default_device_id(request)
    snap = await store.latest_snapshot(device_id)
    if snap is None:
        return RiskScoreResponse(
            score=0.0,
            level="info",
            explanation="aucun scan disponible — déclencher un scan",
        )
    return await _compute_risk_score(snap, store, enricher)


@router.get("/risk-score/history", response_model=RiskScoreHistoryResponse)
async def get_risk_score_history(
    request: Request,
    store: Annotated[SecurityStore, Depends(get_security_store)],
    enricher: Annotated[ExploitEnricher, Depends(get_exploit_enricher)],
    _user: Annotated[User, Depends(get_current_user)],
    limit: int = 30,
) -> RiskScoreHistoryResponse:
    """Risk score timeline across the last N snapshots.

    Each snapshot is re-scored using *today's* exploit enrichment cache, so
    the history shows how the SBOM evolution affected risk under the current
    threat intel — not a snapshot of "what the world knew then". That's
    actually more useful for trend reading: "did patching reduce my exposure
    to today's known exploits?".
    """
    device_id = await _default_device_id(request)
    snaps = await store.list_snapshots(device_id, limit=limit)
    points: list[RiskScoreHistoryPoint] = []
    for snap in reversed(snaps):  # chronological
        scored = await _compute_risk_score(snap, store, enricher)
        # Pull component values by id (set during _compute_risk_score).
        by_id = {c.id: c for c in scored.components}
        points.append(
            RiskScoreHistoryPoint(
                snapshot_id=snap.id,
                taken_at=snap.taken_at,
                score=scored.score,
                level=scored.level,
                critical_exploitable=int(by_id.get("critical_exploitable").value if "critical_exploitable" in by_id else 0),
                kev_count=int(by_id.get("in_the_wild").value if "in_the_wild" in by_id else 0),
                weaponized_count=int(by_id.get("weaponized").value if "weaponized" in by_id else 0),
                remote_critical=int(by_id.get("remote_critical").value if "remote_critical" in by_id else 0),
                cert_fr_alertes=int(by_id.get("cert_fr_alerte").value if "cert_fr_alerte" in by_id else 0),
            )
        )
    return RiskScoreHistoryResponse(points=points)


@router.post("/sources/refresh")
async def sources_refresh(
    enricher: Annotated[ExploitEnricher, Depends(get_exploit_enricher)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Force-refresh the in-memory exploit sources (KEV / Exploit-DB / MSF).

    EPSS and GitHub PoC are pulled per-CVE, so they don't need a global
    refresh — they're re-pulled on the next finding lookup when the cache
    entry is older than 24 h.
    """
    logger.info("security.sources.refresh.triggered", username=user.username)
    return await enricher.refresh_all()
