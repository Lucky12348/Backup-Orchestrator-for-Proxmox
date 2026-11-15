# Installation (legacy: Docker Compose app VM)

> **Legacy path.** The documented install is now a native Debian 13 LXC +
> one-line host agent: see [INSTALLATION.md](INSTALLATION.md). This Docker
> Compose / static-token layout keeps working (existing deployments, `.env`
> imported once into the database) but is no longer the recommended path.

This guide installs the production layout:

- app VM `backupOrchestrator` running Docker Compose: API, Web UI, Postgres
- Proxmox host root agent on port `8090`
- PBS VM root agent on port `8091`
- external USB disk passed from Proxmox host to PBS VM

Do not use `code_secret`. Use strong random values from `openssl rand -hex 32`.

## 1. Prerequisites

- A Proxmox host with firewall enabled.
- A PBS VM running on that Proxmox host.
- An app VM named `backupOrchestrator` with Docker and Docker Compose.
- A dedicated external USB disk for removable PBS backups.
- Network reachability:
  - app VM to Proxmox API `8006`
  - app VM to PBS API `8007`
  - app VM to host agent `8090`
  - app VM to PBS agent `8091`

Record these values before installation:

```text
APP_VM_IP=
PROXMOX_HOST_IP=
PBS_VM_IP=
PBS_VM_ID=
PVE_NODE_NAME=
MAIN_PBS_DATASTORE=<your-source-datastore>
```

`MAIN_PBS_DATASTORE` is the exact name of your existing source PBS datastore (the one
external disks copy from), whatever you named it. List it on the PBS VM with
`proxmox-backup-manager datastore list`. No name is assumed anywhere: you will set it as
`PBS_DATASTORE` on the app VM and on the PBS agent.

## 2. Install the App VM

Clone the repository on `backupOrchestrator`:

```bash
git clone <repo-url> /opt/proxmox_backup_orchestrator
cd /opt/proxmox_backup_orchestrator
cp .env.example .env
chmod 600 .env
```

Edit `.env` and keep only the app VM bootstrap values active for Docker Compose:

```env
APP_ENV=production
API_PORT=8000
MAINTENANCE_TIMEOUT_SECONDS=300
APP_MAINTENANCE_AGENT_BASE_URL=http://host.docker.internal:8092
APP_MAINTENANCE_AGENT_HOST=127.0.0.1
APP_MAINTENANCE_AGENT_PORT=8092
APP_MAINTENANCE_AGENT_TOKEN=<app-maintenance-agent-token>
APP_REPO_PATH=/opt/proxmox_backup_orchestrator
APP_COMPOSE_FILE=infra/docker/docker-compose.yml
APP_MAINTENANCE_TIMEOUT_SECONDS=300

POSTGRES_DB=proxmox_backup_orchestrator
POSTGRES_USER=pbo
POSTGRES_PASSWORD=<random>
DATABASE_URL=postgresql+psycopg://pbo:<random>@db:5432/proxmox_backup_orchestrator

AUTH_SECRET_KEY=<random>
# Session cookie is Secure: keep true behind HTTPS. false only for plain HTTP on a trusted LAN.
AUTH_COOKIE_SECURE=true
```

Proxmox VE, PBS, agent URLs/tokens and ntfy are **not** environment variables
anymore: they are stored in the database (secrets encrypted with a key derived
from `AUTH_SECRET_KEY`) and set from the first-run setup (API:
`/api/v1/setup/proxmox`, `/api/v1/setup/pbs`, `/api/v1/settings`). The
administrator account is created on the first-run screen.

Upgrading an install that still has those values in `.env`
(`PVE_*`, `PBS_*`, `HOST_AGENT_*`, `PBS_AGENT_*`, `NTFY_*`, `NOTIFY_ON_*`,
`AUTH_USERNAME` / `AUTH_PASSWORD_HASH`…): they are imported into the database
**once**, on the first start, then ignored. See the commented block in
`.env.example`. A legacy `AUTH_PASSWORD_HASH` must still be Compose-escaped
(`$$2b$$12$$...`) to be imported.

Generate JWT and agent tokens:

```bash
openssl rand -hex 32  # AUTH_SECRET_KEY
openssl rand -hex 32  # APP_MAINTENANCE_AGENT_TOKEN
openssl rand -hex 32  # HOST_AGENT_TOKEN and Proxmox AGENT_SERVER_TOKEN
openssl rand -hex 32  # PBS_AGENT_TOKEN and PBS AGENT_SERVER_TOKEN
```

Install the local App VM maintenance agent before using Settings > Maintenance:

```bash
cd /opt/proxmox_backup_orchestrator/apps/app-maintenance-agent
python3 -m venv .venv
.venv/bin/pip install -e .
cp deploy/systemd/proxmox-backup-orchestrator-app-maintenance-agent.service \
  /etc/systemd/system/proxmox-backup-orchestrator-app-maintenance-agent.service
systemctl daemon-reload
systemctl enable --now proxmox-backup-orchestrator-app-maintenance-agent.service
systemctl status proxmox-backup-orchestrator-app-maintenance-agent.service
```

Health check on the App VM:

```bash
curl -H "X-Agent-Token: <app-maintenance-agent-token>" http://127.0.0.1:8092/health
```

Start the app stack (run from the repository root; `--env-file .env` is
required — see the note in `docs/setup.md` about why omitting it silently
reverts settings like notifications to their defaults):

```bash
docker compose --env-file .env -f infra/docker/docker-compose.yml up -d --build
docker compose --env-file .env -f infra/docker/docker-compose.yml ps
```

Login and protected API check:

```bash
# First run only: create the administrator (or use the first-run screen in the UI).
curl -sS -c /tmp/pbo.cookies -X POST https://<app-host>/api/v1/setup/admin \
  -H "Content-Type: application/json" \
  -d '{"username": "<admin-username>", "password": "<admin-password>"}'

# Later logins (session = HttpOnly cookie, kept in the cookie jar):
curl -sS -c /tmp/pbo.cookies -X POST https://<app-host>/api/v1/auth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "username=<admin-username>" \
  --data-urlencode "password=<admin-password>"
curl -sS -b /tmp/pbo.cookies https://<app-host>/api/v1/disks/preferred
```

Expected result: the login request returns `{"username": ...}` and sets the session cookie; the preferred disks request returns a JSON array (empty is valid before any preferred disk has been saved).

## 3. Install the Proxmox Host Agent

On the Proxmox host:

```bash
mkdir -p /opt/proxmox-backup-orchestrator-agent
rsync -a apps/agent/ /opt/proxmox-backup-orchestrator-agent/
cd /opt/proxmox-backup-orchestrator-agent
python3 -m venv .venv
.venv/bin/pip install -e .
```

Create `/opt/proxmox-backup-orchestrator-agent/.env`:

```env
AGENT_API_BASE_URL=http://<app-vm-ip>:8000/api/v1
AGENT_HOSTNAME=<proxmox-hostname>
AGENT_VERSION=0.1.0
AGENT_TIMEOUT_SECONDS=10
AGENT_INCLUDE_NON_USB_CANDIDATES=false
AGENT_EXPORT_TIMEOUT_SECONDS=7200
AGENT_DATASTORE_CREATE_TIMEOUT_SECONDS=14400
AGENT_SERVER_HOST=0.0.0.0
AGENT_SERVER_PORT=8090
AGENT_SERVER_TOKEN=<same-value-as-HOST_AGENT_TOKEN>
AGENT_REPO_PATH=/opt/proxmox-backup-orchestrator-agent
AGENT_MAINTENANCE_TIMEOUT_SECONDS=120
```

Protect it:

```bash
chown root:root /opt/proxmox-backup-orchestrator-agent/.env
chmod 600 /opt/proxmox-backup-orchestrator-agent/.env
```

Install the systemd service as `proxmox-backup-orchestrator-agent-http.service`. If your repo service file has another name, copy it under this production name:

```bash
cp apps/agent/deploy/systemd/proxmox-backup-orchestrator-agent-api.service \
  /etc/systemd/system/proxmox-backup-orchestrator-agent-http.service
systemctl daemon-reload
systemctl enable --now proxmox-backup-orchestrator-agent-http.service
systemctl status proxmox-backup-orchestrator-agent-http.service
```

Health check from the app VM:

```bash
curl -H "X-Agent-Token: <host-agent-token>" http://<proxmox-host>:8090/health
```

## 4. Install the PBS Agent

On the PBS VM:

```bash
mkdir -p /opt/proxmox-backup-orchestrator-pbs-agent
rsync -a apps/agent/ /opt/proxmox-backup-orchestrator-pbs-agent/
cd /opt/proxmox-backup-orchestrator-pbs-agent
python3 -m venv .venv
.venv/bin/pip install -e .
```

Create `/opt/proxmox-backup-orchestrator-pbs-agent/.env`:

```env
AGENT_API_BASE_URL=http://<app-vm-ip>:8000/api/v1
AGENT_HOSTNAME=<pbs-hostname>
AGENT_VERSION=0.1.0
AGENT_TIMEOUT_SECONDS=10
AGENT_INCLUDE_NON_USB_CANDIDATES=false
AGENT_EXPORT_TIMEOUT_SECONDS=7200
AGENT_DATASTORE_CREATE_TIMEOUT_SECONDS=14400
AGENT_SERVER_HOST=0.0.0.0
AGENT_SERVER_PORT=8091
AGENT_SERVER_TOKEN=<same-value-as-PBS_AGENT_TOKEN>
AGENT_REPO_PATH=/opt/proxmox-backup-orchestrator-pbs-agent
AGENT_MAINTENANCE_TIMEOUT_SECONDS=120
PBS_DATASTORE=<your-source-datastore>
```

`PBS_DATASTORE` must match the source datastore (same value as on the app VM). At startup the
agent checks that it exists on this PBS, logs an error listing the available datastores if it
does not, and reports the result under `source_datastore` in `GET /health`.

Protect it:

```bash
chown root:root /opt/proxmox-backup-orchestrator-pbs-agent/.env
chmod 600 /opt/proxmox-backup-orchestrator-pbs-agent/.env
```

Install the systemd service:

```bash
cp apps/agent/deploy/systemd/proxmox-backup-orchestrator-pbs-agent-api.service \
  /etc/systemd/system/proxmox-backup-orchestrator-pbs-agent-http.service
sed -i 's#/opt/proxmox-backup-orchestrator-agent#/opt/proxmox-backup-orchestrator-pbs-agent#g' \
  /etc/systemd/system/proxmox-backup-orchestrator-pbs-agent-http.service
systemctl daemon-reload
systemctl enable --now proxmox-backup-orchestrator-pbs-agent-http.service
systemctl status proxmox-backup-orchestrator-pbs-agent-http.service
```

Health check from the app VM:

```bash
curl -H "X-Agent-Token: <pbs-agent-token>" http://<pbs-vm>:8091/health
```

The response must contain `"source_datastore": {"status": "ok", ...}`.

## 5. Firewall Rules

Limit agent ports to the app VM IP.

On Proxmox, add a host firewall rule for TCP `8090` from `<app-vm-ip>` only. See [Security](SECURITY.md) for a `host.fw` example.

On PBS, configure `nftables` to allow TCP `8091` from `<app-vm-ip>` only. See [Security](SECURITY.md) for a full example.

## 6. Final Checks

From the app VM:

```bash
# The session is an HttpOnly cookie: keep it in a cookie jar.
curl -sS -c /tmp/pbo.cookies -X POST https://<app-host>/api/v1/auth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "username=<admin-username>" \
  --data-urlencode "password=<admin-password>"
curl -sS -b /tmp/pbo.cookies https://<app-host>/api/v1/disks/preferred
curl -H "X-Agent-Token: <host-agent-token>" http://<proxmox-host>:8090/health
curl -H "X-Agent-Token: <pbs-agent-token>" http://<pbs-vm>:8091/health
```

Open the UI:

```text
https://<app-host>/        # TLS reverse proxy in front of port 8000 (recommended)
http://<app-vm-ip>:8000    # plain HTTP: only with AUTH_COOKIE_SECURE=false
```

The API serves the web UI on the same port (`8000`); there is no separate web
container anymore. On a fresh database the first screen creates the
administrator account.

## Troubleshooting

**Login succeeds but the UI goes straight back to the login screen**

The session cookie is `Secure`; browsers drop it over plain `http://<ip>`. Use
HTTPS (reverse proxy) or set `AUTH_COOKIE_SECURE=false` on a trusted LAN.

**Legacy `AUTH_PASSWORD_HASH` was not imported (upgrade)**

If Docker Compose receives an unescaped bcrypt hash, it may interpolate `$2b`, `$12`, or later segments; the API then sees a truncated hash, logs a warning and does not import it. Either fix the value with `$$` escaping before the first start, or create the administrator from the first-run screen.

**`code_secret` still works**

Remove it from every environment file, shell profile, service override, and secret store. This deployment uses database accounts, `AUTH_SECRET_KEY`, and agent tokens. Restart the app and both agents after cleanup.

**Agent health returns 401**

The `X-Agent-Token` header does not match `AGENT_SERVER_TOKEN` on that agent. Compare `HOST_AGENT_TOKEN` with the Proxmox agent token, and `PBS_AGENT_TOKEN` with the PBS agent token. Restart the affected service after editing `.env`.

**PBS disk is visible but not mounted**

Confirm the disk is attached to the PBS VM, then inspect the PBS agent logs:

```bash
journalctl -u proxmox-backup-orchestrator-pbs-agent-http.service -n 200 --no-pager
```

The first dedicated preparation may need destructive confirmation. Reuse mode should mount the existing PBS datastore without formatting.

**USB passthrough is still attached**

Check the PBS VM config on the Proxmox host:

```bash
qm config <pbs-vm-id> | grep '^usb'
```

Run safe eject again from the UI. If necessary, detach the stale slot manually only after confirming the PBS datastore is unmounted.

**External datastore reuse vs first format**

First preparation of a new dedicated disk can format the disk. Later runs should reuse the existing filesystem and PBS datastore unless force format is explicitly requested.

**Source datastore renamed or migrated (`source_datastore.status` is `missing`)**

The agent `/health` response and its log list the datastores that really exist on PBS. Update `PBS_DATASTORE` in the PBS agent `.env`, then restart the agent service (`systemctl restart proxmox-backup-orchestrator-pbs-agent-http.service`). Update `PBS_DATASTORE` in the app VM `.env` too and recreate the stack with `docker compose --env-file .env -f infra/docker/docker-compose.yml up --build -d`.
