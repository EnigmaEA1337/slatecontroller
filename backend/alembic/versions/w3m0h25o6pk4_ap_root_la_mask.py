"""ap_root: mask LA bits of first octet to catch UniFi sibling VAPs

Revision ID: w3m0h25o6pk4
Revises: v2l9g14n5oj3
Create Date: 2026-06-23 23:00:00

The previous migration switched ``ap_root`` to the upper 5 bytes of
the BSSID (collapses Ruckus / Aruba / Cisco that increment the LAST
octet across sibling VAPs). UniFi-style firmware varies a different
end : it flips the locally-administered (LA) bit ``0x02`` of the FIRST
octet and increments the LA counter bits (``0x04``, ``0x08``) across
sibling VAPs. So a UniFi anchor ``78:48:dc:20:fe:XX`` spawns siblings
at ``7a:48:dc:20:fe``, ``7c:…``, ``7e:…`` and the upper-5-bytes rule
left each in its own cluster.

We close the gap by masking the LA family bits (``0x0E``) on the first
octet, so ``78 / 7a / 7c / 7e`` all normalise to ``70``. Pure heuristic,
no vendor table — see the inline doc on :func:`_ap_root_for`.

Migration steps :
- ``scan_neighbors`` : recompute ``ap_root`` from ``bssid`` with the
  new masked rule. Batched per distinct new-root for O(k) UPDATEs.
- ``ap_reviews`` : recompute via ``sample_bssid`` when set, fall back
  to masking the existing ``ap_root`` value otherwise. Resolve UNIQUE
  collisions by keeping the most-recently-reviewed row.

Reversal recomputes ``ap_root`` from ``bssid`` with the pre-mask rule
(upper 5 bytes only) on scan_neighbors. ap_reviews is left alone — a
masked review keeps matching new scans on the same masked id, just
with the slight loss that previously-distinct UniFi-anchor reviews
now share a cluster (which is the desired outcome of the upgrade).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "w3m0h25o6pk4"
down_revision = "v2l9g14n5oj3"
branch_labels = None
depends_on = None


def _ap_root_masked(bssid: str) -> str:
    """Match the NEW ``_ap_root_for`` in :mod:`app.wifi.scanner` exactly."""
    norm = (bssid or "").lower().strip()
    parts = norm.split(":")
    if len(parts) != 6:
        return norm
    try:
        first_canonical = int(parts[0], 16) & 0xF1
    except ValueError:
        return ":".join(parts[:5])
    return f"{first_canonical:02x}:" + ":".join(parts[1:5])


def _ap_root_unmasked_from_bssid(bssid: str) -> str:
    """The pre-LA-mask rule (upper 5 bytes), used by downgrade()."""
    norm = (bssid or "").lower().strip()
    parts = norm.split(":")
    if len(parts) != 6:
        return norm
    return ":".join(parts[:5])


def upgrade() -> None:
    bind = op.get_bind()

    # 1) scan_neighbors : recompute ap_root from bssid with the new
    #    masked rule. Batch updates by new ap_root to keep the SQL
    #    statement count low (2K-row scans churn fast otherwise).
    rows = bind.execute(
        sa.text("SELECT id, bssid FROM scan_neighbors")
    ).fetchall()
    by_root: dict[str, list[int]] = {}
    for rid, bssid in rows:
        nr = _ap_root_masked(bssid or "")
        by_root.setdefault(nr, []).append(rid)
    for new_root, ids in by_root.items():
        for chunk_start in range(0, len(ids), 500):
            chunk = ids[chunk_start : chunk_start + 500]
            bind.execute(
                sa.text(
                    "UPDATE scan_neighbors SET ap_root = :nr "
                    "WHERE id IN ("
                    + ",".join(str(i) for i in chunk)
                    + ")"
                ),
                {"nr": new_root},
            )

    # 2) ap_reviews : recompute via sample_bssid when set ; otherwise
    #    mask the existing ap_root in place. Resolve UNIQUE collisions.
    review_rows = bind.execute(
        sa.text(
            "SELECT id, device_slug, ap_root, sample_bssid, reviewed_at "
            "FROM ap_reviews"
        )
    ).fetchall()

    def _mask_existing(old: str) -> str:
        # old is already upper-5-bytes (set by previous migration). We
        # just need to re-mask the first octet.
        parts = (old or "").split(":")
        if len(parts) != 5:
            return old
        try:
            first_canonical = int(parts[0], 16) & 0xF1
        except ValueError:
            return old
        return f"{first_canonical:02x}:" + ":".join(parts[1:])

    survivors: dict[tuple[str, str], tuple[int, str | None, str]] = {}
    drop_ids: list[int] = []
    for row in review_rows:
        rid, slug, old_root, sample_bssid, reviewed = row
        if sample_bssid:
            new_root = _ap_root_masked(sample_bssid)
        else:
            new_root = _mask_existing(old_root or "")
        key = (slug, new_root)
        prev = survivors.get(key)
        if prev is None:
            survivors[key] = (rid, reviewed, new_root)
            continue
        prev_id, prev_rev, _ = prev
        new_wins = (
            (reviewed is not None and (prev_rev is None or reviewed > prev_rev))
            or (reviewed == prev_rev and rid > prev_id)
        )
        if new_wins:
            drop_ids.append(prev_id)
            survivors[key] = (rid, reviewed, new_root)
        else:
            drop_ids.append(rid)

    if drop_ids:
        for chunk_start in range(0, len(drop_ids), 500):
            chunk = drop_ids[chunk_start : chunk_start + 500]
            bind.execute(
                sa.text(
                    "DELETE FROM ap_reviews WHERE id IN ("
                    + ",".join(str(i) for i in chunk)
                    + ")"
                )
            )

    for (_, _), (rid, _, new_root) in survivors.items():
        bind.execute(
            sa.text(
                "UPDATE ap_reviews SET ap_root = :nr WHERE id = :rid"
            ),
            {"nr": new_root, "rid": rid},
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, bssid FROM scan_neighbors")
    ).fetchall()
    by_root: dict[str, list[int]] = {}
    for rid, bssid in rows:
        nr = _ap_root_unmasked_from_bssid(bssid or "")
        by_root.setdefault(nr, []).append(rid)
    for new_root, ids in by_root.items():
        for chunk_start in range(0, len(ids), 500):
            chunk = ids[chunk_start : chunk_start + 500]
            bind.execute(
                sa.text(
                    "UPDATE scan_neighbors SET ap_root = :nr "
                    "WHERE id IN ("
                    + ",".join(str(i) for i in chunk)
                    + ")"
                ),
                {"nr": new_root},
            )
    # ap_reviews left alone — see header doc.
