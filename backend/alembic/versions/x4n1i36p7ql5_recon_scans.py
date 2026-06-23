"""recon scans : ARP / ping sweep / TCP probe / banner grab

Revision ID: x4n1i36p7ql5
Revises: w3m0h25o6pk4
Create Date: 2026-06-23 23:30:00

Adds 3 tables that back the "Reconnaissance WAN" security page :

- ``recon_scans``    : one row per launched scan (status + scope + counters)
- ``recon_hosts``    : one row per discovered IP (per scan, per interface)
- ``recon_ports``    : one row per probed TCP port (per scan, per IP)

Hosts and ports cascade on scan delete so cleaning up an old scan
removes all its derived data in one shot. Per-interface UNIQUE on
hosts and per-(ip,port) UNIQUE on ports so the runner can ``INSERT
ON CONFLICT UPDATE`` to absorb re-discovery (e.g. an IP that
answered both ARP and ping should appear once with source='both',
not twice).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "x4n1i36p7ql5"
down_revision = "w3m0h25o6pk4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recon_scans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_slug", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("scope_json", sa.String(length=2048), nullable=False, server_default="{}"),
        sa.Column("progress", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("error", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("host_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("port_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_recon_scans_device_slug", "recon_scans", ["device_slug"]
    )
    op.create_index(
        "ix_recon_scans_status", "recon_scans", ["status"]
    )
    op.create_index(
        "ix_recon_scans_started_at", "recon_scans", ["started_at"]
    )

    op.create_table(
        "recon_hosts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scan_id",
            sa.Integer(),
            sa.ForeignKey("recon_scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("interface", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("ip", sa.String(length=45), nullable=False),
        sa.Column("mac", sa.String(length=17), nullable=False, server_default=""),
        sa.Column("vendor", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("hostname", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("is_gateway", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_self", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "scan_id", "interface", "ip", name="uq_recon_hosts_scan_iface_ip"
        ),
    )
    op.create_index("ix_recon_hosts_scan_id", "recon_hosts", ["scan_id"])
    op.create_index("ix_recon_hosts_interface", "recon_hosts", ["interface"])
    op.create_index("ix_recon_hosts_ip", "recon_hosts", ["ip"])

    op.create_table(
        "recon_ports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scan_id",
            sa.Integer(),
            sa.ForeignKey("recon_scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ip", sa.String(length=45), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="closed"),
        sa.Column("banner", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("service", sa.String(length=32), nullable=False, server_default=""),
        sa.Column(
            "probed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "scan_id", "ip", "port", name="uq_recon_ports_scan_ip_port"
        ),
    )
    op.create_index("ix_recon_ports_scan_id", "recon_ports", ["scan_id"])
    op.create_index("ix_recon_ports_ip", "recon_ports", ["ip"])


def downgrade() -> None:
    op.drop_index("ix_recon_ports_ip", table_name="recon_ports")
    op.drop_index("ix_recon_ports_scan_id", table_name="recon_ports")
    op.drop_table("recon_ports")
    op.drop_index("ix_recon_hosts_ip", table_name="recon_hosts")
    op.drop_index("ix_recon_hosts_interface", table_name="recon_hosts")
    op.drop_index("ix_recon_hosts_scan_id", table_name="recon_hosts")
    op.drop_table("recon_hosts")
    op.drop_index("ix_recon_scans_started_at", table_name="recon_scans")
    op.drop_index("ix_recon_scans_status", table_name="recon_scans")
    op.drop_index("ix_recon_scans_device_slug", table_name="recon_scans")
    op.drop_table("recon_scans")
