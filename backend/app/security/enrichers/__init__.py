"""Post-match enrichers: take Findings and add extra context per CVE."""

from app.security.enrichers.cve2capec import Cve2CapecEnricher

__all__ = ["Cve2CapecEnricher"]
