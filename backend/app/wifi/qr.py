"""WiFi QR code generation.

Builds the standard "WIFI:..." payload string per the Wi-Fi Alliance QR spec
and renders it as a PNG. The password is encoded inside the image; it never
leaves the server in cleartext form (caller fetches PNG bytes via /qr).
"""

from __future__ import annotations

import io
from typing import Literal

import qrcode

from app.wifi.models import WifiSecurity


def _qr_security(security: WifiSecurity) -> Literal["WPA", "WEP", "nopass"]:
    """Map our security enum to the WiFi QR spec's `T:` field.

    The spec only formally defines WPA/WEP/nopass. WPA3 networks are scanned
    fine when announced as `T:WPA` by virtually every modern phone.
    """
    if security == "open":
        return "nopass"
    return "WPA"


def _escape(value: str) -> str:
    """Escape characters reserved by the WiFi QR spec: \\ ; , : "."""
    out = []
    for ch in value:
        if ch in ("\\", ";", ",", ":", '"'):
            out.append("\\")
        out.append(ch)
    return "".join(out)


def build_wifi_qr_string(
    *,
    ssid_name: str,
    security: WifiSecurity,
    password: str,
    hidden: bool = False,
) -> str:
    """Build the canonical 'WIFI:...' payload for the QR encoder."""
    t = _qr_security(security)
    parts = [f"T:{t}", f"S:{_escape(ssid_name)}"]
    if t != "nopass" and password:
        parts.append(f"P:{_escape(password)}")
    parts.append("H:true" if hidden else "H:false")
    return "WIFI:" + ";".join(parts) + ";;"


def render_qr_png(payload: str, *, box_size: int = 8, border: int = 4) -> bytes:
    """Render an arbitrary string to a PNG bytestring."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
