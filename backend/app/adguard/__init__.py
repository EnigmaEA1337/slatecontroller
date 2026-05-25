"""AdGuard Home integration (DNS-level filtering on the Slate).

Two control surfaces:
- UCI/SSH for enable/disable (the service is gated behind OpenWrt's init.d)
- AdGuard REST API on :3000 for runtime ops (stats, filters)
"""
