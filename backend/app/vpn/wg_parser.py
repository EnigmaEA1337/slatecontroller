"""Parse WireGuard `.conf` files.

WireGuard's config format is an INI-like file with `[Interface]` and one or
more `[Peer]` sections. For VPN clients (which is our use case) there's
typically exactly one `[Peer]`.

We intentionally do not use the stdlib `configparser` directly: it
lowercases keys by default and can choke on duplicate sections. Our
hand-rolled parser is small and resilient.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.exceptions import SlateError


class WGConfigParseError(SlateError):
    """The provided text is not a valid WireGuard client config."""


@dataclass
class ParsedWGConfig:
    """Strongly-typed representation of a WireGuard client config."""

    interface_private_key: str
    interface_address: str
    interface_dns: list[str] = field(default_factory=list)
    peer_public_key: str = ""
    peer_endpoint: str = ""
    peer_allowed_ips: list[str] = field(default_factory=list)


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def parse_wg_config(text: str) -> ParsedWGConfig:
    """Parse a WireGuard client `.conf` text into a `ParsedWGConfig`.

    Raises `WGConfigParseError` for any structural issue or missing field.
    """
    sections: dict[str, dict[str, str]] = {}
    current: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip().lower()
            sections.setdefault(current, {})
            continue
        if current is None:
            raise WGConfigParseError(f"key-value before any section: {line!r}")
        if "=" not in line:
            raise WGConfigParseError(f"line is not key=value: {line!r}")
        key, _, value = line.partition("=")
        sections[current][key.strip().lower()] = value.strip()

    if "interface" not in sections:
        raise WGConfigParseError("missing [Interface] section")
    if "peer" not in sections:
        raise WGConfigParseError("missing [Peer] section")

    iface = sections["interface"]
    peer = sections["peer"]

    for required, where in (
        ("privatekey", iface),
        ("address", iface),
        ("publickey", peer),
        ("endpoint", peer),
    ):
        if required not in where:
            section_name = "Interface" if where is iface else "Peer"
            raise WGConfigParseError(
                f"missing required field {required!r} in [{section_name}]"
            )

    return ParsedWGConfig(
        interface_private_key=iface["privatekey"],
        interface_address=iface["address"],
        interface_dns=_split_csv(iface.get("dns", "")),
        peer_public_key=peer["publickey"],
        peer_endpoint=peer["endpoint"],
        peer_allowed_ips=_split_csv(peer.get("allowedips", "0.0.0.0/0")),
    )
