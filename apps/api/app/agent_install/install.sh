#!/usr/bin/env bash
# Proxmox Backup Orchestrator - one-line agent installer.
#
# Served by the app at https://<app>/agent/install.sh (the app URL below is
# filled in by the app). Run as root on the Proxmox VE host:
#
#   curl -fskSL -o pbo-agent-install.sh https://<app>/agent/install.sh
#   bash pbo-agent-install.sh --code XXXX-XXXX
#
# (`bash -c "$(curl …)" -- --code …` works too, but the app does not show it:
# content blockers flag that pattern in the clipboard as "ClickFix".)
#
# `-k` is accepted for this first download only: the app certificate is
# self-signed. The script then prints the fingerprint of the certificate it
# sees and asks you to compare it with the one shown in the app; every later
# request (agent archive, registration, then all agent -> app calls) pins it.
set -Eeuo pipefail

PBO_APP_URL="__PBO_APP_URL__"

INSTALL_DIR="/opt/pbo-agent"
CONF_DIR="/etc/pbo-agent"
ENV_FILE="${CONF_DIR}/agent.env"
TLS_DIR="${CONF_DIR}/tls"
ROLE="host"
PORT="8090"
BRIDGE="vmbr0"
LISTEN_IP=""
CODE=""
EXPECTED_FP=""
ASSUME_YES=0
UNINSTALL=0
PURGE_PVE_ACCESS=0
WORK_DIR=""

usage() {
  cat <<EOF
Usage: install.sh --code XXXX-XXXX [options]

  --code CODE              pairing code shown by the app (single use, 15 min)
  --app-fingerprint FP     expected SHA-256 of the app certificate (skips the prompt)
  --yes                    accept the app certificate without asking (not recommended)
  --port PORT              agent HTTPS port (default ${PORT})
  --bridge IFACE           listen on this interface's IPv4 address (default ${BRIDGE})
  --listen-ip IP           listen on this address instead
  --role host|pbs          agent role (default host; pbs = legacy PBS agent mode)
  --app-url URL            override the app URL (default ${PBO_APP_URL})

Usage: install.sh --uninstall [--purge-pve-access]

  --uninstall              stop and remove the agent services, ${INSTALL_DIR} and ${CONF_DIR}
  --purge-pve-access       also delete the API identity made by the setup wizard
                           (token pbo@pve!pbo, user pbo@pve, role PBO)
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

info() {
  echo "==> $*"
}

cleanup() {
  if [[ -n "${WORK_DIR}" && -d "${WORK_DIR}" ]]; then
    rm -rf "${WORK_DIR}"
  fi
}
trap cleanup EXIT

normalize_fp() {
  tr -d ': \n' <<<"$1" | tr '[:lower:]' '[:upper:]' | sed 's/../&:/g; s/:$//'
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --code) CODE="${2:-}"; shift 2 ;;
      --app-fingerprint) EXPECTED_FP="${2:-}"; shift 2 ;;
      --yes | -y) ASSUME_YES=1; shift ;;
      --port) PORT="${2:-}"; shift 2 ;;
      --bridge) BRIDGE="${2:-}"; shift 2 ;;
      --listen-ip) LISTEN_IP="${2:-}"; shift 2 ;;
      --role) ROLE="${2:-}"; shift 2 ;;
      --app-url) PBO_APP_URL="${2:-}"; shift 2 ;;
      --uninstall) UNINSTALL=1; shift ;;
      --purge-pve-access) PURGE_PVE_ACCESS=1; shift ;;
      -h | --help) usage; exit 0 ;;
      *) usage >&2; die "unknown option: $1" ;;
    esac
  done
}

# https://<host>[:port] only (IPv4, DNS name or [IPv6]): the value ends up in
# commands. In a bash bracket expression `\]` is not an escape: a literal `]`
# must come first, hence `[][…]` (`]` and `[` then the other characters).
valid_app_url() {
  [[ "$1" =~ ^https://[][A-Za-z0-9.:-]+$ ]]
}

preflight() {
  [[ ${EUID} -eq 0 ]] || die "run this script as root."
  [[ -n "${CODE}" ]] || die "--code is required (generate one in the app: Agents > Add)."
  valid_app_url "${PBO_APP_URL}" || die "invalid app URL: ${PBO_APP_URL}"
  if ! [[ "${PORT}" =~ ^[0-9]+$ ]] || ((PORT < 1 || PORT > 65535)); then
    die "invalid port: ${PORT}"
  fi
  case "${ROLE}" in
    host)
      command -v pveversion >/dev/null 2>&1 || die "this is not a Proxmox VE host (pveversion not found)."
      info "Proxmox VE detected: $(pveversion | head -n1)"
      ;;
    pbs)
      command -v proxmox-backup-manager >/dev/null 2>&1 || die "this is not a Proxmox Backup Server (proxmox-backup-manager not found)."
      ;;
    *) die "--role must be host or pbs." ;;
  esac
}

install_packages() {
  local missing=()
  command -v openssl >/dev/null 2>&1 || missing+=(openssl)
  command -v python3 >/dev/null 2>&1 || missing+=(python3)
  python3 -c 'import ensurepip, venv' >/dev/null 2>&1 || missing+=(python3-venv)
  if ((${#missing[@]})); then
    info "Installing ${missing[*]}"
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${missing[@]}"
  fi
}

# Minimal HTTPS client with certificate pinning (Python standard library only):
# the fingerprint is checked right after the TLS handshake, before any request.
write_https_helper() {
  cat >"${WORK_DIR}/pbo_https.py" <<'PY'
import hashlib
import http.client
import json
import os
import re
import ssl
import sys
import urllib.parse


def connect(url, fingerprint):
    parts = urllib.parse.urlsplit(url)
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # replaced by the pin below
    conn = http.client.HTTPSConnection(parts.hostname, parts.port or 443, context=context, timeout=120)
    conn.connect()
    seen = ":".join(f"{b:02X}" for b in hashlib.sha256(conn.sock.getpeercert(binary_form=True)).digest())
    if seen != fingerprint:
        sys.exit(f"TLS fingerprint mismatch for {parts.hostname}: got {seen}, expected {fingerprint}")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return conn, path


def get(url, fingerprint, output):
    conn, path = connect(url, fingerprint)
    conn.request("GET", path)
    response = conn.getresponse()
    body = response.read()
    if response.status != 200:
        sys.exit(f"GET {url}: HTTP {response.status} {body[:300]!r}")
    with open(output, "wb") as handle:
        handle.write(body)


SAFE = re.compile(r"^[A-Za-z0-9._~:/\[\]-]+$")


def register(url, fingerprint, env_file, code, role, hostname, base_url, agent_fp, extra_env):
    payload = {"code": code, "role": role, "hostname": hostname, "base_url": base_url, "fingerprint": agent_fp}
    conn, path = connect(url, fingerprint)
    conn.request("POST", path, body=json.dumps(payload), headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    body = response.read().decode("utf-8", "replace")
    if response.status != 201:
        try:
            detail = json.loads(body).get("detail", body)
        except ValueError:
            detail = body
        sys.exit(f"Registration refused (HTTP {response.status}): {detail}")
    data = json.loads(body)
    values = {
        "AGENT_SERVER_TOKEN": data["agent_token"],
        "AGENT_CALLBACK_TOKEN": data["callback_token"],
        "AGENT_API_BASE_URL": data["api_base_url"],
        "AGENT_API_FINGERPRINT": fingerprint,
    }
    values.update(dict(item.split("=", 1) for item in extra_env))
    for key, value in values.items():
        if not SAFE.match(value):
            sys.exit(f"Refusing unexpected characters in {key}")
    fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write("# Written by the Proxmox Backup Orchestrator agent installer.\n")
        handle.write("# Re-run the installer with a new pairing code to rotate the tokens.\n")
        for key, value in values.items():
            handle.write(f"{key}={value}\n")
    print(data["agent_id"])


if __name__ == "__main__":
    command, args = sys.argv[1], sys.argv[2:]
    if command == "get":
        get(*args)
    elif command == "register":
        register(*args[:8], args[8:])
    else:
        sys.exit(f"unknown command {command}")
PY
}

app_host_port() {
  local hostport="${PBO_APP_URL#https://}"
  if [[ "${hostport}" == \[* ]]; then
    [[ "${hostport}" == *\]:* ]] && echo "${hostport}" || echo "${hostport}:443"
  elif [[ "${hostport}" == *:* ]]; then
    echo "${hostport}"
  else
    echo "${hostport}:443"
  fi
}

confirm_app_certificate() {
  local hostport host seen answer
  hostport="$(app_host_port)"
  host="${hostport%:*}"
  host="${host#[}"
  host="${host%]}"
  seen="$(openssl s_client -connect "${hostport}" -servername "${host}" </dev/null 2>/dev/null |
    openssl x509 -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2)" || true
  [[ -n "${seen}" ]] || die "cannot read the TLS certificate of ${PBO_APP_URL}."
  APP_FP="$(normalize_fp "${seen}")"

  echo
  echo "App certificate fingerprint (SHA-256), as seen from this host:"
  echo "    ${APP_FP}"
  echo
  if [[ -n "${EXPECTED_FP}" ]]; then
    [[ "$(normalize_fp "${EXPECTED_FP}")" == "${APP_FP}" ]] ||
      die "fingerprint mismatch: expected $(normalize_fp "${EXPECTED_FP}"). Someone may be intercepting the connection."
    info "Matches --app-fingerprint."
  elif ((ASSUME_YES)); then
    info "--yes given: certificate accepted without comparison."
  else
    echo "Compare it with the fingerprint shown in the app (Agents > Add)."
    read -r -p "Does it match? [y/N] " answer </dev/tty || answer=""
    [[ "${answer}" =~ ^[YyOo]$ ]] || die "aborted: certificate not confirmed."
  fi
}

detect_listen_ip() {
  if [[ -z "${LISTEN_IP}" ]]; then
    LISTEN_IP="$(ip -4 -o addr show dev "${BRIDGE}" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)"
    [[ -n "${LISTEN_IP}" ]] || die "no IPv4 address on ${BRIDGE}; use --bridge or --listen-ip."
  fi
  [[ "${LISTEN_IP}" =~ ^[0-9.]+$ && "${LISTEN_IP}" != "0.0.0.0" ]] || die "invalid listen address: ${LISTEN_IP}"
  info "The agent will listen on https://${LISTEN_IP}:${PORT} only."
}

install_agent_code() {
  info "Downloading the agent from the app"
  python3 "${WORK_DIR}/pbo_https.py" get "${PBO_APP_URL}/agent/agent.tar.gz" "${APP_FP}" "${WORK_DIR}/agent.tar.gz"
  mkdir -p "${INSTALL_DIR}"
  rm -rf "${INSTALL_DIR}/app.new"
  mkdir -p "${INSTALL_DIR}/app.new"
  tar -xzf "${WORK_DIR}/agent.tar.gz" -C "${INSTALL_DIR}/app.new" --strip-components=1 --no-same-owner
  rm -rf "${INSTALL_DIR}/app"
  mv "${INSTALL_DIR}/app.new" "${INSTALL_DIR}/app"

  info "Creating the Python environment (${INSTALL_DIR}/venv)"
  if [[ ! -x "${INSTALL_DIR}/venv/bin/python" ]]; then
    python3 -m venv "${INSTALL_DIR}/venv"
  fi
  "${INSTALL_DIR}/venv/bin/pip" install --quiet --disable-pip-version-check --upgrade "${INSTALL_DIR}/app"
}

create_certificate() {
  mkdir -p "${TLS_DIR}"
  chmod 700 "${CONF_DIR}" "${TLS_DIR}"
  local san
  san="$(openssl x509 -in "${TLS_DIR}/cert.pem" -noout -ext subjectAltName 2>/dev/null || true)"
  if [[ -f "${TLS_DIR}/cert.pem" && "${san}" == *"IP Address:${LISTEN_IP}"* ]]; then
    info "Keeping the existing agent certificate"
  else
    info "Generating the agent self-signed certificate"
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 \
      -subj "/CN=pbo-agent-$(hostname -s)" \
      -addext "subjectAltName=IP:${LISTEN_IP},DNS:$(hostname -s)" \
      -keyout "${TLS_DIR}/key.pem" -out "${TLS_DIR}/cert.pem" >/dev/null 2>&1
    chmod 600 "${TLS_DIR}/key.pem"
  fi
  AGENT_FP="$(normalize_fp "$(openssl x509 -in "${TLS_DIR}/cert.pem" -noout -fingerprint -sha256 | cut -d= -f2)")"
}

register_agent() {
  info "Registering with ${PBO_APP_URL}"
  local agent_id
  agent_id="$(python3 "${WORK_DIR}/pbo_https.py" register \
    "${PBO_APP_URL}/api/v1/agents/register" "${APP_FP}" "${ENV_FILE}.new" \
    "${CODE}" "${ROLE}" "$(hostname -s)" "https://${LISTEN_IP}:${PORT}" "${AGENT_FP}" \
    "AGENT_ROLE=${ROLE}" \
    "AGENT_HOSTNAME=$(hostname -s)" \
    "AGENT_SERVER_HOST=${LISTEN_IP}" \
    "AGENT_SERVER_PORT=${PORT}" \
    "AGENT_TLS_CERT_FILE=${TLS_DIR}/cert.pem" \
    "AGENT_TLS_KEY_FILE=${TLS_DIR}/key.pem" \
    "AGENT_HTTP_SERVICE_NAME=pbo-agent.service" \
    "AGENT_REPO_PATH=${INSTALL_DIR}/app")"
  mv "${ENV_FILE}.new" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  info "Registered as agent #${agent_id}"
}

install_services() {
  info "Installing systemd services"
  cat >/etc/systemd/system/pbo-agent.service <<EOF
[Unit]
Description=Proxmox Backup Orchestrator agent (${ROLE})
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
# Root is required for qm (USB passthrough) and disk inspection.
User=root
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${INSTALL_DIR}/venv/bin/python -m agent.main serve
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
RestrictSUIDSGID=yes
LockPersonality=yes

[Install]
WantedBy=multi-user.target
EOF

  cat >/etc/systemd/system/pbo-agent-report.service <<EOF
[Unit]
Description=Proxmox Backup Orchestrator agent heartbeat and disk report
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=root
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${INSTALL_DIR}/venv/bin/python -m agent.main sync-state
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
EOF

  cat >/etc/systemd/system/pbo-agent-report.timer <<EOF
[Unit]
Description=Proxmox Backup Orchestrator agent report every 2 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=2min
AccuracySec=15s
Unit=pbo-agent-report.service
Persistent=true

[Install]
WantedBy=timers.target
EOF

  systemctl daemon-reload
  systemctl enable --quiet pbo-agent.service
  systemctl restart pbo-agent.service
  if [[ "${ROLE}" == "host" ]]; then
    systemctl enable --quiet --now pbo-agent-report.timer
  fi
}

verify() {
  local _
  for _ in $(seq 1 15); do
    if systemctl is-active --quiet pbo-agent.service; then
      break
    fi
    sleep 1
  done
  systemctl is-active --quiet pbo-agent.service ||
    die "pbo-agent.service did not start: journalctl -u pbo-agent.service -n 50"
  if [[ "${ROLE}" == "host" ]]; then
    if systemctl start pbo-agent-report.service; then
      info "First heartbeat and disk report sent to the app."
    else
      echo "WARNING: the first report failed: journalctl -u pbo-agent-report.service -n 50" >&2
    fi
  fi
}

legacy_notice() {
  local unit
  for unit in proxmox-backup-orchestrator-agent-api.service proxmox-backup-orchestrator-agent.timer; do
    if systemctl is-enabled --quiet "${unit}" 2>/dev/null; then
      echo "NOTE: legacy unit ${unit} is still enabled. Once the app shows the new agent as online:"
      echo "      systemctl disable --now ${unit}"
    fi
  done
}

# Removes everything the installer created, to start a test from scratch.
# The registration stays in the app until revoked there (or replaced by the
# next registration of a host agent).
require_root() {
  [[ ${EUID} -eq 0 ]] || die "run this script as root."
}

uninstall() {
  require_root
  local unit
  info "Stopping and removing the agent services"
  for unit in pbo-agent-report.timer pbo-agent-report.service pbo-agent.service; do
    systemctl disable --now --quiet "${unit}" 2>/dev/null || true
    rm -f "/etc/systemd/system/${unit}"
  done
  systemctl daemon-reload
  systemctl reset-failed pbo-agent.service pbo-agent-report.service 2>/dev/null || true
  info "Removing ${INSTALL_DIR} and ${CONF_DIR}"
  rm -rf "${INSTALL_DIR}" "${CONF_DIR}"
  if ((PURGE_PVE_ACCESS)); then
    if command -v pveum >/dev/null 2>&1; then
      info "Removing the Proxmox VE API identity pbo@pve (token, user, role PBO)"
      pveum user token remove pbo@pve pbo 2>/dev/null || true
      pveum user delete pbo@pve 2>/dev/null || true
      pveum role delete PBO 2>/dev/null || true
    fi
  elif command -v pveum >/dev/null 2>&1 && pveum user list --output-format json 2>/dev/null | grep -q '"pbo@pve"'; then
    echo "NOTE: the API user pbo@pve (token pbo@pve!pbo, role PBO) is kept."
    echo "      Re-run with --uninstall --purge-pve-access to delete it as well."
  fi
  info "Agent removed. Revoke it in the app too (Integrations > Agent installation) or register a new one."
}

main() {
  parse_args "$@"
  if ((UNINSTALL)); then
    uninstall
    return 0
  fi
  preflight
  install_packages
  WORK_DIR="$(mktemp -d)"
  write_https_helper
  confirm_app_certificate
  detect_listen_ip
  install_agent_code
  create_certificate
  register_agent
  install_services
  verify
  legacy_notice
  echo
  info "Agent installed (${ROLE}): https://${LISTEN_IP}:${PORT}"
  echo "    Agent certificate fingerprint: ${AGENT_FP}"
  echo "    Only the app needs to reach port ${PORT}: restrict it in the Proxmox firewall."
}

# PBO_INSTALL_SOURCED=1 lets tests source the functions without running.
# (BASH_SOURCE cannot be used: under `bash -c "$(curl …)"` it is empty.)
if [[ -z "${PBO_INSTALL_SOURCED:-}" ]]; then
  main "$@"
fi
