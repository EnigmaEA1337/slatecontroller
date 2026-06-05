"""pcap_captures — LAN tcpdump capture history

Revision ID: o5e2f47g8hd6
Revises: n4d1e36f7gc5
Create Date: 2026-06-05 06:30:00

Each row tracks one LAN-side tcpdump capture session run on the Slate :
iface, duration, BPF filter, current status, output file path, captured
byte count. The pcap binary stays on the Slate at ``/tmp/slate-ctrl-
pcap-<id>.pcap`` while the capture is running ; the controller pulls
it on download.

Phase 1 limitation : 802.11 monitor capture isn't possible on MT7990
(driver only lists managed / AP / AP/VLAN), so this captures L2/L3
LAN traffic only (br-lan, eth0, tailscale0…). A Phase 2 with USB
dongle support will lift that.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "o5e2f47g8hd6"
down_revision = "n4d1e36f7gc5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pcap_captures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_slug", sa.String(length=64), nullable=False),
        sa.Column("iface", sa.String(length=32), nullable=False),
        sa.Column("duration_s", sa.Integer(), nullable=False),
        sa.Column("snaplen", sa.Integer(), nullable=False, server_default="256"),
        sa.Column(
            "filter_expr", sa.String(length=512),
            nullable=False, server_default="",
        ),
        # planned | running | completed | failed | cancelled
        sa.Column(
            "status", sa.String(length=16),
            nullable=False, server_default="planned",
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=False), nullable=False,
        ),
        sa.Column(
            "ended_at", sa.DateTime(timezone=False), nullable=True,
        ),
        sa.Column(
            "bytes_captured", sa.Integer(),
            nullable=False, server_default="0",
        ),
        sa.Column(
            "remote_path", sa.String(length=256),
            nullable=False, server_default="",
        ),
        sa.Column(
            "remote_pid", sa.Integer(), nullable=True,
        ),
        sa.Column(
            "error", sa.String(length=512),
            nullable=False, server_default="",
        ),
        sa.Column(
            "label", sa.String(length=128),
            nullable=False, server_default="",
        ),
    )
    op.create_index(
        "ix_pcap_captures_device_slug", "pcap_captures", ["device_slug"],
    )


def downgrade() -> None:
    op.drop_index("ix_pcap_captures_device_slug", table_name="pcap_captures")
    op.drop_table("pcap_captures")
