"""High-level AdGuard Home control."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from typing import Any

import bcrypt
import httpx
import structlog
import yaml

from app.exceptions import SlateError
from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)

# AdGuard's HTTP control plane port (config.yaml: http.address = 0.0.0.0:3000).
ADGUARD_HTTP_PORT = 3000


class AdGuardError(SlateError):
    """Any failure talking to AdGuard (UCI or REST)."""


@dataclass(frozen=True)
class AdGuardStatus:
    uci_enabled: bool  # adguardhome.config.enabled
    init_running: bool  # /etc/init.d/adguardhome status == running
    web_ui_reachable: bool  # GET :3000/control/status returns 200
    web_ui_url: str  # convenience link for the UI
    protection_enabled: bool | None  # AdGuard's own toggle (only if reachable)
    dns_port: int | None  # the port the AdGuard DNS server binds (from REST)
    version: str | None
    error: str | None  # human-readable error if anything failed


@dataclass(frozen=True)
class AdGuardStats:
    num_dns_queries: int
    num_blocked_filtering: int
    num_replaced_safebrowsing: int
    num_replaced_parental: int
    avg_processing_time_ms: float
    top_queried_domains: list[dict[str, int]] = field(default_factory=list)
    top_blocked_domains: list[dict[str, int]] = field(default_factory=list)
    top_clients: list[dict[str, int]] = field(default_factory=list)


@dataclass(frozen=True)
class AdGuardFilter:
    id: int
    name: str
    url: str
    enabled: bool
    rules_count: int
    last_updated: str | None  # ISO ts, AdGuard returns it as str


class AdGuardManager:
    """Wraps UCI-via-SSH for enable/disable + httpx for the REST API."""

    def __init__(
        self,
        ssh: SlateSSH,
        slate_host: str,
        *,
        admin_username: str,
        admin_password: str,
        http_port: int = ADGUARD_HTTP_PORT,
        timeout: float = 5.0,
    ) -> None:
        self._ssh = ssh
        self._host = slate_host
        self._port = http_port
        self._base_url = f"http://{slate_host}:{http_port}"
        self._admin_username = admin_username
        self._admin_password = admin_password
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers={"Accept": "application/json"},
            auth=(admin_username, admin_password),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---------------------------- status ---------------------------- #

    async def get_status(self) -> AdGuardStatus:
        """Return a combined view: UCI flag + init.d state + REST liveness."""
        web_ui_url = self._base_url
        uci_enabled = False
        init_running = False
        web_ui_reachable = False
        protection_enabled: bool | None = None
        dns_port: int | None = None
        version: str | None = None
        error: str | None = None

        # UCI + init.d via SSH (single multi-command for speed).
        try:
            result = await self._ssh.run(
                "uci get adguardhome.config.enabled 2>/dev/null; "
                "echo '---'; "
                "/etc/init.d/adguardhome status 2>/dev/null; echo exit=$?",
            )
            blocks = result.stdout.split("---", 1)
            uci_part = blocks[0].strip() if blocks else ""
            init_part = blocks[1].strip() if len(blocks) > 1 else ""
            uci_enabled = uci_part == "1"
            init_running = "running" in init_part.lower() or "exit=0" in init_part
        except SlateSSHError as exc:
            error = f"SSH probe failed: {exc}"

        # REST probe (only meaningful if init_running, but try anyway — cheap).
        if init_running:
            try:
                resp = await self._http.get("/control/status")
                if resp.status_code == 200:
                    web_ui_reachable = True
                    data = resp.json()
                    protection_enabled = bool(data.get("protection_enabled"))
                    dns_port = int(data.get("dns_port") or 0) or None
                    version = data.get("version")
            except httpx.HTTPError as exc:
                # Service is up but REST is flaky — note it but don't fail.
                if error is None:
                    error = f"REST probe failed: {exc}"

        return AdGuardStatus(
            uci_enabled=uci_enabled,
            init_running=init_running,
            web_ui_reachable=web_ui_reachable,
            web_ui_url=web_ui_url,
            protection_enabled=protection_enabled,
            dns_port=dns_port,
            version=version,
            error=error,
        )

    # ---------------------------- bootstrap auth ---------------------------- #

    async def is_admin_provisioned(self) -> bool:
        """True if the AdGuard REST API accepts our Basic auth credentials.

        Distinguishes "AdGuard down" (connection error) from "AdGuard up but
        we don't have the right creds" (HTTP 403/401).
        """
        try:
            resp = await self._http.get("/control/status")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def bootstrap_admin(self) -> None:
        """Inject the controller's admin user into /etc/AdGuardHome/config.yaml.

        AdGuard refuses every REST call (`403 Forbidden`) when `users: []` —
        even the install wizard endpoints — because the daemon thinks it's
        already configured (the config file exists). The cleanest unblock is
        to write a `users:` block ourselves and restart the daemon.

        Strategy: read the YAML, mutate `users` in Python (safe), push it back
        via base64-over-SSH (avoids shell-quoting nightmares).
        """
        hashed = await asyncio.to_thread(
            bcrypt.hashpw,
            self._admin_password.encode("utf-8")[:72],  # bcrypt's hard limit
            bcrypt.gensalt(rounds=10),
        )
        hashed_str = hashed.decode("ascii")
        username = self._admin_username

        # 1. Read current config.
        try:
            cat = await self._ssh.run("cat /etc/AdGuardHome/config.yaml")
        except SlateSSHError as exc:
            raise AdGuardError(f"SSH read failed: {exc}") from exc
        if cat.exit_status != 0:
            raise AdGuardError(f"could not read config.yaml: {cat.stderr!r}")

        try:
            config = yaml.safe_load(cat.stdout) or {}
        except yaml.YAMLError as exc:
            raise AdGuardError(f"config.yaml is not valid YAML: {exc}") from exc

        # 2. Mutate users.
        config["users"] = [{"name": username, "password": hashed_str}]

        # Ensure AdGuard's DNS server doesn't try to bind :53 — dnsmasq owns
        # that port on the Slate. The Slate's stock layout uses :3053 with
        # dnsmasq forwarding queries upstream.
        dns_cfg = config.setdefault("dns", {})
        if dns_cfg.get("port") == 53 or dns_cfg.get("port") is None:
            dns_cfg["port"] = 3053

        # 3. Serialize and push back via base64 (avoids quote escaping).
        # The Slate's `openssl base64 -d` is MIME-strict: it only decodes
        # base64 wrapped to ≤76 chars per line. encodebytes() does exactly
        # that (trailing newline included).
        new_yaml = yaml.safe_dump(config, sort_keys=False, default_flow_style=False)
        b64 = base64.encodebytes(new_yaml.encode("utf-8")).decode("ascii")
        push_cmd = (
            "/etc/init.d/adguardhome stop 2>/dev/null; "
            "mkdir -p /etc/AdGuardHome/backup && "
            "cp /etc/AdGuardHome/config.yaml "
            "  /etc/AdGuardHome/backup/config.yaml.pre-bootstrap 2>/dev/null; "
            f"printf '%s' '{b64}' | openssl base64 -d > /etc/AdGuardHome/config.yaml.new && "
            "mv /etc/AdGuardHome/config.yaml.new /etc/AdGuardHome/config.yaml && "
            "chmod 600 /etc/AdGuardHome/config.yaml && "
            "/etc/init.d/adguardhome start && echo OK"
        )
        try:
            result = await self._ssh.run(push_cmd)
        except SlateSSHError as exc:
            raise AdGuardError(f"SSH bootstrap failed: {exc}") from exc
        if "OK" not in result.stdout:
            raise AdGuardError(
                f"bootstrap did not return OK "
                f"(stderr={result.stderr!r}, stdout={result.stdout!r})",
            )
        # Wait for daemon to bind :3000 (max ~10s).
        for _ in range(20):
            await asyncio.sleep(0.5)
            if await self.is_admin_provisioned():
                logger.info("adguard.bootstrap.ok", username=username)
                return
        raise AdGuardError(
            "bootstrap completed but REST API still refusing our credentials "
            "after 10s — check /etc/AdGuardHome/config.yaml manually",
        )

    # ---------------------------- enable/disable ---------------------------- #

    async def set_enabled(self, enabled: bool) -> None:
        """Flip the UCI flag + start/stop the service.

        Persists across reboot (init.d enable/disable).
        """
        flag = "1" if enabled else "0"
        action = "start" if enabled else "stop"
        autostart = "enable" if enabled else "disable"
        cmd = (
            f"uci set adguardhome.config.enabled='{flag}' && "
            f"uci commit adguardhome && "
            f"/etc/init.d/adguardhome {autostart} 2>/dev/null; "
            f"/etc/init.d/adguardhome {action} && echo OK"
        )
        try:
            result = await self._ssh.run(cmd)
        except SlateSSHError as exc:
            raise AdGuardError(f"SSH command failed: {exc}") from exc
        if "OK" not in result.stdout:
            raise AdGuardError(
                f"adguardhome {action} did not return OK "
                f"(stderr={result.stderr!r}, stdout={result.stdout!r})",
            )
        logger.info("adguard.set_enabled", enabled=enabled)

    # ---------------------------- protection ---------------------------- #

    async def set_protection(self, enabled: bool) -> None:
        """Toggle AdGuard's own 'protection' (filters/safesearch/etc) without stopping the daemon."""
        try:
            resp = await self._http.post(
                "/control/protection",
                json={"enabled": enabled, "duration": 0},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AdGuardError(f"AdGuard REST /control/protection failed: {exc}") from exc

    # ---------------------------- DNS config (DNSSEC) ---------------------------- #

    async def get_dns_config(self) -> dict[str, Any]:
        """Read the full /control/dns_info block. We use it to introspect
        enable_dnssec, upstream_dns, fallback_dns, cache_size, etc."""
        try:
            resp = await self._http.get("/control/dns_info")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise AdGuardError(f"AdGuard REST /control/dns_info failed: {exc}") from exc

    async def set_dnssec_enabled(self, enabled: bool) -> dict[str, Any]:
        """Toggle AdGuard's local DNSSEC validation (sets the DO bit on
        upstream queries AND validates the returned RRSIGs).

        Why this matters : without this flag, AdGuard trusts whatever the
        upstream (Quad9, DNS4EU, Cloudflare…) tells it. A BGP hijack on
        the upstream resolver or a cache poisoning attack would slip
        through silently. With it on, the controller catches signature
        failures itself → SERVFAIL surfaces to the client.

        Side effect : ~0.5% of domains have broken DNSSEC (zone owner's
        fault, not ours) → those become inaccessible. The trade-off is
        explicitly acceptable for the Slate's threat model.
        """
        # AdGuard's API quirks: /control/dns_config wants the FULL config
        # blob; partial updates corrupt other fields. So we read first,
        # mutate, write back.
        try:
            current = await self.get_dns_config()
        except AdGuardError:
            raise
        current["enable_dnssec"] = bool(enabled)
        try:
            resp = await self._http.post("/control/dns_config", json=current)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AdGuardError(
                f"AdGuard REST /control/dns_config failed: {exc}",
            ) from exc
        logger.info("adguard.dnssec_set", enabled=enabled)
        return current

    # ---------------------------- stats ---------------------------- #

    async def get_stats(self) -> AdGuardStats:
        try:
            resp = await self._http.get("/control/stats")
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        except httpx.HTTPError as exc:
            raise AdGuardError(f"AdGuard REST /control/stats failed: {exc}") from exc
        return AdGuardStats(
            num_dns_queries=int(data.get("num_dns_queries") or 0),
            num_blocked_filtering=int(data.get("num_blocked_filtering") or 0),
            num_replaced_safebrowsing=int(data.get("num_replaced_safebrowsing") or 0),
            num_replaced_parental=int(data.get("num_replaced_parental") or 0),
            avg_processing_time_ms=float(data.get("avg_processing_time") or 0.0) * 1000,
            top_queried_domains=list(data.get("top_queried_domains") or []),
            top_blocked_domains=list(data.get("top_blocked_domains") or []),
            top_clients=list(data.get("top_clients") or []),
        )

    # ---------------------------- filters ---------------------------- #

    async def list_filters(self) -> list[AdGuardFilter]:
        try:
            resp = await self._http.get("/control/filtering/status")
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise AdGuardError(f"AdGuard REST /control/filtering/status failed: {exc}") from exc

        out: list[AdGuardFilter] = []
        for f in data.get("filters") or []:
            out.append(
                AdGuardFilter(
                    id=int(f.get("id") or 0),
                    name=str(f.get("name") or ""),
                    url=str(f.get("url") or ""),
                    enabled=bool(f.get("enabled")),
                    rules_count=int(f.get("rules_count") or 0),
                    last_updated=f.get("last_updated"),
                ),
            )
        return out

    async def add_filter(self, *, url: str, name: str) -> None:
        try:
            resp = await self._http.post(
                "/control/filtering/add_url",
                json={"url": url, "name": name, "whitelist": False},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AdGuardError(f"AdGuard REST add_url failed: {exc}") from exc

    async def remove_filter(self, *, url: str) -> None:
        try:
            resp = await self._http.post(
                "/control/filtering/remove_url",
                json={"url": url, "whitelist": False},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AdGuardError(f"AdGuard REST remove_url failed: {exc}") from exc

    async def set_filter_enabled(self, *, url: str, enabled: bool) -> None:
        try:
            resp = await self._http.post(
                "/control/filtering/set_url",
                json={
                    "url": url,
                    "whitelist": False,
                    "data": {"url": url, "enabled": enabled},
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AdGuardError(f"AdGuard REST set_url failed: {exc}") from exc

    async def refresh_filters(self) -> None:
        """Force-refresh all blocklists from their upstream URLs."""
        try:
            resp = await self._http.post(
                "/control/filtering/refresh",
                json={"whitelist": False},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AdGuardError(f"AdGuard REST refresh failed: {exc}") from exc
