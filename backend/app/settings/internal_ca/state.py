"""On-disk state for the internal CA.

Layout under `/app/data/ca/` (docker volume → host `./data/ca/`):

  config.json           # CAConfig persisted JSON
  rootCA.pem            # Root CA cert (public, distributable)
  rootCA.key            # Root CA private key (NEVER leaves the controller)
  issued/
    <serial>.json       # IssuedCertSummary (subject, sans, dates, revoked_at)
    <serial>.crt        # leaf cert PEM
    <serial>.key        # leaf private key PEM
  slate/
    current.json        # { serial_hex, pushed_at } — pointer to active Slate cert
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.settings.internal_ca.config import CAConfig, DEFAULT_RGS_2_STAR, IssuedCertSummary


def _to_iso_utc_z(dt: datetime) -> str:
    """Render a datetime as `YYYY-MM-DDTHH:MM:SSZ` (clean ISO 8601 UTC).

    Older revisions of this module concatenated `+ "Z"` onto a
    tz-aware `.isoformat()` output and ended up with `+00:00Z` —
    invalid ISO 8601 that `Date()` in JS rejects with "Invalid Date".
    This helper is the single source of truth so the bug doesn't come
    back.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _heal_legacy_iso(raw: str | None) -> str | None:
    """Rewrite the bad `+00:00Z` suffix to plain `Z` for old records.

    Existing on-disk JSON files written before the writer fix carry the
    buggy format ; we transparently clean them at read time so the UI
    doesn't show "Invalid Date" forever. Returns the cleaned string ;
    callers can persist back if they want to permanently normalise.
    """
    if not raw:
        return raw
    if raw.endswith("+00:00Z"):
        return raw[: -len("+00:00Z")] + "Z"
    return raw

# Root location ; matches the docker volume bind in compose
CA_DIR = Path("/app/data/ca")

CONFIG_PATH = CA_DIR / "config.json"
ROOT_CERT_PATH = CA_DIR / "rootCA.pem"
ROOT_KEY_PATH = CA_DIR / "rootCA.key"
ISSUED_DIR = CA_DIR / "issued"
SLATE_DIR = CA_DIR / "slate"
SLATE_CURRENT = SLATE_DIR / "current.json"


# ---------- CA config persistence ----------

def load_ca_config() -> CAConfig:
    """Return the persisted config, or the RGS 2★ default if absent.

    Raw JSON parse failures (corruption) fall back to defaults rather
    than crashing — the operator can re-save from the UI to overwrite.
    """
    if not CONFIG_PATH.exists():
        return DEFAULT_RGS_2_STAR.model_copy(deep=True)
    try:
        raw = json.loads(CONFIG_PATH.read_text())
        return CAConfig.model_validate(raw)
    except (json.JSONDecodeError, ValueError):
        return DEFAULT_RGS_2_STAR.model_copy(deep=True)


def save_ca_config(cfg: CAConfig) -> None:
    CA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(cfg.model_dump_json(indent=2))


# ---------- Root CA materials ----------

def get_root_cert_pem() -> bytes:
    if not ROOT_CERT_PATH.exists():
        raise FileNotFoundError(str(ROOT_CERT_PATH))
    return ROOT_CERT_PATH.read_bytes()


def write_root_materials(cert_pem: bytes, key_pem: bytes) -> None:
    CA_DIR.mkdir(parents=True, exist_ok=True)
    ROOT_CERT_PATH.write_bytes(cert_pem)
    ROOT_KEY_PATH.write_bytes(key_pem)
    # Tighten private-key perms (best effort — container is root, fine).
    try:
        ROOT_KEY_PATH.chmod(0o600)
    except OSError:
        pass


def read_root_materials() -> tuple[bytes, bytes]:
    return ROOT_CERT_PATH.read_bytes(), ROOT_KEY_PATH.read_bytes()


# ---------- Issued cert log ----------

def write_issued_cert(
    *,
    serial_hex: str,
    common_name: str,
    sans: list[str],
    not_after: datetime,
    cert_pem: bytes,
    key_pem: bytes,
) -> IssuedCertSummary:
    ISSUED_DIR.mkdir(parents=True, exist_ok=True)
    summary = IssuedCertSummary(
        serial_hex=serial_hex,
        common_name=common_name,
        sans=sans,
        issued_at=_to_iso_utc_z(datetime.now(UTC)),
        not_after=_to_iso_utc_z(not_after),
    )
    (ISSUED_DIR / f"{serial_hex}.json").write_text(summary.model_dump_json(indent=2))
    (ISSUED_DIR / f"{serial_hex}.crt").write_bytes(cert_pem)
    (ISSUED_DIR / f"{serial_hex}.key").write_bytes(key_pem)
    try:
        (ISSUED_DIR / f"{serial_hex}.key").chmod(0o600)
    except OSError:
        pass
    return summary


def list_issued_certs() -> list[IssuedCertSummary]:
    """Most-recent first. Self-heals legacy `+00:00Z` ISO strings."""
    if not ISSUED_DIR.exists():
        return []
    out: list[IssuedCertSummary] = []
    slate_serial = _slate_current_serial()
    for p in ISSUED_DIR.glob("*.json"):
        try:
            raw = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        raw["issued_at"] = _heal_legacy_iso(raw.get("issued_at"))
        raw["not_after"] = _heal_legacy_iso(raw.get("not_after"))
        raw["revoked_at"] = _heal_legacy_iso(raw.get("revoked_at"))
        # Persist the cleaned form back so the heal is one-shot per entry.
        try:
            p.write_text(json.dumps(raw, indent=2))
        except OSError:
            pass
        try:
            summary = IssuedCertSummary.model_validate(raw)
        except ValueError:
            continue
        if summary.serial_hex == slate_serial:
            summary.is_slate_cert = True
        out.append(summary)
    out.sort(key=lambda s: s.issued_at, reverse=True)
    return out


def get_issued_materials(serial_hex: str) -> tuple[bytes, bytes]:
    """Read cert + key for a previously-issued serial."""
    cert_path = ISSUED_DIR / f"{serial_hex}.crt"
    key_path = ISSUED_DIR / f"{serial_hex}.key"
    if not cert_path.exists() or not key_path.exists():
        raise FileNotFoundError(f"no materials for serial {serial_hex}")
    return cert_path.read_bytes(), key_path.read_bytes()


def mark_revoked(serial_hex: str) -> IssuedCertSummary:
    meta_path = ISSUED_DIR / f"{serial_hex}.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"no issuance record for serial {serial_hex}")
    summary = IssuedCertSummary.model_validate_json(meta_path.read_text())
    if summary.revoked_at is None:
        summary.revoked_at = _to_iso_utc_z(datetime.now(UTC))
        meta_path.write_text(summary.model_dump_json(indent=2))
    return summary


# ---------- Slate cert pointer ----------

def set_slate_current(serial_hex: str, pushed_at: datetime) -> None:
    SLATE_DIR.mkdir(parents=True, exist_ok=True)
    SLATE_CURRENT.write_text(
        json.dumps({"serial_hex": serial_hex, "pushed_at": _to_iso_utc_z(pushed_at)})
    )


def get_slate_cert_metadata() -> dict | None:
    if not SLATE_CURRENT.exists():
        return None
    try:
        return json.loads(SLATE_CURRENT.read_text())
    except json.JSONDecodeError:
        return None


def _slate_current_serial() -> str | None:
    meta = get_slate_cert_metadata()
    return meta.get("serial_hex") if meta else None
