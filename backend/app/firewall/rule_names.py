"""Naming convention for every firewall rule the controller injects.

Every UCI firewall section we create on the Slate uses :

  ``SC_FR_<INTENT>_<DETAIL>``

  - ``SC``     fixed prefix — "Slate Controller"
  - ``FR``     fixed sub-prefix — "Firewall" (so we can later add SC_DNS_*,
               SC_VPN_* without ambiguity)
  - ``INTENT`` 2-4 letter uppercase code from the table below
  - ``DETAIL`` short uppercase suffix describing the specific rule
               (port, country, IP family, etc.). Stays under
               ``32 - len(prefix)`` chars so the whole name fits in
               UCI's 32-char section-id limit.

Intent codes :

  =====  ================================================================
  AB     Anti-bypass : forces clients to use the local resolver, blocks
         hard-coded DoT/DoH/VPN-DNS endpoints
  KS     Kill-switch : drops all WAN traffic when VPN is required but
         down
  LD     Lockdown : strict default-deny posture for a profile
  GEO    Geoip whitelist/blacklist (per-country)
  BLK    Generic block (port / IP)
  RDR    Redirect / NAT
  =====  ================================================================

Both the UCI section id AND ``option name`` are set to the SAME string,
so the user sees the exact same identifier in LuCI and in
``uci show firewall | grep SC_FR_``. Single source of truth.

Filtering all our rules :

  uci show firewall | grep -E "^firewall\\.SC_FR_"

Cleanup all our rules at once :

  uci show firewall | grep -oE '^firewall\\.SC_FR_[^=]+' | sort -u \\
    | while read sec; do uci delete "$sec"; done; uci commit firewall

Legacy migration : the table ``LEGACY_NAMES`` maps old (pre-2026-05)
rule section ids to their new SC_FR_* counterparts so callers can
issue best-effort deletes of the old names on next apply without
breaking anything.
"""

from __future__ import annotations

from typing import Final


# Hard cap from UCI : section identifiers must be ≤ 32 chars.
UCI_MAX_SECTION_ID = 32

PREFIX: Final = "SC_FR_"


def make_name(intent: str, detail: str) -> str:
    """Build a `SC_FR_<INTENT>_<DETAIL>` name.

    Both arguments are uppercased + sanitized (only [A-Z0-9_], other
    characters → ``_``). Raises if the resulting name exceeds the UCI
    section-id length limit — caller should pick a shorter detail.
    """
    def _scrub(s: str) -> str:
        out = []
        for ch in s.strip().upper():
            if ch.isalnum() or ch == "_":
                out.append(ch)
            else:
                out.append("_")
        return "".join(out).strip("_") or "X"

    intent_clean = _scrub(intent)
    detail_clean = _scrub(detail)
    name = f"{PREFIX}{intent_clean}_{detail_clean}"
    if len(name) > UCI_MAX_SECTION_ID:
        raise ValueError(
            f"rule name {name!r} exceeds UCI limit "
            f"({len(name)} > {UCI_MAX_SECTION_ID})",
        )
    return name


def is_managed_rule(section_id: str) -> bool:
    """True if `section_id` looks like a rule we created."""
    return section_id.startswith(PREFIX)


# Pre-2026-05-25 rule names that have been renamed. Used at apply time
# to delete the old section before writing the new one — avoids leaving
# two near-duplicate rules in the UCI config after an upgrade.
LEGACY_NAMES: Final[dict[str, str]] = {
    # was slate_ctrl_block_dot_lan, now SC_FR_AB_DOT853_LAN
    "slate_ctrl_block_dot_lan": "SC_FR_AB_DOT853_LAN",
}
