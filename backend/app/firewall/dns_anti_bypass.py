"""DNS anti-bypass: empêche les clients de contourner le résolveur local.

Deux mécanismes :

1. **Block TCP/853 LAN→WAN** : une règle UCI nommée `slate_ctrl_block_dot_lan`
   qui REJECT les connexions sortantes sur le port 853 depuis la zone `lan`
   vers la zone `wan`. Casse les navigateurs/apps qui utilisent un DoT
   propre vers Cloudflare/Quad9/Google. Ils fallback automatiquement sur le
   DNS système (= AdGuard).

   N'affecte PAS le DoT du Slate lui-même vers son résolveur upstream : ce
   trafic est en OUTPUT (originé par le routeur), pas FORWARD (transitant
   d'un client vers le WAN).

2. **Activation des `*_drop_leaked_dns` GL.iNet préinstallés** : sur les
   firmwares récents GL.iNet, des règles existent déjà mais sont désactivées
   par défaut (`enabled=0`). Elles bloquent les fuites DNS sortantes des
   tunnels WireGuard server / OpenVPN server. On les flipe à `1`.

L'ensemble est exposé via 3 endpoints REST (status / enable / disable),
toggle-able depuis l'UI Protection > DNS.

État live introspecté par `get_status()` qui parse `uci show firewall` et
agrège les flags `enabled` des règles concernées.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from app.exceptions import SlateError
from app.firewall.rule_names import LEGACY_NAMES, make_name
from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)

# Nom UCI de notre règle custom — convention `SC_FR_<INTENT>_<DETAIL>`
# centralisée dans `app/firewall/rule_names.py`. Intent AB = anti-bypass.
CUSTOM_RULE_NAME = make_name("AB", "DOT853_LAN")
# Pre-2026-05-25 the rule was named `slate_ctrl_block_dot_lan`. We delete
# that section on every enable/disable so users who had it before our
# rename don't end up with two near-duplicate rules in their firewall.
LEGACY_CUSTOM_RULE_NAMES = [
    old for old, new in LEGACY_NAMES.items() if new == CUSTOM_RULE_NAME
]

# Règles GL.iNet préinstallées qu'on active (toutes zones LAN-side qui peuvent
# router vers le WAN). Les ovpnserver/wgserver vivent dans la même config
# UCI mais sur leurs propres zones.
GL_LEAKED_RULES = [
    "lan_drop_leaked_dns",
    "lan_drop_leak_adgdns",
    "guest_drop_leaked_dns",
    "guest_drop_leak_adgdns",
    "wgserver_drop_leaked_dns",
    "wgserver_drop_leaked_adgdns",
    "ovpnserver_drop_leaked_dns",
    "ovpnserver_drop_leaked_adgdns",
]


class AntiBypassError(SlateError):
    """Raised when an SSH/UCI operation for anti-bypass fails."""


@dataclass(frozen=True)
class AntiBypassStatus:
    """Snapshot de ce qui est en place côté firewall.

    `custom_block_dot_active` : True si notre règle TCP/853 LAN→WAN est
    en place ET activée. False si absente OU `enabled=0`.

    `gl_rules_enabled` : map slug → True/False/None. None signifie "la règle
    n'existe pas dans cette config firewall" (les zones wgserver/ovpnserver
    ne sont pas toujours présentes, par exemple).
    """

    custom_block_dot_active: bool
    gl_rules_enabled: dict[str, bool | None]

    @property
    def all_active(self) -> bool:
        """True ssi le block custom EST actif ET toutes les règles GL.iNet
        existantes (non-None) sont enabled."""
        if not self.custom_block_dot_active:
            return False
        return all(v is True for v in self.gl_rules_enabled.values() if v is not None)

    @property
    def any_active(self) -> bool:
        """True ssi au moins un mécanisme est actif. Utile pour distinguer
        l'état "tout off" du "partiel"."""
        if self.custom_block_dot_active:
            return True
        return any(v is True for v in self.gl_rules_enabled.values() if v is not None)


async def _uci_show(ssh: SlateSSH, prefix: str) -> dict[str, str]:
    """Parse `uci show firewall.*` and return a flat dict of options."""
    try:
        r = await ssh.run(f"uci show {prefix} 2>/dev/null", timeout=8)
    except SlateSSHError as exc:
        raise AntiBypassError(f"SSH uci show failed: {exc}") from exc
    result: dict[str, str] = {}
    for line in r.stdout.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        # Strip surrounding quotes uci wraps values with.
        if value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        result[key.strip()] = value
    return result


async def get_status(ssh: SlateSSH) -> AntiBypassStatus:
    """Read the firewall config and report what's enabled."""
    fw = await _uci_show(ssh, "firewall")

    # Custom rule: enabled iff section exists AND its `enabled` option is
    # missing (UCI default = enabled) OR explicitly == '1'.
    custom_section_key = f"firewall.{CUSTOM_RULE_NAME}"
    custom_exists = custom_section_key in fw
    custom_enabled_raw = fw.get(f"{custom_section_key}.enabled", "1" if custom_exists else "0")
    custom_active = custom_exists and custom_enabled_raw in ("1", "yes", "true", "on")

    gl_rules: dict[str, bool | None] = {}
    for slug in GL_LEAKED_RULES:
        rule_key = f"firewall.{slug}"
        if rule_key not in fw:
            gl_rules[slug] = None  # règle n'existe pas dans cette config
            continue
        enabled_raw = fw.get(f"{rule_key}.enabled", "1")
        gl_rules[slug] = enabled_raw in ("1", "yes", "true", "on")

    return AntiBypassStatus(
        custom_block_dot_active=custom_active,
        gl_rules_enabled=gl_rules,
    )


async def enable(ssh: SlateSSH) -> AntiBypassStatus:
    """Activate every anti-bypass mechanism. Idempotent.

    Pipeline:
      1. Upsert the custom TCP/853 LAN→WAN reject rule.
      2. Flip every existing GL_LEAKED_RULES to enabled=1.
      3. uci commit firewall + service firewall reload.
      4. Re-introspect and return the new status.
    """
    # Step 1: build a script that creates/updates our rule and enables
    # all the GL.iNet ones in a single SSH round-trip. The script uses
    # `uci -q get` first to detect existence; on absent, we create the
    # section via the typed `uci add firewall rule` form (no fragile
    # @rule[N] reference handling).
    legacy_purge = " ; ".join(
        f"uci -q delete firewall.{old} 2>/dev/null"
        for old in LEGACY_CUSTOM_RULE_NAMES
    ) or "true"
    create_or_update_custom = f"""
    {legacy_purge}
    if ! uci -q get firewall.{CUSTOM_RULE_NAME} >/dev/null 2>&1; then
      uci set firewall.{CUSTOM_RULE_NAME}=rule
    fi
    uci set firewall.{CUSTOM_RULE_NAME}.name='{CUSTOM_RULE_NAME}'
    uci set firewall.{CUSTOM_RULE_NAME}.src='lan'
    uci set firewall.{CUSTOM_RULE_NAME}.dest='wan'
    uci set firewall.{CUSTOM_RULE_NAME}.proto='tcp'
    uci set firewall.{CUSTOM_RULE_NAME}.dest_port='853'
    uci set firewall.{CUSTOM_RULE_NAME}.target='REJECT'
    uci set firewall.{CUSTOM_RULE_NAME}.enabled='1'
    """
    enable_gl_rules = " ; ".join(
        # `uci -q get` returns non-zero if absent — we skip silently.
        f"uci -q get firewall.{slug} >/dev/null 2>&1 && uci set firewall.{slug}.enabled='1'"
        for slug in GL_LEAKED_RULES
    )
    script = (
        f"{create_or_update_custom}\n"
        f"{enable_gl_rules}\n"
        "uci commit firewall && "
        "/etc/init.d/firewall reload >/dev/null 2>&1 && "
        "echo OK"
    )
    try:
        r = await ssh.run(script, timeout=30)
    except SlateSSHError as exc:
        raise AntiBypassError(f"SSH enable failed: {exc}") from exc
    if "OK" not in r.stdout:
        raise AntiBypassError(
            f"enable did not return OK (stdout={r.stdout!r}, stderr={r.stderr!r})",
        )
    logger.info("dns.anti_bypass.enabled")
    return await get_status(ssh)


async def disable(ssh: SlateSSH) -> AntiBypassStatus:
    """Deactivate every anti-bypass mechanism. Idempotent.

    The custom rule is DELETED (not just disabled) — we don't want to leave
    a confusing section around when the user opts out. The GL.iNet rules
    are flipped back to enabled=0 (we never delete them since they're shipped
    by the firmware).
    """
    disable_gl_rules = " ; ".join(
        f"uci -q get firewall.{slug} >/dev/null 2>&1 && uci set firewall.{slug}.enabled='0'"
        for slug in GL_LEAKED_RULES
    )
    script = (
        f"uci -q delete firewall.{CUSTOM_RULE_NAME} 2>/dev/null ; "
        f"{disable_gl_rules} ; "
        "uci commit firewall && "
        "/etc/init.d/firewall reload >/dev/null 2>&1 && "
        "echo OK"
    )
    try:
        r = await ssh.run(script, timeout=30)
    except SlateSSHError as exc:
        raise AntiBypassError(f"SSH disable failed: {exc}") from exc
    if "OK" not in r.stdout:
        raise AntiBypassError(
            f"disable did not return OK (stdout={r.stdout!r}, stderr={r.stderr!r})",
        )
    logger.info("dns.anti_bypass.disabled")
    return await get_status(ssh)
