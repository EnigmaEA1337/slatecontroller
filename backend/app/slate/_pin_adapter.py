"""SHA256 leaf-cert pinning HTTPAdapter for requests.

Used by SlateClient when a ``tls_fingerprint_sha256`` is configured on
the device row. Mounted on the requests.Session that pyglinet uses
internally, so EVERY RPC call (including the initial ``login`` POST)
runs over a TLS connection whose leaf cert SHA256 matches the pinned
value. Mismatch → ``urllib3.exceptions.SSLError`` (wrapped by requests
as ``requests.exceptions.SSLError``) and the connection is torn down.

This module is independent from asyncssh / SlateSSH ; it deals
exclusively with the JSON-RPC TLS path.
"""

from __future__ import annotations

from requests.adapters import HTTPAdapter


class FingerprintAdapter(HTTPAdapter):
    """Pin the TLS leaf cert by SHA256 fingerprint.

    The fingerprint must be the bare hex digest (lowercase, no colons)
    of the leaf cert's DER-encoded form — same format as what
    ``urllib3.util.ssl_.assert_fingerprint`` expects.
    """

    def __init__(self, fingerprint: str, *args, **kwargs) -> None:
        # Normalise : strip colons + spaces, lowercase. Defensive even
        # though the caller already pre-normalised — better belt-and-
        # suspenders for a security-sensitive value.
        self._fingerprint = (
            fingerprint.replace(":", "").replace(" ", "").lower()
        )
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args, **kwargs):  # noqa: D401
        kwargs["assert_fingerprint"] = self._fingerprint
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):  # noqa: D401
        kwargs["assert_fingerprint"] = self._fingerprint
        return super().proxy_manager_for(*args, **kwargs)
