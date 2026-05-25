"""add security: inventory snapshots + vulnerability findings + acks

Revision ID: ce8ceda46259
Revises: 08d38c7b0ee4
Create Date: 2026-05-21 16:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ce8ceda46259"
down_revision: Union[str, Sequence[str], None] = "08d38c7b0ee4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_inventory_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("openwrt_distrib_id", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("openwrt_release", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("openwrt_target", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("openwrt_arch", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("openwrt_taints", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("firmware_version", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("kernel", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("board_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("hostname", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("packages_json", sa.JSON(), nullable=False),
        sa.Column("package_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scan_status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("scan_error", sa.String(length=512), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_device_inventory_snapshots_device_taken",
        "device_inventory_snapshots",
        ["device_id", "taken_at"],
    )

    op.create_table(
        "vulnerability_findings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("cve_id", sa.String(length=64), nullable=False),
        sa.Column("package_name", sa.String(length=128), nullable=False),
        sa.Column("package_version", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("fixed_in", sa.String(length=64), nullable=True),
        sa.Column("url", sa.String(length=512), nullable=True),
        sa.Column("summary", sa.String(length=2048), nullable=False, server_default=""),
        sa.Column("cvss_score", sa.Float(), nullable=True),
        sa.Column("aliases_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["device_inventory_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id", "source", "cve_id", "package_name",
            name="uq_vuln_finding_dedup",
        ),
    )
    op.create_index(
        "ix_vulnerability_findings_snapshot",
        "vulnerability_findings",
        ["snapshot_id"],
    )

    op.create_table(
        "vulnerability_acknowledgements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cve_id", sa.String(length=64), nullable=False),
        sa.Column("package_name", sa.String(length=128), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(length=512), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cve_id", "package_name", name="uq_vuln_ack_pair"),
    )


def downgrade() -> None:
    op.drop_table("vulnerability_acknowledgements")
    op.drop_index(
        "ix_vulnerability_findings_snapshot", table_name="vulnerability_findings"
    )
    op.drop_table("vulnerability_findings")
    op.drop_index(
        "ix_device_inventory_snapshots_device_taken",
        table_name="device_inventory_snapshots",
    )
    op.drop_table("device_inventory_snapshots")
