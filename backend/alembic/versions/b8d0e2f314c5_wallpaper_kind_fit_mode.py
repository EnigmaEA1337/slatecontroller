"""add kind + fit_mode to profile_wallpapers (one row per profile×kind)

The original schema stored 1 wallpaper per profile (uniq on profile_name).
We now support TWO kinds:
  - 'home' → /etc/gl_screen/wallpaper_home.png
  - 'lock' → /etc/gl_screen/wallpaper_wake_display.png

Plus a fit_mode that controls the resize strategy ('contain' / 'cover' /
'stretch'). Existing rows are reclassified as 'home' + 'contain' so
nothing is lost.

Revision ID: b8d0e2f314c5
Revises: a3f7e9c12b04
Create Date: 2026-05-23 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8d0e2f314c5"
down_revision: Union[str, Sequence[str], None] = "a3f7e9c12b04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch_alter_table is required on SQLite for index/constraint changes:
    # it copies → drops → renames the table under the hood.
    with op.batch_alter_table("profile_wallpapers") as batch:
        batch.add_column(
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="home")
        )
        batch.add_column(
            sa.Column("fit_mode", sa.String(length=16), nullable=False, server_default="contain")
        )
        batch.drop_constraint("uq_profile_wallpapers_profile_name", type_="unique")
        batch.create_unique_constraint(
            "uq_profile_wallpapers_pn_kind", ["profile_name", "kind"]
        )


def downgrade() -> None:
    # Drop any 'lock' wallpapers — the legacy schema can't hold them.
    op.execute("DELETE FROM profile_wallpapers WHERE kind != 'home'")
    with op.batch_alter_table("profile_wallpapers") as batch:
        batch.drop_constraint("uq_profile_wallpapers_pn_kind", type_="unique")
        batch.create_unique_constraint(
            "uq_profile_wallpapers_profile_name", ["profile_name"]
        )
        batch.drop_column("fit_mode")
        batch.drop_column("kind")
