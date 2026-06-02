"""wifi: add `hidden` flag to SSIDs

Revision ID: d9a3e5b71402
Revises: c7e1f942d0a5
Create Date: 2026-05-26 10:15:00

The user asked for an SSID-hide checkbox in the Radio UI. Translates
to UCI ``option hidden '1'`` on the wifi-iface section ; the AP then
omits the SSID from beacons. Not actually private (clients still leak
the name in probe requests) but it's a standard UI option and the
column is cheap to carry.

All existing rows default to ``hidden=False`` ; no data migration needed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "d9a3e5b71402"
down_revision = "c7e1f942d0a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("wifi_ssids") as batch:
        batch.add_column(
            sa.Column(
                "hidden", sa.Boolean(),
                nullable=False, server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("wifi_ssids") as batch:
        batch.drop_column("hidden")
