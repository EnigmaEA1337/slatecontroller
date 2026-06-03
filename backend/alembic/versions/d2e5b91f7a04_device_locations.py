"""device_locations — per-device location history

Revision ID: d2e5b91f7a04
Revises: c1d8a4f25b3e
Create Date: 2026-06-03 11:00:00

A device (Slate) is mobile : it can move between office / mission /
home. Each location entry captures one such point with timestamp,
label, source. The most recent entry is the device's "current"
location, used to stamp new scans by default.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "d2e5b91f7a04"
down_revision = "c1d8a4f25b3e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_locations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("device_slug", sa.String(length=64), nullable=False, index=True),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("accuracy_m", sa.Float(), nullable=True),
        sa.Column(
            "source", sa.String(length=16), nullable=False, server_default="manual",
        ),
        sa.Column("label", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("note", sa.String(length=256), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.current_timestamp(), index=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("device_locations")
