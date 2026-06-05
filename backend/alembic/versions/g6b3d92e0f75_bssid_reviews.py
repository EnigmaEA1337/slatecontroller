"""bssid_reviews — per-BSSID review override on top of ap_reviews

Revision ID: g6b3d92e0f75
Revises: f5a2c81d9e64
Create Date: 2026-06-03 15:30:00

Adds the per-BSSID layer of the review system. Effective status of a
neighbour BSSID is :

    bssid_reviews[bssid].status                  if a row exists,
    else ap_reviews[neighbour.ap_root].status    if a row exists,
    else "unknown"                               (implicit, no row).

This lets the operator trust a whole physical AP in one click but still
flag a particular VAP that misbehaves (or vice versa : ignore the
overall AP but explicitly trust one specific SSID for tighter scope).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "g6b3d92e0f75"
down_revision = "f5a2c81d9e64"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bssid_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_slug", sa.String(length=64), nullable=False),
        sa.Column("bssid", sa.String(length=17), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False,
            server_default="known",
        ),
        sa.Column("label", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("note", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("ssid", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("vendor", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("band", sa.String(length=2), nullable=False, server_default=""),
        sa.Column("channel", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "reviewed_at", sa.DateTime(timezone=False),
            nullable=False, server_default=sa.func.current_timestamp(),
        ),
        sa.Column("reviewed_by", sa.String(length=64), nullable=False, server_default=""),
        sa.UniqueConstraint(
            "device_slug", "bssid", name="uq_bssid_reviews_device_bssid",
        ),
    )
    op.create_index("ix_bssid_reviews_device_slug", "bssid_reviews", ["device_slug"])
    op.create_index("ix_bssid_reviews_bssid", "bssid_reviews", ["bssid"])


def downgrade() -> None:
    op.drop_index("ix_bssid_reviews_bssid", table_name="bssid_reviews")
    op.drop_index("ix_bssid_reviews_device_slug", table_name="bssid_reviews")
    op.drop_table("bssid_reviews")
