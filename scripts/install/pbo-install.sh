#!/usr/bin/env bash
# Proxmox Backup Orchestrator - native install in a fresh Debian 13 LXC.
#
# Run as root INSIDE the container:
#   bash pbo-install.sh                  # latest GitHub release
#   bash pbo-install.sh --ref main       # a branch or tag (web UI built locally)
#   bash pbo-install.sh --tarball x.tgz  # a local source archive (dev)
#
# Result: code in /opt/pbo, config in /etc/pbo (pbo.env 0600, tls/), local
# PostgreSQL (localhost only), service pbo.service = uvicorn HTTPS on :443 as
# user `pbo`, and the `update` command (pbo-update.sh).
#
# This file is also sourced by pbo-update.sh for its helper functions: nothing
# runs unless it is executed directly.
set -Eeuo pipefail

PBO_REPO="${PBO_REPO:-Lucky12348/Backup-Orchestrator-for-Proxmox}"
PBO_DIR="${PBO_DIR:-/opt/pbo}"
PBO_ETC="/etc/pbo"
PBO_ENV="${PBO_ETC}/pbo.env"
PBO_TLS="${PBO_ETC}/tls"
PBO_USER="pbo"
PBO_DB="pbo"
PBO_PORT="${PBO_PORT:-443}"
PBO_NODE_MAJOR="${PBO_NODE_MAJOR:-22}"
PBO_BACKUP_DIR="/var/backups/pbo"

PBO_REF=""
PBO_TARBALL=""
PBO_SOURCE_VERSION=""

pbo_info() { echo -e "\e[1;34m==>\e[0m $*"; }
pbo_ok() { echo -e "\e[1;32m ok\e[0m $*"; }
pbo_warn() { echo -e "\e[1;33mWARN\e[0m $*" >&2; }
pbo_die() {
  echo -e "\e[1;31mERROR\e[0m $*" >&2
  exit 1
}

pbo_require_root() {
  [[ ${EUID} -eq 0 ]] || pbo_die "run as root."
}

pbo_require_debian() {
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID:-}" == "debian" ]] || pbo_die "Debian is required (found ${ID:-unknown})."
  if [[ "${VERSION_ID:-}" != "13" ]]; then
    pbo_warn "tested on Debian 13; found ${PRETTY_NAME:-unknown}."
  fi
}

pbo_install_packages() {
  pbo_info "Installing system packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends \
    ca-certificates curl git openssl xz-utils \
    python3 python3-venv \
    postgresql postgresql-client >/dev/null
  pbo_ok "packages installed"
}

# --- Sources -----------------------------------------------------------------------

pbo_latest_release() {
  curl -fsSL "https://api.github.com/repos/${PBO_REPO}/releases/latest" |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])'
}

# Resolve PBO_REF (empty = latest release) into PBO_REF + PBO_RELEASE (1/0).
pbo_resolve_ref() {
  PBO_RELEASE=0
  if [[ -n "${PBO_TARBALL}" ]]; then
    return
  fi
  if [[ -z "${PBO_REF}" ]]; then
    PBO_REF="$(pbo_latest_release 2>/dev/null)" ||
      pbo_die "no GitHub release found for ${PBO_REPO}; use --ref <branch|tag>."
    PBO_RELEASE=1
  fi
}

# Fetch the sources into $1 (must not exist). Sets PBO_SOURCE_VERSION.
pbo_fetch_source() {
  local dest="$1" work version asset
  work="$(mktemp -d)"
  mkdir -p "${dest}"
  if [[ -n "${PBO_TARBALL}" ]]; then
    pbo_info "Extracting ${PBO_TARBALL}"
    tar -xzf "${PBO_TARBALL}" -C "${dest}" --strip-components=1 --no-same-owner
    PBO_SOURCE_VERSION="local-$(date +%Y%m%d%H%M%S)"
  else
    # A release tag provides a pre-built web UI (apps/web/dist) in its asset.
    version="${PBO_REF#v}"
    asset="https://github.com/${PBO_REPO}/releases/download/${PBO_REF}/pbo-${version}.tar.gz"
    if curl -fsSL -o "${work}/pbo.tar.gz" "${asset}" 2>/dev/null; then
      pbo_info "Release ${PBO_REF}"
      if curl -fsSL -o "${work}/pbo.tar.gz.sha256" "${asset}.sha256" 2>/dev/null; then
        (cd "${work}" && sed "s#  .*#  pbo.tar.gz#" pbo.tar.gz.sha256 | sha256sum -c --quiet -) ||
          pbo_die "checksum mismatch for ${asset}"
      fi
      tar -xzf "${work}/pbo.tar.gz" -C "${dest}" --strip-components=1 --no-same-owner
      PBO_RELEASE=1
      PBO_SOURCE_VERSION="${PBO_REF}"
    else
      if [[ "${PBO_RELEASE:-0}" == "1" ]]; then
        pbo_die "release asset not found: ${asset}"
      fi
      pbo_info "Cloning ${PBO_REPO} @ ${PBO_REF} (not a release: the web UI is built here)"
      git clone --quiet --depth 1 --branch "${PBO_REF}" "https://github.com/${PBO_REPO}.git" "${dest}" ||
        pbo_die "cannot clone ref ${PBO_REF}"
      PBO_SOURCE_VERSION="${PBO_REF}@$(git -C "${dest}" rev-parse --short HEAD)"
      rm -rf "${dest}/.git"
    fi
  fi
  rm -rf "${work}"
  [[ -f "${dest}/apps/api/pyproject.toml" ]] || pbo_die "unexpected source layout in ${dest}"
  echo "${PBO_SOURCE_VERSION}" >"${dest}/VERSION"
}

# Build apps/web/dist with a temporary Node.js unless it is already there
# (release archives ship it pre-built). Node is removed afterwards.
pbo_build_web() {
  local src="$1" web arch node_dir base file
  web="${src}/apps/web"
  if [[ -f "${web}/dist/index.html" ]]; then
    pbo_ok "web UI already built"
    return
  fi
  case "$(uname -m)" in
    x86_64) arch="x64" ;;
    aarch64) arch="arm64" ;;
    *) pbo_die "unsupported architecture for Node.js: $(uname -m)" ;;
  esac
  node_dir="$(mktemp -d)"
  base="https://nodejs.org/dist/latest-v${PBO_NODE_MAJOR}.x"
  pbo_info "Building the web UI (temporary Node.js ${PBO_NODE_MAJOR})"
  curl -fsSL -o "${node_dir}/SHASUMS256.txt" "${base}/SHASUMS256.txt"
  file="$(awk -v suffix="linux-${arch}.tar.xz" '$2 ~ suffix"$" {print $2; exit}' "${node_dir}/SHASUMS256.txt")"
  [[ -n "${file}" ]] || pbo_die "Node.js archive not found in ${base}"
  curl -fsSL -o "${node_dir}/${file}" "${base}/${file}"
  (cd "${node_dir}" && grep " ${file}\$" SHASUMS256.txt | sha256sum -c --quiet -) || pbo_die "Node.js checksum mismatch"
  mkdir "${node_dir}/node"
  tar -xJf "${node_dir}/${file}" -C "${node_dir}/node" --strip-components=1
  (
    cd "${web}"
    export PATH="${node_dir}/node/bin:${PATH}" npm_config_update_notifier=false npm_config_fund=false npm_config_audit=false
    npm ci --loglevel=error >/dev/null
    npm run build --silent >/dev/null
  ) || pbo_die "web UI build failed"
  rm -rf "${web}/node_modules" "${node_dir}" /root/.npm
  pbo_ok "web UI built, Node.js removed"
}

# Python environment in $1/.venv (path must be the final one: venvs are not relocatable).
pbo_setup_venv() {
  local dir="$1"
  pbo_info "Creating the Python environment"
  rm -rf "${dir}/.venv"
  python3 -m venv "${dir}/.venv"
  "${dir}/.venv/bin/pip" install --quiet --disable-pip-version-check --upgrade pip
  "${dir}/.venv/bin/pip" install --quiet --disable-pip-version-check -e "${dir}/apps/api"
  pbo_ok "Python environment ready"
}

# --- System configuration ---------------------------------------------------------------

pbo_create_user() {
  if ! id -u "${PBO_USER}" >/dev/null 2>&1; then
    useradd --system --home-dir "${PBO_DIR}" --no-create-home --shell /usr/sbin/nologin "${PBO_USER}"
  fi
}

pbo_setup_postgres() {
  local conf password
  pbo_info "Configuring PostgreSQL (localhost only)"
  conf="$(find /etc/postgresql -name postgresql.conf -path '*/main/*' | sort -V | tail -n1)"
  [[ -n "${conf}" ]] || pbo_die "postgresql.conf not found"
  if grep -qE "^\s*listen_addresses" "${conf}"; then
    sed -i -E "s/^\s*listen_addresses\s*=.*/listen_addresses = 'localhost'/" "${conf}"
  else
    echo "listen_addresses = 'localhost'" >>"${conf}"
  fi
  systemctl enable --quiet postgresql
  systemctl restart postgresql

  if [[ -f "${PBO_ENV}" ]] && grep -q '^DATABASE_URL=' "${PBO_ENV}"; then
    pbo_ok "database already configured (kept)"
    return
  fi
  password="$(openssl rand -hex 24)"
  if runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${PBO_USER}'" | grep -q 1; then
    runuser -u postgres -- psql -q -c "ALTER ROLE ${PBO_USER} WITH LOGIN PASSWORD '${password}'"
  else
    runuser -u postgres -- psql -q -c "CREATE ROLE ${PBO_USER} WITH LOGIN PASSWORD '${password}'"
  fi
  if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='${PBO_DB}'" | grep -q 1; then
    runuser -u postgres -- createdb -O "${PBO_USER}" "${PBO_DB}"
  fi
  PBO_NEW_DATABASE_URL="postgresql+psycopg://${PBO_USER}:${password}@127.0.0.1:5432/${PBO_DB}"
  pbo_ok "database ${PBO_DB} ready"
}

pbo_write_env() {
  install -d -m 0755 "${PBO_ETC}"
  if [[ -f "${PBO_ENV}" ]]; then
    pbo_ok "${PBO_ENV} kept (AUTH_SECRET_KEY must never change: it encrypts stored secrets)"
    return
  fi
  pbo_info "Writing ${PBO_ENV}"
  umask 077
  cat >"${PBO_ENV}" <<EOF
# Proxmox Backup Orchestrator - generated by pbo-install.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ).
# Bootstrap values only: everything else is configured in the web UI.
# AUTH_SECRET_KEY signs sessions AND encrypts the secrets stored in the
# database: changing it logs everyone out and makes those secrets unreadable.
APP_ENV=production
AUTH_SECRET_KEY=$(openssl rand -hex 32)
DATABASE_URL=${PBO_NEW_DATABASE_URL}
AUTH_COOKIE_SECURE=true
WEB_DIST_DIR=${PBO_DIR}/apps/web/dist
PBO_AGENT_SOURCE_DIR=${PBO_DIR}/apps/agent
PBO_TLS_CERT_FILE=${PBO_TLS}/cert.pem
EOF
  chmod 0600 "${PBO_ENV}"
  umask 022
}

pbo_primary_ip() {
  hostname -I 2>/dev/null | awk '{print $1}'
}

pbo_create_certificate() {
  local ip
  install -d -m 0750 -o root -g "${PBO_USER}" "${PBO_TLS}"
  if [[ -f "${PBO_TLS}/cert.pem" && -f "${PBO_TLS}/key.pem" ]]; then
    pbo_ok "TLS certificate kept"
  else
    pbo_info "Generating a self-signed TLS certificate"
    ip="$(pbo_primary_ip)"
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 \
      -subj "/CN=$(hostname -s)" \
      -addext "subjectAltName=DNS:$(hostname -s),DNS:localhost${ip:+,IP:${ip}},IP:127.0.0.1" \
      -keyout "${PBO_TLS}/key.pem" -out "${PBO_TLS}/cert.pem" >/dev/null 2>&1
  fi
  chown root:"${PBO_USER}" "${PBO_TLS}/key.pem" "${PBO_TLS}/cert.pem"
  chmod 0640 "${PBO_TLS}/key.pem"
  chmod 0644 "${PBO_TLS}/cert.pem"
}

pbo_cert_fingerprint() {
  openssl x509 -in "${PBO_TLS}/cert.pem" -noout -fingerprint -sha256 | cut -d= -f2
}

pbo_write_service() {
  cat >/etc/systemd/system/pbo.service <<EOF
[Unit]
Description=Proxmox Backup Orchestrator (web UI + API)
Wants=network-online.target
After=network-online.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=${PBO_USER}
Group=${PBO_USER}
WorkingDirectory=${PBO_DIR}/apps/api
EnvironmentFile=${PBO_ENV}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=${PBO_DIR}/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PBO_PORT} --ssl-certfile ${PBO_TLS}/cert.pem --ssl-keyfile ${PBO_TLS}/key.pem --no-server-header
Restart=on-failure
RestartSec=5

# Port 443 without root.
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes
# Hardening (needs the LXC "nesting" feature, on by default for community-scripts).
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictNamespaces=yes
RestrictRealtime=yes
LockPersonality=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
SystemCallArchitectures=native
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
}

pbo_install_update_command() {
  install -m 0755 "${PBO_DIR}/scripts/install/pbo-update.sh" /usr/local/bin/pbo-update
  # community-scripts convention: `update` inside the container.
  ln -sf /usr/local/bin/pbo-update /usr/bin/update
}

pbo_wait_healthy() {
  local timeout="${1:-90}" i
  for ((i = 0; i < timeout; i += 2)); do
    if curl -fsk --max-time 3 "https://127.0.0.1:${PBO_PORT}/health" >/dev/null 2>&1; then
      return 0
    fi
    if [[ "$(systemctl show -p ExecMainStatus --value pbo.service)" == "226" ]]; then
      pbo_warn "systemd sandboxing failed (226/NAMESPACE): enable 'nesting' on the LXC (pct set <id> --features nesting=1)."
      return 1
    fi
    sleep 2
  done
  return 1
}

pbo_parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --ref) PBO_REF="${2:-}"; shift 2 ;;
      --tarball) PBO_TARBALL="$(readlink -f "${2:-}")"; shift 2 ;;
      --repo) PBO_REPO="${2:-}"; shift 2 ;;
      --port) PBO_PORT="${2:-}"; shift 2 ;;
      -h | --help)
        sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
      *) pbo_die "unknown option: $1 (see --help)" ;;
    esac
  done
}

pbo_install_main() {
  pbo_parse_args "$@"
  pbo_require_root
  pbo_require_debian
  [[ ! -f "${PBO_DIR}/VERSION" ]] || pbo_die "already installed (${PBO_DIR}): use 'update'."
  pbo_install_packages
  pbo_resolve_ref
  rm -rf "${PBO_DIR}"
  pbo_fetch_source "${PBO_DIR}"
  pbo_build_web "${PBO_DIR}"
  pbo_create_user
  pbo_setup_venv "${PBO_DIR}"
  chown -R root:root "${PBO_DIR}"
  chmod -R go-w "${PBO_DIR}"
  pbo_setup_postgres
  pbo_write_env
  pbo_create_certificate
  pbo_write_service
  pbo_install_update_command
  install -d -m 0700 "${PBO_BACKUP_DIR}"

  pbo_info "Starting pbo.service"
  systemctl enable --quiet pbo.service
  systemctl restart pbo.service
  pbo_wait_healthy 120 || pbo_die "the app did not become healthy: journalctl -u pbo.service -n 80"

  local ip
  ip="$(pbo_primary_ip)"
  echo
  pbo_ok "Proxmox Backup Orchestrator $(cat "${PBO_DIR}/VERSION") is running."
  echo
  echo "  Open https://${ip:-<container-ip>} to create the admin account."
  echo "  (self-signed certificate; SHA-256 fingerprint:"
  echo "   $(pbo_cert_fingerprint))"
  echo
  echo "  Update later with:  update"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  pbo_install_main "$@"
fi
