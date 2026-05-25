"""add network_dns_protection table

Stores per-network DNS security level mapping. Each row says "network X
uses security level Y with provider Z (optional override)". Applied via
AdGuard Home Clients API by `app.dns.manager.DnsProtectionManager`.

Revision ID: c93e74b201ff
Revises: f2ae06682ca8
Create Date: 2026-05-24 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c93e74b201ff"
down_revision: Union[str, Sequence[str], None] = "b8d0e2f314c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "network_dns_protection",
        sa.Column("network_slug", sa.String(length=64), primary_key=True),
        sa.Column("level_slug", sa.String(length=32), nullable=False),
        sa.Column("provider_slug", sa.String(length=64), nullable=True),
        sa.Column(
            "adguard_client_name",
            sa.String(length=128),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )


def downgrade() -> None:
    op.drop_table("network_dns_protection")
