"""tor: add exit_country_code to tor_settings

Revision ID: e9b1d5a87c43
Revises: c8d4a7b2e591
Create Date: 2026-05-31 20:30:00

Lets the user constrain Tor exit nodes to a single ISO-3166-1 alpha-2
country (e.g. "ch", "de", "se"). The handler emits ``ExitNodes {xx}`` +
``StrictNodes 1`` in torrc when this is set ; empty = no constraint.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "e9b1d5a87c43"
down_revision = "c8d4a7b2e591"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tor_settings") as batch:
        batch.add_column(
            sa.Column(
                "exit_country_code", sa.String(2),
                nullable=False, server_default=sa.text("''"),
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("tor_settings") as batch:
        batch.drop_column("exit_country_code")
