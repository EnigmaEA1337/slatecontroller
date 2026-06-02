"""CA configuration models + RGS profile presets.

Profiles map the ANSSI Référentiel Général de Sécurité (RGS) levels to
concrete X.509 parameters :

  - RGS 1★ (low)   : minimum legal baseline. RSA 2048, SHA-256.
  - RGS 2★ (std)   : standard for SIIV / OIV / pro internal PKIs.
                     ECDSA P-384, SHA-384, ~10y CA / ~3y leaf, 128-bit
                     serials. Recommended default.
  - RGS 3★ (high)  : "Diffusion Restreinte"-grade. RSA 3072+ or P-521,
                     SHA-512, shorter validity (<2y leaf).

Sources :
  - `https://cyber.gouv.fr/publications/referentiel-general-de-securite`
  - RGS B1 cryptographique : algorithmes & paramètres minimaux
  - RFC 5280 : X.509 v3 cert + CRL profile
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class KeyAlgorithm(StrEnum):
    """Asymmetric key family for the CA + leaf certs.

    ECDSA preferred for performance + smaller cert size (lower bandwidth
    on the tailnet, faster handshake on the Slate's modest CPU).
    """

    ECDSA_P256 = "ecdsa-p256"   # RGS 1★+ ok
    ECDSA_P384 = "ecdsa-p384"   # RGS 2★ recommended
    ECDSA_P521 = "ecdsa-p521"   # RGS 3★ ok
    RSA_2048 = "rsa-2048"        # legacy, RGS 1★ minimum
    RSA_3072 = "rsa-3072"        # RGS 2★ via RSA
    RSA_4096 = "rsa-4096"        # RGS 3★ via RSA


class SignatureHash(StrEnum):
    SHA256 = "sha256"
    SHA384 = "sha384"
    SHA512 = "sha512"


class SubjectDN(BaseModel):
    """X.509 Distinguished Name. RFC 5280 §4.1.2.4."""

    common_name: str = Field(..., min_length=1, max_length=64, description="CN")
    organization: str | None = Field(default=None, max_length=64, description="O")
    organizational_unit: str | None = Field(
        default=None, max_length=64, description="OU"
    )
    country: str | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description="ISO 3166-1 alpha-2 (FR, US, etc.)",
    )
    state: str | None = Field(default=None, max_length=128, description="ST")
    locality: str | None = Field(default=None, max_length=128, description="L")

    @field_validator("country")
    @classmethod
    def _country_upper(cls, v: str | None) -> str | None:
        return v.upper() if v else v


class CAValidity(BaseModel):
    """Validity windows applied at issuance.

    Caller passes days (UI is days-based — human-friendly). The CA cert
    itself uses `ca_days` ; leaf certs (Slate, future devices) get
    `leaf_days`. Browser policy currently caps leaf certs at 825 days —
    our default of 825 hits the legal max without forcing the operator
    to re-issue more often than necessary.
    """

    ca_days: int = Field(default=3650, ge=365, le=7305, description="CA cert lifetime (1y-20y)")
    leaf_days: int = Field(default=825, ge=1, le=825, description="leaf cert lifetime (max 825d per CAB Forum)")

    @property
    def ca_delta(self) -> timedelta:
        return timedelta(days=self.ca_days)

    @property
    def leaf_delta(self) -> timedelta:
        return timedelta(days=self.leaf_days)


class CAConfig(BaseModel):
    """Persisted CA configuration. Editable end-to-end from the UI.

    Stored as JSON in `data/ca/config.json`. Changes here only affect
    FUTURE issuance — existing Root CA + issued certs are immutable
    once created (to revoke + reissue, the operator regenerates).
    """

    profile_label: str = Field(default="RGS 2★", description="Display label for the active profile")
    subject: SubjectDN
    key_algorithm: KeyAlgorithm = KeyAlgorithm.ECDSA_P384
    signature_hash: SignatureHash = SignatureHash.SHA384
    validity: CAValidity = Field(default_factory=CAValidity)
    leaf_subject_template: SubjectDN | None = Field(
        default=None,
        description=(
            "Template applied to leaf certs (Slate + future devices). "
            "Common Name is overridden per-cert by the first SAN."
        ),
    )
    # Phase 2 hook — toggle stored but no effect until PQ implementation
    # ships (cf. Phase 2 todo). UI displays a clear "experimental, not
    # functional with current browsers" warning when set to True.
    pq_hybrid_experimental: bool = Field(default=False)


class IssuedCertSummary(BaseModel):
    """Public-safe view of one entry in the issuance log."""

    serial_hex: str
    common_name: str
    sans: list[str]
    issued_at: str  # ISO datetime
    not_after: str
    revoked_at: str | None = None
    is_slate_cert: bool = False  # True when this is the currently-deployed Slate cert


# ---------- RGS profile presets ----------

DEFAULT_RGS_2_STAR = CAConfig(
    profile_label="RGS 2★ (recommandé)",
    subject=SubjectDN(
        common_name="Trust Controller",
        organization="Slate Controller PKI",
        organizational_unit="Internal Root CA",
        country="FR",
    ),
    key_algorithm=KeyAlgorithm.ECDSA_P384,
    signature_hash=SignatureHash.SHA384,
    validity=CAValidity(ca_days=3650, leaf_days=825),
    leaf_subject_template=SubjectDN(
        common_name="(set per cert)",
        organization="Slate Controller PKI",
        organizational_unit="Slate Device",
        country="FR",
    ),
)

_RGS_1_STAR = CAConfig(
    profile_label="RGS 1★ (minimum réglementaire)",
    subject=SubjectDN(
        common_name="Trust Controller",
        organization="Slate Controller PKI",
        organizational_unit="Internal Root CA",
        country="FR",
    ),
    key_algorithm=KeyAlgorithm.RSA_2048,
    signature_hash=SignatureHash.SHA256,
    validity=CAValidity(ca_days=1825, leaf_days=825),
    leaf_subject_template=DEFAULT_RGS_2_STAR.leaf_subject_template,
)

_RGS_3_STAR = CAConfig(
    profile_label="RGS 3★ (Diffusion Restreinte)",
    subject=SubjectDN(
        common_name="Trust Controller",
        organization="Slate Controller PKI",
        organizational_unit="Internal Root CA",
        country="FR",
    ),
    key_algorithm=KeyAlgorithm.ECDSA_P521,
    signature_hash=SignatureHash.SHA512,
    validity=CAValidity(ca_days=2555, leaf_days=730),
    leaf_subject_template=DEFAULT_RGS_2_STAR.leaf_subject_template,
)


RGS_PROFILES: dict[str, CAConfig] = {
    "rgs-1-star": _RGS_1_STAR,
    "rgs-2-star": DEFAULT_RGS_2_STAR,
    "rgs-3-star": _RGS_3_STAR,
}
