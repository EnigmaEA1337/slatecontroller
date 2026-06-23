"""On-demand build + sideload of openfortivpn for the Slate.

Two operations exposed to the routes layer :

  - :func:`build_binary` — synchronously spawns the ``slate-forti-builder``
    Docker container (declared in docker-compose under profile ``forti``),
    which builds a statically-linked aarch64-musl binary and drops it,
    along with a small ``build.json`` manifest, in the
    ``forti-artifacts`` named volume.
  - :func:`sideload_binary` — reads the freshly built binary from the
    backend-side mount ``/forti-artifacts/openfortivpn``, SCPs it to
    ``/usr/sbin/openfortivpn`` on the Slate, ``chmod 755`` it, then
    re-runs the manager preflight to confirm.

Together they turn the "build openfortivpn for ARM somehow" rabbit-hole
into a single-click flow next to the ``preflight`` banner in the UI.

Docker socket dependency : the backend container in
``docker-compose{.dev}.yml`` mounts ``/var/run/docker.sock`` so the
``docker`` SDK works without docker-in-docker tricks. The image
``slate-forti-builder:latest`` must already be built — either through
``docker compose --profile forti build`` once, or by hitting the
``/build`` endpoint which auto-builds the image lazily if missing.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path

import structlog

from app.exceptions import SlateError
from app.slate.ssh import SlateSSH, SlateSSHError


logger = structlog.get_logger(__name__)


ARTIFACT_DIR = Path("/forti-artifacts")
ARTIFACT_PATH = ARTIFACT_DIR / "openfortivpn"
MANIFEST_PATH = ARTIFACT_DIR / "build.json"

BUILDER_IMAGE = "slate-forti-builder:latest"
BUILDER_CONTEXT = "/app/../builders/forti"  # only used by lazy build hint

REMOTE_PATH = "/usr/sbin/openfortivpn"

# Build can take 4-12 minutes depending on host CPU (openssl 3.0 + the
# openfortivpn autoconf + make stages). 20 minutes is the operator-facing
# deadline ; failures bubble back with the container logs attached.
BUILD_TIMEOUT_SECONDS = 1_200


class FortinetBuilderError(SlateError):
    pass


@dataclass
class BuildArtifact:
    available: bool
    path: str = ""
    size_bytes: int = 0
    sha256: str = ""
    version: str = ""
    git_ref: str = ""
    built_at_seconds: int = 0  # unix epoch ; 0 when unknown


def get_artifact_status() -> BuildArtifact:
    """Inspect the shared volume for a previously-built binary. Cheap —
    one ``stat`` + one JSON read. Returns ``available=False`` when the
    binary hasn't been built yet."""
    if not ARTIFACT_PATH.is_file():
        return BuildArtifact(available=False)
    try:
        st = ARTIFACT_PATH.stat()
    except OSError:
        return BuildArtifact(available=False)
    manifest = {}
    if MANIFEST_PATH.is_file():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            manifest = {}
    return BuildArtifact(
        available=True,
        path=str(ARTIFACT_PATH),
        size_bytes=st.st_size,
        sha256=manifest.get("sha256", ""),
        version=manifest.get("version", ""),
        git_ref=manifest.get("git_ref", ""),
        built_at_seconds=int(st.st_mtime),
    )


async def build_binary(openfortivpn_ref: str = "v1.21.0") -> dict:
    """Run the builder container synchronously. Returns a dict with
    ``ok``, ``logs`` (tail of stdout/stderr), and the post-build
    :class:`BuildArtifact` shape.

    Streams to a thread because the docker SDK is sync ; awaiting from
    the asyncio loop without offloading would block uvicorn for minutes.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        _run_builder_sync,
        openfortivpn_ref,
    )


def _resolve_artifacts_volume_name(client) -> str | None:
    """Find the project-prefixed name of the ``forti-artifacts`` volume.

    Three lookup strategies, tried in order :
      1. Read our own container's Mounts via Docker API. Self-identification
         goes through the boot id in /proc/self/cgroup (works on cgroup v1
         + v2) or falls back to the hostname (works when compose didn't
         set a custom one).
      2. Scan Docker's named volumes for any name ending in
         ``forti-artifacts``. Robust to compose project renames.
      3. Return None — the caller surfaces an instructive error.
    """
    import re

    self_id = _read_self_container_id()
    if self_id:
        try:
            me = client.containers.get(self_id)
            for mount in me.attrs.get("Mounts", []):
                if mount.get("Destination") == "/forti-artifacts":
                    return mount.get("Name")
        except Exception:  # noqa: BLE001
            pass

    # Fallback : enumerate volumes and pick the one whose name ends with
    # "forti-artifacts" (compose project prefixes are random per-clone,
    # but the suffix is stable across hosts).
    try:
        for v in client.volumes.list():
            name = v.name
            if name and re.search(r"forti-artifacts$", name):
                return name
    except Exception:  # noqa: BLE001
        pass
    return None


def _read_self_container_id() -> str | None:
    """Best-effort extraction of our own container id without relying on
    hostname (which compose may override). cgroup v1 paths look like
    ``/docker/<id>`` ; cgroup v2 like ``0::/system.slice/docker-<id>.scope``.
    Returns the 64-char hex id, ``None`` on miss."""
    import re

    cgroup_paths = ("/proc/self/cgroup", "/proc/1/cgroup")
    pattern = re.compile(r"[0-9a-f]{64}")
    for path in cgroup_paths:
        try:
            with open(path) as f:
                content = f.read()
        except OSError:
            continue
        m = pattern.search(content)
        if m:
            return m.group(0)
    return None


def _run_builder_sync(openfortivpn_ref: str) -> dict:
    """Blocking section, intended for ``run_in_executor``.

    Lazy-pulls / lazy-builds the builder image if absent (first call), then
    runs ``slate-forti-builder`` with the artifact volume mounted, and
    reports back the artifact + logs.
    """
    try:
        import docker
        from docker.errors import APIError, ImageNotFound
    except ImportError as exc:  # pragma: no cover — pyproject pins it
        raise FortinetBuilderError(
            "docker SDK not installed in the backend image — rebuild it",
        ) from exc

    try:
        client = docker.from_env()
    except Exception as exc:  # noqa: BLE001
        raise FortinetBuilderError(
            f"can't reach the docker daemon (is /var/run/docker.sock "
            f"mounted in the backend container?): {exc}",
        ) from exc

    # Ensure the builder image exists. We DON'T auto-build it from this
    # process — instead the operator runs `docker compose --profile forti
    # build forti-builder` once (~3-5 min) before clicking Build in the
    # UI. The reason : building images via the docker SDK requires
    # streaming a context tarball, which works but adds 200+ LOC of
    # error-handling for marginal UX gain when the operator can just run
    # the compose command once. We surface a clear instruction instead.
    try:
        client.images.get(BUILDER_IMAGE)
    except ImageNotFound as exc:
        raise FortinetBuilderError(
            f"{BUILDER_IMAGE} image not found. Run once on the host : "
            f"`docker compose --profile forti build forti-builder` "
            f"(takes 3-5 min — it cross-compiles openssl statically).",
        ) from exc
    except APIError as exc:  # pragma: no cover
        raise FortinetBuilderError(f"docker API error: {exc}") from exc

    # Resolve the artifact volume name dynamically rather than hardcoding
    # ``<project>_forti-artifacts`` — the compose project name varies by
    # working directory (spaces stripped, lowercased, etc.) and we'd be
    # fragile if someone clones the repo to a different path. We inspect
    # OUR OWN container : we already mount the volume at /forti-artifacts
    # (ro), so the canonical name is right there in the Mounts array.
    volume_name = _resolve_artifacts_volume_name(client)
    if not volume_name:
        raise FortinetBuilderError(
            "couldn't locate the forti-artifacts named volume — is the "
            "backend container started from docker-compose with the "
            "updated volume mount?",
        )

    logger.info(
        "forti.builder.run.start",
        ref=openfortivpn_ref, volume=volume_name,
    )
    try:
        container = client.containers.run(
            image=BUILDER_IMAGE,
            environment={"OPENFORTIVPN_REF": openfortivpn_ref},
            volumes={volume_name: {"bind": "/out", "mode": "rw"}},
            detach=True,
            remove=False,
            name=f"slate-forti-build-{os.getpid()}",
        )
    except APIError as exc:
        raise FortinetBuilderError(f"docker run failed: {exc}") from exc

    try:
        exit_status = container.wait(timeout=BUILD_TIMEOUT_SECONDS)
        rc = exit_status.get("StatusCode", -1)
        # Keep only the tail of the log to avoid blasting the UI with
        # 50 KB of make output ; full log lives in the container until
        # it's removed below, plus we log everything to stderr.
        full_logs = container.logs().decode("utf-8", errors="replace")
        tail = "\n".join(full_logs.splitlines()[-200:])
        logger.info(
            "forti.builder.run.done", rc=rc, log_chars=len(full_logs),
        )
    except Exception as exc:  # noqa: BLE001
        try:
            container.kill()
        except Exception:  # noqa: BLE001
            pass
        raise FortinetBuilderError(f"build container wait failed: {exc}") from exc
    finally:
        try:
            container.remove()
        except Exception as exc:  # noqa: BLE001
            logger.warning("forti.builder.cleanup_failed", error=str(exc))

    if rc != 0:
        return {
            "ok": False,
            "rc": rc,
            "logs": tail,
            "artifact": get_artifact_status().__dict__,
        }
    return {
        "ok": True,
        "rc": 0,
        "logs": tail,
        "artifact": get_artifact_status().__dict__,
    }


async def sideload_binary(ssh: SlateSSH) -> dict:
    """SCP the locally-built binary to the Slate at /usr/sbin/openfortivpn
    and chmod 755. Returns the post-action preflight so the UI can
    immediately re-render the banner."""
    art = get_artifact_status()
    if not art.available:
        raise FortinetBuilderError(
            "no built binary available — click Build first to produce one",
        )
    try:
        data = ARTIFACT_PATH.read_bytes()
    except OSError as exc:
        raise FortinetBuilderError(
            f"can't read {ARTIFACT_PATH}: {exc}",
        ) from exc

    try:
        # Push to a staging path so a half-written copy never lives at
        # the canonical location ; rename is atomic on the same fs.
        await ssh.put_bytes(data, "/tmp/openfortivpn.new", mode=0o755)
        await ssh.run(
            "mv /tmp/openfortivpn.new "
            f"{REMOTE_PATH} && chmod 755 {REMOTE_PATH}",
            timeout=10,
        )
    except SlateSSHError as exc:
        raise FortinetBuilderError(f"SCP push failed: {exc}") from exc

    logger.info(
        "forti.builder.sideloaded",
        size=len(data), version=art.version, sha=art.sha256[:12],
    )
    return {
        "ok": True,
        "remote_path": REMOTE_PATH,
        "size_bytes": len(data),
        "version": art.version,
        "sha256": art.sha256,
    }
