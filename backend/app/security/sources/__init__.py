"""Vulnerability sources. Each module exposes a `VulnSource` subclass."""

from app.security.sources.base import VulnSource
from app.security.sources.osv import OsvSource

__all__ = ["VulnSource", "OsvSource"]
