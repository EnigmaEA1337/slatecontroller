"""scan_neighbors : ap_root + vendor + is_randomized

Revision ID: e3b1c9f02a17
Revises: d2e5b91f7a04
Create Date: 2026-06-03 13:00:00

Adds OSINT enrichment columns to ``scan_neighbors`` :

  - ``ap_root``       : physical-AP cluster id (shared 5-byte suffix +
                        channel). Lets the History view show VAPs grouped
                        by physical radio.
  - ``vendor``        : resolved manufacturer string from OUI registry.
  - ``vendor_slug``   : short slug for logo placement.
  - ``is_randomized`` : MAC has the U/L bit set (privacy randomisation).

Existing rows get empty defaults — they won't render the new columns
but legacy history stays intact.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "e3b1c9f02a17"
down_revision = "d2e5b91f7a04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scan_neighbors") as batch:
        batch.add_column(
            sa.Column("ap_root", sa.String(length=32), nullable=False, server_default=""),
        )
        batch.add_column(
            sa.Column("vendor", sa.String(length=128), nullable=False, server_default=""),
        )
        batch.add_column(
            sa.Column("vendor_slug", sa.String(length=32), nullable=False, server_default=""),
        )
        batch.add_column(
            sa.Column(
                "is_randomized", sa.Boolean(),
                nullable=False, server_default=sa.false(),
            ),
        )
        batch.create_index("ix_scan_neighbors_ap_root", ["ap_root"])


def downgrade() -> None:
    with op.batch_alter_table("scan_neighbors") as batch:
        batch.drop_index("ix_scan_neighbors_ap_root")
        batch.drop_column("is_randomized")
        batch.drop_column("vendor_slug")
        batch.drop_column("vendor")
        batch.drop_column("ap_root")
