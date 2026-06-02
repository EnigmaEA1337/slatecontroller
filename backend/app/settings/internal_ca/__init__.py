"""Internal CA — full-fledged, RGS-compliant X.509 PKI for Slate Controller.

Module surface re-exports the four public concerns the route layer
needs : configuration, CA lifecycle, certificate lifecycle, and Slate
push. Implementation is split across submodules to keep each file under
~300 lines and focused on one concern.

Submodules :
  - `config`   : Pydantic models (Subject DN, KeyAlgorithm, profile presets)
  - `state`    : on-disk layout + JSON persistence of CA config + issued certs
  - `pki`      : cryptography-lib wrappers (generate Root CA, sign leaf certs)
  - `push`     : push the deployed Slate cert via SSH (uhttpd reload)
"""

from app.settings.internal_ca.config import (
    CAConfig,
    DEFAULT_RGS_2_STAR,
    IssuedCertSummary,
    KeyAlgorithm,
    RGS_PROFILES,
    SignatureHash,
    SubjectDN,
)
from app.settings.internal_ca.pki import (
    init_root_ca,
    issue_cert,
    is_initialized,
    revoke_cert,
)
from app.settings.internal_ca.push import push_slate_cert
from app.settings.internal_ca.state import (
    get_root_cert_pem,
    list_issued_certs,
    load_ca_config,
    save_ca_config,
    get_slate_cert_metadata,
)

__all__ = [
    "CAConfig",
    "DEFAULT_RGS_2_STAR",
    "IssuedCertSummary",
    "KeyAlgorithm",
    "RGS_PROFILES",
    "SignatureHash",
    "SubjectDN",
    "get_root_cert_pem",
    "get_slate_cert_metadata",
    "init_root_ca",
    "is_initialized",
    "issue_cert",
    "list_issued_certs",
    "load_ca_config",
    "push_slate_cert",
    "revoke_cert",
    "save_ca_config",
]
