"""Phase 5g: agent self-update (agent/self_update.py) and its units (agent/deploy.py)."""

import hashlib
import io
import os
import subprocess
import tarfile
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import __version__, deploy
from agent import self_update as su

BUILD_OLD = "aaaaaaaaaaaaaaaa"
BUILD_NEW = "bbbbbbbbbbbbbbbb"


def _symlinks_supported(tmp_path: Path) -> bool:
    try:
        os.symlink(tmp_path, tmp_path / "probe")
    except (OSError, NotImplementedError):
        return False
    (tmp_path / "probe").unlink()
    return True


@pytest.fixture
def layout(tmp_path):
    if not _symlinks_supported(tmp_path):
        pytest.skip("symlinks not available on this platform")
    lay = deploy.Layout(
        install_dir=tmp_path / "opt",
        env_file=tmp_path / "etc" / "agent.env",
        systemd_dir=tmp_path / "systemd",
        udev_rule_file=tmp_path / "udev" / "90-pbo-agent.rules",
        state_dir=tmp_path / "state",
    )
    old = lay.releases / BUILD_OLD
    (old / "src" / "agent").mkdir(parents=True)
    (old / "BUILD_ID").write_text(BUILD_OLD + "\n")
    (old / "pyproject.toml").write_text('[project]\nname="a"\nversion="0.1"\ndependencies=["httpx"]\n')
    os.symlink(old, lay.current)
    deploy.write_files({lay.systemd_dir / "pbo-agent.service": "OLD UNIT\n"})
    return lay


def archive(build=BUILD_NEW, extra=None, dependencies='["httpx"]'):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        files = {
            "pbo-agent/BUILD_ID": f"{build}\n",
            "pbo-agent/pyproject.toml": f'[project]\nname="a"\nversion="0.2"\ndependencies={dependencies}\n',
            "pbo-agent/src/agent/__init__.py": "",
            **(extra or {}),
        }
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def settings(**overrides):
    values = dict(
        api_base_url="https://10.0.0.5/api/v1",
        api_fingerprint="AA:BB",
        repo_path="/nonexistent",
        role="host",
        server_host="10.0.0.1",
        server_port=8090,
        server_token="t",
        tls_cert_file="",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class Runner:
    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        return subprocess.CompletedProcess(command, self.returncode, "", "boom" if self.returncode else "")


# --- download ------------------------------------------------------------------------------------


class FakeStream:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self):
        yield self.data


class FakeClient:
    seen = []

    def __init__(self, data, **kwargs):
        self.data, self.kwargs = data, kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def stream(self, method, url):
        FakeClient.seen.append((method, url, self.kwargs.get("verify")))
        return FakeStream(self.data)


def test_download_only_from_the_paired_app_pinned_and_checked():
    data = archive()
    FakeClient.seen = []
    result = su.download_archive(settings(), hashlib.sha256(data).hexdigest(), client_factory=lambda **kw: FakeClient(data, **kw))
    assert result == data
    method, url, verify = FakeClient.seen[0]
    assert (method, url) == ("GET", "https://10.0.0.5/agent/agent.tar.gz")
    assert verify is not True and getattr(verify, "pinned", None) == "AA:BB"  # pinned context, not CA verification


def test_a_wrong_sha256_installs_nothing():
    data = archive()
    with pytest.raises(su.SelfUpdateError) as error:
        su.download_archive(settings(), "0" * 64, client_factory=lambda **kw: FakeClient(data, **kw))
    assert error.value.code == "sha256_mismatch"


def test_an_unpaired_agent_does_not_update_itself():
    with pytest.raises(su.SelfUpdateError) as error:
        su.download_archive(settings(api_fingerprint=""), "0" * 64)
    assert error.value.code == "not_registered"


# --- extraction ----------------------------------------------------------------------------------


def test_unsafe_archives_are_refused(layout):
    for extra in ({"pbo-agent/../evil": "x"}, {"other/file": "x"}):
        with pytest.raises(su.SelfUpdateError) as error:
            su.extract_release(archive(extra=extra), layout, BUILD_NEW)
        assert error.value.code == "bad_archive"
    with pytest.raises(su.SelfUpdateError):
        su.extract_release(archive(build="cccccccccccccccc"), layout, BUILD_NEW)  # BUILD_ID differs
    assert not (layout.releases / BUILD_NEW).exists()


# --- stage + apply -------------------------------------------------------------------------------


def _start(layout, run, data=None):
    data = data or archive()
    return su.start_update(
        settings(), BUILD_NEW, hashlib.sha256(data).hexdigest(), layout=layout, run=run, downloader=lambda s, sha: data
    )


def test_start_stages_the_release_and_hands_over_to_a_transient_unit(layout):
    run = Runner()
    result = _start(layout, run)
    assert result["status"] == "started" and result["from_build"] == BUILD_OLD
    assert su.build_of(layout.releases / BUILD_NEW) == BUILD_NEW
    assert su.current_release(layout) == (layout.releases / BUILD_OLD).resolve()  # not switched yet
    (command,) = run.calls  # same dependencies: no pip
    assert command[:3] == ["systemd-run", "--unit", "pbo-agent-update"]
    assert f"--setenv=PYTHONPATH={layout.releases / BUILD_NEW}/src" in command
    assert command[-3:] == ["-m", "agent.self_update", "apply"]
    assert su.read_state(layout)["status"] == "staged"


def test_new_dependencies_are_installed_before_the_switch(layout):
    run = Runner()
    _start(layout, run, archive(dependencies='["httpx", "newdep"]'))
    assert run.calls[0][1:4] == ["-m", "pip", "install"] and "newdep" in run.calls[0]


def test_already_on_the_build(layout):
    run = Runner()
    data = archive(build=BUILD_OLD)
    result = su.start_update(settings(), BUILD_OLD, hashlib.sha256(data).hexdigest(), layout=layout, run=run, downloader=lambda s, sha: data)
    assert result["status"] == "up_to_date" and run.calls == []


def test_apply_switches_and_keeps_the_previous_release(layout):
    run = Runner()
    _start(layout, run)
    result = su.apply_update(settings(), layout=layout, run=run, check=lambda s, build: build == BUILD_NEW)
    assert result["status"] == "success"
    assert su.current_release(layout) == (layout.releases / BUILD_NEW).resolve()
    assert "PYTHONPATH" in (layout.systemd_dir / "pbo-agent.service").read_text()
    assert layout.udev_rule_file.exists()
    assert ["systemctl", "restart", "pbo-agent.service"] in run.calls
    assert (layout.releases / BUILD_OLD).exists()  # kept for a manual rollback
    assert not layout.env_file.exists()  # tokens untouched (never written)


def test_apply_rolls_back_when_the_agent_does_not_answer(layout):
    run = Runner()
    _start(layout, run)
    checks = []

    def check(_settings, build):
        checks.append(build)
        return build == BUILD_OLD  # the new build never answers

    result = su.apply_update(settings(), layout=layout, run=run, check=check)
    assert result["status"] == "rolled_back"
    assert checks == [BUILD_NEW, BUILD_OLD]
    assert su.current_release(layout) == (layout.releases / BUILD_OLD).resolve()
    assert (layout.systemd_dir / "pbo-agent.service").read_text() == "OLD UNIT\n"
    assert not layout.udev_rule_file.exists()  # absent before: removed again
    assert [call for call in run.calls if call == ["systemctl", "restart", "pbo-agent.service"]].__len__() == 2


def test_a_second_update_is_refused_while_one_runs(layout):
    _start(layout, Runner())
    with pytest.raises(su.SelfUpdateError) as error:
        _start(layout, Runner())
    assert error.value.code == "update_in_progress"


# --- units, versions -----------------------------------------------------------------------------


def test_units_run_the_active_release():
    files = deploy.deploy_files("host")
    service = files[Path("/etc/systemd/system/pbo-agent.service")]
    assert "Environment=PYTHONPATH=/opt/pbo-agent/current/src" in service
    assert "ExecStart=/opt/pbo-agent/venv/bin/python -m agent.main serve" in service
    assert Path("/etc/udev/rules.d/90-pbo-agent.rules") in files
    assert Path("/etc/udev/rules.d/90-pbo-agent.rules") not in deploy.deploy_files("pbs")


def test_version_matches_pyproject_and_self_update_is_announced():
    from agent.main import AGENT_CAPABILITIES

    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__
    assert "self-update" in AGENT_CAPABILITIES
