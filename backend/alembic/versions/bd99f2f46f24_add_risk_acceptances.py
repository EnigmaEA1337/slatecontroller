"""add vulnerability_risk_acceptances table

Revision ID: bd99f2f46f24
Revises: f2ae06682ca8
Create Date: 2026-05-21 20:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "bd99f2f46f24"
down_revision: Union[str, Sequence[str], None] = "f2ae06682ca8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vulnerability_risk_acceptances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cve_id", sa.String(length=64), nullable=False),
        sa.Column("package_name", sa.String(length=128), nullable=False),
        sa.Column("accepted_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cve_id", "package_name", name="uq_vuln_risk_pair"),
    )


def downgrade() -> None:
    op.drop_table("vulnerability_risk_acceptances")
