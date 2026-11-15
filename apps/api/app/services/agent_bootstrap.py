"""What the app serves to install an agent in one line.

* `GET /agent/install.sh`: app/agent_install/install.sh with the app URL baked in.
* `GET /agent/agent.tar.gz`: the agent sources of *this* app version
  (apps/agent), so app and agent always match.
"""

from __future__ import annotations

import io
import logging
import os
import re
import ssl
import tarfile
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


def install_command(app_url: str, code: str) -> str:
    return f'bash -c "$(curl -fskSL {app_url}/agent/install.sh)" -- --code {code}'


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


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    parts = set(Path(info.name).parts)
    if parts & _TAR_EXCLUDED_PARTS or info.name.endswith((".pyc", ".egg-info")) or ".egg-info" in info.name:
        return None
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = 0  # reproducible archive
    return info


@lru_cache(maxsize=4)
def build_agent_archive(source_dir: str) -> bytes:
    """tar.gz with pyproject.toml, README.md and src/ under `pbo-agent/`."""
    root = Path(source_dir)
    if not (root / "pyproject.toml").is_file() or not (root / "src" / "agent").is_dir():
        raise FileNotFoundError(f"Agent sources not found in {root}.")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name in ("pyproject.toml", "README.md"):
            if (root / name).is_file():
                archive.add(root / name, arcname=f"pbo-agent/{name}", filter=_tar_filter)
        archive.add(root / "src", arcname="pbo-agent/src", filter=_tar_filter)
    return buffer.getvalue()
