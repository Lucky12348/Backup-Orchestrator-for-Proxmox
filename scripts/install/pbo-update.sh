#!/usr/bin/env bash
# Proxmox Backup Orchestrator - update, installed as `update` (and `pbo-update`).
#
#   update                  # latest GitHub release
#   update --ref v1.2.0     # a given tag (or a branch: the web UI is built here)
#   update --tarball x.tgz  # a local source archive (dev)
#   update --force          # reinstall even if already on that version
#
# Steps: database backup (pg_dump) -> fetch the new version into /opt/pbo.new
# -> stop -> swap directories -> Python env -> migrations -> start -> /health.
# If migrations or /health fail: previous code AND database are restored.
set -Eeuo pipefail
# See pbo-install.sh: a locale missing from the container breaks PostgreSQL tools.
export LANG=C.UTF-8 LC_ALL=C.UTF-8

INSTALL_LIB="${PBO_INSTALL_LIB:-/opt/pbo/scripts/install/pbo-install.sh}"
[[ -f "${INSTALL_LIB}" ]] || {
  echo "ERROR: ${INSTALL_LIB} not found: is Proxmox Backup Orchestrator installed?" >&2
  exit 1
}
# shellcheck source=pbo-install.sh
source "${INSTALL_LIB}"

FORCE=0
KEEP_BACKUPS=5
STAGE_DIR="${PBO_DIR}.new"
PREVIOUS_DIR="${PBO_DIR}.previous"
BACKUP_FILE=""
SWAPPED=0

pbo_update_parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --ref) PBO_REF="${2:-}"; shift 2 ;;
      --tarball) PBO_TARBALL="$(readlink -f "${2:-}")"; shift 2 ;;
      --repo) PBO_REPO="${2:-}"; shift 2 ;;
      --force) FORCE=1; shift ;;
      -h | --help)
        sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
      *) pbo_die "unknown option: $1 (see --help)" ;;
    esac
  done
}

pbo_backup_database() {
  local version
  version="$(head -n1 "${PBO_DIR}/VERSION" 2>/dev/null || echo unknown)"
  version="${version//[^A-Za-z0-9._-]/_}"
  install -d -m 0700 "${PBO_BACKUP_DIR}"
  BACKUP_FILE="${PBO_BACKUP_DIR}/pbo-$(date +%Y%m%d-%H%M%S)-${version}.dump"
  pbo_info "Backing up the database to ${BACKUP_FILE}"
  runuser -u postgres -- pg_dump --format=custom "${PBO_DB}" >"${BACKUP_FILE}"
  chmod 0600 "${BACKUP_FILE}"
  # Keep the most recent dumps only.
  find "${PBO_BACKUP_DIR}" -maxdepth 1 -name 'pbo-*.dump' -printf '%T@ %p\n' | sort -rn |
    tail -n +$((KEEP_BACKUPS + 1)) | cut -d' ' -f2- | xargs -r rm -f
  pbo_ok "database backed up"
}

pbo_run_migrations() {
  pbo_info "Applying database migrations"
  (
    set -a
    # shellcheck disable=SC1090
    . "${PBO_ENV}"
    set +a
    cd "${PBO_DIR}/apps/api"
    PYTHONDONTWRITEBYTECODE=1 "${PBO_DIR}/.venv/bin/python" -c '
from app.db.init import create_tables
from app.db.migrate import run_migrations
from app.db.session import engine
create_tables()
run_migrations(engine)
'
  )
}

# Phase 5g: bring the agents to the build this version ships, when
# "Update agents automatically" is on (Settings). Never fails the app update.
pbo_update_agents() {
  pbo_info "Agents: checking their version"
  (
    set -a
    # shellcheck disable=SC1090
    . "${PBO_ENV}"
    set +a
    cd "${PBO_DIR}/apps/api"
    PYTHONDONTWRITEBYTECODE=1 timeout 900 "${PBO_DIR}/.venv/bin/python" -m app.agent_update_cli
  ) || pbo_warn "agent update did not complete: see Integrations > Agents (the app itself is updated)"
}

pbo_rollback() {
  local reason="$1"
  pbo_warn "update failed (${reason}): rolling back"
  systemctl stop pbo.service || true
  if ((SWAPPED)) && [[ -d "${PREVIOUS_DIR}" ]]; then
    rm -rf "${PBO_DIR}.failed"
    mv "${PBO_DIR}" "${PBO_DIR}.failed"
    mv "${PREVIOUS_DIR}" "${PBO_DIR}"
    pbo_info "previous code restored (failed version kept in ${PBO_DIR}.failed)"
  fi
  if [[ -n "${BACKUP_FILE}" && -f "${BACKUP_FILE}" ]]; then
    pbo_info "Restoring the database from ${BACKUP_FILE}"
    runuser -u postgres -- dropdb --if-exists "${PBO_DB}"
    runuser -u postgres -- createdb -O "${PBO_USER}" -E UTF8 --locale=C.UTF-8 -T template0 "${PBO_DB}"
    runuser -u postgres -- pg_restore --dbname "${PBO_DB}" --no-owner --role "${PBO_USER}" <"${BACKUP_FILE}" ||
      pbo_warn "pg_restore reported errors, check the database"
  fi
  # The functions loaded at start come from the previous version: its unit.
  pbo_write_service
  systemctl start pbo.service
  if pbo_wait_healthy 120; then
    pbo_die "update rolled back; still running $(cat "${PBO_DIR}/VERSION"). Logs: journalctl -u pbo.service"
  fi
  pbo_die "rollback did not bring the app back: journalctl -u pbo.service -n 80"
}

pbo_update_main() {
  pbo_update_parse_args "$@"
  pbo_require_root
  local current target
  current="$(cat "${PBO_DIR}/VERSION" 2>/dev/null || echo unknown)"
  pbo_resolve_ref
  target="${PBO_REF:-local}"
  if [[ -z "${PBO_TARBALL}" && "${target}" == "${current}" && ${FORCE} -eq 0 ]]; then
    pbo_ok "already up to date (${current})."
    exit 0
  fi
  pbo_info "Updating ${current} -> ${target}"
  # The app refuses a non-UTF8 database: fix an empty one, stop on a full one.
  if declare -F pbo_ensure_utf8_database >/dev/null; then
    pbo_ensure_utf8_database
  fi

  rm -rf "${STAGE_DIR}"
  pbo_fetch_source "${STAGE_DIR}"
  pbo_build_web "${STAGE_DIR}"

  pbo_backup_database

  trap 'pbo_rollback "unexpected error on line ${LINENO}"' ERR
  pbo_info "Stopping pbo.service"
  systemctl stop pbo.service
  rm -rf "${PREVIOUS_DIR}"
  mv "${PBO_DIR}" "${PREVIOUS_DIR}"
  mv "${STAGE_DIR}" "${PBO_DIR}"
  SWAPPED=1

  pbo_setup_venv "${PBO_DIR}"
  chown -R root:root "${PBO_DIR}"
  chmod -R go-w "${PBO_DIR}"
  trap - ERR

  pbo_run_migrations || pbo_rollback "migrations"
  # Refresh the unit and the `update` command from the new version.
  # shellcheck source=pbo-install.sh
  source "${PBO_DIR}/scripts/install/pbo-install.sh"
  pbo_write_service
  pbo_install_update_command

  systemctl start pbo.service
  pbo_wait_healthy 120 || pbo_rollback "/health did not answer"

  pbo_ok "updated to $(cat "${PBO_DIR}/VERSION") (previous code kept in ${PREVIOUS_DIR}, database dump ${BACKUP_FILE})."
  pbo_update_agents
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  pbo_update_main "$@"
fi
