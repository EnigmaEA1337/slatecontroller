"""HMAC auth + replay protection for Slate → Controller webhooks.

Wire format :

  POST /api/webhooks/slate/event HTTP/1.1
  X-Slate-Slug: s7p-roadwarrior
  X-Slate-Timestamp: 1717435812
  X-Slate-Signature: <hex(HMAC-SHA256(secret, "<ts>.<raw_body>"))>
  Content-Type: application/json
  <raw json body>

Server-side verification :

  1. Reject if Timestamp is missing OR ``|now - timestamp| > 300`` seconds
     (5-min window) — replay-resistance, doesn't require server-side state.
  2. Read the per-slug secret (current + within-grace previous_secret).
  3. Compute HMAC, ``hmac.compare_digest`` against the header.

Rotation : a fresh secret is generated server-side and pushed to the
device. The old one is kept in ``previous_secret`` with
``previous_valid_until = now + 30s`` so a request in flight at the moment
of rotation still validates against the old key.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets as _secrets
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import WebhookSecretRow

logger = structlog.get_logger(__name__)


# Max clock skew (seconds) tolerated between Slate and Controller. The
# Slate sets its time via NTP after WAN comes up ; a healthy device is
# within 1-2s. 5 min covers boot-without-NTP edge cases.
MAX_SKEW_S = 300

# How long a rotated-out secret remains valid after rotation. Long enough
# that a push fired right before rotation still passes ; short enough
# that a leaked old key is useless within seconds.
PREVIOUS_GRACE_S = 30

SECRET_BYTES = 32


def make_secret() -> str:
    """64-char hex secret = 32 bytes of OS randomness."""
    return _secrets.token_hex(SECRET_BYTES)


class WebhookAuthService:
    """Validates inbound webhook signatures + manages per-device secrets."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def get_or_create_secret(self, slug: str) -> str:
        """Return the *current* secret for a device, materialising one
        on first call. Idempotent — re-running returns the same secret."""
        async with self._sf() as s:
            row = await s.scalar(
                select(WebhookSecretRow).where(
                    WebhookSecretRow.device_slug == slug,
                ),
            )
            if row is not None:
                return row.secret
            secret = make_secret()
            row = WebhookSecretRow(
                device_slug=slug,
                secret=secret,
                previous_secret="",
                previous_valid_until=None,
            )
            s.add(row)
            await s.commit()
            return secret

    async def rotate_secret(self, slug: str) -> str:
        """Generate a fresh secret. The previous one stays valid for
        :data:`PREVIOUS_GRACE_S` seconds so in-flight requests don't 401."""
        new_secret = make_secret()
        now = datetime.now(UTC).replace(tzinfo=None)
        async with self._sf() as s:
            row = await s.scalar(
                select(WebhookSecretRow).where(
                    WebhookSecretRow.device_slug == slug,
                ),
            )
            if row is None:
                row = WebhookSecretRow(
                    device_slug=slug,
                    secret=new_secret,
                    previous_secret="",
                    previous_valid_until=None,
                )
                s.add(row)
            else:
                row.previous_secret = row.secret
                row.previous_valid_until = now + timedelta(
                    seconds=PREVIOUS_GRACE_S,
                )
                row.secret = new_secret
                row.rotated_at = now
            await s.commit()
        logger.info("webhook_auth.secret_rotated", slug=slug)
        return new_secret

    async def verify(
        self,
        *,
        slug: str,
        timestamp_header: str,
        signature_header: str,
        raw_body: bytes,
    ) -> None:
        """Raises :class:`HTTPException` 401 on invalid signature or
        out-of-window timestamp. Returns silently on success."""
        if not slug or not timestamp_header or not signature_header:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing slug / timestamp / signature header",
            )
        try:
            ts = int(timestamp_header)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid timestamp",
            ) from exc
        now = int(datetime.now(UTC).timestamp())
        if abs(now - ts) > MAX_SKEW_S:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    f"timestamp skew {abs(now - ts)}s exceeds "
                    f"max {MAX_SKEW_S}s"
                ),
            )

        async with self._sf() as s:
            row = await s.scalar(
                select(WebhookSecretRow).where(
                    WebhookSecretRow.device_slug == slug,
                ),
            )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"no webhook secret for slug {slug!r}",
            )

        payload = f"{ts}.".encode() + raw_body
        # Try current secret.
        expected = hmac.new(
            row.secret.encode(), payload, hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(expected, signature_header):
            return
        # Try previous within grace.
        if row.previous_secret and row.previous_valid_until is not None:
            now_dt = datetime.now(UTC).replace(tzinfo=None)
            if row.previous_valid_until > now_dt:
                expected_prev = hmac.new(
                    row.previous_secret.encode(), payload, hashlib.sha256,
                ).hexdigest()
                if hmac.compare_digest(expected_prev, signature_header):
                    logger.info(
                        "webhook_auth.previous_secret_accepted", slug=slug,
                    )
                    return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="signature mismatch",
        )
