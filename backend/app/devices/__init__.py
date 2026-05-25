"""Device management — adoption, credentials, hardening.

Each managed GL.iNet device (Slate, future Mudi, …) has:
- a `DeviceRow` (host, ports, TLS fingerprint, status)
- one or more `DeviceSecretRow` entries (RPC password, SSH keypair, …)

The controller designates one device as `is_default=True`; all current
routes use that one implicitly. Multi-device active selection lands later.
"""
