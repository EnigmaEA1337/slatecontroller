"""Push a previously-issued leaf cert onto the Slate's uhttpd.

Separated from `pki.py` so the PKI module stays pure crypto + state ;
this module owns the SSH wire format (base64 heredoc, busybox `openssl
base64 -d -A`) and the uhttpd UCI flip.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime

import structlog

from app.settings.internal_ca.state import (
    get_issued_materials,
    set_slate_current,
)
from app.slate.ssh import SlateSSH, SlateSSHError

logger = structlog.get_logger(__name__)


async def push_slate_cert(ssh: SlateSSH, serial_hex: str) -> str:
    """Push the issued cert to BOTH HTTPS servers on the Slate.

    The Slate ships TWO web frontends listening on different ports :
      - **nginx** (GL.iNet UI) — :443 (and :80) — what the operator
        actually hits when typing the bare IP in a browser. Uses
        `/etc/nginx/nginx.cer` + `/etc/nginx/nginx.key`.
      - **uhttpd** (LuCI) — :8443 — advanced web UI. Uses
        `/etc/uhttpd.crt` + `/etc/uhttpd.key`.

    Both must receive the new cert + key for the browser warning to
    disappear regardless of which UI the operator hits. We push to all
    four paths in one SSH round-trip (busybox `openssl base64 -d -A`
    accepts single-line, no MIME folding ; cf. [[reference-slate-quirks]])
    then reload both services. Each reload is independent — if one
    service is missing on a stripped firmware, the other still gets
    its cert.

    Returns a short human status string. Updates the slate/current.json
    pointer so subsequent UI reads know which cert is live.
    """
    cert_pem, key_pem = get_issued_materials(serial_hex)
    cert_b64 = base64.b64encode(cert_pem).decode()
    key_b64 = base64.b64encode(key_pem).decode()

    cmd = f"""set -e
        # Decode once into reusable temp files.
        printf '%s\\n' '{cert_b64}' | openssl base64 -d -A > /tmp/_sc_cert.pem
        printf '%s\\n' '{key_b64}'  | openssl base64 -d -A > /tmp/_sc_key.pem

        # uhttpd (LuCI on :8443). Same caveat as nginx — `reload` re-reads
        # config but caches the cert in memory ; we restart for the new
        # cert to actually be served.
        cp /tmp/_sc_cert.pem /etc/uhttpd.crt
        cp /tmp/_sc_key.pem  /etc/uhttpd.key
        chmod 644 /etc/uhttpd.crt
        chmod 600 /etc/uhttpd.key
        uci set uhttpd.main.cert='/etc/uhttpd.crt'
        uci set uhttpd.main.key='/etc/uhttpd.key'
        uci commit uhttpd
        /etc/init.d/uhttpd restart 2>/dev/null && echo UHTTPD_RESTARTED || echo UHTTPD_SKIPPED

        # nginx (GL.iNet UI on :443) — primary user-facing endpoint.
        # IMPORTANT : on this GL.iNet/OpenWrt build, `reload` (SIGHUP)
        # re-reads the config but does NOT re-load the cert files from
        # disk — the master keeps the old cert PEM cached in memory.
        # We therefore RESTART instead of reload. A few hundred ms of
        # interruption is fine for an admin UI.
        if [ -d /etc/nginx ]; then
            cp /tmp/_sc_cert.pem /etc/nginx/nginx.cer
            cp /tmp/_sc_key.pem  /etc/nginx/nginx.key
            chmod 644 /etc/nginx/nginx.cer
            chmod 600 /etc/nginx/nginx.key
            /etc/init.d/nginx restart 2>/dev/null && echo NGINX_RESTARTED \\
                || echo NGINX_FAILED
        else
            echo NGINX_ABSENT
        fi

        rm -f /tmp/_sc_cert.pem /tmp/_sc_key.pem
        echo PUSHED_OK
    """
    try:
        r = await ssh.run(cmd, timeout=30)
    except SlateSSHError as exc:
        raise RuntimeError(f"SSH push failed: {exc}") from exc
    if "PUSHED_OK" not in r.stdout:
        raise RuntimeError(
            f"unexpected push output : stdout={r.stdout!r} stderr={r.stderr!r}"
        )

    # Summarize which services took the cert — useful in logs + UI.
    parts: list[str] = []
    if "UHTTPD_RESTARTED" in r.stdout:
        parts.append("uhttpd:restarted")
    elif "UHTTPD_SKIPPED" in r.stdout:
        parts.append("uhttpd:skipped")
    if "NGINX_RESTARTED" in r.stdout:
        parts.append("nginx:restarted")
    elif "NGINX_ABSENT" in r.stdout:
        parts.append("nginx:absent")
    elif "NGINX_FAILED" in r.stdout:
        parts.append("nginx:FAILED")

    set_slate_current(serial_hex=serial_hex, pushed_at=datetime.now(UTC))
    logger.info("internal_ca.push.ok", serial_hex=serial_hex, services=parts)
    return f"cert {serial_hex[:16]}… pushed ({', '.join(parts)})"
