---
name: ops-and-agents
description: Work on Proxmox/PBS agents, Docker Compose deployment, systemd services, updates, and operational troubleshooting in proxmox_backup_orchestrator. Use when touching host agents, PBS execution behavior, agent self-update, environment wiring, deployment files, or production-facing operational docs.
---

# Ops And Agents

Read these files first for operational tasks:

- `README.md`
- `docs/INSTALLATION.md`
- `docs/OPERATIONS.md`
- `docs/MAINTENANCE.md`
- `docs/SECURITY.md`
- `infra/docker/docker-compose.yml`

Know the component boundaries:

- `apps/agent` is deployed in two roles: Proxmox host agent and PBS agent.
- `apps/api` orchestrates those agents through authenticated HTTP calls.

Use these code entrypoints:

- `apps/agent/src/agent/main.py`
- `apps/agent/src/agent/server.py`
- `apps/api/app/services/host_agent.py`
- `apps/api/app/services/external_backup_agent.py`
- `apps/api/app/services/maintenance.py`

Respect these operational rules:

- Treat agent ports `8090` and `8091` as restricted surfaces.
- Preserve `X-Agent-Token` authentication on all privileged HTTP actions.
- Assume disk preparation, partitioning, mounting, USB passthrough, and PBS datastore work are safety-critical.
- Confirm whether a change affects the Proxmox host agent, the PBS agent, or both.

Read deployment wiring before editing:

- Read `scripts/install/pbo-install.sh` and `pbo-update.sh` for the documented LXC install (`pbo.service`, `/etc/pbo`, `update` with rollback).
- Read `apps/api/app/agent_install/install.sh` and `apps/api/app/services/agent_registry.py` for the one-line host agent install (pairing code, per-agent tokens, pinned TLS both ways, `--uninstall [--purge-pve-access]`).
- Read `apps/agent/src/agent/hotplug.py` and the udev rule written by `install.sh` (`/etc/udev/rules.d/90-pbo-agent.rules`) for instant USB disk detection, and `apps/api/app/services/events.py` for the SSE channel (AGENTS.md, "Real-time channel").
- Read `apps/agent/src/agent/pve_access.py` (host-role `POST /pve/provision-access`: `pveum` creates pbo@pve, role PBO, token pbo@pve!pbo) and `docs/PERMISSIONS.md` before changing API privileges.
- Read `infra/docker/docker-compose.yml` for the legacy app VM stack.
- Read `apps/agent/deploy/systemd/` for host and PBS agent service/timer deployment.
- Read `.env.example` for the expected variable contract across app VM and agents.

Use cautious review rules for destructive paths:

- Inspect code paths that call mount, format, wipe, `qm`, `parted`, `wipefs`, `sgdisk`, or `proxmox-backup-manager`.
- Do not relax device filtering or destructive confirmations without checking the operational docs and tests.
- Keep maintenance behavior explicit about git state, `.env` preservation, and restart side effects.

Use these tests when relevant:

- `apps/api/tests/test_proxmox_client.py`
- `apps/api/tests/test_external_backup_execution.py`
- `apps/api/tests/test_disk_handoff.py`
- `apps/agent/tests/test_external_export.py`

For troubleshooting work:

- Start from the failing surface: app, Proxmox host agent, or PBS agent.
- Trace the exact HTTP boundary and token/env dependency before proposing a fix.
- Update the matching docs when changing deployment assumptions, ports, service names, or required environment variables.
