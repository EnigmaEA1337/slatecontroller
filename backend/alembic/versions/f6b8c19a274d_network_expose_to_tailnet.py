"""network: add `expose_to_tailnet` flag

Revision ID: f6b8c19a274d
Revises: e4f2a8c91357
Create Date: 2026-05-26 11:30:00

Tailscale was always all-or-nothing on the Slate side : either the
daemon ran (and advertised every reachable subnet) or it didn't. The
user's hotel use case needs finer control — neuralcore (Plex etc.)
should be reachable from the phone via Tailscale, but blackice
(hotel-segment, locked down) should NOT be advertised.

This flag lets each network declare whether its CIDR is advertised on
the tailnet. The agent generates ``tailscale up --advertise-routes=…``
from the set of networks that have it set.

All existing rows default to ``False`` — opt-in. Users explicitly
toggle the flag on Networks they want remotely reachable.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "f6b8c19a274d"
down_revision = "e4f2a8c91357"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("networks") as batch:
        batch.add_column(
            sa.Column(
                "expose_to_tailnet", sa.Boolean(),
                nullable=False, server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("networks") as batch:
        batch.drop_column("expose_to_tailnet")
