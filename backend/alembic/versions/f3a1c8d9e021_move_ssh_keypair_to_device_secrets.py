"""move ssh keypair from app_secrets to device_secrets

Revision ID: f3a1c8d9e021
Revises: f72b9d1a3e08
Create Date: 2026-05-25 12:00:00

Pre-multi-device, the controller-Slate SSH private key lived in
`app_secrets[key='slate_ssh_keypair']`. With multi-device support, each
adopted device has its own keypair, stored in
`device_secrets[device_id, kind='ssh_keypair']`.

This migration moves the existing keypair (if any) onto the **default**
device row — that's where the singleton previously implicitly belonged.
Then it deletes the source row so callers can't accidentally read a
stale copy.

Rollback strategy : copy back to `app_secrets`. The fingerprint +
public key in metadata are preserved either direction, so revoke /
redeploy after rollback is not required.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "f3a1c8d9e021"
down_revision = "f72b9d1a3e08"
branch_labels = None
depends_on = None


_LEGACY_KEY = "slate_ssh_keypair"
_KIND = "ssh_keypair"


def _json_loads(v):
    """Tolerate metadata_json being either dict (SQLAlchemy) or text (SQLite)."""
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8")
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return {}


def upgrade() -> None:
    bind = op.get_bind()

    # Fetch the legacy row, if any.
    legacy = bind.execute(
        sa.text(
            "SELECT encrypted_value, metadata_json, created_at "
            "FROM app_secrets WHERE key = :k"
        ),
        {"k": _LEGACY_KEY},
    ).first()
    if legacy is None:
        return  # nothing to migrate

    # Find the default device. If multiple match (shouldn't), pick the
    # lowest id deterministically. If none match, fall back to the
    # lowest-id device — that mirrors how the lifespan picks one on
    # cold boot.
    default = bind.execute(
        sa.text(
            "SELECT id FROM devices WHERE is_default = 1 ORDER BY id LIMIT 1"
        )
    ).first()
    if default is None:
        default = bind.execute(
            sa.text("SELECT id FROM devices ORDER BY id LIMIT 1")
        ).first()
    if default is None:
        # No devices at all — leave the legacy row untouched so the
        # lifespan can recover it on the next boot. The new SSHKeypairStore
        # API will simply report "no keypair" until a device exists.
        return
    device_id = default[0]

    encrypted_value, metadata_json, created_at = (
        legacy[0], legacy[1], legacy[2],
    )

    # Only insert if there's no existing per-device row already — preserves
    # idempotency across re-runs and protects against accidentally
    # overwriting a freshly-generated key.
    existing = bind.execute(
        sa.text(
            "SELECT id FROM device_secrets "
            "WHERE device_id = :d AND kind = :k LIMIT 1"
        ),
        {"d": device_id, "k": _KIND},
    ).first()
    if existing is None:
        bind.execute(
            sa.text(
                "INSERT INTO device_secrets "
                "(device_id, kind, encrypted_value, metadata_json, created_at, updated_at) "
                "VALUES (:d, :k, :ev, :m, :c, :c)"
            ),
            {
                "d": device_id,
                "k": _KIND,
                "ev": encrypted_value,
                # metadata_json column is JSON in the model but text in SQLite.
                # Pass a JSON string either way — SQLAlchemy round-trips it.
                "m": json.dumps(_json_loads(metadata_json)),
                "c": created_at,
            },
        )

    # Drop the source so old read paths can't grab a stale copy.
    bind.execute(
        sa.text("DELETE FROM app_secrets WHERE key = :k"),
        {"k": _LEGACY_KEY},
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Find the default device's keypair (or the first device's, mirror of upgrade).
    default = bind.execute(
        sa.text(
            "SELECT id FROM devices WHERE is_default = 1 ORDER BY id LIMIT 1"
        )
    ).first()
    if default is None:
        default = bind.execute(
            sa.text("SELECT id FROM devices ORDER BY id LIMIT 1")
        ).first()
    if default is None:
        return
    device_id = default[0]

    row = bind.execute(
        sa.text(
            "SELECT encrypted_value, metadata_json, created_at "
            "FROM device_secrets "
            "WHERE device_id = :d AND kind = :k LIMIT 1"
        ),
        {"d": device_id, "k": _KIND},
    ).first()
    if row is None:
        return

    encrypted_value, metadata_json, created_at = row[0], row[1], row[2]

    # Re-insert into app_secrets only if the legacy key isn't there.
    existing = bind.execute(
        sa.text("SELECT key FROM app_secrets WHERE key = :k"),
        {"k": _LEGACY_KEY},
    ).first()
    if existing is None:
        bind.execute(
            sa.text(
                "INSERT INTO app_secrets "
                "(key, encrypted_value, metadata_json, created_at, updated_at) "
                "VALUES (:k, :ev, :m, :c, :c)"
            ),
            {
                "k": _LEGACY_KEY,
                "ev": encrypted_value,
                "m": json.dumps(_json_loads(metadata_json)),
                "c": created_at,
            },
        )

    # Drop the per-device copy.
    bind.execute(
        sa.text(
            "DELETE FROM device_secrets WHERE device_id = :d AND kind = :k"
        ),
        {"d": device_id, "k": _KIND},
    )
