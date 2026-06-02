"""radio_configs + threat_events tables

Revision ID: b7e4f1a23c08
Revises: a4f3d29b8c01
Create Date: 2026-06-03 02:00:00

Adds the per-band radio configuration table (channel/htmode/txpower/
country, keyed by device+band) and the threat events log used by the
AUDIT → Air Watch surface.

Both tables are independent — no cross-FK constraints. Rolling back is
safe : the application falls back to band defaults when ``radio_configs``
is empty, and Air Watch just shows an empty timeline when threat_events
is gone.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "b7e4f1a23c08"
down_revision = "a4f3d29b8c01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "radio_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("device_slug", sa.String(length=64), nullable=False, index=True),
        sa.Column("band", sa.String(length=2), nullable=False),
        sa.Column("channel", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "htmode", sa.String(length=16), nullable=False, server_default="EHT160",
        ),
        sa.Column(
            "txpower_percent", sa.Integer(), nullable=False, server_default="100",
        ),
        sa.Column("country", sa.String(length=2), nullable=False, server_default="FR"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.current_timestamp(),
        ),
        sa.UniqueConstraint(
            "device_slug", "band", name="uq_radio_configs_device_band",
        ),
    )
    op.create_table(
        "threat_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("device_slug", sa.String(length=64), nullable=False, index=True),
        sa.Column("kind", sa.String(length=32), nullable=False, index=True),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("bssid", sa.String(length=17), nullable=False),
        sa.Column("ssid", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("channel", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rssi_dbm", sa.Integer(), nullable=False, server_default="-100"),
        sa.Column("message", sa.String(length=512), nullable=False, server_default=""),
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "dismissed", sa.Boolean(), nullable=False, server_default=sa.false(),
        ),
        sa.Column(
            "dismissed_at", sa.DateTime(timezone=True), nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("threat_events")
    op.drop_table("radio_configs")
