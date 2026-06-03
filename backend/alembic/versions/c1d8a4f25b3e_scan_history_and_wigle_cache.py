"""scan_history + scan_neighbors + bssid_wigle_cache

Revision ID: c1d8a4f25b3e
Revises: b7e4f1a23c08
Create Date: 2026-06-03 10:00:00

Adds persistence for scan runs and their per-BSSID neighbour records,
plus a local cache of WiGLE.net lookups so the OSINT enrichment
doesn't burn the free-tier quota on every scan.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "c1d8a4f25b3e"
down_revision = "b7e4f1a23c08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scan_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("device_slug", sa.String(length=64), nullable=False, index=True),
        sa.Column("band", sa.String(length=2), nullable=False),
        sa.Column("iface", sa.String(length=16), nullable=False, server_default=""),
        sa.Column(
            "started_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.current_timestamp(), index=True,
        ),
        sa.Column("duration_s", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("accuracy_m", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("neighbors_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("threats_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recommended_channel", sa.Integer(), nullable=True),
        sa.Column("current_channel", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=256), nullable=False, server_default=""),
    )
    op.create_table(
        "scan_neighbors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "scan_id", sa.Integer(),
            sa.ForeignKey("scan_history.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("bssid", sa.String(length=17), nullable=False, index=True),
        sa.Column("ssid", sa.String(length=128), nullable=False, server_default=""),
        sa.Column(
            "hidden", sa.Boolean(), nullable=False, server_default=sa.false(),
        ),
        sa.Column("channel", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("band", sa.String(length=2), nullable=False, server_default=""),
        sa.Column("rssi_dbm", sa.Integer(), nullable=False, server_default="-100"),
        sa.Column("security", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("ht_mode", sa.String(length=16), nullable=False, server_default=""),
        sa.Column(
            "is_wps_enabled", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
    )
    op.create_table(
        "bssid_wigle_cache",
        sa.Column("bssid", sa.String(length=17), primary_key=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("qos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_seen_at", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("last_seen_at", sa.String(length=32), nullable=False, server_default=""),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "not_found", sa.Boolean(), nullable=False, server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_table("bssid_wigle_cache")
    op.drop_table("scan_neighbors")
    op.drop_table("scan_history")
