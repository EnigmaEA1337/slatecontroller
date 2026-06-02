"""X.509 PKI primitives — Root CA generation + leaf cert issuance.

Implemented on the cryptography library (already pinned in the project
for SSH key + Fernet operations). All algorithm + key-size choices come
from `CAConfig`. The CA's private key NEVER leaves disk in plaintext
outside this process — readable bytes returned by helper functions are
PEM-encoded and only ever flow to the operator's browser (Root CA cert
only, never the private key) or onto the target device (leaf cert + its
own dedicated private key).

What this module does NOT do (intentional simplifications) :
  - No HSM integration ; the CA private key lives on disk. For RGS 3★
    deployments where the key should be hardware-bound, the operator
    would mount a PKCS#11 device and we'd swap this layer ; out of
    scope for the current threat model (homelab + portable mission).
  - No OCSP responder ; revocation goes through a regenerated CRL on
    demand (cf. `build_crl`).
  - No name constraints on the Root ; we're not chaining sub-CAs.
"""

from __future__ import annotations

import secrets
import structlog
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

from app.settings.internal_ca.config import (
    CAConfig,
    IssuedCertSummary,
    KeyAlgorithm,
    SignatureHash,
    SubjectDN,
)
from app.settings.internal_ca.state import (
    ROOT_CERT_PATH,
    ROOT_KEY_PATH,
    read_root_materials,
    write_issued_cert,
    write_root_materials,
)

logger = structlog.get_logger(__name__)


# ---------- predicates ----------

def is_initialized() -> bool:
    return ROOT_CERT_PATH.exists() and ROOT_KEY_PATH.exists()


# ---------- algorithm dispatch ----------

PrivateKey = ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey


def _generate_key(algo: KeyAlgorithm) -> PrivateKey:
    """Generate a fresh private key matching the requested algorithm."""
    match algo:
        case KeyAlgorithm.ECDSA_P256:
            return ec.generate_private_key(ec.SECP256R1())
        case KeyAlgorithm.ECDSA_P384:
            return ec.generate_private_key(ec.SECP384R1())
        case KeyAlgorithm.ECDSA_P521:
            return ec.generate_private_key(ec.SECP521R1())
        case KeyAlgorithm.RSA_2048:
            return rsa.generate_private_key(public_exponent=65537, key_size=2048)
        case KeyAlgorithm.RSA_3072:
            return rsa.generate_private_key(public_exponent=65537, key_size=3072)
        case KeyAlgorithm.RSA_4096:
            return rsa.generate_private_key(public_exponent=65537, key_size=4096)


def _hash_for(sig: SignatureHash) -> hashes.HashAlgorithm:
    match sig:
        case SignatureHash.SHA256:
            return hashes.SHA256()
        case SignatureHash.SHA384:
            return hashes.SHA384()
        case SignatureHash.SHA512:
            return hashes.SHA512()


def _build_name(dn: SubjectDN) -> x509.Name:
    """Map our SubjectDN dataclass to a cryptography.x509.Name."""
    attrs: list[x509.NameAttribute] = [
        x509.NameAttribute(NameOID.COMMON_NAME, dn.common_name),
    ]
    if dn.organization:
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, dn.organization))
    if dn.organizational_unit:
        attrs.append(
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, dn.organizational_unit)
        )
    if dn.country:
        attrs.append(x509.NameAttribute(NameOID.COUNTRY_NAME, dn.country))
    if dn.state:
        attrs.append(x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, dn.state))
    if dn.locality:
        attrs.append(x509.NameAttribute(NameOID.LOCALITY_NAME, dn.locality))
    return x509.Name(attrs)


def _random_serial() -> int:
    """128-bit random serial.

    RFC 5280 §4.1.2.2 requires non-negative, up to 20 octets. ANSSI RGS
    B1 §2.2.1 recommends at least 64 bits of entropy ; 128 bits is the
    industry standard for "unguessable" serials (Let's Encrypt, AWS PCA,
    SmallStep all use 128-bit).
    """
    return int.from_bytes(secrets.token_bytes(16), "big") | (1 << 127)


# ---------- Root CA ----------

def init_root_ca(config: CAConfig) -> bytes:
    """Generate a fresh Root CA cert + key, write to disk.

    Returns the Root CA cert PEM (the public part). Idempotency : if
    materials already exist, this REUSES them — caller has to delete
    the existing files first if they want a regeneration. This protects
    against accidental UI clicks wiping a Root CA users have already
    distributed.
    """
    if is_initialized():
        cert_pem, _ = read_root_materials()
        return cert_pem

    key = _generate_key(config.key_algorithm)
    now = datetime.now(UTC).replace(microsecond=0)
    serial = _random_serial()

    subject = issuer = _build_name(config.subject)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(serial)
        .not_valid_before(now - timedelta(minutes=5))  # clock-skew tolerance
        .not_valid_after(now + config.validity.ca_delta)
        # Basic Constraints CA=true, path_length=0 → no sub-CA chaining
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=0),
            critical=True,
        )
        # Key Usage : cert+CRL signing only
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        # Subject + Authority Key Identifier (self-signed CA → AKI = SKI)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
    )
    aki_source = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    builder = builder.add_extension(
        x509.AuthorityKeyIdentifier(
            key_identifier=aki_source.digest,
            authority_cert_issuer=None,
            authority_cert_serial_number=None,
        ),
        critical=False,
    )

    cert = builder.sign(key, _hash_for(config.signature_hash))

    cert_pem = cert.public_bytes(Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    write_root_materials(cert_pem, key_pem)
    logger.info(
        "internal_ca.root.created",
        subject=config.subject.common_name,
        algo=config.key_algorithm.value,
        hash=config.signature_hash.value,
        serial_hex=f"{serial:032x}",
        days=config.validity.ca_days,
    )
    return cert_pem


def regenerate_root_ca(config: CAConfig) -> bytes:
    """Wipe + rebuild the Root CA. Destructive — caller confirms in UI.

    Issued certs are kept on disk but become useless (chain broken).
    Operator should manually mark them revoked + reissue.
    """
    if ROOT_CERT_PATH.exists():
        ROOT_CERT_PATH.unlink()
    if ROOT_KEY_PATH.exists():
        ROOT_KEY_PATH.unlink()
    logger.warning("internal_ca.root.wiped")
    return init_root_ca(config)


# ---------- Leaf cert issuance ----------

def _load_ca_key() -> PrivateKey:
    _, key_pem = read_root_materials()
    key = serialization.load_pem_private_key(key_pem, password=None)
    if not isinstance(key, (ec.EllipticCurvePrivateKey, rsa.RSAPrivateKey)):
        raise TypeError(f"unexpected CA key type: {type(key)}")
    return key


def _load_ca_cert() -> x509.Certificate:
    cert_pem, _ = read_root_materials()
    return x509.load_pem_x509_certificate(cert_pem)


def _build_sans(sans: list[str]) -> x509.SubjectAlternativeName:
    """Mix hostnames (DNSName) and IP literals (IPAddress)."""
    entries: list[x509.GeneralName] = []
    for raw in sans:
        try:
            ip = ip_address(raw)
        except ValueError:
            entries.append(x509.DNSName(raw))
        else:
            entries.append(x509.IPAddress(ip))
    if not entries:
        raise ValueError("at least one SAN required")
    return x509.SubjectAlternativeName(entries)


def issue_cert(
    *,
    config: CAConfig,
    common_name: str,
    sans: list[str],
) -> tuple[IssuedCertSummary, bytes, bytes]:
    """Sign a fresh leaf cert.

    The leaf gets its own key pair (the CA never sees it). Returns
    `(summary, cert_pem, key_pem)`. Caller is responsible for transit
    (pushing to the Slate, downloading via UI, etc.) ; we write all
    three to disk under issued/<serial>/.
    """
    if not is_initialized():
        raise RuntimeError("Root CA not initialized")

    ca_cert = _load_ca_cert()
    ca_key = _load_ca_key()
    leaf_key = _generate_key(config.key_algorithm)
    serial = _random_serial()
    now = datetime.now(UTC).replace(microsecond=0)
    not_after = now + config.validity.leaf_delta

    # Build leaf subject : start from template if any, override CN.
    base = config.leaf_subject_template or SubjectDN(common_name=common_name)
    leaf_subject_dn = base.model_copy(update={"common_name": common_name})

    builder = (
        x509.CertificateBuilder()
        .subject_name(_build_name(leaf_subject_dn))
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(serial)
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            critical=False,
        )
        .add_extension(_build_sans(sans), critical=False)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()),
            critical=False,
        )
    )
    cert = builder.sign(ca_key, _hash_for(config.signature_hash))

    cert_pem = cert.public_bytes(Encoding.PEM)
    key_pem = leaf_key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    serial_hex = f"{serial:032x}"

    summary = write_issued_cert(
        serial_hex=serial_hex,
        common_name=common_name,
        sans=sans,
        not_after=not_after,
        cert_pem=cert_pem,
        key_pem=key_pem,
    )
    logger.info(
        "internal_ca.leaf.issued",
        cn=common_name,
        serial_hex=serial_hex,
        sans=sans,
        days=config.validity.leaf_days,
    )
    return summary, cert_pem, key_pem


def revoke_cert(serial_hex: str) -> IssuedCertSummary:
    """Mark a cert revoked in the local log.

    No CRL is published by default — caller can call `build_crl()` to
    generate one on demand. For an internal CA serving a handful of
    devices, the operator typically rolls forward with a fresh cert +
    re-pushes ; a CRL becomes useful if/when external relying parties
    start consuming our certs.
    """
    from app.settings.internal_ca.state import mark_revoked
    return mark_revoked(serial_hex)


# ---------- Cert introspection (for the UI's "all params" view) ----------

def parse_cert_details(pem: bytes) -> dict:
    """Return a comprehensive view of an X.509 cert.

    Used by the UI's "Détails du CA" panel — exposes every field a
    reasonable operator would want to verify before distributing the
    Root CA or before installing a leaf on a third party. Output shape
    is purely a flat dict of strings so the React side can render it
    with zero extra logic.
    """
    cert = x509.load_pem_x509_certificate(pem)
    pub = cert.public_key()

    # Subject + Issuer
    def _name_to_str(name: x509.Name) -> str:
        return ", ".join(
            f"{attr.oid._name}={attr.value}" for attr in name
        )

    # Public key descriptor
    if isinstance(pub, ec.EllipticCurvePublicKey):
        pk_desc = f"ECDSA · courbe {pub.curve.name} · {pub.curve.key_size} bits"
    elif isinstance(pub, rsa.RSAPublicKey):
        pk_desc = f"RSA · {pub.key_size} bits · e={pub.public_numbers().e}"
    else:
        pk_desc = type(pub).__name__

    # Signature algorithm — cryptography exposes the OID; we render it
    # to the human-friendly form (e.g. "ecdsa-with-SHA384").
    sig_alg = cert.signature_algorithm_oid._name or cert.signature_algorithm_oid.dotted_string
    sig_hash = (
        cert.signature_hash_algorithm.name.upper()
        if cert.signature_hash_algorithm
        else "(unknown)"
    )

    # Fingerprints — what `openssl x509 -fingerprint` prints, with
    # colons every 2 hex chars for readability.
    def _colon(b: bytes) -> str:
        h = b.hex().upper()
        return ":".join(h[i : i + 2] for i in range(0, len(h), 2))

    fp_sha256 = _colon(cert.fingerprint(hashes.SHA256()))
    fp_sha1 = _colon(cert.fingerprint(hashes.SHA1()))

    # Serial number — same hex layout as elsewhere in the project (no
    # colons) so it cross-references the issuance log keys cleanly.
    serial_hex = f"{cert.serial_number:032x}"

    # Extensions — collect the ones we set ourselves at issuance time,
    # plus anything else we might find. We render to short strings to
    # keep the UI flat.
    extensions: list[dict] = []
    for ext in cert.extensions:
        item: dict[str, str | bool] = {
            "oid": ext.oid.dotted_string,
            "name": ext.oid._name or ext.oid.dotted_string,
            "critical": ext.critical,
            "value": "",
        }
        v = ext.value
        if isinstance(v, x509.BasicConstraints):
            item["value"] = (
                f"CA={v.ca}"
                + (f", pathLen={v.path_length}" if v.path_length is not None else "")
            )
        elif isinstance(v, x509.KeyUsage):
            flags = []
            for attr in (
                "digital_signature", "content_commitment", "key_encipherment",
                "data_encipherment", "key_agreement", "key_cert_sign",
                "crl_sign",
            ):
                if getattr(v, attr):
                    flags.append(attr)
            item["value"] = ", ".join(flags) or "(none)"
        elif isinstance(v, x509.ExtendedKeyUsage):
            item["value"] = ", ".join(
                oid._name or oid.dotted_string for oid in v
            )
        elif isinstance(v, x509.SubjectKeyIdentifier):
            item["value"] = _colon(v.digest)
        elif isinstance(v, x509.AuthorityKeyIdentifier):
            ki = v.key_identifier
            item["value"] = _colon(ki) if ki else "(no key id)"
        elif isinstance(v, x509.SubjectAlternativeName):
            entries = []
            for entry in v:
                if isinstance(entry, x509.DNSName):
                    entries.append(f"DNS:{entry.value}")
                elif isinstance(entry, x509.IPAddress):
                    entries.append(f"IP:{entry.value}")
                else:
                    entries.append(str(entry))
            item["value"] = ", ".join(entries)
        else:
            item["value"] = str(v)[:200]
        extensions.append(item)

    return {
        "version": f"v{cert.version.value + 1}",
        "serial_hex": serial_hex,
        "serial_colon_hex": _colon(cert.serial_number.to_bytes((cert.serial_number.bit_length() + 7) // 8, "big")),
        "subject": _name_to_str(cert.subject),
        "issuer": _name_to_str(cert.issuer),
        "is_self_signed": cert.subject == cert.issuer,
        "not_before": cert.not_valid_before_utc.isoformat(),
        "not_after": cert.not_valid_after_utc.isoformat(),
        "public_key": pk_desc,
        "signature_algorithm": sig_alg,
        "signature_hash": sig_hash,
        "fingerprint_sha256": fp_sha256,
        "fingerprint_sha1": fp_sha1,
        "extensions": extensions,
        "pem": pem.decode(),
    }


def parse_root_ca_details() -> dict:
    """Convenience wrapper : parse the current Root CA on disk."""
    cert_pem, _ = read_root_materials()
    return parse_cert_details(cert_pem)
