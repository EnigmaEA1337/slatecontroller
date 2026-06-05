"""Inbound webhook endpoint : Slate → Controller event push.

Mounted at ``/api/webhooks/slate``. Validates HMAC-SHA256 over
``{timestamp}.{raw_body}``, dispatches the parsed event by ``type`` to
the :class:`WebhookDispatcher` handlers wired at app boot.

Auth = HMAC only — there is NO Bearer token / JWT here. The Slate is
not a human session ; the signed body proves origin + integrity.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Header, Request, status
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks/slate", tags=["webhooks"])


class WebhookEnvelope(BaseModel):
    """Parsed JSON shape of an inbound event."""

    type: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    sent_at: float | None = Field(default=None, ge=0)


@router.post("/event", status_code=status.HTTP_204_NO_CONTENT)
async def slate_event(
    request: Request,
    x_slate_slug: str = Header(..., alias="X-Slate-Slug"),
    x_slate_timestamp: str = Header(..., alias="X-Slate-Timestamp"),
    x_slate_signature: str = Header(..., alias="X-Slate-Signature"),
) -> None:
    """Receive one Slate-pushed event.

    The verifier and dispatcher live on app.state.webhook_auth and
    app.state.webhook_dispatcher respectively. We deliberately read the
    raw body BEFORE Pydantic parses : the HMAC is computed over the
    exact bytes the Slate sent, parser leniency would break verification.
    """
    auth = getattr(request.app.state, "webhook_auth", None)
    dispatcher = getattr(request.app.state, "webhook_dispatcher", None)
    if auth is None or dispatcher is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="webhook infra not initialised",
        )

    raw_body = await request.body()
    await auth.verify(
        slug=x_slate_slug,
        timestamp_header=x_slate_timestamp,
        signature_header=x_slate_signature,
        raw_body=raw_body,
    )

    # After verification : safe to parse + dispatch.
    import json
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid JSON body: {exc.msg}",
        ) from exc
    try:
        envelope = WebhookEnvelope.model_validate(parsed)
    except Exception as exc:  # pydantic ValidationError
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid event envelope: {exc}",
        ) from exc

    sent_at_dt: datetime | None = None
    if envelope.sent_at is not None:
        try:
            sent_at_dt = datetime.fromtimestamp(envelope.sent_at, tz=UTC)
        except (OverflowError, OSError, ValueError):
            sent_at_dt = None

    try:
        await dispatcher.dispatch(
            slug=x_slate_slug,
            event_type=envelope.type,
            payload=envelope.payload,
            sent_at=sent_at_dt,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "webhook.dispatch_failed",
            slug=x_slate_slug, type=envelope.type, error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"handler failed: {exc}",
        ) from exc
