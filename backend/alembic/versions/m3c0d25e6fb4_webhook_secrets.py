"""webhook_secrets — per-device HMAC secret for Slate → Controller push

Revision ID: m3c0d25e6fb4
Revises: l2b9c14d5fa3
Create Date: 2026-06-03 20:00:00

Per-device shared secret used by the Slate-side ``slate-ctrl-event-push``
helper to HMAC-sign event POSTs to the controller. Auto-provisioned at
device adoption (or on first use), pushed to the Slate at
``/etc/slate-controller/secrets/webhook.secret``.

Rotation : the operator can trigger a rotate via the API — the new secret
is regenerated, pushed to the Slate, the old one stops being accepted
after a 30s grace.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "m3c0d25e6fb4"
down_revision = "l2b9c14d5fa3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_secrets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_slug", sa.String(length=64), nullable=False),
        # hex-encoded 32 random bytes = 64 chars
        sa.Column("secret", sa.String(length=128), nullable=False),
        # Previous secret kept for a short grace window after rotation,
        # so an in-flight push from the Slate isn't rejected.
        sa.Column(
            "previous_secret", sa.String(length=128),
            nullable=False, server_default="",
        ),
        sa.Column(
            "previous_valid_until", sa.DateTime(timezone=False),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=False),
            nullable=False, server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "rotated_at", sa.DateTime(timezone=False), nullable=True,
        ),
        sa.UniqueConstraint(
            "device_slug", name="uq_webhook_secrets_device",
        ),
    )


def downgrade() -> None:
    op.drop_table("webhook_secrets")
