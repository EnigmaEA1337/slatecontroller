"""Minimal Tailscale admin REST API client (api.tailscale.com/api/v2).

Used by the audit module to fetch tailnet-wide policy that is NOT visible
from the local `tailscale` CLI: ACL structure, key inventory, settings
(tailnet lock, device approval, key durations), device tags across the
tailnet, etc.

Design choices:
  - Plain httpx.AsyncClient; we own ~6 GET calls so no SDK dependency.
  - Bearer auth on a Personal Access Token (PAT). OAuth client flow is a
    later upgrade path (admin_store would gain refresh logic).
  - Per-call timeout of 15s. Tailscale's API is usually <500ms but the
    audit fans out, so we keep budgets explicit.
  - The tailnet identifier `-` resolves to "the tailnet of the token",
    which means we never have to ask the user for it.
  - Each method returns the raw decoded JSON on 2xx, or raises
    TailscaleAdminAPIError with the HTTP status — the audit layer
    converts those into "skip" findings without penalty.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

BASE_URL = "https://api.tailscale.com/api/v2"
DEFAULT_TIMEOUT = 15.0


class TailscaleAdminAPIError(Exception):
    """Raised on 4xx/5xx, network, or token errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TailscaleAdminAPI:
    """Async client. One short-lived instance per audit run."""

    def __init__(self, pat: str, tailnet: str = "-") -> None:
        self._pat = pat
        self._tailnet = tailnet
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=DEFAULT_TIMEOUT,
            headers={
                "Authorization": f"Bearer {pat}",
                "Accept": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "TailscaleAdminAPI":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def _get(self, path: str, **kwargs: Any) -> Any:
        try:
            r = await self._client.get(path, **kwargs)
        except httpx.HTTPError as exc:
            raise TailscaleAdminAPIError(f"network error: {exc}") from exc
        if r.status_code == 401:
            raise TailscaleAdminAPIError("PAT rejected (401)", 401)
        if r.status_code == 403:
            raise TailscaleAdminAPIError("PAT lacks scope (403)", 403)
        if r.status_code == 404:
            raise TailscaleAdminAPIError(f"not found: {path}", 404)
        if r.status_code >= 400:
            raise TailscaleAdminAPIError(
                f"HTTP {r.status_code}: {r.text[:200]}", r.status_code
            )
        try:
            return r.json()
        except ValueError as exc:
            raise TailscaleAdminAPIError(f"invalid JSON from {path}") from exc

    # ---- One probe per audit-relevant resource. -----------------------

    async def whoami(self) -> dict[str, Any]:
        """Token introspection — confirms PAT works + returns tailnet name.

        Note: there's no formal /whoami; we use /tailnet/-/devices?limit=1
        because it's cheap and proves both reachability and read-scope.
        The Tailnet field is parsed from the device entry.
        """
        # Limit isn't a supported param but `fields=default` keeps response small.
        data = await self._get(f"/tailnet/{self._tailnet}/devices")
        devices = data.get("devices") or []
        tailnet_name = "-"
        if devices:
            # Device .name is "<hostname>.<tailnet>" — extract suffix.
            n = devices[0].get("name") or ""
            tailnet_name = n.split(".", 1)[1] if "." in n else "-"
        return {"ok": True, "device_count": len(devices), "tailnet": tailnet_name}

    async def devices(self) -> list[dict[str, Any]]:
        data = await self._get(f"/tailnet/{self._tailnet}/devices")
        return list(data.get("devices") or [])

    async def keys(self) -> list[dict[str, Any]]:
        """List ALL active keys (auth keys + API keys). Returns []
        if the endpoint returns 403 — happens when the PAT is scoped
        narrow (some scopes don't include keys:read)."""
        try:
            data = await self._get(f"/tailnet/{self._tailnet}/keys")
        except TailscaleAdminAPIError as exc:
            if exc.status_code in (403, 404):
                return []
            raise
        return list(data.get("keys") or [])

    async def acl(self) -> dict[str, Any]:
        """Parsed JSON ACL (request Accept: application/json forces the
        server to return resolved JSON rather than HuJSON source)."""
        return await self._get(f"/tailnet/{self._tailnet}/acl")

    async def settings(self) -> dict[str, Any]:
        """Tailnet settings — device approval, max key duration, etc.

        Returns {} on 404 (older Tailscale plans don't have this endpoint).
        """
        try:
            return await self._get(f"/tailnet/{self._tailnet}/settings")
        except TailscaleAdminAPIError as exc:
            if exc.status_code in (403, 404):
                return {}
            raise

    async def dns_preferences(self) -> dict[str, Any]:
        try:
            return await self._get(f"/tailnet/{self._tailnet}/dns/preferences")
        except TailscaleAdminAPIError as exc:
            if exc.status_code in (403, 404):
                return {}
            raise
