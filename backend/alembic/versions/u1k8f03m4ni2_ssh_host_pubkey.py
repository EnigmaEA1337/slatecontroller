"""ssh host pubkey TOFU pinning on devices

Revision ID: u1k8f03m4ni2
Revises: t0j7e92l3mi1
Create Date: 2026-06-23 13:20:00

Adds a column to remember the Slate's SSH server pubkey after the first
successful connect. Subsequent connects compare the live pubkey against
the stored one ; a mismatch is treated as a possible MITM (or a
re-flashed device) and refuses to proceed without the operator's
explicit intervention.

Nightly audit 2026-06-23 high finding : the original
``SlateSSH(known_hosts=None)`` accepted ANY host key, which combined
with the LAN/Tailscale failover resolver meant a transparent MITM on
either path could spoof the device. Worse, on ``Permission denied`` the
adoption ``_task_ensure_ssh_access`` would silently re-push the
controller's pubkey to the impostor — escalating a MITM to a full
compromise of the controller's SSH credentials.

The stored value is the raw OpenSSH public-key text (e.g.
``ssh-ed25519 AAAA…``), capped at 512 chars to leave room for RSA-4096
or future hybrid keys. Empty = no TOFU recorded yet (fresh device or
schema-upgraded existing one) ; SlateSSH falls back to permissive mode
on first connect and records the seen key. Operators can clear the
column via the UI to "re-trust" after a deliberate device re-flash.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "u1k8f03m4ni2"
down_revision = "t0j7e92l3mi1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(
            sa.Column(
                "ssh_host_pubkey",
                sa.String(length=512),
                nullable=False,
                server_default="",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("ssh_host_pubkey")
