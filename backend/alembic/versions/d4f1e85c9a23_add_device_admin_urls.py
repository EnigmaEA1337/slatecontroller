"""add devices.admin_urls (ordered list of admin URLs with failover)

Replaces the single SLATE_URL env var with a per-device ordered list of
URLs the controller will try in order (LAN first, Tailscale fallback,
WireGuard tunnel, custom IPs, etc.). The first reachable wins.

The legacy `host` column stays as a fallback when `admin_urls` is empty.
On first boot after this migration, existing rows are populated with
[host] (or [SLATE_URL] for the default device) so behavior is unchanged.

Revision ID: d4f1e85c9a23
Revises: e51d8a4f2bc7
Create Date: 2026-05-24 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4f1e85c9a23"
down_revision: Union[str, Sequence[str], None] = "e51d8a4f2bc7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(
            sa.Column(
                "admin_urls",
                sa.JSON,
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("admin_urls")
