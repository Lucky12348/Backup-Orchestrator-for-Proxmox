# Installation

The documented layout:

```text
Proxmox VE host
├── LXC "pbo" (Debian 13, unprivileged)      ← web UI + API + PostgreSQL, https://<lxc-ip>
├── host agent (/opt/pbo-agent, root)         ← USB passthrough, disk discovery, https://<vmbr0-ip>:8090
└── PBS VM (PBS ≥ 3.3)                        ← driven through its REST API, no agent needed
```

- **App**: one native LXC, no Docker. Code in `/opt/pbo`, configuration in
  `/etc/pbo` (`pbo.env`, `tls/`), PostgreSQL listening on localhost only,
  `pbo.service` = uvicorn serving HTTPS on port 443 as the unprivileged `pbo`
  user.
- **Host agent**: installed on the Proxmox VE host with one command shown by
  the app; it registers itself with a single-use pairing code.
- **PBS**: no agent with `PBS_MODE=api` (default for new installs, see
  [PBS_API.md](PBS_API.md)).

The previous Docker Compose layout is still supported for existing deployments:
[INSTALLATION_DOCKER_LEGACY.md](INSTALLATION_DOCKER_LEGACY.md).

## 1. Prerequisites

- Proxmox VE 8 or 9, with a PBS **3.3 or newer** VM on the same node (removable
  datastores). The external USB disk is plugged into the Proxmox host.
- Internet access from the LXC (Debian packages, PyPI, GitHub) and from the
  Proxmox host (Debian packages, PyPI) during installation.
- Network: LXC → PVE API `8006`, LXC → PBS API `8007`, LXC → host agent `8090`,
  host agent → LXC `443` (heartbeat and disk reports).

## 2. Install the app (LXC)

### Option A — community-scripts (once published)

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/ct/pbo.sh)"
```

Drafts live in [`scripts/community/`](../scripts/community/README.md) until they
are accepted.

### Option B — any Debian 13 LXC

Create an **unprivileged** Debian 13 container with the **nesting** feature
(`pbo.service` uses systemd sandboxing, which needs it), 2 vCPU, 2 GiB RAM,
8 GiB disk, then inside it, as root:

```bash
curl -fsSLO https://raw.githubusercontent.com/Lucky12348/Backup-Orchestrator-for-Proxmox/main/scripts/install/pbo-install.sh
bash pbo-install.sh                # latest GitHub release
# bash pbo-install.sh --ref main   # a branch or tag (the web UI is built in the CT with a temporary Node.js)
```

The script installs the packages, creates the `pbo` system user, fetches the
code into `/opt/pbo` (release archive with a pre-built UI, or a `git clone`
of `--ref`), creates the Python environment, the PostgreSQL database and user
(random password), `/etc/pbo/pbo.env` (mode 0600: `AUTH_SECRET_KEY`,
`DATABASE_URL`…), a self-signed certificate in `/etc/pbo/tls`, and
`pbo.service`. It ends with:

```text
 ok Proxmox Backup Orchestrator v1.0.0 is running.

  Open https://192.168.1.60 to create the admin account.
```

### Option C — test container from the Proxmox host (development)

```bash
# On the PVE host, from a checkout (or a copy of scripts/):
scripts/dev/create-test-lxc.sh --ref public-release                   # DHCP
scripts/dev/create-test-lxc.sh --ref main --ip 192.168.1.60/24 --gw 192.168.1.1
```

It creates the CT (2 vCPU, 2 GiB, 8 GiB, unprivileged, nesting), pushes
`pbo-install.sh` and runs it with `--ref`. `--ref` must exist on GitHub; to
test unpushed code, build an archive and pass `--tarball`:

```bash
git archive --prefix=pbo/ -o /tmp/pbo-src.tar.gz HEAD      # on the dev machine
scripts/dev/create-test-lxc.sh --tarball /tmp/pbo-src.tar.gz  # on the PVE host
```

## 3. First run

1. Open `https://<lxc-ip>` (accept the self-signed certificate; its SHA-256
   fingerprint is printed at the end of the install and shown by
   `openssl x509 -in /etc/pbo/tls/cert.pem -noout -fingerprint -sha256`).
2. Create the administrator account (first-run screen).
3. Configure Proxmox VE and PBS. Until the setup wizard screens land, use the
   API with the session cookie, e.g. from the LXC:

   ```bash
   B=https://127.0.0.1; C=/root/pbo.cookies
   curl -sk -c $C -X POST $B/api/v1/auth/token --data-urlencode username=admin --data-urlencode 'password=…'
   # Probe first: without a fingerprint the answer only contains the certificate fingerprint (trust on first use).
   curl -sk -b $C -H 'Content-Type: application/json' -X POST $B/api/v1/setup/proxmox/test \
     -d '{"api_url":"https://192.168.1.10:8006","token_id":"pbo@pve!pbo","token_secret":"…"}'
   curl -sk -b $C -H 'Content-Type: application/json' -X PUT $B/api/v1/setup/proxmox \
     -d '{"api_url":"https://192.168.1.10:8006","token_id":"pbo@pve!pbo","token_secret":"…","fingerprint":"AB:CD:…","node_name":"pve","pbs_vm_id":110}'
   curl -sk -b $C -H 'Content-Type: application/json' -X PUT $B/api/v1/setup/pbs \
     -d '{"api_url":"https://192.168.1.20:8007","token_id":"pbo@pbs!pbo","token_secret":"…","fingerprint":"12:34:…","datastore":"main","mode":"api"}'
   ```

   Token privileges: read-only inventory on PVE (`PVEAuditor` on `/`); on PBS
   see [PBS_API.md](PBS_API.md#recommended-pbs-token).

## 4. Install the host agent (one line)

The agent runs on the **Proxmox VE host** (root: it drives `qm set` for USB
passthrough and inspects disks).

1. In the app: **Integrations → Agent installation → Generate install command**.
   The app shows two commands with a single-use code (valid 15 minutes), and
   the fingerprint of its certificate:

   ```bash
   curl -fskSL -o pbo-agent-install.sh https://192.168.1.60/agent/install.sh
   bash pbo-agent-install.sh --code K7QF-3MZP
   ```

   The block is selected as a whole on click; there is deliberately no "Copy"
   button and no `bash -c "$(curl …)"` one-liner: content blockers such as
   uBlock Origin flag that pattern written to the clipboard as a "ClickFix"
   attack, and downloading first lets you read the script before running it.

2. Run both lines as root in the Proxmox host shell. The script:
   - checks it runs on a PVE host (`pveversion`);
   - prints the fingerprint of the app certificate it sees and asks you to
     compare it with the one shown in the app (`-k` is only used for this
     first download; `--app-fingerprint <fp>` skips the prompt, `--yes`
     accepts blindly);
   - downloads the agent **from the app** (same version) over a pinned
     connection and installs it in `/opt/pbo-agent` (Python venv);
   - generates a self-signed certificate (`/etc/pbo-agent/tls`) and listens
     **only on the IPv4 address of `vmbr0`** (`--bridge`/`--listen-ip` to
     change), port 8090 (`--port`);
   - registers with the pairing code: the app records the agent URL and
     certificate fingerprint and returns two tokens, written to
     `/etc/pbo-agent/agent.env` (0600):
     - `AGENT_SERVER_TOKEN`: app → agent (the app stores it encrypted);
     - `AGENT_CALLBACK_TOKEN`: agent → app (the app stores only its hash);
   - installs `pbo-agent.service` and `pbo-agent-report.timer` (heartbeat +
     disk report every 2 minutes) and sends a first report.

From then on, every app → agent call pins the agent certificate fingerprint,
and every agent → app call pins the app certificate.

Re-running the command with a new code re-registers the agent (new tokens, the
previous registration is revoked). **Revoke** in the same card disables an
agent; the app then falls back to the static `HOST_AGENT_*` settings if any.

Restrict port 8090 to the LXC address in the Proxmox firewall
([SECURITY.md](SECURITY.md#proxmox-firewall-hostfw-for-port-8090)).

## 5. Updates

Inside the LXC:

```bash
update                 # latest release
update --ref v1.2.0    # a given tag (or a branch)
```

`update` (= `/usr/local/bin/pbo-update`, from `scripts/install/pbo-update.sh`)
dumps the database to `/var/backups/pbo/` (5 kept), fetches the new version
into `/opt/pbo.new`, stops the service, swaps directories (previous code kept
in `/opt/pbo.previous`), rebuilds the Python environment, runs the migrations,
restarts and waits for `/health`. If migrations or `/health` fail, the
previous code **and** the database dump are restored automatically.

The host agent is updated by re-running its install command (new pairing code):
it downloads the agent shipped with the running app.

## 6. Checks and troubleshooting

```bash
systemctl status pbo.service                   # LXC
journalctl -u pbo.service -n 100
curl -sk https://127.0.0.1/health

systemctl status pbo-agent.service             # Proxmox host
journalctl -u pbo-agent-report.service -n 50
```

- **`pbo.service` fails with `226/NAMESPACE`**: enable nesting on the CT
  (`pct set <ctid> --features nesting=1`, then restart it).
- **`pbo.service` refuses to start: "database 'pbo' uses encoding SQL_ASCII"**:
  PostgreSQL was initialised under a locale missing from the container (SSH
  forwarding e.g. `LANG=en_US.UTF-8`). The installers now force
  `LANG=C.UTF-8` and create the database with `-E UTF8 --locale=C.UTF-8 -T
  template0`; re-running `pbo-install.sh` recreates an **empty** SQL_ASCII
  database in UTF8. A database that already holds data is not touched: the
  error message (also printed by `python -m app.db.preflight`, the unit's
  `ExecStartPre`) gives the dump / recreate / restore commands.
- **Install script: "this is not a Proxmox VE host"**: it must run on the PVE
  host itself, not in a VM or CT.
- **"Invalid or expired pairing code"**: codes are single use and last 15
  minutes; generate a new one. Ten failures within 15 minutes from one address
  block registration for 15 minutes.
- **"TLS fingerprint mismatch"** (agent side): the app certificate changed
  (e.g. `/etc/pbo/tls` regenerated). Re-run the install command.
- **Agent shown offline**: `journalctl -u pbo-agent-report.service` on the host;
  the host must reach `https://<lxc-ip>:443`.
- **Login loops back to the login page**: the session cookie is `Secure`; use
  `https://`.

## Files

| Path | Content |
|---|---|
| `/opt/pbo` | code (`VERSION`, `apps/`, `scripts/`), Python env `.venv` |
| `/etc/pbo/pbo.env` | bootstrap settings (0600). **Never change `AUTH_SECRET_KEY`**: it encrypts the secrets stored in the database. |
| `/etc/pbo/tls/` | app certificate and key |
| `/var/backups/pbo/` | database dumps made by `update` |
| `/opt/pbo-agent`, `/etc/pbo-agent/` | host agent code, venv, `agent.env`, certificate |

Everything else (Proxmox/PBS connections, notifications, agents) is stored in
the database and edited from the UI/API.
