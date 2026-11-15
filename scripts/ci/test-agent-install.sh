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

if ((failures)); then
  echo "${failures} failure(s)"
  exit 1
fi
echo "all agent installer checks passed"
