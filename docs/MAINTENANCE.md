# Maintenance / Updates

## LXC install (documented path)

Inside the container, run `update` (see
[INSTALLATION.md](INSTALLATION.md#5-updates)). It runs these steps, and on
failure rolls back both the code and the database:

1. Dump the database.
2. Stage the new version.
3. Swap it in and run the migrations.
4. Check `/health`.

The host agent installed with the one-line command updates itself to the
agent shipped with the app: pinned download, SHA-256 checked, atomic switch,
automatic rollback (see
[INSTALLATION.md](INSTALLATION.md#automatic-agent-update-phase-5g)). An agent
installed before this feature needs its install command one last time.
Settings › Agents turns the automatic update on or off.

**Settings › About** shows:

- the installed PBO version (the `VERSION` file the installer writes);
- the agents' versions;
- whether a newer release exists. PBO asks GitHub for it, at most every 6
  hours; if the check fails, it says "unknown".

## The old update center was removed (Phase 5j)

The Settings page used to run `git pull` + `docker compose up --build` through
an "app maintenance agent", and `git pull` in each agent's checkout. It is
gone, along with everything that depended on it:

- the `/api/v1/maintenance/*` routes;
- the agents' `/maintenance/check|update` routes and their
  `AGENT_REPO_PATH` / `AGENT_HTTP_SERVICE_NAME` / `AGENT_MAINTENANCE_TIMEOUT_SECONDS`
  maintenance settings. `AGENT_REPO_PATH` is still read by the self-update to
  locate the current build;
- `apps/app-maintenance-agent`;
- the `APP_MAINTENANCE_*` / `MAINTENANCE_TIMEOUT_SECONDS` variables.

Updating is now:

| Install | How |
| --- | --- |
| LXC | `update` in the container |
| Host agent installed with the one-line command | automatic self-update; run the install command again only if it predates Phase 5g |
| Legacy Docker app VM | by hand, see below |

### Legacy Docker app VM

```bash
cd /opt/proxmox_backup_orchestrator
git pull --ff-only
docker compose --env-file .env -f infra/docker/docker-compose.yml up --build -d --remove-orphans
```

Always pass `--env-file .env`: without it, Compose resolves the `${VAR}`
references of the compose file without the real `.env`.

If the old `proxmox-backup-orchestrator-app-maintenance-agent.service` is
still installed on that VM, you can remove it:

```bash
systemctl disable --now proxmox-backup-orchestrator-app-maintenance-agent.service
rm /etc/systemd/system/proxmox-backup-orchestrator-app-maintenance-agent.service
systemctl daemon-reload
```

### Agents installed from a git checkout (legacy)

Agents installed by hand from a git clone are no longer updated from the UI.
Either reinstall them with the one-line command (recommended: the agent then
updates itself), or update the clone and restart the service on that machine.
