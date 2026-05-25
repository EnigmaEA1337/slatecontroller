"""cyberpunk slug alignment: 1:1 SSID↔network, broadcast-named slugs.

Renames:
  networks.slug:   lan→neuralcore, kids→grid, work→blackice, dmz→chromelounge
                   + insert new 'shadowrun' for the burner OSINT zone
  wifi_ssids.slug: lan-main→neuralcore, kids-tablet→grid, work-mission→blackice,
                   dmz-guest→chromelounge, dmz-osint→shadowrun
                   (dmz-osint also moves: network_slug dmz→shadowrun)
  profiles.payload.ssids[].slug updated in lockstep.

Why split osint into its own network: guest and burner OSINT used to share
the dmz bridge → client iso protects intra-SSID but inter-SSID L2 leak was
theoretically possible through the bridge. Separate networks remove that.

Revision ID: 281b831b96da
Revises: c6effd12786b
Create Date: 2026-05-22 09:00:00.000000
"""
from __future__ import annotations

import json
from typing import Sequence, Union
from datetime import datetime, UTC

from alembic import op
import sqlalchemy as sa


revision: str = "281b831b96da"
down_revision: Union[str, Sequence[str], None] = "c6effd12786b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NETWORK_RENAME = {
    "lan": "neuralcore",
    "kids": "grid",
    "work": "blackice",
    "dmz": "chromelounge",
}

# New 5th network for the burner OSINT (split from old dmz).
SHADOWRUN_NETWORK = {
    "slug": "shadowrun",
    "display_name": "Shadowrun (burner OSINT)",
    "bridge_name": "br-shadowrun",
    "subnet_cidr": "10.183.7.0/24",
    "gateway_ip": "10.183.7.1",
    "ipv6_subnet_cidr": "fd5a:6c14:e23b:21::/64",
    "isolated_from_lan": True,
    "notes": (
        "Zone burner OSINT, séparée L2 de chromelounge pour empêcher "
        "tout inter-SSID leak via bridge partagé."
    ),
}

SSID_RENAME = {
    "lan-main": ("neuralcore", "neuralcore"),     # (new_slug, new_network_slug)
    "kids-tablet": ("grid", "grid"),
    "work-mission": ("blackice", "blackice"),
    "dmz-guest": ("chromelounge", "chromelounge"),
    "dmz-osint": ("shadowrun", "shadowrun"),       # also changes network
}

# Mirror to update profiles.payload.ssids[].slug.
PROFILE_SLUG_MAP = {old: new for old, (new, _) in SSID_RENAME.items()}


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC).isoformat(sep=" ")

    existing_nets = {
        row[0] for row in bind.execute(sa.text("SELECT slug FROM networks")).fetchall()
    }
    existing_ssids = {
        row[0] for row in bind.execute(sa.text("SELECT slug FROM wifi_ssids")).fetchall()
    }

    # ---- 1. Insert shadowrun network if missing -------------------------- #
    if "shadowrun" not in existing_nets:
        n = SHADOWRUN_NETWORK
        bind.execute(
            sa.text(
                "INSERT INTO networks "
                "(slug, display_name, bridge_name, subnet_cidr, gateway_ip, "
                "dhcp_enabled, isolated_from_lan, is_builtin, notes, "
                "ipv6_enabled, ipv6_subnet_cidr, created_at, updated_at) "
                "VALUES (:slug, :dn, :br, :s, :g, 1, 1, 1, :nt, 1, :v6, :ts, :ts)"
            ),
            {
                "slug": n["slug"], "dn": n["display_name"], "br": n["bridge_name"],
                "s": n["subnet_cidr"], "g": n["gateway_ip"],
                "nt": n["notes"], "v6": n["ipv6_subnet_cidr"], "ts": now,
            },
        )

    # ---- 2. Move dmz-osint to shadowrun BEFORE renaming dmz -------------- #
    # Otherwise the FK-like reference becomes orphan during the rename window.
    bind.execute(
        sa.text(
            "UPDATE wifi_ssids SET network_slug='shadowrun' "
            "WHERE slug='dmz-osint' AND network_slug='dmz'"
        )
    )

    # ---- 3. Update wifi_ssids.network_slug for the rest ------------------ #
    # Avant de renommer networks, on update toutes les références.
    for old_net, new_net in NETWORK_RENAME.items():
        bind.execute(
            sa.text(
                "UPDATE wifi_ssids SET network_slug=:new "
                "WHERE network_slug=:old"
            ),
            {"new": new_net, "old": old_net},
        )

    # ---- 4. Rename networks --------------------------------------------- #
    for old_slug, new_slug in NETWORK_RENAME.items():
        if old_slug not in existing_nets:
            continue
        if new_slug in existing_nets:
            continue
        bind.execute(
            sa.text(
                "UPDATE networks SET slug=:new, updated_at=:ts WHERE slug=:old"
            ),
            {"new": new_slug, "old": old_slug, "ts": now},
        )

    # ---- 5. Rename wifi_ssids slugs ------------------------------------- #
    for old_slug, (new_slug, _) in SSID_RENAME.items():
        if old_slug not in existing_ssids:
            continue
        if new_slug in existing_ssids:
            continue
        bind.execute(
            sa.text(
                "UPDATE wifi_ssids SET slug=:new, updated_at=:ts WHERE slug=:old"
            ),
            {"new": new_slug, "old": old_slug, "ts": now},
        )

    # ---- 6. Update profiles.payload.ssids[].slug ------------------------ #
    rows = bind.execute(
        sa.text("SELECT id, payload FROM profiles")
    ).fetchall()
    for prof_id, payload_raw in rows:
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        ssids = payload.get("ssids") or []
        changed = False
        for entry in ssids:
            old = entry.get("slug")
            if old in PROFILE_SLUG_MAP:
                entry["slug"] = PROFILE_SLUG_MAP[old]
                changed = True
        if changed:
            bind.execute(
                sa.text(
                    "UPDATE profiles SET payload=:p, updated_at=:ts WHERE id=:id"
                ),
                {
                    "p": json.dumps(payload, ensure_ascii=False),
                    "ts": now, "id": prof_id,
                },
            )


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade not supported — slugs are referenced across YAML files, "
        "DB profile payloads, and (eventually) UCI config on the Slate."
    )
