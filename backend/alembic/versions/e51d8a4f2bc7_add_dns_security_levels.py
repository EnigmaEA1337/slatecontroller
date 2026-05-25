"""add dns_security_levels table (editable preset copies)

Stores per-level config that the user can tune (default provider,
blocked services, toggles). The Python `FACTORY_LEVELS` constant is the
seed source + "reset to factory" reference; the DB row is what the
manager reads at apply time.

Revision ID: e51d8a4f2bc7
Revises: c93e74b201ff
Create Date: 2026-05-24 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e51d8a4f2bc7"
down_revision: Union[str, Sequence[str], None] = "c93e74b201ff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dns_security_levels",
        sa.Column("slug", sa.String(length=32), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("icon", sa.String(length=32), nullable=False, server_default="Shield"),
        sa.Column("color", sa.String(length=16), nullable=False, server_default="#3b82f6"),
        sa.Column("default_provider_slug", sa.String(length=64), nullable=False),
        sa.Column("allowed_provider_slugs", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("adguard_filtering", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("safe_browsing", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("parental_control", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("safe_search", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("blocked_services", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("adguard_blocklist_slugs", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("require_dot", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("require_dnssec", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("eu_only", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("intensity", sa.String(length=16), nullable=False, server_default="balanced"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )


def downgrade() -> None:
    op.drop_table("dns_security_levels")
