#!/usr/bin/env bash
# Create a throw-away Debian 13 LXC on a Proxmox VE host and install
# Proxmox Backup Orchestrator in it with scripts/install/pbo-install.sh.
#
# Run as root on the PVE host, next to a copy of pbo-install.sh (or let the
# script download it from GitHub for the given ref):
#
#   ./create-test-lxc.sh --ref public-release                   # DHCP
#   ./create-test-lxc.sh --ref main --ip 192.168.1.60/24 --gw 192.168.1.1
#   ./create-test-lxc.sh --tarball pbo-src.tar.gz               # unpushed code (git archive)
#
# Container: unprivileged, nesting=1 (needed by the systemd sandboxing of
# pbo.service), 2 vCPU, 2 GiB RAM, 8 GiB disk.
set -Eeuo pipefail

REPO="${PBO_REPO:-Lucky12348/Backup-Orchestrator-for-Proxmox}"
REF=""
TARBALL=""
CTID=""
HOSTNAME_CT="pbo-test"
STORAGE="local-lvm"
TEMPLATE_STORAGE="local"
BRIDGE="vmbr0"
IP="dhcp"
GW=""
CORES=2
MEMORY=2048
DISK_GB=8
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() {
  echo "ERROR: $*" >&2
  exit 1
}
info() { echo "==> $*"; }

usage() {
  sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  cat <<EOF

Options:
  --ref REF            branch or tag to install (default: latest release)
  --tarball FILE       install from a local source archive instead (pushed into the CT)
  --ip CIDR|dhcp       container address (default dhcp)
  --gw IP              gateway (required with a static --ip)
  --ctid ID            container id (default: next free id)
  --hostname NAME      (default ${HOSTNAME_CT})
  --storage NAME       rootfs storage (default ${STORAGE})
  --template-storage   storage holding vztmpl (default ${TEMPLATE_STORAGE})
  --bridge NAME        (default ${BRIDGE})
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref) REF="${2:-}"; shift 2 ;;
    --tarball) TARBALL="$(readlink -f "${2:-}")"; shift 2 ;;
    --ip) IP="${2:-}"; shift 2 ;;
    --gw) GW="${2:-}"; shift 2 ;;
    --ctid) CTID="${2:-}"; shift 2 ;;
    --hostname) HOSTNAME_CT="${2:-}"; shift 2 ;;
    --storage) STORAGE="${2:-}"; shift 2 ;;
    --template-storage) TEMPLATE_STORAGE="${2:-}"; shift 2 ;;
    --bridge) BRIDGE="${2:-}"; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done

[[ ${EUID} -eq 0 ]] || die "run as root on the Proxmox VE host."
command -v pct >/dev/null 2>&1 || die "pct not found: this must run on a Proxmox VE host."
if [[ "${IP}" != "dhcp" ]]; then
  [[ "${IP}" == */* ]] || die "--ip must be in CIDR form (e.g. 192.168.1.60/24)."
  [[ -n "${GW}" ]] || die "--gw is required with a static --ip."
fi
if [[ -n "${TARBALL}" ]]; then
  [[ -f "${TARBALL}" ]] || die "tarball not found: ${TARBALL}"
fi

# --- Installer ------------------------------------------------------------------------
INSTALLER="${SCRIPT_DIR}/../install/pbo-install.sh"
if [[ ! -f "${INSTALLER}" ]]; then
  INSTALLER="$(mktemp)"
  info "Downloading pbo-install.sh (${REF:-main})"
  curl -fsSL -o "${INSTALLER}" \
    "https://raw.githubusercontent.com/${REPO}/${REF:-main}/scripts/install/pbo-install.sh"
fi

# --- Template ------------------------------------------------------------------------
info "Looking for a Debian 13 template"
pveam update >/dev/null
TEMPLATE="$(pveam available --section system | awk '{print $2}' | grep -E '^debian-13-standard_.*_amd64\.tar\.(zst|xz|gz)$' | sort -V | tail -n1)"
[[ -n "${TEMPLATE}" ]] || die "no debian-13-standard template available (pveam available --section system)."
if ! pveam list "${TEMPLATE_STORAGE}" | grep -q "${TEMPLATE}"; then
  info "Downloading ${TEMPLATE}"
  pveam download "${TEMPLATE_STORAGE}" "${TEMPLATE}" >/dev/null
fi

# --- Container -----------------------------------------------------------------------
CTID="${CTID:-$(pvesh get /cluster/nextid)}"
NET="name=eth0,bridge=${BRIDGE},ip=${IP}"
if [[ "${IP}" != "dhcp" ]]; then
  NET="${NET},gw=${GW}"
fi
info "Creating CT ${CTID} (${HOSTNAME_CT})"
pct create "${CTID}" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
  --hostname "${HOSTNAME_CT}" \
  --unprivileged 1 \
  --features nesting=1 \
  --cores "${CORES}" \
  --memory "${MEMORY}" \
  --swap 512 \
  --rootfs "${STORAGE}:${DISK_GB}" \
  --net0 "${NET}" \
  --onboot 1 \
  --tags pbo-test >/dev/null
pct start "${CTID}"

info "Waiting for the network"
for _ in $(seq 1 60); do
  if pct exec "${CTID}" -- sh -c 'getent hosts deb.debian.org >/dev/null 2>&1'; then
    break
  fi
  sleep 2
done
pct exec "${CTID}" -- sh -c 'getent hosts deb.debian.org >/dev/null 2>&1' || die "no network/DNS in CT ${CTID}."

# --- Install -------------------------------------------------------------------------
pct push "${CTID}" "${INSTALLER}" /root/pbo-install.sh --perms 0755
ARGS=()
if [[ -n "${TARBALL}" ]]; then
  pct push "${CTID}" "${TARBALL}" /root/pbo-src.tar.gz
  ARGS+=(--tarball /root/pbo-src.tar.gz)
elif [[ -n "${REF}" ]]; then
  ARGS+=(--ref "${REF}")
fi
info "Running pbo-install.sh ${ARGS[*]} in CT ${CTID}"
pct exec "${CTID}" -- env PBO_REPO="${REPO}" bash /root/pbo-install.sh "${ARGS[@]}"

echo
info "Done. CT ${CTID}: pct enter ${CTID}  |  destroy: pct stop ${CTID} && pct destroy ${CTID}"
