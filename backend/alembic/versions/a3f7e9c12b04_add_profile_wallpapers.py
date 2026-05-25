"""add profile_wallpapers table

Stores user-uploaded background images attached to a profile. 1:1 with
profiles.name; cascade-delete + rename handled application-side (we use
profile_name as a logical FK without a hard SQLite FK to avoid migration
quirks on older SQLite versions).

Revision ID: a3f7e9c12b04
Revises: 281b831b96da
Create Date: 2026-05-23 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3f7e9c12b04"
down_revision: Union[str, Sequence[str], None] = "281b831b96da"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "profile_wallpapers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_name", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("profile_name", name="uq_profile_wallpapers_profile_name"),
    )


def downgrade() -> None:
    op.drop_table("profile_wallpapers")
