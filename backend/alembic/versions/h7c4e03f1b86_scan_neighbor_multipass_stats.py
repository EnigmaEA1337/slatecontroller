"""scan_neighbors : multi-pass stats (seen_count, rssi_max/min, offsets)

Revision ID: h7c4e03f1b86
Revises: g6b3d92e0f75
Create Date: 2026-06-03 11:45:00

Adds accumulated statistics for multi-pass scans (see
:func:`app.wifi.scanner.scan_band_extended`). When a long-running scan
loops several passes, each BSSID is seen 1..N times :

  - seen_count           number of passes that observed it
  - rssi_max / rssi_min  strongest / weakest RSSI across passes (an
                         AP that drifts between -55 and -85 dBm is more
                         likely a mobile device than a fixed installation)
  - first_seen_offset_s  seconds after scan start when first seen
  - last_seen_offset_s   seconds after scan start when last seen

For single-pass scans (legacy + the "Standard" button), seen_count=1
and the two RSSI extrema equal ``rssi_dbm``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "h7c4e03f1b86"
down_revision = "g6b3d92e0f75"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scan_neighbors") as batch:
        batch.add_column(
            sa.Column(
                "seen_count", sa.Integer(),
                nullable=False, server_default="1",
            ),
        )
        batch.add_column(
            sa.Column(
                "rssi_max", sa.Integer(),
                nullable=False, server_default="-100",
            ),
        )
        batch.add_column(
            sa.Column(
                "rssi_min", sa.Integer(),
                nullable=False, server_default="-100",
            ),
        )
        batch.add_column(
            sa.Column(
                "first_seen_offset_s", sa.Float(),
                nullable=False, server_default="0.0",
            ),
        )
        batch.add_column(
            sa.Column(
                "last_seen_offset_s", sa.Float(),
                nullable=False, server_default="0.0",
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("scan_neighbors") as batch:
        batch.drop_column("last_seen_offset_s")
        batch.drop_column("first_seen_offset_s")
        batch.drop_column("rssi_min")
        batch.drop_column("rssi_max")
        batch.drop_column("seen_count")
