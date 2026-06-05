"""surveillance_sessions + session_id on scan_history

Revision ID: j9e6a25b3d08
Revises: i8d5f14a2c97
Create Date: 2026-06-03 13:00:00

Backs the Q2-C "Surveillance session" feature : a named, time-bounded
period of intensive scanning. The session schedules its own
``IntervalTrigger`` job that runs ``scan_band()`` at ``interval_s``
on each band in ``bands`` (CSV "2,5" or "5,6"…) until the target
duration is spent.

Each persisted scan_history row gets a nullable ``session_id`` FK so
the session can compute analytics across its passes (presence ratio,
RSSI drift, stable / edge / drifting / transient classification).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "j9e6a25b3d08"
down_revision = "i8d5f14a2c97"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "surveillance_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        # active / completed / cancelled / failed
        sa.Column(
            "status", sa.String(length=16),
            nullable=False, server_default="active",
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=False), nullable=False,
        ),
        sa.Column(
            "ended_at", sa.DateTime(timezone=False), nullable=True,
        ),
        sa.Column(
            "target_duration_s", sa.Integer(), nullable=False,
        ),
        sa.Column(
            "interval_s", sa.Integer(),
            nullable=False, server_default="60",
        ),
        # CSV of bands : "2,5" / "5" / "2,5,6"
        sa.Column(
            "bands", sa.String(length=8),
            nullable=False, server_default="5",
        ),
        sa.Column("location_lat", sa.Float(), nullable=True),
        sa.Column("location_lon", sa.Float(), nullable=True),
        sa.Column(
            "location_label", sa.String(length=128),
            nullable=False, server_default="",
        ),
        sa.Column(
            "note", sa.String(length=1024),
            nullable=False, server_default="",
        ),
        sa.Column(
            "total_passes", sa.Integer(),
            nullable=False, server_default="0",
        ),
        sa.Column(
            "unique_bssids", sa.Integer(),
            nullable=False, server_default="0",
        ),
    )
    op.create_index(
        "ix_surveillance_sessions_device_slug",
        "surveillance_sessions", ["device_slug"],
    )
    op.create_index(
        "ix_surveillance_sessions_status",
        "surveillance_sessions", ["status"],
    )

    # Attach scan_history rows to a session (nullable — manual & ambient
    # scans live without a session). SQLite's batch_alter_table needs
    # every FK to carry an explicit name.
    with op.batch_alter_table("scan_history") as batch:
        batch.add_column(
            sa.Column("session_id", sa.Integer(), nullable=True),
        )
        batch.create_foreign_key(
            "fk_scan_history_session_id",
            "surveillance_sessions",
            ["session_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_scan_history_session_id", ["session_id"])


def downgrade() -> None:
    with op.batch_alter_table("scan_history") as batch:
        batch.drop_index("ix_scan_history_session_id")
        batch.drop_column("session_id")
    op.drop_index(
        "ix_surveillance_sessions_status",
        table_name="surveillance_sessions",
    )
    op.drop_index(
        "ix_surveillance_sessions_device_slug",
        table_name="surveillance_sessions",
    )
    op.drop_table("surveillance_sessions")
