"""ap_reviews — per-AP-root review status (trusted/known/ignored/suspicious)

Revision ID: f5a2c81d9e64
Revises: e3b1c9f02a17
Create Date: 2026-06-03 14:00:00

Review is keyed by ``ap_root`` (the physical-AP cluster id), not BSSID,
so a single decision covers every VAP of the same radio. Status drives :

  - UI badges + filter
  - Air Watch suppression on ``trusted`` (no evil-twin / strong-neighbour
    alerts for a known-good AP)

``device_slug`` is part of the unique constraint : the same SSID could
exist in the user's office on one device's scan and in a hotel for
another. Reviews stay per-device.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "f5a2c81d9e64"
down_revision = "e3b1c9f02a17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ap_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_slug", sa.String(length=64), nullable=False),
        sa.Column("ap_root", sa.String(length=32), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False,
            server_default="known",
        ),
        sa.Column("label", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("note", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("vendor", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("sample_ssids", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("sample_bssid", sa.String(length=17), nullable=False, server_default=""),
        sa.Column("band", sa.String(length=2), nullable=False, server_default=""),
        sa.Column("channel", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "reviewed_at", sa.DateTime(timezone=False),
            nullable=False, server_default=sa.func.current_timestamp(),
        ),
        sa.Column("reviewed_by", sa.String(length=64), nullable=False, server_default=""),
        sa.UniqueConstraint("device_slug", "ap_root", name="uq_ap_reviews_device_root"),
    )
    op.create_index("ix_ap_reviews_device_slug", "ap_reviews", ["device_slug"])
    op.create_index("ix_ap_reviews_ap_root", "ap_reviews", ["ap_root"])


def downgrade() -> None:
    op.drop_index("ix_ap_reviews_ap_root", table_name="ap_reviews")
    op.drop_index("ix_ap_reviews_device_slug", table_name="ap_reviews")
    op.drop_table("ap_reviews")
