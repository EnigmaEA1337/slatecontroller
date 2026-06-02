"""Tor subsystem — global daemon settings + bridges + live status.

Per-network routing toggles live on :class:`app.networks.models.NetworkPublic`
(tor_route_mode / tor_dns_over_tor / tor_kill_switch). This package owns the
cross-cutting parts : whether the daemon is on at all, the bridges to use,
and the live status (queried from the device over SSH).
"""
