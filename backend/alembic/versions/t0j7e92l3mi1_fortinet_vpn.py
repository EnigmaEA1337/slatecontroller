"""fortinet vpn : config table + per-network egress flags

Revision ID: t0j7e92l3mi1
Revises: s9i6d81k2lh0
Create Date: 2026-06-09 21:30:00

Adds the storage layer for Fortinet SSL VPN (openfortivpn-based) :

  - ``fortinet_configs`` : one row per FortiGate gateway the operator wants
    to reach. Public fields (gateway_host, port, username, trusted_cert
    pin, optional CA PEM, notes) are plaintext for fast listing ; the
    password is Fernet-encrypted in ``fortinet_secrets`` keyed by config
    id, same pattern as device RPC creds. OTP is NEVER stored — the
    operator types it at connect-time on every session (TOTP is short-
    lived enough that storage costs more than it buys).

  - ``networks.egress_via_forti`` (bool, default false) : opt-in flag the
    operator sets per network to route ALL traffic from that bridge
    through the Forti ppp interface. Independent from profile activation
    so the routing config persists across profile switches (a "Mission"
    profile turns the tunnel ON, a "Home" profile turns it OFF — the
    egress flag is the routing intent).

  - ``networks.forti_kill_switch`` (bool, default true) : when set, if the
    Forti tunnel is down the bridge's egress is REJECTed (fail-closed).
    When false, the bridge falls back to WAN in clear (fail-open). Per-
    network because not every subnet has the same risk tolerance — a
    guest network might tolerate fail-open while a mission network must
    not.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "t0j7e92l3mi1"
down_revision = "s9i6d81k2lh0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fortinet_configs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("gateway_host", sa.String(length=255), nullable=False),
        sa.Column("gateway_port", sa.Integer, nullable=False, server_default="443"),
        sa.Column("username", sa.String(length=128), nullable=False),
        # SHA256 hex of the FortiGate server cert (--trusted-cert option in
        # openfortivpn). Empty = no pinning, the client trusts the system
        # CA store. Pinning is strongly recommended for corporate VPNs ;
        # the UI surfaces a "fetch + show fingerprint" helper.
        sa.Column("trusted_cert_sha256", sa.String(length=128), nullable=False, server_default=""),
        # Optional CA PEM the operator can paste when the gateway's chain
        # isn't in the system store (self-signed corporate CAs). Pasted
        # verbatim, openfortivpn invokes --ca-file <path>.
        sa.Column("ca_cert_pem", sa.Text, nullable=False, server_default=""),
        sa.Column("notes", sa.String(length=512), nullable=False, server_default=""),
        # Connection lifecycle state, mirrored from the Slate. Useful to
        # show "UP since HH:MM" badges in the UI without polling the agent
        # on every render.
        sa.Column("last_status", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("slug", name="uq_fortinet_configs_slug"),
    )

    op.create_table(
        "fortinet_secrets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "config_id",
            sa.Integer,
            sa.ForeignKey("fortinet_configs.id", ondelete="CASCADE", name="fk_fortinet_secrets_config_id"),
            nullable=False,
        ),
        # 'password' for now ; kept extensible for future kinds (e.g.
        # 'client_cert_pem', 'client_key_pem').
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="password"),
        sa.Column("encrypted_value", sa.LargeBinary, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("config_id", "kind", name="uq_fortinet_secrets_config_kind"),
    )

    with op.batch_alter_table("networks") as batch:
        batch.add_column(
            sa.Column(
                "egress_via_forti",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch.add_column(
            sa.Column(
                "forti_kill_switch",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("1"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("networks") as batch:
        batch.drop_column("forti_kill_switch")
        batch.drop_column("egress_via_forti")
    op.drop_table("fortinet_secrets")
    op.drop_table("fortinet_configs")
