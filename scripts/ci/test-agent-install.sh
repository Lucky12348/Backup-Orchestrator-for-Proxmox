#!/usr/bin/env bash
# Unit tests for apps/api/app/agent_install/install.sh (run in CI, needs bash).
#   bash scripts/ci/test-agent-install.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../../apps/api/app/agent_install/install.sh
PBO_INSTALL_SOURCED=1 source "${ROOT}/apps/api/app/agent_install/install.sh"
set +e  # the installer enables errexit; failures are counted here instead

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
    require_root() { :; }
    systemctl() { echo "systemctl $*" >>"${calls}"; }
    pveum() { echo "pveum $*" >>"${calls}"; [[ "$1 $2" == "user list" ]] && echo '[{"userid":"pbo@pve"}]'; return 0; }
    rm() { if [[ "$*" == *"/etc/systemd/system/"* ]]; then echo "rm $*" >>"${calls}"; else command rm "$@"; fi; }
    UNINSTALL=1
    PURGE_PVE_ACCESS="${purge}"
    main --uninstall >/dev/null
    [[ ! -e "${INSTALL_DIR}" && ! -e "${CONF_DIR}" && ! -e "${STATE_DIR}" ]] || { echo "FAIL  uninstall left files"; exit 1; }
    [[ ! -e "${UDEV_RULE_FILE}" ]] || { echo "FAIL  uninstall left the udev rule"; exit 1; }
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
  for expect in     'ACTION=="add", SUBSYSTEM=="block", ENV{DEVTYPE}=="disk", ENV{ID_BUS}=="usb", TAG+="systemd", ENV{SYSTEMD_WANTS}+="pbo-agent-report.service"'     'ACTION=="remove", SUBSYSTEM=="block", ENV{DEVTYPE}=="disk", ENV{ID_BUS}=="usb", RUN+="/usr/bin/systemctl --no-block start pbo-agent-report.service"'; do
    if ! grep -qxF "${expect}" "${rule}"; then
      echo "FAIL  udev rule line missing: ${expect}"
      failures=$((failures + 1))
    fi
  done
  if [[ "$(grep -vc '^#' "${rule}")" != 2 ]]; then
    echo "FAIL  udev rule has unexpected lines"
    failures=$((failures + 1))
  fi
  command rm -rf "${tmp}"
  echo "ok    udev rule"
}
rule_case

# The report service is debounced and the timer is only a 5-minute fallback.
INSTALLER="${ROOT}/apps/api/app/agent_install/install.sh"
for expect in "sync-state --settle 2" "OnUnitActiveSec=5min" "StateDirectory=pbo-agent"; do
  if ! grep -qF -- "${expect}" "${INSTALLER}"; then
    echo "FAIL  installer misses: ${expect}"
    failures=$((failures + 1))
  fi
done

if ((failures)); then
  echo "${failures} failure(s)"
  exit 1
fi
echo "all agent installer checks passed"
