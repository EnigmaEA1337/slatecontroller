"""tailnet_destinations on networks — per-CIDR reverse routing

Revision ID: p6f3a58h9ie7
Revises: o5e2f47g8hd6
Create Date: 2026-06-09 21:00:00

Adds a JSON column on the `networks` table that holds a list of tailnet
subnets THIS LAN is allowed to reach, with the NAT mode applied per
entry :

    [
      {"cidr": "10.13.69.0/24", "mode": "routed"},
      {"cidr": "10.13.14.0/24", "mode": "snat"}
    ]

The firewall reconciler translates this into per-pair iptables rules
(`src=<lan-cidr>, dst=<dest-cidr>` -> ACCEPT, with an optional SNAT in
POSTROUTING when mode == "snat"). Empty list means "no reverse routing"
— the LAN cannot reach any tailnet peer.

This replaces the previous coarse "src lan -> dst tailscale0 ACCEPT
everything" model (PR before this one) with fine-grained per-destination
control. The earlier rule set, if present, is left alone — the apply
pipeline will rewrite it from this new state on the first apply.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "p6f3a58h9ie7"
down_revision = "o5e2f47g8hd6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite + JSON : the column is added with a default of '[]' for
    # backwards compat — every existing network starts with no tailnet
    # destinations allowed (matches the prior off-by-default behaviour).
    with op.batch_alter_table("networks") as batch:
        batch.add_column(
            sa.Column(
                "tailnet_destinations",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("networks") as batch:
        batch.drop_column("tailnet_destinations")
