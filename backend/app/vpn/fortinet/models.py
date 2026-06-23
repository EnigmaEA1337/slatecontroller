"""Pydantic models for the Fortinet SSL VPN feature.

The DB layer (:mod:`app.db.models`) holds the row shape ; this module
carries the API surface :

  - :class:`FortinetConfigCreate` — body of POST, password in clear (the
    store encrypts it). OTP is NEVER on this model — it's strictly a
    runtime input on /connect, never persisted.
  - :class:`FortinetConfigUpdate` — body of PATCH, every field optional ;
    password is OMITTED when the operator just renames or edits notes,
    so the encrypted blob in the secrets table is left intact.
  - :class:`FortinetConfigPublic` — listing/return shape. Plaintext metadata
    + status timestamps + a `has_password` flag (we never return the
    password, even encrypted).
  - :class:`FortinetConnectRequest` — body of /connect : just the OTP. The
    Slate inherits username/password/host from the stored config.
  - :class:`FortinetStatus` — runtime status reported by the agent after
    each connect/disconnect/poll. The ``ppp_iface`` is what the per-network
    egress reconciler binds its rules to.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Tunnel state machine — kept narrow so the UI can match exhaustively.
# `unknown` covers fresh installs and post-restart windows where we
# haven't yet polled the agent. `failed` carries the last operator-visible
# error in `last_error` ; transient retries don't reach the user.
FortinetStatusStr = Literal[
    "unknown",
    "connecting",
    "up",
    "disconnecting",
    "down",
    "failed",
]

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
# TOTP is 6 digits on Forti by default ; some integrations issue 8-digit
# codes (e.g. RSA-style hardware tokens). Accept either ; reject anything
# else to fail fast before openfortivpn does.
_OTP_RE = re.compile(r"^[0-9]{6,8}$")
# SHA256 hex is 64 lowercase hex chars. We accept either bare (a-f0-9{64})
# or colon-separated (e.g. "AA:BB:CC...") — most cert tools output the
# latter and the UI normalises before submit.
_SHA256_RE = re.compile(r"^(?:[a-fA-F0-9]{64}|(?:[a-fA-F0-9]{2}:){31}[a-fA-F0-9]{2})$")


def _normalise_sha256(raw: str) -> str:
    """Strip colons + lowercase. Empty string → empty (no pinning)."""
    s = raw.strip().replace(":", "").lower()
    return s


class FortinetConfigCreate(BaseModel):
    """Body of POST /api/vpn/fortinet."""

    slug: str = Field(min_length=1, max_length=63)
    display_name: str = Field(default="", max_length=128)
    gateway_host: str = Field(min_length=1, max_length=255)
    gateway_port: int = Field(default=443, ge=1, le=65535)
    # username + password live OUTSIDE the configuration entity ; both are
    # provided at connect-time on the dedicated login page. The fields
    # remain on the model for backward compatibility (configs created
    # before this change carry them) but they default to empty and the
    # config form no longer surfaces them.
    username: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=512)
    trusted_cert_sha256: str = Field(default="", max_length=128)
    ca_cert_pem: str = Field(default="", max_length=32_000)
    notes: str = Field(default="", max_length=512)

    @field_validator("slug")
    @classmethod
    def _valid_slug(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(
                "slug must be 1-63 chars of [a-z0-9_-], start with a letter or digit",
            )
        return v

    @field_validator("trusted_cert_sha256")
    @classmethod
    def _valid_pin(cls, v: str) -> str:
        if not v:
            return ""
        if not _SHA256_RE.match(v):
            raise ValueError(
                "trusted_cert_sha256 must be a SHA256 hex (64 chars, optional "
                "colon separators) or empty for no pinning",
            )
        return _normalise_sha256(v)


class FortinetConfigUpdate(BaseModel):
    """Body of PATCH /api/vpn/fortinet/{slug}.

    Every field is optional. Password update is opt-in : leave it null
    when the operator is just renaming or pinning a cert and the existing
    secret is reused.
    """

    display_name: str | None = Field(default=None, max_length=128)
    gateway_host: str | None = Field(default=None, min_length=1, max_length=255)
    gateway_port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, min_length=1, max_length=128)
    password: str | None = Field(default=None, min_length=1, max_length=512)
    trusted_cert_sha256: str | None = Field(default=None, max_length=128)
    ca_cert_pem: str | None = Field(default=None, max_length=32_000)
    notes: str | None = Field(default=None, max_length=512)

    @field_validator("trusted_cert_sha256")
    @classmethod
    def _valid_pin(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return v
        if not _SHA256_RE.match(v):
            raise ValueError(
                "trusted_cert_sha256 must be a SHA256 hex or empty",
            )
        return _normalise_sha256(v)


class FortinetConfigPublic(BaseModel):
    """GET shape. Password is NEVER returned, even encrypted."""

    slug: str
    display_name: str
    gateway_host: str
    gateway_port: int
    username: str
    trusted_cert_sha256: str
    # `has_ca_cert` so the UI can render "CA: configurée" without leaking
    # the PEM in every list query. The PEM is included in the detail view
    # only (GET /api/vpn/fortinet/{slug}/ca-cert).
    has_ca_cert: bool
    has_password: bool
    notes: str
    last_status: FortinetStatusStr
    last_connected_at: datetime | None = None
    last_disconnected_at: datetime | None = None
    last_error: str
    created_at: datetime
    updated_at: datetime


class FortinetConnectRequest(BaseModel):
    """POST /api/vpn/fortinet/{slug}/connect.

    The login page submits ``username`` + ``password`` typed fresh at
    each connect. Many Fortinet deployments fold the 2FA token directly
    into the password field (the operator types the TOTP from their
    authenticator app as the "password" value) — that's the supported
    flow here. For deployments that have a separate ``--otp`` field on
    the gateway, set ``otp`` to that code and ``password`` to the static
    one.

    Nothing on this model is persisted — credentials live in RAM only
    for the ~5-25 s the connect call takes.
    """

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)
    # Optional : kept for deployments where the gateway requires a
    # separate static password + TOTP pair. Most setups fold the TOTP
    # into ``password`` and leave this empty.
    otp: str | None = Field(default=None, max_length=8)

    @field_validator("otp")
    @classmethod
    def _valid_otp(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not _OTP_RE.match(v):
            raise ValueError("otp must be 6 or 8 digits when provided")
        return v


class FortinetLogsResponse(BaseModel):
    """Tail of openfortivpn's stderr log on the Slate, returned as one
    string per line. Lines are time-stamped by openfortivpn itself."""

    lines: list[str] = Field(default_factory=list)
    truncated: bool = False  # True when the source log was larger than `max_lines`


class FortinetStatus(BaseModel):
    """Runtime status reported by the agent.

    `ppp_iface` is the interface name openfortivpn assigned (typically
    ``ppp0`` but the kernel picks the next free index — the per-network
    egress reconciler reads this rather than hardcoding the name).
    """

    slug: str | None = None  # which config is currently active (None when down)
    state: FortinetStatusStr
    ppp_iface: str | None = None
    tunnel_ip: str | None = None
    gateway_ip: str | None = None
    rx_bytes: int = 0
    tx_bytes: int = 0
    uptime_seconds: int = 0
    last_error: str = ""
