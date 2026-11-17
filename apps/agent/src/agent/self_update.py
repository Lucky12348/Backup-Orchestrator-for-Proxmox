"""Self-update of an agent installed by the one-line installer.

The app announces the build it ships (`build_id`) and the SHA-256 of its
agent archive (`POST /self-update`). The agent then:

1. downloads `<app>/agent/agent.tar.gz` ONLY from its own app URL
   (AGENT_API_BASE_URL), over the pinned TLS connection (AGENT_API_FINGERPRINT);
   nothing is taken from the request but the expected hashes;
2. checks the archive's SHA-256 against the announced one, extracts it safely
   to `releases/<build_id>` and checks its BUILD_ID;
3. installs new Python dependencies only if the pyproject's list changed;
4. hands over to a transient systemd unit (`pbo-agent-update`, outside the
   agent's own cgroup) that writes the units and the udev rule, switches
   `current` to the new release atomically, restarts the agent and checks it
   answers with the new build. Otherwise everything is put back (symlink,
   units, udev rule) and the previous agent restarted: automatic rollback.

The env file (tokens, certificates) is never touched: no new pairing, no
revocation. The result is kept in `<state>/self-update.json`
(`GET /self-update/status`).
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import time
import tomllib
import urllib.parse
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from agent import deploy

logger = logging.getLogger("agent.self_update")

MAX_ARCHIVE_BYTES = 30 * 1024 * 1024
HEALTH_TIMEOUT_SECONDS = 60.0
UPDATE_UNIT = "pbo-agent-update"
ARCHIVE_PREFIX = "pbo-agent/"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_BUILD = re.compile(r"^[0-9a-f]{12,64}$")

Runner = Callable[..., Any]


class SelfUpdateError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- state -------------------------------------------------------------------------------------


def state_file(layout: deploy.Layout) -> Path:
    return layout.state_dir / "self-update.json"


def read_state(layout: deploy.Layout = deploy.Layout()) -> dict[str, Any]:
    try:
        data = json.loads(state_file(layout).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(layout: deploy.Layout, **values: Any) -> dict[str, Any]:
    data = {**read_state(layout), **values, "updated_at": _now()}
    layout.state_dir.mkdir(parents=True, exist_ok=True)
    tmp = state_file(layout).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, state_file(layout))
    return data


# --- builds ------------------------------------------------------------------------------------


def build_of(release: Path | None) -> str | None:
    if release is None:
        return None
    try:
        value = (release / "BUILD_ID").read_text(encoding="ascii").strip()
    except OSError:
        return None
    return value if _BUILD.match(value) else None


def current_release(layout: deploy.Layout) -> Path | None:
    """The release `current` points to (None: legacy layout without it)."""
    if not layout.current.is_symlink():
        return None
    return layout.current.resolve()


def app_origin(api_base_url: str) -> str:
    parts = urllib.parse.urlsplit(api_base_url)
    if parts.scheme != "https" or not parts.netloc:
        raise SelfUpdateError("not_registered", "The agent only updates from an https app it is paired with.")
    return f"https://{parts.netloc}"


# --- download + extract ------------------------------------------------------------------------


def download_archive(settings: Any, expected_sha256: str, *, client_factory: Callable[..., Any] | None = None) -> bytes:
    """From the app this agent is paired with, pinned: never from a URL given
    in the request."""
    from agent.main import app_tls_verify

    if not settings.api_fingerprint:
        raise SelfUpdateError("not_registered", "Only an agent installed with a pairing code (pinned app certificate) updates itself.")
    url = f"{app_origin(settings.api_base_url)}/agent/agent.tar.gz"
    if client_factory is None:
        import httpx

        client_factory = httpx.Client
    digest = hashlib.sha256()
    buffer = io.BytesIO()
    with client_factory(timeout=120.0, verify=app_tls_verify(settings)) as client:
        with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise SelfUpdateError("download_failed", f"GET {url}: HTTP {response.status_code}.")
            for chunk in response.iter_bytes():
                buffer.write(chunk)
                digest.update(chunk)
                if buffer.tell() > MAX_ARCHIVE_BYTES:
                    raise SelfUpdateError("download_failed", "The agent archive is unexpectedly large.")
    if not hmac.compare_digest(digest.hexdigest(), expected_sha256.lower()):
        raise SelfUpdateError(
            "sha256_mismatch",
            f"Downloaded archive SHA-256 {digest.hexdigest()} does not match the announced {expected_sha256}: nothing installed.",
        )
    return buffer.getvalue()


def _safe_member(member: tarfile.TarInfo) -> str | None:
    name = member.name
    if not name.startswith(ARCHIVE_PREFIX):
        raise SelfUpdateError("bad_archive", f"Unexpected entry in the agent archive: {name!r}.")
    relative = PurePosixPath(name[len(ARCHIVE_PREFIX) :])
    if relative.is_absolute() or ".." in relative.parts:
        raise SelfUpdateError("bad_archive", f"Unsafe path in the agent archive: {name!r}.")
    if not (member.isfile() or member.isdir()):
        raise SelfUpdateError("bad_archive", f"Links and special files are refused: {name!r}.")
    return str(relative) if relative.parts else None


def extract_release(archive: bytes, layout: deploy.Layout, build_id: str) -> Path:
    """Extracts to releases/<build_id> (through a temporary directory)."""
    target = layout.releases / build_id
    tmp = layout.releases / f".{build_id}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            for member in tar.getmembers():
                relative = _safe_member(member)
                if relative is None:
                    continue
                destination = tmp / relative
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                assert source is not None
                with source, open(destination, "wb") as handle:
                    shutil.copyfileobj(source, handle)
                os.chmod(destination, 0o644)
        found = build_of(tmp)
        if found != build_id:
            raise SelfUpdateError("bad_archive", f"The archive is build {found}, the app announced {build_id}.")
        if target.exists() and target != current_release(layout):
            shutil.rmtree(target)
        if not target.exists():
            os.replace(tmp, target)
        return target
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def dependencies(release: Path | None) -> list[str] | None:
    if release is None:
        return None
    try:
        data = tomllib.loads((release / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return sorted(data.get("project", {}).get("dependencies") or [])


# --- stage (in the agent's HTTP request) -------------------------------------------------------


def start_update(
    settings: Any,
    build_id: str,
    sha256: str,
    *,
    layout: deploy.Layout = deploy.Layout(),
    run: Runner = subprocess.run,
    downloader: Callable[[Any, str], bytes] = download_archive,
) -> dict[str, Any]:
    if not _BUILD.match(build_id or "") or not _HEX64.match((sha256 or "").lower()):
        raise SelfUpdateError("bad_request", "build_id / sha256 are malformed.")
    current = current_release(layout)
    from_build = build_of(current) or build_of(Path(settings.repo_path))
    if from_build == build_id:
        return {"ok": True, "status": "up_to_date", "build_id": build_id, "message": "Already on this build."}
    state = read_state(layout)
    if state.get("status") in {"staged", "applying"} and time.time() - float(state.get("started_ts") or 0) < 600:
        raise SelfUpdateError("update_in_progress", "An update of this agent is already in progress.")
    archive = downloader(settings, sha256)
    release = extract_release(archive, layout, build_id)
    new_deps = dependencies(release)
    if new_deps and new_deps != dependencies(current or Path(settings.repo_path)):
        logger.info("Installing the new agent dependencies: %s", ", ".join(new_deps))
        done = run([str(layout.python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", *new_deps],
                   capture_output=True, text=True, check=False, timeout=600)
        if done.returncode != 0:
            raise SelfUpdateError("dependencies_failed", f"pip install failed: {(done.stderr or done.stdout or '').strip()[-500:]}")
    write_state(
        layout,
        status="staged",
        from_build=from_build,
        to_build=build_id,
        previous_target=str(current) if current else None,
        started_at=_now(),
        started_ts=time.time(),
        message=None,
    )
    command = [
        "systemd-run", "--unit", UPDATE_UNIT, "--collect", "--quiet",
        f"--property=EnvironmentFile={layout.env_file}",
        f"--setenv=PYTHONPATH={release}/src",
        str(layout.python), "-m", "agent.self_update", "apply",
    ]
    launched = run(command, capture_output=True, text=True, check=False, timeout=30)
    if launched.returncode != 0:
        write_state(layout, status="failed", message=f"systemd-run failed: {(launched.stderr or '').strip()}")
        raise SelfUpdateError("launch_failed", f"Cannot start the update unit: {(launched.stderr or '').strip()}")
    return {"ok": True, "status": "started", "from_build": from_build, "build_id": build_id, "message": "Update started."}


# --- apply (transient unit, outside the agent's cgroup) ----------------------------------------


def own_fingerprint(cert_file: str) -> str:
    from agent.main import normalize_fingerprint

    der = ssl.PEM_cert_to_DER_cert(Path(cert_file).read_text(encoding="ascii"))
    return normalize_fingerprint(hashlib.sha256(der).hexdigest())


def health_check(settings: Any, expected_build: str | None, *, timeout: float = HEALTH_TIMEOUT_SECONDS) -> bool:
    """The restarted agent answers /version (pinned on its own certificate)
    with the expected build."""
    import httpx

    from agent.main import PinnedAppSSLContext

    verify: Any = PinnedAppSSLContext.create(own_fingerprint(settings.tls_cert_file)) if settings.tls_cert_file else False
    scheme = "https" if settings.tls_cert_file else "http"
    host = settings.server_host if settings.server_host not in {"", "0.0.0.0"} else "127.0.0.1"
    url = f"{scheme}://{host}:{settings.server_port}/version"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=5.0, verify=verify) as client:
                response = client.get(url, headers={"X-Agent-Token": settings.server_token})
            if response.status_code == 200 and (expected_build is None or response.json().get("build_id") == expected_build):
                return True
        except Exception:  # noqa: BLE001 — not up yet
            pass
        time.sleep(2.0)
    return False


def _switch(layout: deploy.Layout, target: Path) -> None:
    tmp = layout.install_dir / ".current.tmp"
    tmp.unlink(missing_ok=True)
    os.symlink(target, tmp)
    os.replace(tmp, layout.current)


def _reload_and_restart(role: str, run: Runner) -> None:
    run(["systemctl", "daemon-reload"], check=False, timeout=60)
    if role == "host":
        run(["udevadm", "control", "--reload-rules"], check=False, timeout=30)
    run(["systemctl", "restart", "pbo-agent.service"], check=False, timeout=120)


def apply_update(
    settings: Any,
    *,
    layout: deploy.Layout = deploy.Layout(),
    run: Runner = subprocess.run,
    check: Callable[[Any, str | None], bool] = health_check,
) -> dict[str, Any]:
    state = read_state(layout)
    to_build = str(state.get("to_build") or "")
    release = layout.releases / to_build
    if state.get("status") != "staged" or not to_build or build_of(release) != to_build:
        return write_state(layout, status="failed", message="No staged update to apply.")
    previous_target = state.get("previous_target")
    role = settings.role or "host"
    write_state(layout, status="applying")
    previous_files = deploy.write_files(deploy.deploy_files(role, layout))
    _switch(layout, release)
    _reload_and_restart(role, run)
    if check(settings, to_build):
        _cleanup_releases(layout, keep={release, Path(previous_target) if previous_target else None})
        run(["systemctl", "--no-block", "start", "pbo-agent-report.service"], check=False, timeout=30)
        return write_state(layout, status="success", finished_at=_now(), message=f"Updated to build {to_build}.")
    # Rollback: the previous release, units and udev rule, then the previous agent.
    logger.error("The agent did not answer after the update to %s: rolling back.", to_build)
    if previous_target:
        _switch(layout, Path(previous_target))
    else:
        layout.current.unlink(missing_ok=True)
    deploy.restore_files(previous_files)
    _reload_and_restart(role, run)
    back = check(settings, state.get("from_build"))
    return write_state(
        layout,
        status="rolled_back" if back else "failed",
        finished_at=_now(),
        message=(
            f"The agent did not answer after the update to {to_build}: previous version restored."
            if back
            else f"The agent did not answer after the update to {to_build}, nor after the rollback."
        ),
    )


def _cleanup_releases(layout: deploy.Layout, keep: set[Path | None]) -> None:
    kept = {path.resolve() for path in keep if path is not None}
    for path in layout.releases.iterdir() if layout.releases.is_dir() else []:
        if path.is_dir() and not path.name.startswith(".") and path.resolve() not in kept:
            shutil.rmtree(path, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args[:1] != ["apply"]:
        print("usage: python -m agent.self_update apply", file=sys.stderr)
        return 2
    from agent.main import AgentSettings

    result = apply_update(AgentSettings())
    print(json.dumps(result))
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
