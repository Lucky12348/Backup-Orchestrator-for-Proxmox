#!/usr/bin/env bash
# shellcheck disable=SC2034,SC2154  # variables/helpers come from community-scripts build.func
_cs_boot="${COMMUNITY_SCRIPTS_CORE_DIR:-$(dirname "${BASH_SOURCE[0]}")/../../core}/core/build.func"
# shellcheck disable=SC1090
source "$_cs_boot" 2>/dev/null || source <(curl -fsSL "${COMMUNITY_SCRIPTS_CORE_URL:-https://raw.githubusercontent.com/community-scripts/core/main}/core/build.func")
# Copyright (c) 2021-2026 community-scripts ORG
# Author: Lucky12348
# License: MIT | https://github.com/community-scripts/ProxmoxVE/raw/main/LICENSE
# Source: https://github.com/Lucky12348/proxmox_backup_orchestrator
#
# DRAFT for community-scripts (DevScripts first, per their CONTRIBUTING.md).
# Not tested against the community-scripts helpers: see scripts/community/README.md.

APP="Proxmox Backup Orchestrator"
var_tags="${var_tags:-backup;proxmox}"
var_cpu="${var_cpu:-2}"
var_ram="${var_ram:-2048}"
var_disk="${var_disk:-8}"
var_os="${var_os:-debian}"
var_version="${var_version:-13}"
var_unprivileged="${var_unprivileged:-1}"

header_info "$APP"
variables
color
catch_errors

function update_script() {
  header_info
  check_container_storage
  check_container_resources
  if [[ ! -d /opt/pbo ]]; then
    msg_error "No ${APP} Installation Found!"
    exit
  fi

  if check_for_gh_release "pbo" "Lucky12348/proxmox_backup_orchestrator"; then
    msg_info "Backing up the database"
    mkdir -p /var/backups/pbo
    BACKUP_FILE="/var/backups/pbo/pbo-$(date +%Y%m%d-%H%M%S).dump"
    $STD runuser -u postgres -- pg_dump --format=custom --file="$BACKUP_FILE" pbo
    msg_ok "Backed up the database to $BACKUP_FILE"

    msg_info "Stopping Service"
    systemctl stop pbo
    msg_ok "Stopped Service"

    CLEAN_INSTALL=1 fetch_and_deploy_gh_release "pbo" "Lucky12348/proxmox_backup_orchestrator" "prebuild" "latest" "/opt/pbo" "pbo-*.tar.gz"

    msg_info "Updating ${APP}"
    rm -rf /opt/pbo/.venv
    $STD python3 -m venv /opt/pbo/.venv
    $STD /opt/pbo/.venv/bin/pip install --upgrade pip
    $STD /opt/pbo/.venv/bin/pip install -e /opt/pbo/apps/api
    cd /opt/pbo/apps/api || exit
    set -a
    # shellcheck disable=SC1091
    source /etc/pbo/pbo.env
    set +a
    $STD /opt/pbo/.venv/bin/python -c 'from app.db.init import create_tables; from app.db.migrate import run_migrations; from app.db.session import engine; create_tables(); run_migrations(engine)'
    msg_ok "Updated ${APP}"

    msg_info "Starting Service"
    systemctl start pbo
    msg_ok "Started Service"
    msg_ok "Updated successfully!"
  fi
  exit
}

start
build_container
description

msg_ok "Completed successfully!\n"
echo -e "${CREATING}${GN}${APP} setup has been successfully initialized!${CL}"
echo -e "${INFO}${YW} Open it to create the admin account:${CL}"
echo -e "${TAB}${GATEWAY}${BGN}https://${IP}${CL}"
