"""What the app serves to install an agent in one line.

* `GET /agent/install.sh`: app/agent_install/install.sh with the app URL baked in.
* `GET /agent/agent.tar.gz`: the agent sources of *this* app version
  (apps/agent), so app and agent always match.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import logging
import os
import re
import ssl
import tarfile
import tomllib
from functools import lru_cache
from pathlib import Path

from app.core.tls import certificate_fingerprint

logger = logging.getLogger(__name__)

INSTALL_SCRIPT_TEMPLATE = Path(__file__).resolve().parents[1] / "agent_install" / "install.sh"
APP_URL_PLACEHOLDER = "__PBO_APP_URL__"
API_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_AGENT_SOURCE = API_ROOT.parent / "agent"
# Scheme + host[:port] only: the value is pasted into a shell script.
_APP_URL_RE = re.compile(r"^https://(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?$")
_TAR_EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", "tests", ".venv"}


class AppUrlError(ValueError):
    pass


def app_url_from_base(base_url: str) -> str:
    """'https://10.0.0.5/' → 'https://10.0.0.5' (refuses anything that is not
    a plain https origin, the Host header being client-controlled)."""
    url = base_url.strip().rstrip("/")
    if url.endswith(":443"):
        url = url[: -len(":443")]
    if not _APP_URL_RE.match(url):
        raise AppUrlError("The app must be reached over https://<host>[:port] to install an agent.")
    return url


INSTALL_SCRIPT_NAME = "pbo-agent-install.sh"


def install_commands(app_url: str, code: str) -> list[str]:
    """Download, then run: two lines on purpose. A `bash -c "$(curl …)"`
    one-liner placed in the clipboard is flagged as "ClickFix" by content
    blockers (uBlock Origin), and a downloaded file can be read before running."""
    return [
        f"curl -fskSL -o {INSTALL_SCRIPT_NAME} {app_url}/agent/install.sh",
        f"bash {INSTALL_SCRIPT_NAME} --code {code}",
    ]


def app_certificate_fingerprint(cert_file: str) -> str | None:
    if not cert_file:
        return None
    try:
        pem = Path(cert_file).read_text(encoding="ascii")
        return certificate_fingerprint(ssl.PEM_cert_to_DER_cert(pem))
    except (OSError, ValueError) as exc:
        logger.warning("Cannot read the app TLS certificate %s: %s", cert_file, exc)
        return None


def render_install_script(app_url: str) -> str:
    if not _APP_URL_RE.match(app_url):
        raise AppUrlError("Invalid app URL.")
    return INSTALL_SCRIPT_TEMPLATE.read_text(encoding="utf-8").replace(APP_URL_PLACEHOLDER, app_url)


def agent_source_dir() -> Path:
    configured = os.getenv("PBO_AGENT_SOURCE_DIR", "")
    return Path(configured) if configured else _DEFAULT_AGENT_SOURCE


def _included(root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    if set(relative.parts) & _TAR_EXCLUDED_PARTS or ".egg-info" in str(relative):
        return False
    return not path.name.endswith(".pyc")


def _source_files(root: Path) -> list[tuple[str, Path]]:
    """(name in the archive, file) in a stable order: pyproject, README, src/…"""
    files = [(name, root / name) for name in ("pyproject.toml", "README.md") if (root / name).is_file()]
    files += sorted(
        (f"src/{path.relative_to(root / 'src').as_posix()}", path)
        for path in (root / "src").rglob("*")
        if path.is_file() and _included(root, path)
    )
    return files


def _check_sources(root: Path) -> None:
    if not (root / "pyproject.toml").is_file() or not (root / "src" / "agent").is_dir():
        raise FileNotFoundError(f"Agent sources not found in {root}.")


def _build_id(files: list[tuple[str, Path]]) -> str:
    digest = hashlib.sha256()
    for name, path in files:
        digest.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:16]


def _add(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(f"pbo-agent/{name}")
    info.size = len(data)
    info.mode = 0o644
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = 0  # reproducible archive
    archive.addfile(info, io.BytesIO(data))


@lru_cache(maxsize=4)
def build_agent_archive(source_dir: str) -> bytes:
    """tar.gz with pyproject.toml, README.md, src/ and BUILD_ID under
    `pbo-agent/`. Reproducible: same sources = same bytes = same SHA-256."""
    root = Path(source_dir)
    _check_sources(root)
    files = _source_files(root)
    buffer = io.BytesIO()
    # mtime=0 in the gzip header too, so the archive hash only depends on the sources.
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for name, path in files:
                _add(archive, name, path.read_bytes())
            _add(archive, "BUILD_ID", f"{_build_id(files)}\n".encode())
    return buffer.getvalue()


@lru_cache(maxsize=4)
def agent_package(source_dir: str) -> dict[str, str | int]:
    """What the app ships: version (pyproject), build (hash of the sources,
    also in the archive's BUILD_ID), SHA-256 and size of the archive."""
    root = Path(source_dir)
    _check_sources(root)
    archive = build_agent_archive(source_dir)
    try:
        version = str(tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])
    except (OSError, KeyError, ValueError):
        version = "unknown"
    return {
        "version": version,
        "build_id": _build_id(_source_files(root)),
        "sha256": hashlib.sha256(archive).hexdigest(),
        "size": len(archive),
    }


def shipped_agent() -> dict[str, str | int] | None:
    try:
        return agent_package(str(agent_source_dir()))
    except FileNotFoundError:
        logger.warning("Agent sources not found: agents cannot be updated.")
        return None
