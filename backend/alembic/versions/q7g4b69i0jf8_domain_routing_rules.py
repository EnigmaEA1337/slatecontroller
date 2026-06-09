"""domain_routing_rules on networks — per-domain reverse routing

Revision ID: q7g4b69i0jf8
Revises: p6f3a58h9ie7
Create Date: 2026-06-09 22:30:00

Adds a JSON column on `networks` carrying a list of
`{label, domains[], mode, via}` rules. The slate-ctrl agent translates
each rule into :
  - a `/etc/dnsmasq.d/slate-ctrl-policies.conf` `ipset=` directive per
    domain
  - an iptables mangle PREROUTING rule that MARKs packets whose dst is
    in the rule's ipset
  - one ip rule + ip route entry that policy-routes the marked packets
    out via the chosen egress (tailscale0, WAN, proton, tor)

This complements `tailnet_destinations` (which is CIDR-based) — both
columns can be populated together on the same network.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "q7g4b69i0jf8"
down_revision = "p6f3a58h9ie7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("networks") as batch:
        batch.add_column(
            sa.Column(
                "domain_routing_rules",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("networks") as batch:
        batch.drop_column("domain_routing_rules")
