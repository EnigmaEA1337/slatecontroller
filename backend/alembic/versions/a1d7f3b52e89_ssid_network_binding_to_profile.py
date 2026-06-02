"""move SSID→network binding from wifi_ssids to profile.ssids refs

Revision ID: a1d7f3b52e89
Revises: f6b8c19a274d
Create Date: 2026-05-28 10:00:00

Architectural change : an SSID is a pure layer-2 access definition
(name / bands / security / PSK / isolation). WHICH network (bridge /
subnet) it routes to is contextual — it depends on the active profile,
exactly like a physical switch port. So the `network_slug` binding
moves OFF the wifi_ssids catalog and ONTO each profile's SSID ref.

Data migration :
  1. Snapshot the current wifi_ssids slug → network_slug mapping.
  2. For every profile payload, inject `network_slug` into each
     `ssids[*]` ref using that mapping (default 'lan' when the SSID
     isn't found — e.g. a stale ref).
  3. Drop the wifi_ssids.network_slug column.

Downgrade restores the column and best-effort copies the binding back
from the first profile that references each SSID.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


revision = "a1d7f3b52e89"
down_revision = "f6b8c19a274d"
branch_labels = None
depends_on = None


def _load_payload(raw: object) -> dict | None:
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Snapshot slug → network_slug from wifi_ssids (only if the column
    #    still exists — idempotency on re-runs).
    wifi_cols = [
        r[1] for r in bind.execute(
            sa.text("PRAGMA table_info('wifi_ssids')"),
        ).fetchall()
    ]
    slug_to_net: dict[str, str] = {}
    if "network_slug" in wifi_cols:
        for slug, net in bind.execute(
            sa.text("SELECT slug, network_slug FROM wifi_ssids"),
        ).fetchall():
            slug_to_net[slug] = net or "lan"

    # 2. Inject network_slug into each profile's ssids refs.
    rows = bind.execute(sa.text("SELECT id, payload FROM profiles")).fetchall()
    for row_id, payload_raw in rows:
        payload = _load_payload(payload_raw)
        if payload is None:
            continue
        ssids = payload.get("ssids")
        if not isinstance(ssids, list):
            continue
        changed = False
        for ref in ssids:
            if not isinstance(ref, dict):
                continue
            if "network_slug" not in ref:
                ref["network_slug"] = slug_to_net.get(ref.get("slug", ""), "lan")
                changed = True
        if changed:
            bind.execute(
                sa.text("UPDATE profiles SET payload = :p WHERE id = :id"),
                {"p": json.dumps(payload), "id": row_id},
            )

    # 3. Drop the legacy column.
    if "network_slug" in wifi_cols:
        with op.batch_alter_table("wifi_ssids") as batch:
            batch.drop_column("network_slug")


def downgrade() -> None:
    bind = op.get_bind()

    # 1. Re-add the column with the historical default.
    with op.batch_alter_table("wifi_ssids") as batch:
        batch.add_column(
            sa.Column(
                "network_slug", sa.String(length=64),
                nullable=False, server_default="lan",
            ),
        )

    # 2. Best-effort restore : first profile ref that mentions each SSID
    #    wins. Then strip network_slug from the profile refs.
    rows = bind.execute(sa.text("SELECT id, payload FROM profiles")).fetchall()
    restored: dict[str, str] = {}
    for row_id, payload_raw in rows:
        payload = _load_payload(payload_raw)
        if payload is None:
            continue
        ssids = payload.get("ssids")
        if not isinstance(ssids, list):
            continue
        changed = False
        for ref in ssids:
            if not isinstance(ref, dict):
                continue
            slug = ref.get("slug")
            if slug and slug not in restored and ref.get("network_slug"):
                restored[slug] = ref["network_slug"]
            if "network_slug" in ref:
                ref.pop("network_slug", None)
                changed = True
        if changed:
            bind.execute(
                sa.text("UPDATE profiles SET payload = :p WHERE id = :id"),
                {"p": json.dumps(payload), "id": row_id},
            )

    for slug, net in restored.items():
        bind.execute(
            sa.text("UPDATE wifi_ssids SET network_slug = :n WHERE slug = :s"),
            {"n": net, "s": slug},
        )
