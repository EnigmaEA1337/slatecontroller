"""Proton Account V4 auth flow.

`proton-client 0.5.1` (the package on PyPI) implements the legacy V1 flow,
which Proton has retired — `/auth/info` now returns 401 unless you first
create an anonymous session via `/auth/v4/sessions`. This module reimplements
the modern V4 flow manually:

    1. POST /auth/v4/sessions               # anonymous session token
    2. POST /auth/v4/info  (with anon UID)  # SRP modulus + salt + nonce
    3. compute SRP proof (client-side)
    4. POST /auth/v4                        # submit proof, get user UID/token
    5. POST /auth/v4/2fa     (if needed)    # upgrade scope with TOTP

We reuse the SRP math from the lib (`proton.srp.User`) and the PGP modulus
verification key. Everything else — HTTP, session bookkeeping, error mapping —
is ours.

Future hardening:
  - Captcha (HV) handling on 9001
  - Session persistence in SQLite (currently in-memory)
  - Auto-refresh of expired access tokens
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from typing import Any

import gnupg  # type: ignore[import-untyped]
import httpx
import structlog

from app.exceptions import SlateError

try:  # SRP math + Proton's PGP modulus key still come from the lib
    from proton.constants import (  # type: ignore[import-untyped]
        SRP_MODULUS_KEY,
        SRP_MODULUS_KEY_FINGERPRINT,
    )
    from proton.srp import User as SrpUser  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    SRP_MODULUS_KEY = None
    SRP_MODULUS_KEY_FINGERPRINT = None
    SrpUser = None  # type: ignore[assignment, misc]

logger = structlog.get_logger(__name__)

# Proton account-wide API (auth endpoints live here, not on vpn-api.proton.me)
PROTON_API_BASE = "https://account.proton.me/api"

# Spoof Proton's own web VPN settings client. Their abuse system trusts the
# web surface more than unknown clients ("Other"), which gets you a captcha
# challenge or an outright `5003 client outdated`.
APP_VERSION = "web-vpn-settings@5.0.262.0"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
)

PROTON_OK_CODE = 1000
PROTON_HUMAN_VERIFICATION_CODE = 9001


# ----------------------------- public types ----------------------------- #


class ProtonAuthError(SlateError):
    """Proton refused credentials, 2FA, or returned an error payload."""


class ProtonHumanVerificationError(ProtonAuthError):
    """Proton requires hCaptcha / email / SMS verification before letting us in."""


class ProtonNotLoggedInError(SlateError):
    """An operation needs an authenticated session but none exists."""


@dataclass(frozen=True)
class ProtonAuthState:
    authenticated: bool
    two_factor_pending: bool


# ----------------------------- client ----------------------------- #


@dataclass
class _Session:
    uid: str
    access_token: str
    refresh_token: str = ""
    scope: list[str] | None = None

    @property
    def needs_two_factor(self) -> bool:
        return "twofactor" in (self.scope or [])


class ProtonClient:
    """Stateful Proton V4 session manager (single user, in-memory)."""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=PROTON_API_BASE,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/vnd.protonmail.v1+json",
                "x-pm-appversion": APP_VERSION,
                "User-Agent": USER_AGENT,
            },
            timeout=httpx.Timeout(15.0, connect=10.0),
        )
        self._anon: _Session | None = None
        self._user: _Session | None = None
        self._lock = asyncio.Lock()
        self._gpg = self._build_gpg()

    @staticmethod
    def _build_gpg() -> Any:
        if SRP_MODULUS_KEY is None:  # proton-client not installed
            return None
        gpg = gnupg.GPG()
        gpg.import_keys(SRP_MODULUS_KEY)
        return gpg

    # ----------------------------- introspection ----------------------------- #

    def state(self) -> ProtonAuthState:
        if self._user is None:
            return ProtonAuthState(authenticated=False, two_factor_pending=False)
        needs_2fa = self._user.needs_two_factor
        return ProtonAuthState(
            authenticated=not needs_2fa,
            two_factor_pending=needs_2fa,
        )

    # ----------------------------- HTTP helper ----------------------------- #

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json: dict[str, Any] | None = None,
        session: _Session | None = None,
    ) -> dict[str, Any]:
        """POST/DELETE to Proton; raises ProtonAuthError on any non-1000 response."""
        headers: dict[str, str] = {}
        if session is not None:
            headers["x-pm-uid"] = session.uid
            headers["Authorization"] = f"Bearer {session.access_token}"

        try:
            response = await self._http.request(
                method, endpoint, json=json, headers=headers
            )
        except httpx.HTTPError as exc:
            raise ProtonAuthError(f"Proton network error: {exc}") from exc

        try:
            data: dict[str, Any] = response.json()
        except ValueError as exc:
            raise ProtonAuthError(
                f"Proton returned non-JSON ({response.status_code}): {response.text[:200]}"
            ) from exc

        code = data.get("Code")
        if code == PROTON_OK_CODE:
            return data

        message = data.get("Error") or response.reason_phrase or "unknown"
        if code == PROTON_HUMAN_VERIFICATION_CODE:
            details = data.get("Details") or {}
            methods = details.get("HumanVerificationMethods") or []
            # DEV ONLY: log full Details so we can extract WebUrl + HV token
            # to wire the captcha widget. Remove once HV flow is stable.
            logger.warning(
                "proton.hv.required",
                methods=methods,
                hv_token=details.get("HumanVerificationToken"),
                web_url=details.get("WebUrl"),
                expires_at=details.get("ExpiresAt"),
                direct=details.get("Direct"),
            )
            raise ProtonHumanVerificationError(
                f"[Proton {code}] {message} — méthodes acceptées : {methods or 'inconnues'}"
            )
        raise ProtonAuthError(f"[Proton {code}] {message}")

    # ----------------------------- auth flow ----------------------------- #

    async def _ensure_anon_session(self) -> _Session:
        if self._anon is not None:
            return self._anon
        data = await self._request("POST", "/auth/v4/sessions")
        self._anon = _Session(uid=data["UID"], access_token=data["AccessToken"])
        return self._anon

    def _verify_modulus(self, armored: str) -> bytes:
        if self._gpg is None or SRP_MODULUS_KEY_FINGERPRINT is None:
            raise ProtonAuthError("PGP verification unavailable")
        verified = self._gpg.decrypt(armored)
        fingerprint = (getattr(verified, "fingerprint", "") or "").lower()
        if not verified.valid or fingerprint != SRP_MODULUS_KEY_FINGERPRINT:
            raise ProtonAuthError("Proton modulus signature invalid (possible MITM)")
        return base64.b64decode(verified.data.strip())

    async def login(self, username: str, password: str) -> ProtonAuthState:
        if SrpUser is None:
            raise ProtonAuthError("proton-client (SRP math) not installed")

        async with self._lock:
            # Reset any prior session so we start clean.
            self._user = None

            anon = await self._ensure_anon_session()

            # 1. SRP info
            info = await self._request(
                "POST",
                "/auth/v4/info",
                json={"Username": username, "Intent": "Proton"},
                session=anon,
            )

            modulus = self._verify_modulus(info["Modulus"])
            server_eph = base64.b64decode(info["ServerEphemeral"])
            salt = base64.b64decode(info["Salt"])

            # 2. SRP proof (CPU-bound; offload from event loop)
            usr = SrpUser(password, modulus)
            client_eph = await asyncio.to_thread(usr.get_challenge)
            proof = await asyncio.to_thread(
                usr.process_challenge, salt, server_eph, info["Version"]
            )
            if proof is None:
                raise ProtonAuthError("SRP challenge failed (bad server params)")

            # 3. Submit proof
            auth = await self._request(
                "POST",
                "/auth/v4",
                json={
                    "Username": username,
                    "ClientEphemeral": base64.b64encode(client_eph).decode(),
                    "ClientProof": base64.b64encode(proof).decode(),
                    "SRPSession": info["SRPSession"],
                },
                session=anon,
            )

            usr.verify_session(base64.b64decode(auth["ServerProof"]))
            if not usr.authenticated():
                raise ProtonAuthError(
                    "Server SRP proof failed verification (possible MITM)"
                )

            scope_raw = auth.get("Scope", "")
            scope = (
                scope_raw.split() if isinstance(scope_raw, str) else list(scope_raw or [])
            )

            self._user = _Session(
                uid=auth["UID"],
                access_token=auth["AccessToken"],
                refresh_token=auth.get("RefreshToken", ""),
                scope=scope,
            )
            # Anonymous session no longer useful once we hold a user session.
            self._anon = None

        result = self.state()
        logger.info(
            "proton.login.ok",
            two_factor_pending=result.two_factor_pending,
        )
        return result

    async def submit_two_factor(self, code: str) -> ProtonAuthState:
        async with self._lock:
            if self._user is None:
                raise ProtonNotLoggedInError("login() must succeed first")
            data = await self._request(
                "POST",
                "/auth/v4/2fa",
                json={"TwoFactorCode": code},
                session=self._user,
            )
            scope_raw = data.get("Scope", "")
            new_scope = (
                scope_raw.split() if isinstance(scope_raw, str) else list(scope_raw or [])
            )
            self._user.scope = new_scope

        result = self.state()
        logger.info("proton.2fa.ok", authenticated=result.authenticated)
        return result

    async def logout(self) -> None:
        async with self._lock:
            session = self._user
            self._user = None
            self._anon = None
        if session is None:
            return
        try:
            await self._request("DELETE", "/auth/v4", session=session)
        except ProtonAuthError as exc:
            logger.warning("proton.logout.api_error", error=str(exc))

    # ----------------------------- authenticated API ----------------------------- #

    async def api_request(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated request on behalf of the logged-in user."""
        async with self._lock:
            if self._user is None or self._user.needs_two_factor:
                raise ProtonNotLoggedInError("Not fully authenticated")
            session = self._user
        return await self._request(method, endpoint, json=json, session=session)

    async def aclose(self) -> None:
        """Release the underlying HTTPX client (call at app shutdown)."""
        await self._http.aclose()
