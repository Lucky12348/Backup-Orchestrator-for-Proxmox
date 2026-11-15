#!/usr/bin/env bash
# shellcheck disable=SC2154  # helpers and $STD come from community-scripts install.func

# Copyright (c) 2021-2026 community-scripts ORG
# Author: Lucky12348
# License: MIT | https://github.com/community-scripts/ProxmoxVE/raw/main/LICENSE
# Source: https://github.com/Lucky12348/Backup-Orchestrator-for-Proxmox
#
# DRAFT for community-scripts. Mirrors scripts/install/pbo-install.sh (the
# reference installer) with the community-scripts helpers.

# The SSH client may forward a locale missing from the container (e.g.
# LANG=en_US.UTF-8): PostgreSQL would initialise its cluster in C/SQL_ASCII.
export LANG=C.UTF-8 LC_ALL=C.UTF-8

# shellcheck disable=SC1091
source /dev/stdin <<<"$FUNCTIONS_FILE_PATH"
color
verb_ip6
catch_errors
setting_up_container
network_check
update_os

msg_info "Installing Dependencies"
$STD apt install -y \
  openssl \
  python3-venv
msg_ok "Installed Dependencies"

PG_VERSION="17" setup_postgresql

msg_info "Creating the database (UTF8)"
# Created explicitly rather than with setup_postgresql_db: the encoding must be
# UTF8 whatever the cluster default (template0 accepts any encoding).
PG_DB_NAME="pbo"
PG_DB_USER="pbo"
PG_DB_PASS="$(openssl rand -hex 24)"
$STD runuser -u postgres -- psql -c "CREATE ROLE ${PG_DB_USER} WITH LOGIN PASSWORD '${PG_DB_PASS}'"
$STD runuser -u postgres -- createdb -O "$PG_DB_USER" -E UTF8 --locale=C.UTF-8 -T template0 "$PG_DB_NAME"
msg_ok "Created the database (UTF8)"
fetch_and_deploy_gh_release "pbo" "Lucky12348/Backup-Orchestrator-for-Proxmox" "prebuild" "latest" "/opt/pbo" "pbo-*.tar.gz"

msg_info "Setting up ${APPLICATION}"
useradd --system --home-dir /opt/pbo --no-create-home --shell /usr/sbin/nologin pbo
$STD python3 -m venv /opt/pbo/.venv
$STD /opt/pbo/.venv/bin/pip install --upgrade pip
$STD /opt/pbo/.venv/bin/pip install -e /opt/pbo/apps/api
mkdir -p /etc/pbo/tls /var/backups/pbo
IP_ADDR="$(hostname -I | awk '{print $1}')"
$STD openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 \
  -subj "/CN=$(hostname -s)" \
  -addext "subjectAltName=DNS:$(hostname -s),DNS:localhost,IP:${IP_ADDR},IP:127.0.0.1" \
  -keyout /etc/pbo/tls/key.pem -out /etc/pbo/tls/cert.pem
chown -R root:pbo /etc/pbo/tls
chmod 0750 /etc/pbo/tls
chmod 0640 /etc/pbo/tls/key.pem
# PostgreSQL listens on localhost only (Debian default, enforced).
PG_CONF="$(find /etc/postgresql -name postgresql.conf -path '*/main/*' | sort -V | tail -n1)"
sed -i -E "s/^#?\s*listen_addresses\s*=.*/listen_addresses = 'localhost'/" "$PG_CONF"
systemctl restart postgresql
cat <<EOF >/etc/pbo/pbo.env
APP_ENV=production
AUTH_SECRET_KEY=$(openssl rand -hex 32)
DATABASE_URL=postgresql+psycopg://${PG_DB_USER}:${PG_DB_PASS}@127.0.0.1:5432/${PG_DB_NAME}
AUTH_COOKIE_SECURE=true
WEB_DIST_DIR=/opt/pbo/apps/web/dist
PBO_AGENT_SOURCE_DIR=/opt/pbo/apps/agent
PBO_TLS_CERT_FILE=/etc/pbo/tls/cert.pem
EOF
chmod 0600 /etc/pbo/pbo.env
msg_ok "Set up ${APPLICATION}"

msg_info "Creating Service"
cat <<EOF >/etc/systemd/system/pbo.service
[Unit]
Description=Proxmox Backup Orchestrator (web UI + API)
Wants=network-online.target
After=network-online.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=pbo
Group=pbo
WorkingDirectory=/opt/pbo/apps/api
EnvironmentFile=/etc/pbo/pbo.env
Environment=PYTHONDONTWRITEBYTECODE=1
# Clear message (no traceback) when the database is not UTF8.
ExecStartPre=/opt/pbo/.venv/bin/python -m app.db.preflight
ExecStart=/opt/pbo/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 443 --ssl-certfile /etc/pbo/tls/cert.pem --ssl-keyfile /etc/pbo/tls/key.pem --no-server-header
Restart=on-failure
RestartSec=5
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictNamespaces=yes
LockPersonality=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
systemctl enable -q --now pbo
msg_ok "Created Service"

motd_ssh
customize
cleanup_lxc
