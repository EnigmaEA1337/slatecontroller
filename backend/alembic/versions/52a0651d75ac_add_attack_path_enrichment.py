"""add attack_path column + cve_attack_path_cache table

Revision ID: 52a0651d75ac
Revises: ce8ceda46259
Create Date: 2026-05-21 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "52a0651d75ac"
down_revision: Union[str, Sequence[str], None] = "ce8ceda46259"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite needs batch mode for ALTER TABLE.
    with op.batch_alter_table("vulnerability_findings") as batch:
        batch.add_column(sa.Column("attack_path_json", sa.JSON(), nullable=True))

    op.create_table(
        "cve_attack_path_cache",
        sa.Column("cve_id", sa.String(length=64), nullable=False),
        sa.Column("attack_path_json", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("cve_id"),
    )


def downgrade() -> None:
    op.drop_table("cve_attack_path_cache")
    with op.batch_alter_table("vulnerability_findings") as batch:
        batch.drop_column("attack_path_json")
