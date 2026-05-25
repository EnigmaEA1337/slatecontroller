"""remap networks + ssids: 4 zones segmentation (lan/kids/work/dmz) with
random RFC1918 subnets, SSID slugs aligned to zones, MLO + WPA3 upgrades,
client isolation, and matching profile payloads.

Revision ID: c6effd12786b
Revises: bd99f2f46f24
Create Date: 2026-05-22 12:00:00.000000

Data-only migration. No schema change. Idempotent: detects already-renamed
state and skips the rename steps.
"""
from __future__ import annotations

import json
from typing import Sequence, Union
from datetime import datetime, UTC

from alembic import op
import sqlalchemy as sa


revision: str = "c6effd12786b"
down_revision: Union[str, Sequence[str], None] = "bd99f2f46f24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --- Target state ---------------------------------------------------------- #
# Subnets chosen with random offsets (option C: no common base) to minimise
# collision risk in hotel/coworking environments. See project notes.

NETWORKS = {
    "lan": {
        "display_name": "LAN principal (parents/perso)",
        "bridge_name": "br-lan",
        "subnet_cidr": "10.137.42.0/24",
        "gateway_ip": "10.137.42.1",
        "ipv6_subnet_cidr": "fd5a:6c14:e23b:8::/64",
        "isolated_from_lan": False,
        "notes": "Zone trusted: NAS, imprimante, AirPlay, smart home perso. "
                 "Subnet random pour éviter conflit mobilité.",
    },
    "kids": {
        "display_name": "Kids",
        "bridge_name": "br-kids",
        "subnet_cidr": "10.91.18.0/24",
        "gateway_ip": "10.91.18.1",
        "ipv6_subnet_cidr": "fd5a:6c14:e23b:9::/64",
        "isolated_from_lan": True,
        "notes": "Zone enfants. REJECT vers lan/work. AdGuard filtrage strict.",
    },
    "work": {
        "display_name": "Work (mission corporate)",
        "bridge_name": "br-work",
        "subnet_cidr": "10.204.5.0/24",
        "gateway_ip": "10.204.5.1",
        "ipv6_subnet_cidr": "fd5a:6c14:e23b:10::/64",
        "isolated_from_lan": True,
        "notes": "Zone mission corporate. Forcé via VPN, REJECT vers lan. "
                 "Defense in depth — compromis corp ne touche pas perso.",
    },
    "dmz": {
        "display_name": "DMZ (guest + burner OSINT)",
        "bridge_name": "br-dmz",
        "subnet_cidr": "10.66.211.0/24",
        "gateway_ip": "10.66.211.1",
        "ipv6_subnet_cidr": "fd5a:6c14:e23b:20::/64",
        "isolated_from_lan": True,
        "notes": "Zone untrusted: invités + burner OSINT. Client iso ON. "
                 "REJECT vers tout LAN interne.",
    },
}

# Old slug -> new slug + per-SSID overrides.
SSID_RENAME = {
    "parents": {
        "new_slug": "lan-main",
        "band": "MLO",
        "security": "WPA3-SAE",
        "network_slug": "lan",
        "client_isolation": False,
    },
    "enfants": {
        "new_slug": "kids-tablet",
        "band": "5GHz",
        "security": "WPA3-SAE",  # transition mode; uhttpd config handles legacy WPA2 fallback
        "network_slug": "kids",
        "client_isolation": False,
    },
    "missionpro": {
        "new_slug": "work-mission",
        "band": "MLO",
        "security": "WPA3-SAE",
        "network_slug": "work",
        "client_isolation": True,
    },
    "invites": {
        "new_slug": "dmz-guest",
        "band": "2GHz",
        "security": "WPA2-PSK",
        "network_slug": "dmz",
        "client_isolation": True,
    },
    "osinttemp": {
        "new_slug": "dmz-osint",
        "band": "5GHz",
        "security": "WPA3-SAE",
        "network_slug": "dmz",
        "client_isolation": True,
    },
}

# SSIDs active per profile after remap.
PROFILE_SSIDS = {
    "home":     [("lan-main", True), ("kids-tablet", True), ("dmz-guest", True),
                 ("work-mission", False), ("dmz-osint", False)],
    "vacances": [("lan-main", True), ("kids-tablet", True), ("dmz-guest", True),
                 ("work-mission", False), ("dmz-osint", False)],
    "mission":  [("work-mission", True), ("dmz-osint", True),
                 ("lan-main", False), ("kids-tablet", False), ("dmz-guest", False)],
    "osint":    [("dmz-osint", True),
                 ("lan-main", False), ("kids-tablet", False), ("dmz-guest", False),
                 ("work-mission", False)],
    "lockdown": [("lan-main", False), ("kids-tablet", False), ("dmz-guest", False),
                 ("work-mission", False), ("dmz-osint", False)],
}


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC).isoformat(sep=" ")

    # ---- 1. networks ----------------------------------------------------- #
    # Pull current slugs to drive idempotency.
    existing = {
        row[0]
        for row in bind.execute(sa.text("SELECT slug FROM networks")).fetchall()
    }

    # 1a. Update lan in place (keep id, change subnet).
    if "lan" in existing:
        n = NETWORKS["lan"]
        bind.execute(
            sa.text(
                "UPDATE networks SET subnet_cidr=:s, gateway_ip=:g, "
                "ipv6_subnet_cidr=:v6, display_name=:dn, notes=:nt, "
                "updated_at=:ts WHERE slug='lan'"
            ),
            {
                "s": n["subnet_cidr"], "g": n["gateway_ip"],
                "v6": n["ipv6_subnet_cidr"], "dn": n["display_name"],
                "nt": n["notes"], "ts": now,
            },
        )

    # 1b. Rename guest → dmz (in place to preserve any FK).
    if "guest" in existing and "dmz" not in existing:
        n = NETWORKS["dmz"]
        bind.execute(
            sa.text(
                "UPDATE networks SET slug='dmz', subnet_cidr=:s, gateway_ip=:g, "
                "ipv6_subnet_cidr=:v6, display_name=:dn, bridge_name=:br, "
                "notes=:nt, isolated_from_lan=1, updated_at=:ts WHERE slug='guest'"
            ),
            {
                "s": n["subnet_cidr"], "g": n["gateway_ip"],
                "v6": n["ipv6_subnet_cidr"], "dn": n["display_name"],
                "br": n["bridge_name"], "nt": n["notes"], "ts": now,
            },
        )

    # 1c. Insert kids + work if missing.
    for slug in ("kids", "work"):
        if slug in existing:
            continue
        n = NETWORKS[slug]
        bind.execute(
            sa.text(
                "INSERT INTO networks "
                "(slug, display_name, bridge_name, subnet_cidr, gateway_ip, "
                "dhcp_enabled, isolated_from_lan, is_builtin, notes, "
                "ipv6_enabled, ipv6_subnet_cidr, created_at, updated_at) "
                "VALUES (:slug, :dn, :br, :s, :g, 1, :iso, 1, :nt, 1, :v6, :ts, :ts)"
            ),
            {
                "slug": slug, "dn": n["display_name"], "br": n["bridge_name"],
                "s": n["subnet_cidr"], "g": n["gateway_ip"],
                "iso": 1 if n["isolated_from_lan"] else 0,
                "nt": n["notes"], "v6": n["ipv6_subnet_cidr"], "ts": now,
            },
        )

    # ---- 2. wifi_ssids --------------------------------------------------- #
    # Idempotency: if any *new* slug already exists, skip the whole rename.
    new_slugs = {v["new_slug"] for v in SSID_RENAME.values()}
    existing_ssid_slugs = {
        row[0]
        for row in bind.execute(sa.text("SELECT slug FROM wifi_ssids")).fetchall()
    }

    if not new_slugs & existing_ssid_slugs:
        for old_slug, target in SSID_RENAME.items():
            bind.execute(
                sa.text(
                    "UPDATE wifi_ssids SET slug=:new, band=:band, security=:sec, "
                    "network_slug=:net, client_isolation=:iso, updated_at=:ts "
                    "WHERE slug=:old"
                ),
                {
                    "new": target["new_slug"], "band": target["band"],
                    "sec": target["security"], "net": target["network_slug"],
                    "iso": 1 if target["client_isolation"] else 0,
                    "ts": now, "old": old_slug,
                },
            )

    # ---- 3. profiles payload --------------------------------------------- #
    for name, ssid_list in PROFILE_SSIDS.items():
        row = bind.execute(
            sa.text("SELECT payload FROM profiles WHERE name=:n"), {"n": name}
        ).fetchone()
        if row is None:
            continue
        payload = row[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        payload["ssids"] = [
            {"slug": slug, "enabled": enabled} for slug, enabled in ssid_list
        ]
        bind.execute(
            sa.text(
                "UPDATE profiles SET payload=:p, updated_at=:ts WHERE name=:n"
            ),
            {"p": json.dumps(payload, ensure_ascii=False), "ts": now, "n": name},
        )


def downgrade() -> None:
    # Revert is not safe — user data (passwords, custom edits) may have been
    # bound to the new slugs by the time downgrade runs. Refuse explicitly.
    raise NotImplementedError(
        "Downgrade not supported for the segmentation v2 migration."
    )
