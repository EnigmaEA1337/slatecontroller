"""wifi_ssids : add 8 advanced MTK options

Revision ID: n4d1e36f7gc5
Revises: m3c0d25e6fb4
Create Date: 2026-06-04 15:45:00

Adds the operator-facing advanced section in the SSID edit form,
mapping 1:1 to UCI options that the MTK MT7990 driver respects :

    pmf            → ieee80211w     ("disabled" | "optional" | "required")
    ft_802_11r     → ieee80211r     (bool)
    rrm_802_11k    → ieee80211k     (bool)
    btm_802_11v    → ieee80211v     (bool)
    dtim_period    → dtim_period    (int 1-10, default 2)
    wmm            → wmm            (bool, default True)
    proxy_arp      → proxy_arp      (bool)
    wds            → wds            (bool)

Existing rows get the safe defaults so behaviour doesn't change for
SSIDs created before this migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "n4d1e36f7gc5"
down_revision = "m3c0d25e6fb4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("wifi_ssids") as batch:
        batch.add_column(
            sa.Column(
                "pmf", sa.String(length=16),
                nullable=False, server_default="optional",
            ),
        )
        batch.add_column(
            sa.Column(
                "ft_802_11r", sa.Boolean(),
                nullable=False, server_default=sa.false(),
            ),
        )
        batch.add_column(
            sa.Column(
                "rrm_802_11k", sa.Boolean(),
                nullable=False, server_default=sa.false(),
            ),
        )
        batch.add_column(
            sa.Column(
                "btm_802_11v", sa.Boolean(),
                nullable=False, server_default=sa.false(),
            ),
        )
        batch.add_column(
            sa.Column(
                "dtim_period", sa.Integer(),
                nullable=False, server_default="2",
            ),
        )
        batch.add_column(
            sa.Column(
                "wmm", sa.Boolean(),
                nullable=False, server_default=sa.true(),
            ),
        )
        batch.add_column(
            sa.Column(
                "proxy_arp", sa.Boolean(),
                nullable=False, server_default=sa.false(),
            ),
        )
        batch.add_column(
            sa.Column(
                "wds", sa.Boolean(),
                nullable=False, server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("wifi_ssids") as batch:
        for col in (
            "wds", "proxy_arp", "wmm", "dtim_period",
            "btm_802_11v", "rrm_802_11k", "ft_802_11r", "pmf",
        ):
            batch.drop_column(col)
