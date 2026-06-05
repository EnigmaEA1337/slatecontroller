"""Event type registry + dispatcher for incoming Slate webhooks.

Lives on ``app.state.webhook_dispatcher``. Handlers register at app
boot ; the inbound route calls :meth:`dispatch` with the validated
event payload.

A handler receives ``(slug, payload, sent_at)`` and is async. It may
raise — the route catches and returns 500 ; the Slate-side push helper
retries with exponential backoff so transient handler failures recover.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable

import structlog

logger = structlog.get_logger(__name__)


HandlerFn = Callable[[str, dict[str, Any], datetime | None], Awaitable[None]]


class WebhookDispatcher:
    def __init__(self) -> None:
        self._handlers: dict[str, HandlerFn] = {}

    def register(self, event_type: str, handler: HandlerFn) -> None:
        """Idempotent — re-registering replaces, useful at hot-reload."""
        self._handlers[event_type] = handler
        logger.info("webhook_dispatcher.handler_registered", type=event_type)

    async def dispatch(
        self,
        *,
        slug: str,
        event_type: str,
        payload: dict[str, Any],
        sent_at: datetime | None,
    ) -> None:
        handler = self._handlers.get(event_type)
        if handler is None:
            logger.warning(
                "webhook_dispatcher.unknown_type",
                slug=slug, type=event_type,
            )
            return
        await handler(slug, payload, sent_at)
