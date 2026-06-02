"""move ssh keypair from device_secrets back to app_secrets

Revision ID: a4f3d29b8c01
Revises: e9b1d5a87c43
Create Date: 2026-06-02 21:00:00

Bug I (2026-06-02) : the keypair is the CONTROLLER's SSH client
identity, not a per-device secret. Migration f3a1c8d9e021 had moved it
from `app_secrets` to `device_secrets` on the (incorrect) reasoning
that multi-device meant per-device key. In reality, multi-device just
means many servers know the same client public key — exactly like one
human laptop's `~/.ssh/id_ed25519` authenticating to N servers.

Symptom : every time the user deleted a device row (or factory-reset a
Slate then re-adopted), the controller's keypair was wiped along with
the device record, forcing a fresh keypair generation. This made
factory resets unnecessarily painful.

This migration restores the original location and preserves the per-
device deployment history inside a `deployed_to: {slug: timestamp}`
ledger nested in the app_secrets metadata.

Rollback : revert to f3a1c8d9e021's flow. The fingerprint + public key
in metadata are preserved either direction.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op


revision = "a4f3d29b8c01"
down_revision = "e9b1d5a87c43"
branch_labels = None
depends_on = None


_APP_KEY = "controller_ssh_keypair"
_DS_KIND = "ssh_keypair"


def _json_loads(v):
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8")
    try:
        return json.loads(v)
    except Exception:  # noqa: BLE001
        return {}


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Find the most-recent device_secrets row with the ssh_keypair kind.
    # If multiple devices were adopted, pick the newest one — its key is
    # what we'll promote to global. The older rows had already had their
    # public side pushed to other devices (the deployment ledger captures
    # that), so we lose nothing by picking the freshest private blob.
    rows = list(bind.execute(sa.text(
        "SELECT ds.device_id, ds.encrypted_value, ds.metadata_json, "
        "       ds.created_at, ds.updated_at, d.slug "
        "FROM device_secrets ds "
        "JOIN devices d ON ds.device_id = d.id "
        "WHERE ds.kind = :kind "
        "ORDER BY ds.updated_at DESC"
    ), {"kind": _DS_KIND}))

    if rows:
        winner = rows[0]
        winner_meta = _json_loads(winner.metadata_json)
        deployed_to: dict[str, str] = {}
        # Replay every (slug, deployed_at) pair from the device_secrets
        # rows that match the WINNING key's fingerprint, so the per-device
        # ledger reflects reality. Rows with a different fingerprint
        # represent stale historical keys — we drop their deployment
        # claims (their public side is no longer trusted).
        winner_fp = winner_meta.get("fingerprint_sha256")
        for r in rows:
            meta = _json_loads(r.metadata_json)
            if meta.get("fingerprint_sha256") != winner_fp:
                continue
            deployed_at = meta.get("deployed_at")
            if deployed_at:
                deployed_to[r.slug] = deployed_at

        global_meta = {
            "public_openssh": winner_meta.get("public_openssh"),
            "fingerprint_sha256": winner_fp,
            "deployed_to": deployed_to,
        }

        # 2. Upsert the row into app_secrets.
        existing = bind.execute(sa.text(
            "SELECT key FROM app_secrets WHERE key = :k"
        ), {"k": _APP_KEY}).first()
        now_iso = datetime.now(UTC).isoformat()
        if existing is None:
            bind.execute(sa.text(
                "INSERT INTO app_secrets "
                "(key, encrypted_value, metadata_json, created_at, updated_at) "
                "VALUES (:k, :v, :m, :c, :u)"
            ), {
                "k": _APP_KEY,
                "v": winner.encrypted_value,
                "m": json.dumps(global_meta),
                "c": winner.created_at or now_iso,
                "u": now_iso,
            })
        else:
            bind.execute(sa.text(
                "UPDATE app_secrets "
                "SET encrypted_value = :v, metadata_json = :m, updated_at = :u "
                "WHERE key = :k"
            ), {
                "k": _APP_KEY,
                "v": winner.encrypted_value,
                "m": json.dumps(global_meta),
                "u": now_iso,
            })

    # 3. Drop every device_secrets row with kind=ssh_keypair. From now on
    # the keypair only lives in app_secrets.
    bind.execute(sa.text(
        "DELETE FROM device_secrets WHERE kind = :kind"
    ), {"kind": _DS_KIND})


def downgrade() -> None:
    bind = op.get_bind()
    row = bind.execute(sa.text(
        "SELECT encrypted_value, metadata_json, created_at "
        "FROM app_secrets WHERE key = :k"
    ), {"k": _APP_KEY}).first()
    if row is None:
        return
    meta = _json_loads(row.metadata_json)
    deployed_to: dict = meta.get("deployed_to") or {}

    # Re-create one device_secrets row per (slug, deployed_at) tuple so
    # the f3a1c8d9e021-era code can read its own state back.
    for slug, deployed_at in deployed_to.items():
        device_row = bind.execute(sa.text(
            "SELECT id FROM devices WHERE slug = :s"
        ), {"s": slug}).first()
        if device_row is None:
            continue
        per_device_meta = {
            "public_openssh": meta.get("public_openssh"),
            "fingerprint_sha256": meta.get("fingerprint_sha256"),
            "deployed_at": deployed_at,
        }
        bind.execute(sa.text(
            "INSERT INTO device_secrets "
            "(device_id, kind, encrypted_value, metadata_json, created_at, updated_at) "
            "VALUES (:did, :kind, :v, :m, :c, :u)"
        ), {
            "did": device_row.id,
            "kind": _DS_KIND,
            "v": row.encrypted_value,
            "m": json.dumps(per_device_meta),
            "c": row.created_at,
            "u": datetime.now(UTC).isoformat(),
        })

    bind.execute(sa.text(
        "DELETE FROM app_secrets WHERE key = :k"
    ), {"k": _APP_KEY})
