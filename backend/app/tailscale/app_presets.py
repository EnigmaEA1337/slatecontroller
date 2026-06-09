"""Catalogue of well-known application IP ranges, used by the routing
policy UI in NetworkForm.

An « app preset » is just a labelled bundle of CIDR ranges. The operator
ticks one in the NetworkForm and the UI expands it into individual
`TailnetDestination` rows, all carrying the preset's `label`. From the
reconciler's standpoint these are ordinary destinations — the label
field exists purely for UI grouping.

This is a deliberately simple MVP : the catalogue is **hardcoded**, the
CIDR snapshots are **point in time**, and there's **no DNS-based
matching**. A future phase will add :
  - a periodic refresh that pulls CIDRs from RIPE / BGP / Routeviews
  - DNS-based rules backed by ipset + dnsmasq + fwmark policy routing
  - SNI / DPI-based rules for cases where DNS doesn't fingerprint cleanly

Sources used for the snapshots below :
  - Netflix : ASN 2906 — published openconnect ranges
  - Plex    : public ranges (plex.tv api endpoints)
  - YouTube : Google's ASN 15169 — restricted to streaming-IP ranges
  - Spotify : ASN 8403 — Spotify Music
  - Discord : Cloudflare CDN — public ranges from Discord's status page
  - Apple   : Apple ASN 6185 — main ranges
  - Steam   : Valve / Cloudfront / Akamai mix — typical streaming pool

All snapshots dated 2026-06 ; refresh manually for now.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AppPreset:
    id: str                          # slug ; also used as `label` on dests
    name: str                        # human-friendly title
    description: str                 # short hint
    cidrs: list[str]                 # IPv4 ranges as CIDR strings
    # Domain patterns dnsmasq watches. Each entry is fed verbatim into a
    # `ipset=/<domain>/<set>` directive — prefix with a dot to match
    # subdomains (".netflix.com" matches netflix.com AND any subdomain).
    domains: tuple[str, ...] = ()


# Conservative snapshot — we list only the most stable ranges, not the
# bleeding edge. Better to under-cover (operator can add CIDRs manually)
# than over-cover and route the wrong traffic.
PRESETS: list[AppPreset] = [
    AppPreset(
        id="netflix",
        name="Netflix",
        description="ASN 2906 streaming pool (Open Connect partial).",
        cidrs=[
            "23.246.0.0/18",
            "37.77.184.0/21",
            "45.57.0.0/17",
            "64.120.128.0/17",
            "66.197.128.0/17",
            "108.175.32.0/20",
            "192.173.64.0/18",
            "198.38.96.0/19",
            "198.45.48.0/20",
        ],
        domains=(
            "netflix.com",
            "netflix.net",
            "nflxvideo.net",
            "nflximg.com",
            "nflxext.com",
            "nflxso.net",
        ),
    ),
    AppPreset(
        id="plex",
        name="Plex",
        description="Plex.tv API + media servers (public-facing).",
        cidrs=[
            "157.245.0.0/16",
            "147.182.0.0/16",
        ],
        domains=(
            "plex.tv",
            "plex.direct",
        ),
    ),
    AppPreset(
        id="youtube",
        name="YouTube",
        description="Google streaming/CDN ranges (subset of ASN 15169).",
        cidrs=[
            "208.65.152.0/22",
            "208.117.224.0/19",
            "216.58.192.0/19",
            "172.217.0.0/19",
            "172.217.32.0/20",
            "172.217.128.0/19",
            "172.217.160.0/20",
            "142.250.0.0/15",
        ],
        domains=(
            "youtube.com",
            "youtu.be",
            "ytimg.com",
            "googlevideo.com",
            "youtubei.googleapis.com",
        ),
    ),
    AppPreset(
        id="spotify",
        name="Spotify",
        description="ASN 8403 (Spotify Music).",
        cidrs=[
            "35.186.224.0/20",
            "35.186.240.0/22",
            "104.154.127.0/24",
            "194.132.192.0/19",
        ],
        domains=(
            "spotify.com",
            "scdn.co",
            "spotifycdn.com",
        ),
    ),
    AppPreset(
        id="discord",
        name="Discord",
        description="Discord voice + media (Cloudflare-backed).",
        cidrs=[
            "162.159.128.0/19",
            "162.159.160.0/19",
        ],
        domains=(
            "discord.com",
            "discord.gg",
            "discordapp.com",
            "discordapp.net",
            "discord.media",
        ),
    ),
    AppPreset(
        id="apple",
        name="Apple",
        description="Apple ASN 6185 main pool (iCloud, App Store…).",
        cidrs=[
            "17.0.0.0/8",
        ],
        domains=(
            "apple.com",
            "icloud.com",
            "mzstatic.com",
            "applemusic.com",
            "itunes.apple.com",
        ),
    ),
    AppPreset(
        id="steam",
        name="Steam",
        description="Valve game CDN — typical streaming pool.",
        cidrs=[
            "155.133.224.0/19",
            "162.254.192.0/21",
            "190.217.32.0/20",
            "208.78.164.0/22",
        ],
        domains=(
            "steampowered.com",
            "steamcommunity.com",
            "steamstatic.com",
            "steamserver.net",
        ),
    ),
]


PRESETS_BY_ID: dict[str, AppPreset] = {p.id: p for p in PRESETS}


def to_api_payload() -> list[dict]:
    """Shape used by the GET /api/tailscale/app-presets endpoint."""
    return [
        {
            "id": p.id,
            "domains": list(p.domains),
            "name": p.name,
            "description": p.description,
            "cidrs": list(p.cidrs),
        }
        for p in PRESETS
    ]
