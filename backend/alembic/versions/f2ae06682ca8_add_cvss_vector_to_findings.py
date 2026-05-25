"""add cvss_vector column to vulnerability_findings

Revision ID: f2ae06682ca8
Revises: 8d2cdb323bcd
Create Date: 2026-05-21 20:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2ae06682ca8"
down_revision: Union[str, Sequence[str], None] = "8d2cdb323bcd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("vulnerability_findings") as batch:
        batch.add_column(sa.Column("cvss_vector", sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("vulnerability_findings") as batch:
        batch.drop_column("cvss_vector")
