"""Domain-specific exceptions."""

from __future__ import annotations


class SlateError(Exception):
    """Base class for all Slate-related errors."""


class SlateUnreachableError(SlateError):
    """Raised when the Slate cannot be contacted (network, auth, timeout)."""


class SlateRpcError(SlateError):
    """Raised when a JSON-RPC call returns an error payload."""

    def __init__(self, message: str, *, group: str, method: str) -> None:
        super().__init__(message)
        self.group = group
        self.method = method
