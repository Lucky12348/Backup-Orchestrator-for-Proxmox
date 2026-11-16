import json
import logging
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import pve_access, server
from agent.main import AgentSettings
from agent.pve_access import (
    PBO_PRIVILEGES,
    ProvisionError,
    guest_addresses,
    parse_guest_ipv4,
    parse_pveversion,
    provision_pve_access,
)

SECRET = "11111111-2222-3333-4444-555555555555"


# Static self-signed certificates (the agent test environment has no `cryptography`).
def _pem(*lines: str) -> str:
    return "\n".join(["-----BEGIN CERTIFICATE-----", *lines, "-----END CERTIFICATE-----", ""])


PEM_A = _pem(
    "MIIBCDCBr6ADAgECAgEBMAoGCCqGSM49BAMCMA4xDDAKBgNVBAMMA3B2ZTAeFw0y",
    "NjAxMDEwMDAwMDBaFw0zNTEyMzAwMDAwMDBaMA4xDDAKBgNVBAMMA3B2ZTBZMBMG",
    "ByqGSM49AgEGCCqGSM49AwEHA0IABNW4o9MYTh1P99EiUUsUtjjjWi2BFOUV2Za5",
    "zn0yk1RwMNSiuylTnyPmK0BiK/vMRrNdsuHfQWZ/lGUjacmxnyswCgYIKoZIzj0E",
    "AwIDSAAwRQIhAINo7rCo36jed0LjvWJQ9vC5hQ2+WU25JTvXML5K/V5aAiAQnncv",
    "SP8Wzy37L5NlGHDfQAkT2+zUm2Wmzg+mVXs7rg==",
)
PEM_B = _pem(
    "MIIBDjCBtaADAgECAgEBMAoGCCqGSM49BAMCMBExDzANBgNVBAMMBmN1c3RvbTAe",
    "Fw0yNjAxMDEwMDAwMDBaFw0zNTEyMzAwMDAwMDBaMBExDzANBgNVBAMMBmN1c3Rv",
    "bTBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABFOnEjHV7iMqctMOYOf+4UtsER88",
    "z4qapavg2A4lZ/o5l6IPEnhE0VqY+HQ/Uujyj4fH/xaBnjRMJDvrHiRlSgIwCgYI",
    "KoZIzj0EAwIDSAAwRQIgZlRttNIkSIPw8mgSaEUywAspYDnVGpQ1zteNxlMCPU8C",
    "IQCA/reiPsqarfC+VwWIixnMQx5rFpyrkKpjgz6To8GVFQ==",
)


class FakePveum:
    """Stateful stand-in for pveum / pveversion / ip."""

    def __init__(self, *, user=False, role=False, token=False):
        self.user, self.role, self.token = user, role, token
        self.calls: list[list[str]] = []
        self.role_privs = ""
        self.acls: list[tuple[str, str]] = []
        self.fail_on: str | None = None

    def __call__(self, command, timeout):
        self.calls.append(command)
        joined = " ".join(command)
        out = ""
        if self.fail_on and self.fail_on in joined:
            return subprocess.CompletedProcess(command, 255, "", "permission denied")
        if command[:3] == ["pveum", "user", "list"]:
            out = json.dumps([{"userid": "root@pam"}] + ([{"userid": "pbo@pve"}] if self.user else []))
        elif command[:3] == ["pveum", "user", "add"]:
            self.user = True
        elif command[:3] == ["pveum", "role", "list"]:
            out = json.dumps([{"roleid": "PVEAuditor"}] + ([{"roleid": "PBO"}] if self.role else []))
        elif command[:3] == ["pveum", "role", "add"] or command[:3] == ["pveum", "role", "modify"]:
            self.role = True
            self.role_privs = command[command.index("--privs") + 1]
        elif command[:4] == ["pveum", "user", "token", "list"]:
            out = json.dumps([{"tokenid": "pbo"}] if self.token else [])
        elif command[:4] == ["pveum", "user", "token", "remove"]:
            assert self.token, "removing a token that does not exist"
            self.token = False
        elif command[:4] == ["pveum", "user", "token", "add"]:
            assert not self.token, "token add on an existing token fails on PVE"
            self.token = True
            out = json.dumps({"full-tokenid": "pbo@pve!pbo", "info": {"privsep": "1"}, "value": SECRET})
        elif command[:3] == ["pveum", "acl", "modify"]:
            kind = command[4]
            self.acls.append((kind, command[5]))
        elif command == ["pveversion"]:
            out = "pve-manager/8.2.4/faa83925c9641325 (running kernel: 6.8.8-2-pve)\n"
        elif command[:2] == ["ip", "-4"]:
            out = "5: vmbr0    inet 192.168.1.10/24 brd 192.168.1.255 scope global vmbr0\n"
        return subprocess.CompletedProcess(command, 0, out, "")


class ProvisionTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cert = root / "pve-ssl.pem"
        self.cert.write_text(PEM_A)
        self.nodes = root / "nodes"
        (self.nodes / "pve").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _provision(self, fake, host="192.168.1.10"):
        with patch("agent.pve_access.socket.gethostname", return_value="pve.lan"):
            return provision_pve_access(host, runner=fake, cert_files=(self.cert,), nodes_dir=self.nodes)

    def test_fresh_host_creates_everything(self):
        fake = FakePveum()
        result = self._provision(fake)
        self.assertEqual(result["token_id"], "pbo@pve!pbo")
        self.assertEqual(result["secret"], SECRET)
        self.assertEqual(result["node"], "pve")
        self.assertEqual(result["pve_version"], "8.2.4")
        self.assertEqual(result["api_url"], "https://192.168.1.10:8006")
        self.assertRegex(result["fingerprint"], r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")
        self.assertEqual(fake.role_privs, ",".join(PBO_PRIVILEGES))
        self.assertIn(("--users", "pbo@pve"), fake.acls)
        self.assertIn(("--tokens", "pbo@pve!pbo"), fake.acls)
        add = next(c for c in fake.calls if c[:4] == ["pveum", "user", "token", "add"])
        self.assertIn("--privsep", add)
        self.assertEqual(add[add.index("--privsep") + 1], "1")

    def test_idempotent_rerun_replaces_token_and_updates_role(self):
        fake = FakePveum(user=True, role=True, token=True)
        result = self._provision(fake)
        self.assertEqual(result["secret"], SECRET)
        commands = [" ".join(c[:4]) for c in fake.calls]
        self.assertNotIn("pveum user add pbo@pve", commands)
        self.assertIn("pveum role modify PBO --privs", " ".join(" ".join(c) for c in fake.calls))
        remove = commands.index("pveum user token remove")
        add = commands.index("pveum user token add")
        self.assertLess(remove, add)

    def test_secret_never_logged(self):
        fake = FakePveum(token=True)
        with self.assertLogs("agent.pve_access", level=logging.DEBUG) as logs:
            self._provision(fake)
        self.assertNotIn(SECRET, "\n".join(logs.output))

    def test_failure_is_explicit(self):
        fake = FakePveum()
        fake.fail_on = "role add"
        with self.assertRaises(ProvisionError) as ctx:
            self._provision(fake)
        self.assertIn("Create role failed", str(ctx.exception))
        self.assertIn("permission denied", str(ctx.exception))

    def test_unspecified_listen_address_falls_back_to_vmbr0(self):
        result = self._provision(FakePveum(), host="0.0.0.0")
        self.assertEqual(result["api_url"], "https://192.168.1.10:8006")

    def test_custom_certificate_preferred(self):
        custom = Path(self.tmp.name) / "pveproxy-ssl.pem"
        custom.write_text(PEM_B)
        self.assertNotEqual(
            pve_access.pve_certificate_fingerprint((custom, self.cert)),
            pve_access.pve_certificate_fingerprint((self.cert,)),
        )

    def test_parse_pveversion(self):
        self.assertEqual(parse_pveversion("pve-manager/9.0.3/abc (running kernel: x)"), "9.0.3")
        self.assertEqual(parse_pveversion("garbage"), "")


class GuestAddressTests(TestCase):
    def test_parse_guest_ipv4(self):
        raw = json.dumps(
            [
                {"name": "lo", "ip-addresses": [{"ip-address-type": "ipv4", "ip-address": "127.0.0.1"}]},
                {
                    "name": "ens18",
                    "ip-addresses": [
                        {"ip-address-type": "ipv4", "ip-address": "192.168.1.20"},
                        {"ip-address-type": "ipv6", "ip-address": "fe80::1"},
                        {"ip-address-type": "ipv4", "ip-address": "169.254.3.3"},
                    ],
                },
            ]
        )
        self.assertEqual(parse_guest_ipv4(raw), ["192.168.1.20"])
        self.assertEqual(parse_guest_ipv4("not json"), [])

    def test_only_guests_with_agent_enabled_are_queried(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp)
            (conf / "110.conf").write_text("name: pbs\nagent: 1\n")
            (conf / "111.conf").write_text("name: other\n[snap]\nagent: 1\n")
            calls = []

            def runner(command, timeout):
                calls.append(command)
                out = json.dumps([{"name": "eth0", "ip-addresses": [{"ip-address-type": "ipv4", "ip-address": "10.0.0.5"}]}])
                return subprocess.CompletedProcess(command, 0, out, "")

            result = guest_addresses([110, 111, 112], runner=runner, conf_dir=conf)
        self.assertEqual(result["addresses"], {"110": ["10.0.0.5"], "111": [], "112": []})
        self.assertEqual(len(calls), 1)


class RouteTests(TestCase):
    def tearDown(self):
        server.app.dependency_overrides.clear()

    def test_provision_route_is_host_only(self):
        settings = AgentSettings(server_token="t", role="pbs")
        server.app.dependency_overrides[server.get_settings] = lambda: settings
        with patch("agent.server.provision_pve_access") as provision:
            response = TestClient(server.app).post("/pve/provision-access", headers={"X-Agent-Token": "t"})
        self.assertEqual(response.status_code, 404)
        provision.assert_not_called()

    def test_provision_route_on_host(self):
        settings = AgentSettings(server_token="t", role="host", server_host="192.168.1.10")
        server.app.dependency_overrides[server.get_settings] = lambda: settings
        with patch("agent.server.provision_pve_access", return_value={"ok": True, "secret": "x"}) as provision:
            response = TestClient(server.app).post("/pve/provision-access", headers={"X-Agent-Token": "t"})
        self.assertEqual(response.status_code, 200)
        provision.assert_called_once_with("192.168.1.10")

    def test_provision_failure_payload(self):
        settings = AgentSettings(server_token="t", role="host")
        server.app.dependency_overrides[server.get_settings] = lambda: settings
        with patch("agent.server.provision_pve_access", side_effect=ProvisionError("Create role failed: nope")):
            response = TestClient(server.app).post("/pve/provision-access", headers={"X-Agent-Token": "t"})
        self.assertEqual(response.status_code, 500)
        self.assertIn("Create role failed", response.json()["message"])


class VmAddressTests(TestCase):
    CONFIG = (
        "boot: order=scsi0\n"
        "name: pbs\n"
        "net0: virtio=BC:24:11:AA:BB:CC,bridge=vmbr0,firewall=1\n"
        "net1: e1000=BC:24:11:00:00:01,bridge=vmbr1\n"
        "[snap1]\n"
        "net0: virtio=DE:AD:BE:EF:00:00,bridge=vmbr9\n"
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conf = Path(self.tmp.name)
        (self.conf / "110.conf").write_text("name: pbs\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_vm_nics_reads_current_config_only(self):
        self.assertEqual(
            pve_access.vm_nics(self.CONFIG),
            [
                {"net": "net0", "mac": "bc:24:11:aa:bb:cc", "bridge": "vmbr0"},
                {"net": "net1", "mac": "bc:24:11:00:00:01", "bridge": "vmbr1"},
            ],
        )

    def test_parse_ip_neigh(self):
        output = (
            "192.168.1.20 dev vmbr0 lladdr bc:24:11:aa:bb:cc REACHABLE\n"
            "192.168.1.30 dev vmbr0 lladdr bc:24:11:aa:bb:cc STALE\n"
            "192.168.1.99 dev vmbr0  FAILED\n"
            "fe80::1 dev vmbr0 lladdr 00:11:22:33:44:55 router STALE\n"
        )
        self.assertEqual(pve_access.parse_ip_neigh(output), {"bc:24:11:aa:bb:cc": ["192.168.1.20", "192.168.1.30"]})

    def test_sweep_is_capped_to_a_slash_24(self):
        import ipaddress

        hosts = pve_access.sweep_hosts(ipaddress.IPv4Interface("10.0.4.10/22"))
        self.assertEqual(len(hosts), 253)
        self.assertEqual((hosts[0], hosts[-1]), ("10.0.4.1", "10.0.4.254"))
        self.assertNotIn("10.0.4.10", hosts)
        self.assertEqual(len(pve_access.sweep_hosts(ipaddress.IPv4Interface("192.168.1.10/28"))), 13)

    def _runner(self, *, neigh_before="", neigh_after="", guest=None):
        calls = []
        state = {"pinged": False}

        def runner(command, timeout):
            calls.append(command)
            out = ""
            if command[:2] == ["qm", "config"]:
                out = self.CONFIG
            elif command[:3] == ["qm", "guest", "cmd"]:
                if guest is None:
                    return subprocess.CompletedProcess(command, 255, "", "QEMU guest agent is not running")
                out = guest
            elif command[:3] == ["ip", "-4", "neigh"]:
                out = neigh_after if state["pinged"] else neigh_before
            elif command[:3] == ["ip", "-4", "-o"]:
                out = f"5: {command[-1]}    inet 192.168.1.10/24 brd 192.168.1.255 scope global {command[-1]}\n"
            elif command[0] == "ping":
                state["pinged"] = True
            return subprocess.CompletedProcess(command, 0, out, "")

        return runner, calls

    def test_mac_already_in_the_neighbour_table(self):
        runner, calls = self._runner(neigh_before="192.168.1.20 dev vmbr0 lladdr bc:24:11:aa:bb:cc REACHABLE\n")
        result = pve_access.vm_addresses(110, runner=runner, conf_dir=self.conf)
        self.assertEqual(result["addresses"], [{"ip": "192.168.1.20", "mac": "bc:24:11:aa:bb:cc", "source": "neighbour"}])
        self.assertFalse(any(c[0] == "ping" for c in calls))

    def test_ping_sweep_fills_the_table(self):
        runner, calls = self._runner(neigh_after="192.168.1.20 dev vmbr0 lladdr bc:24:11:aa:bb:cc REACHABLE\n")
        result = pve_access.vm_addresses(110, runner=runner, conf_dir=self.conf)
        self.assertEqual([a["ip"] for a in result["addresses"]], ["192.168.1.20"])
        pings = [c for c in calls if c[0] == "ping"]
        self.assertEqual(len(pings), 2 * 253)  # vmbr0 and vmbr1, one /24 each

    def test_guest_agent_comes_first(self):
        (self.conf / "110.conf").write_text("name: pbs\nagent: 1\n")
        guest = json.dumps([{"name": "eth0", "ip-addresses": [{"ip-address-type": "ipv4", "ip-address": "192.168.1.21"}]}])
        runner, _ = self._runner(neigh_before="192.168.1.20 dev vmbr0 lladdr bc:24:11:aa:bb:cc REACHABLE\n", guest=guest)
        result = pve_access.vm_addresses(110, runner=runner, conf_dir=self.conf)
        self.assertEqual([(a["ip"], a["source"]) for a in result["addresses"]], [("192.168.1.21", "guest-agent"), ("192.168.1.20", "neighbour")])

    def test_nothing_found(self):
        runner, _ = self._runner()
        self.assertEqual(pve_access.vm_addresses(110, runner=runner, conf_dir=self.conf)["addresses"], [])

    def test_route_is_host_only(self):
        settings = AgentSettings(server_token="t", role="pbs")
        server.app.dependency_overrides[server.get_settings] = lambda: settings
        try:
            response = TestClient(server.app).post("/pve/vm-addresses", json={"vmid": 110}, headers={"X-Agent-Token": "t"})
        finally:
            server.app.dependency_overrides.clear()
        self.assertEqual(response.status_code, 404)
