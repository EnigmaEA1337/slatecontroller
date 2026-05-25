"""Tests for the WireGuard .conf parser."""

from __future__ import annotations

import pytest

from app.vpn.wg_parser import WGConfigParseError, parse_wg_config

PROTON_SAMPLE = """\
# WireGuard configuration generated for Proton VPN
[Interface]
PrivateKey = oFAEYO9j7gNQwAAr3pe9LH+UMyqoLBHRzkS+QCDuHWY=
Address = 10.2.0.2/32
DNS = 10.2.0.1

[Peer]
PublicKey = CzogIfsr1lHt6QmaTOoLnBhJ7Z3vIflxK0w0pUYIY0o=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = node-fr-12.protonvpn.net:51820
"""


def test_parse_proton_sample() -> None:
    parsed = parse_wg_config(PROTON_SAMPLE)
    assert parsed.interface_private_key.endswith("HWY=")
    assert parsed.interface_address == "10.2.0.2/32"
    assert parsed.interface_dns == ["10.2.0.1"]
    assert parsed.peer_public_key.endswith("Y0o=")
    assert parsed.peer_endpoint == "node-fr-12.protonvpn.net:51820"
    assert parsed.peer_allowed_ips == ["0.0.0.0/0", "::/0"]


def test_parse_handles_comments_and_blank_lines() -> None:
    text = """\
; semicolon comment
# hash comment
[Interface]

PrivateKey = abc
Address = 10.0.0.1/32

[Peer]
PublicKey = def
Endpoint = host:51820
"""
    parsed = parse_wg_config(text)
    assert parsed.interface_private_key == "abc"
    assert parsed.peer_endpoint == "host:51820"


def test_parse_keys_are_case_insensitive() -> None:
    text = """\
[Interface]
privatekey = abc
ADDRESS = 10.0.0.1/32
[Peer]
PUBLICKEY = def
endpoint = host:51820
"""
    parsed = parse_wg_config(text)
    assert parsed.interface_private_key == "abc"
    assert parsed.interface_address == "10.0.0.1/32"


def test_parse_rejects_missing_interface() -> None:
    text = "[Peer]\nPublicKey = a\nEndpoint = host:1"
    with pytest.raises(WGConfigParseError, match="Interface"):
        parse_wg_config(text)


def test_parse_rejects_missing_peer() -> None:
    text = "[Interface]\nPrivateKey = a\nAddress = 10/32"
    with pytest.raises(WGConfigParseError, match="Peer"):
        parse_wg_config(text)


def test_parse_rejects_missing_required_fields() -> None:
    text = "[Interface]\nPrivateKey = a\n[Peer]\nPublicKey = b\nEndpoint = h:1"
    with pytest.raises(WGConfigParseError, match="address"):
        parse_wg_config(text)


def test_parse_rejects_key_before_section() -> None:
    with pytest.raises(WGConfigParseError, match="before any section"):
        parse_wg_config("Key = value\n[Interface]\n")


def test_parse_default_allowed_ips_when_missing() -> None:
    text = """\
[Interface]
PrivateKey = a
Address = 10.0.0.1/32
[Peer]
PublicKey = b
Endpoint = host:1
"""
    parsed = parse_wg_config(text)
    assert parsed.peer_allowed_ips == ["0.0.0.0/0"]
