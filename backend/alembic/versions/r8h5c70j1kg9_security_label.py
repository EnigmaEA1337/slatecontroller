"""security_label on devices — tamper-evident sticker tracking

Revision ID: r8h5c70j1kg9
Revises: q7g4b69i0jf8
Create Date: 2026-06-09 23:00:00

Adds a short string column on `devices` that stores the serial number
written on the tamper-evident sticker covering the device screws. The
operator types it in once at adoption. Later, a physical inspection
that finds a different sticker number = chassis was opened in between.

Free-form string (printers use different schemes : 8-char alnum, 6-digit
numeric, QR-side serials…), max 64 chars. Empty string = no sticker
tracked.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "r8h5c70j1kg9"
down_revision = "q7g4b69i0jf8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(
            sa.Column(
                "security_label",
                sa.String(length=64),
                nullable=False,
                server_default="",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("security_label")
