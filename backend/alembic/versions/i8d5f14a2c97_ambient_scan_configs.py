"""ambient_scan_configs — per-device-per-band background scan settings

Revision ID: i8d5f14a2c97
Revises: h7c4e03f1b86
Create Date: 2026-06-03 12:30:00

Backs the Q2-A "ambient scan" feature : an APScheduler job per
(device_slug, band) tuple that runs a single-pass ``iw scan`` at
``interval_s`` and persists each pass to ``scan_history`` with
``source="ambient"``.

A daily cleanup job purges ambient scans older than ``retention_days``
(default 7) — manual scans are preserved indefinitely (operator work).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "i8d5f14a2c97"
down_revision = "h7c4e03f1b86"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ambient_scan_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_slug", sa.String(length=64), nullable=False),
        sa.Column("band", sa.String(length=2), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
        sa.Column(
            "interval_s", sa.Integer(),
            nullable=False, server_default="60",
        ),
        sa.Column(
            "retention_days", sa.Integer(),
            nullable=False, server_default="7",
        ),
        sa.Column(
            "last_run_at", sa.DateTime(timezone=False), nullable=True,
        ),
        sa.Column(
            "last_status", sa.String(length=16),
            nullable=False, server_default="",
        ),
        sa.Column(
            "last_error", sa.String(length=512),
            nullable=False, server_default="",
        ),
        sa.UniqueConstraint(
            "device_slug", "band", name="uq_ambient_scan_device_band",
        ),
    )
    op.create_index(
        "ix_ambient_scan_device_slug", "ambient_scan_configs", ["device_slug"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ambient_scan_device_slug", table_name="ambient_scan_configs",
    )
    op.drop_table("ambient_scan_configs")
