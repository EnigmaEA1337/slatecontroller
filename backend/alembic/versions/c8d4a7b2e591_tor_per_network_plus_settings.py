"""tor: per-network routing + global settings + bridges

Revision ID: c8d4a7b2e591
Revises: a1d7f3b52e89
Create Date: 2026-05-31 18:00:00

Three changes for the Tor refactor :

1. Add 3 per-network Tor toggles to ``networks`` :
   - ``tor_route_mode`` (off|transparent|socks_only, default off)
   - ``tor_dns_over_tor`` (bool, default false)
   - ``tor_kill_switch`` (bool, default false)
   All existing rows default to OFF — opt-in per network.

2. Create ``tor_settings`` (singleton row id=1) holding the global daemon
   master switch + a "use bridges" flag.

3. Create ``tor_bridges`` (one row per pasted obfs4 / webtunnel / etc.
   line) so the user can paste many and toggle them individually.

Bridges aren't stored in JSON on ``tor_settings`` because they're a
1-to-N relationship that benefits from CRUD endpoints.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "c8d4a7b2e591"
down_revision = "a1d7f3b52e89"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Per-network Tor toggles.
    with op.batch_alter_table("networks") as batch:
        batch.add_column(
            sa.Column(
                "tor_route_mode", sa.String(16),
                nullable=False, server_default=sa.text("'off'"),
            ),
        )
        batch.add_column(
            sa.Column(
                "tor_dns_over_tor", sa.Boolean(),
                nullable=False, server_default=sa.text("0"),
            ),
        )
        batch.add_column(
            sa.Column(
                "tor_kill_switch", sa.Boolean(),
                nullable=False, server_default=sa.text("0"),
            ),
        )

    # 2. Global daemon settings (singleton).
    op.create_table(
        "tor_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "daemon_enabled", sa.Boolean(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "use_bridges", sa.Boolean(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # 3. Bridges.
    op.create_table(
        "tor_bridges",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "kind", sa.String(16),
            nullable=False, server_default=sa.text("'obfs4'"),
        ),
        sa.Column("bridge_line", sa.String(512), nullable=False),
        sa.Column(
            "note", sa.String(128),
            nullable=False, server_default=sa.text("''"),
        ),
        sa.Column(
            "enabled", sa.Boolean(),
            nullable=False, server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    op.drop_table("tor_bridges")
    op.drop_table("tor_settings")
    with op.batch_alter_table("networks") as batch:
        batch.drop_column("tor_kill_switch")
        batch.drop_column("tor_dns_over_tor")
        batch.drop_column("tor_route_mode")
