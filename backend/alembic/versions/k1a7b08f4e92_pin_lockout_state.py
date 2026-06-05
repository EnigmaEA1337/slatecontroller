"""pin_lockout_state — anti-bruteforce counter for PIN verification

Revision ID: k1a7b08f4e92
Revises: j9e6a25b3d08
Create Date: 2026-06-03 14:30:00

Backs the Q-PIN lockout feature : whenever a UI flow asks the operator
to confirm a sensitive action with their touchscreen PIN, the verifier
calls into :class:`PinLockoutService`. The service keeps a per-
``(device_slug, scope)`` row here :

  - failed_count    failed attempts within the rolling 60s window
  - locked_until    after 3 failures, no verification accepted before this
  - last_attempt_at used to expire the window (a 65s-old failure counts as
                    a fresh slate)

The ``scope`` column lets future flows (at-rest encryption unlock,
factory-reset confirmation, sensitive setting reveal…) maintain
independent counters — burning attempts on one doesn't lock the others.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "k1a7b08f4e92"
down_revision = "j9e6a25b3d08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pin_lockout_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_slug", sa.String(length=64), nullable=False),
        sa.Column(
            "scope", sa.String(length=32),
            nullable=False, server_default="controller_verify",
        ),
        sa.Column(
            "failed_count", sa.Integer(),
            nullable=False, server_default="0",
        ),
        sa.Column(
            "locked_until", sa.DateTime(timezone=False), nullable=True,
        ),
        sa.Column(
            "last_attempt_at", sa.DateTime(timezone=False), nullable=True,
        ),
        sa.UniqueConstraint(
            "device_slug", "scope", name="uq_pin_lockout_device_scope",
        ),
    )
    op.create_index(
        "ix_pin_lockout_device_slug", "pin_lockout_state", ["device_slug"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pin_lockout_device_slug", table_name="pin_lockout_state",
    )
    op.drop_table("pin_lockout_state")
