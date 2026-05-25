"""DNS protection module — secure DNS provider catalog + security levels.

Independent from the per-profile `dns:` block (which sets WAN resolvers via
`dns.sh` handler). This module is the *control plane* for choosing among
curated secure resolvers (Cloudflare, Quad9, DNS4EU, Mullvad, dns0.eu, etc.)
with encryption (DoT/DoH) and selectable filtering profiles, plus 5 preset
"security levels" that wrap a provider choice with AdGuard/DNSSEC policy.

Public:
- catalog.CATALOG, catalog.get_provider, catalog.DnsProvider
- security_levels.LEVELS, security_levels.get_level, security_levels.SecurityLevel
- manager.DnsProtectionManager (apply + introspect on the Slate)
"""
