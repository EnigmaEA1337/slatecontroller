"""Abstract vulnerability source.

Each source ingests a list of installed packages and emits Findings. Sources
are independent: they may share zero CVE coverage and the orchestrator dedups
on `(cve_id, package_name)` across sources.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.security.models import Finding, Package, SourceId


class VulnSource(ABC):
    """Common interface for a CVE/advisory feed."""

    id: SourceId

    @abstractmethod
    async def scan(self, packages: Sequence[Package]) -> list[Finding]:
        """Return all findings this source can attribute to the given packages.

        Implementations must not raise for an individual package failure —
        log and skip instead. Network failures that prevent any scan should
        raise `RuntimeError` so the orchestrator can mark scan_status=error.
        """
