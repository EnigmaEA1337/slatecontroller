"""profile: strip the legacy `adguard` block from stored payloads

Revision ID: e4f2a8c91357
Revises: d9a3e5b71402
Create Date: 2026-05-26 11:00:00

The `adguard` field was removed from the Profile Pydantic model
(see [[models/profile]]) because per-network DNS protection now
fully drives AdGuard's filtering / blocklists / safebrowsing /
parental / safe-search policy. Keeping it at the profile level
created config ambiguity (profile says OFF but a network says
paranoid — which wins ?).

Old rows can still load thanks to ``_drop_legacy_keys`` in the
Pydantic model, but that's a silent runtime band-aid. We strip
the key from disk here so the JSON payloads on disk match the
schema in source.

Idempotent : rows without an `adguard` key are no-ops.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "e4f2a8c91357"
down_revision = "d9a3e5b71402"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, payload FROM profiles"),
    ).fetchall()
    for row_id, payload_raw in rows:
        if not payload_raw:
            continue
        try:
            payload = (
                json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
            )
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if "adguard" not in payload:
            continue
        payload.pop("adguard", None)
        bind.execute(
            sa.text("UPDATE profiles SET payload = :p WHERE id = :id"),
            {"p": json.dumps(payload), "id": row_id},
        )


def downgrade() -> None:
    # Restoring the field with sane defaults so old code paths don't
    # NPE if someone rolls back. We use {enabled: False, lists: []} —
    # which preserves the "AdGuard not used by profile" intent.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, payload FROM profiles"),
    ).fetchall()
    for row_id, payload_raw in rows:
        if not payload_raw:
            continue
        try:
            payload = (
                json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
            )
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if "adguard" in payload:
            continue
        payload["adguard"] = {"enabled": False, "lists": []}
        bind.execute(
            sa.text("UPDATE profiles SET payload = :p WHERE id = :id"),
            {"p": json.dumps(payload), "id": row_id},
        )
