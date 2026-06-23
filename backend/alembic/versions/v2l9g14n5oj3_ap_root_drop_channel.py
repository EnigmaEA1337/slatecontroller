"""ap_root: drop @channel + switch to upper-5-bytes anchor

Revision ID: v2l9g14n5oj3
Revises: u1k8f03m4ni2
Create Date: 2026-06-23 22:30:00

The original ``_ap_root_for(bssid, channel)`` keyed clusters on the
LOWER 5 bytes of the BSSID plus the channel. Two problems :

1. Adding the channel split a single physical box into one cluster per
   radio (a 2-radio Ruckus would render as two rows in the RF scanner
   UI even though the operator is pointing at one device).
2. Lower-5-bytes only catches UniFi-style firmware where the FIRST
   octet varies (U/L bit flip). Ruckus / Aruba / Cisco vary the LAST
   octet across VAPs (``…:b3:81:20`` → ``:22`` → ``:25`` for the
   hotel ACCORHOTELS Ruckus), so every SSID landed in its own
   cluster (G1/G2/G3/G4 in the screenshot the operator reported).

The new key is the UPPER 5 bytes of the BSSID alone (e.g.
``a8:0b:fb:b3:81`` for the ACCORHOTELS Ruckus). That collapses the
Ruckus-style box correctly, preserves OUI semantics on the cluster id
(vendor lookup still works), and dropping the channel collapses the
multi-radio split. Trade-off : UniFi boxes that flip the U/L bit on
the first octet sub-cluster by anchor MAC instead of collapsing into
one row. We accept that — Ruckus/Aruba/Cisco is the much more
common multi-VAP pattern on observed hotel/corporate networks.

Migration steps :
- ``scan_neighbors`` : recompute every ``ap_root`` from ``bssid``
  using the new rule (upper 5 bytes). No constraint to manage.
- ``ap_reviews`` : recompute via ``sample_bssid`` when available,
  fall back to stripping ``@N`` from the existing value otherwise.
  Resolve collisions on ``(device_slug, ap_root)`` by keeping the
  most-recently-reviewed row.

Reversal restores ``ap_root = bssid`` on scan_neighbors (no way to
reconstruct the lost channel or the old suffix) and leaves
``ap_reviews`` alone (next scan re-keys them the same shape).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "v2l9g14n5oj3"
down_revision = "u1k8f03m4ni2"
branch_labels = None
depends_on = None


def _ap_root_new(bssid: str) -> str:
    """Match the new ``_ap_root_for`` in :mod:`app.wifi.scanner` exactly."""
    norm = (bssid or "").lower().strip()
    parts = norm.split(":")
    if len(parts) != 6:
        return norm
    return ":".join(parts[:5])


def upgrade() -> None:
    bind = op.get_bind()

    # 1) scan_neighbors : recompute ap_root from bssid using the new
    #    upper-5-bytes rule. No constraint to manage.
    rows = bind.execute(
        sa.text("SELECT id, bssid FROM scan_neighbors")
    ).fetchall()
    # Batch updates by new ap_root so we issue O(distinct new_roots)
    # UPDATEs instead of O(rows) — 2500-row scans churn fast otherwise.
    by_root: dict[str, list[int]] = {}
    for rid, bssid in rows:
        nr = _ap_root_new(bssid or "")
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

    # 2) ap_reviews : recompute via sample_bssid when set (the row's
    #    canonical anchor), fall back to stripping ``@N`` otherwise.
    #    Resolve UNIQUE(device_slug, ap_root) collisions by keeping the
    #    most-recently-reviewed row.
    review_rows = bind.execute(
        sa.text(
            "SELECT id, device_slug, ap_root, sample_bssid, reviewed_at "
            "FROM ap_reviews"
        )
    ).fetchall()

    def _strip(ap_root: str) -> str:
        if "@" in ap_root:
            return ap_root.split("@", 1)[0]
        return ap_root

    survivors: dict[tuple[str, str], tuple[int, str | None, str]] = {}
    drop_ids: list[int] = []
    for row in review_rows:
        rid, slug, old_root, sample_bssid, reviewed = row
        if sample_bssid:
            new_root = _ap_root_new(sample_bssid)
        else:
            new_root = _strip(old_root or "")
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
    # We can't reconstruct the original channel suffix — set ap_root
    # back to the row's BSSID so the cluster id at least stays
    # parseable, then let the next scan repopulate it.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE scan_neighbors SET ap_root = bssid "
            "WHERE ap_root NOT LIKE '%@%'"
        )
    )
    # ap_reviews has no bssid column ; leave the stripped values as-is.
    # New scans will hash to the same shape (no @N), so the reviews
    # remain effective after downgrade — they just won't disambiguate
    # by channel anymore. That's the desired outcome of the new model.
