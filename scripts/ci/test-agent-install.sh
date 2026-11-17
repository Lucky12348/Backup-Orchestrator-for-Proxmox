#!/usr/bin/env bash
# Unit tests for apps/api/app/agent_install/install.sh (run in CI, needs bash).
#   bash scripts/ci/test-agent-install.sh
# Command stubs (systemctl, hostname, pve-firewall…) are called by the sourced installer:
# shellcheck disable=SC2329
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../../apps/api/app/agent_install/install.sh
PBO_INSTALL_SOURCED=1 source "${ROOT}/apps/api/app/agent_install/install.sh"
set +e  # the installer enables errexit; failures are counted here instead
# Units and udev rule come from the agent package (agent/deploy.py).
AGENT_SRC="${ROOT}/apps/agent/src"

failures=0
check() {
  local expected="$1" url="$2"
  if valid_app_url "${url}"; then got="accept"; else got="reject"; fi
  if [[ "${got}" == "${expected}" ]]; then
    echo "ok    ${expected} ${url}"
  else
    echo "FAIL  expected ${expected}, got ${got}: ${url}"
    failures=$((failures + 1))
  fi
}

check accept "https://192.168.1.202"
check accept "https://pbo.lan:8443"
check accept "https://[fe80::1]:443"
check accept "https://[fd00::5]"
check reject "https://x/;rm"
check reject "http://a"
check reject "https://a b"
# shellcheck disable=SC2016  # literal on purpose: must not expand
check reject 'https://a$(reboot)'
check reject "https://"
check reject "https://a/path"

# The template placeholder must be the only app URL in the served script.
if ! grep -q '^PBO_APP_URL="__PBO_APP_URL__"$' "${ROOT}/apps/api/app/agent_install/install.sh"; then
  echo "FAIL  PBO_APP_URL placeholder line missing"
  failures=$((failures + 1))
fi

# --uninstall: services stopped, files removed, pveum only with --purge-pve-access.
# Commands are stubbed with functions and the paths point to a temporary dir.
uninstall_case() {
  local purge="$1" tmp calls
  tmp="$(mktemp -d)"
  calls="${tmp}/calls"
  (
    INSTALL_DIR="${tmp}/opt/pbo-agent"
    CONF_DIR="${tmp}/etc/pbo-agent"
    UDEV_RULE_FILE="${tmp}/etc/udev/rules.d/90-pbo-agent.rules"
    STATE_DIR="${tmp}/var/lib/pbo-agent"
    mkdir -p "${INSTALL_DIR}/venv" "${CONF_DIR}/tls" "${STATE_DIR}"
    write_udev_rule "${UDEV_RULE_FILE}"
    udevadm() { echo "udevadm $*" >>"${calls}"; }
    PVE_NODES_DIR="${tmp}/nodes"
    hostname() { echo pve; }
    pve-firewall() { :; }
    write_firewall_rules "${PVE_NODES_DIR}/pve/host.fw" 192.168.1.60 8090
    require_root() { :; }
    systemctl() { echo "systemctl $*" >>"${calls}"; }
    pveum() { echo "pveum $*" >>"${calls}"; [[ "$1 $2" == "user list" ]] && echo '[{"userid":"pbo@pve"}]'; return 0; }
    rm() { if [[ "$*" == *"/etc/systemd/system/"* ]]; then echo "rm $*" >>"${calls}"; else command rm "$@"; fi; }
    UNINSTALL=1
    PURGE_PVE_ACCESS="${purge}"
    main --uninstall >/dev/null
    [[ ! -e "${INSTALL_DIR}" && ! -e "${CONF_DIR}" && ! -e "${STATE_DIR}" ]] || { echo "FAIL  uninstall left files"; exit 1; }
    [[ ! -e "${UDEV_RULE_FILE}" ]] || { echo "FAIL  uninstall left the udev rule"; exit 1; }
    ! grep -q "pbo-agent" "${PVE_NODES_DIR}/pve/host.fw" || { echo "FAIL  uninstall left the firewall rules"; exit 1; }
  ) || failures=$((failures + 1))
  if ! grep -q "systemctl disable --now --quiet pbo-agent.service" "${calls}"; then
    echo "FAIL  uninstall did not stop pbo-agent.service"
    failures=$((failures + 1))
  fi
  if ! grep -q "udevadm control --reload-rules" "${calls}"; then
    echo "FAIL  uninstall did not reload the udev rules"
    failures=$((failures + 1))
  fi
  if ((purge)) && ! grep -q "pveum user delete pbo@pve" "${calls}"; then
    echo "FAIL  --purge-pve-access did not delete pbo@pve"
    failures=$((failures + 1))
  fi
  if ((!purge)) && grep -q "pveum user delete" "${calls}"; then
    echo "FAIL  --uninstall without --purge-pve-access deleted pbo@pve"
    failures=$((failures + 1))
  fi
  command rm -rf "${tmp}"
  echo "ok    uninstall (purge=${purge})"
}
uninstall_case 0
uninstall_case 1

# Generated udev rule: USB whole disks only, add and remove, nothing slow in udev.
rule_case() {
  local tmp rule
  tmp="$(mktemp -d)"
  rule="${tmp}/rules.d/90-pbo-agent.rules"
  write_udev_rule "${rule}"
  local expect
  for expect in     'ACTION=="add", SUBSYSTEM=="block", ENV{DEVTYPE}=="disk", ENV{ID_BUS}=="usb", TAG+="systemd", ENV{SYSTEMD_WANTS}+="pbo-agent-report.service"'     'ACTION=="remove", SUBSYSTEM=="block", ENV{DEVTYPE}=="disk", ENV{ID_BUS}=="usb", RUN+="/usr/bin/systemctl --no-block start pbo-agent-report.service"' \
    'ACTION=="remove", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", RUN+="/usr/bin/systemctl --no-block start pbo-agent-report.service"' \
    'ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", RUN+="/usr/bin/systemctl --no-block start pbo-agent-report.service"'; do
    if ! grep -qxF "${expect}" "${rule}"; then
      echo "FAIL  udev rule line missing: ${expect}"
      failures=$((failures + 1))
    fi
  done
  if [[ "$(grep -vc '^#' "${rule}")" != 4 ]]; then
    echo "FAIL  udev rule has unexpected lines"
    failures=$((failures + 1))
  fi
  command rm -rf "${tmp}"
  echo "ok    udev rule"
}
rule_case

# The report service is debounced and the timer is only a 5-minute fallback;
# the units run the code of the active release (self-update, Phase 5g).
units_case() {
  local tmp
  tmp="$(mktemp -d)"
  PYTHONPATH="${AGENT_SRC}" python3 -m agent.deploy write --role host --install-dir /opt/pbo-agent     --env-file /etc/pbo-agent/agent.env --systemd-dir "${tmp}/systemd" --udev-rule-file "${tmp}/udev.rules" >/dev/null
  for expect in "sync-state --settle 2" "StateDirectory=pbo-agent" "Environment=PYTHONPATH=/opt/pbo-agent/current/src"; do
    if ! grep -qF -- "${expect}" "${tmp}/systemd/pbo-agent-report.service"; then
      echo "FAIL  report service misses: ${expect}"
      failures=$((failures + 1))
    fi
  done
  grep -qF "OnUnitActiveSec=5min" "${tmp}/systemd/pbo-agent-report.timer" || { echo "FAIL  timer"; failures=$((failures + 1)); }
  grep -qF "ExecStart=/opt/pbo-agent/venv/bin/python -m agent.main serve" "${tmp}/systemd/pbo-agent.service" ||
    { echo "FAIL  agent service"; failures=$((failures + 1)); }
  [[ -f "${tmp}/udev.rules" ]] || { echo "FAIL  host role without udev rule"; failures=$((failures + 1)); }
  command rm -rf "${tmp}"
  echo "ok    units"
}
units_case

# Proxmox firewall rule (B12): app IP from the URL, rules first in [RULES],
# idempotent, removed by --uninstall, skipped with --no-firewall.
expect_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "${expected}" != "${actual}" ]]; then
    echo "FAIL  ${label}"
    echo "      expected: ${expected//$'\n'/⏎}"
    echo "      actual:   ${actual//$'\n'/⏎}"
    failures=$((failures + 1))
  fi
}

firewall_case() {
  local tmp file
  tmp="$(mktemp -d)"
  file="${tmp}/nodes/pve/host.fw"

  expect_eq "app ip literal" "192.168.1.60" "$(app_ip_from_url https://192.168.1.60)"
  expect_eq "app ip with port" "192.168.1.60" "$(app_ip_from_url https://192.168.1.60:8443)"
  if app_ip_from_url "https://[fd00::1]:443" >/dev/null; then
    echo "FAIL  IPv6 app address must not produce a rule"
    failures=$((failures + 1))
  fi

  write_firewall_rules "${file}" 192.168.1.60 8090
  expect_eq "new host.fw" "[RULES]
IN ACCEPT -source 192.168.1.60 -p tcp -dport 8090 -log nolog # pbo-agent
IN DROP -p tcp -dport 8090 -log nolog # pbo-agent" "$(cat "${file}")"

  printf '[OPTIONS]\nenable: 1\n\n[RULES]\nIN ACCEPT -p tcp -dport 22 -log nolog\n' >"${file}"
  write_firewall_rules "${file}" 192.168.1.60 8090
  write_firewall_rules "${file}" 192.168.1.61 8090  # re-install: replaced, not duplicated
  expect_eq "existing host.fw" "[OPTIONS]
enable: 1

[RULES]
IN ACCEPT -source 192.168.1.61 -p tcp -dport 8090 -log nolog # pbo-agent
IN DROP -p tcp -dport 8090 -log nolog # pbo-agent
IN ACCEPT -p tcp -dport 22 -log nolog" "$(cat "${file}")"

  remove_firewall_rules "${file}"
  expect_eq "removed" "[OPTIONS]
enable: 1

[RULES]
IN ACCEPT -p tcp -dport 22 -log nolog" "$(cat "${file}")"

  (
    PVE_NODES_DIR="${tmp}/nodes2"
    hostname() { echo pve; }
    pve-firewall() { [[ "$1" == "status" ]] && echo "Status: disabled/stopped"; return 0; }
    ROLE=host PORT=8090 PBO_APP_URL="https://192.168.1.60"
    FIREWALL=0
    configure_firewall >/dev/null
    [[ ! -e "${PVE_NODES_DIR}/pve/host.fw" ]] || { echo "FAIL  --no-firewall wrote a rule"; exit 1; }
    FIREWALL=1
    out="$(configure_firewall)"
    grep -qF "IN ACCEPT -source 192.168.1.60 -p tcp -dport 8090" "${PVE_NODES_DIR}/pve/host.fw" ||
      { echo "FAIL  --firewall wrote no rule"; exit 1; }
    [[ "${out}" == *"datacenter firewall is disabled"* ]] || { echo "FAIL  no warning for a disabled datacenter firewall"; exit 1; }
    FIREWALL=ask ASSUME_YES=1
    configure_firewall >/dev/null
    [[ "${FIREWALL}" == 1 ]] || { echo "FAIL  default answer must be yes"; exit 1; }
  ) || failures=$((failures + 1))

  command rm -rf "${tmp}"
  echo "ok    firewall rule"
}
firewall_case

if ((failures)); then
  echo "${failures} failure(s)"
  exit 1
fi
echo "all agent installer checks passed"
