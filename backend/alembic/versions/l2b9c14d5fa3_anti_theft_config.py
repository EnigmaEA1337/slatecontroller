"""anti_theft_config — autonomous mode + auto-erase settings

Revision ID: l2b9c14d5fa3
Revises: k1a7b08f4e92
Create Date: 2026-06-03 18:00:00

Per-device anti-theft policy :

  - autonomous_mode      master switch (OFF = pure lockout, no wipe)
  - failure_threshold    cumulative PIN failures before action fires
  - action               "alert" | "soft_wipe" (factory_reset deferred)
  - notify_webhook_url   optional ; called BEFORE action
  - total_failures       running counter, reset on success
  - last_action_at       audit ; last time an action fired
  - last_action_kind     "alert" / "soft_wipe" — what fired
  - last_action_note     human-readable summary of what got cleared

The ``total_failures`` counter is distinct from the lockout's
``failed_count`` : the latter resets on every 60s rolling window, the
former only resets on a *successful* verification. So an attacker who
exhausts the 3-try lockout, waits 60s, exhausts another 3, etc. still
trips the auto-erase threshold.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "l2b9c14d5fa3"
down_revision = "k1a7b08f4e92"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "anti_theft_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_slug", sa.String(length=64), nullable=False),
        sa.Column(
            "autonomous_mode", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
        sa.Column(
            "failure_threshold", sa.Integer(),
            nullable=False, server_default="10",
        ),
        sa.Column(
            "action", sa.String(length=16),
            nullable=False, server_default="alert",
        ),
        sa.Column(
            "notify_webhook_url", sa.String(length=256),
            nullable=False, server_default="",
        ),
        sa.Column(
            "total_failures", sa.Integer(),
            nullable=False, server_default="0",
        ),
        sa.Column(
            "last_action_at", sa.DateTime(timezone=False), nullable=True,
        ),
        sa.Column(
            "last_action_kind", sa.String(length=16),
            nullable=False, server_default="",
        ),
        sa.Column(
            "last_action_note", sa.String(length=512),
            nullable=False, server_default="",
        ),
        sa.UniqueConstraint(
            "device_slug", name="uq_anti_theft_config_device",
        ),
    )


def downgrade() -> None:
    op.drop_table("anti_theft_config")
