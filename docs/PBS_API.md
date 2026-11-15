# PBS API mode (`PBS_MODE=api`)

With `PBS_MODE=api` the orchestrator drives removable-disk datastores through
the **Proxmox Backup Server REST API (PBS ≥ 3.3)** instead of the root agent on
the PBS VM. The legacy path (`PBS_MODE=agent`) is unchanged and remains the
default for upgraded installs.

- Fresh install → `api` is stored on first start.
- Upgrade (legacy `.env` imported, a PBS agent configured, or a disk already
  prepared by the agent) → `agent` is stored. See `ensure_pbs_mode` in
  `apps/api/app/services/settings_service.py`.
- Change it with `PUT /api/v1/setup/pbs` (`"mode": "api" | "agent"`) or
  `PATCH /api/v1/settings` (`{"values": {"PBS_MODE": "api"}}`). The mode is
  global (all disks) — read the migration section before switching an
  existing install.

Code map:

| File | Role |
|---|---|
| `apps/api/app/services/pbs_client.py` | REST client (endpoints below), UPID task follow-up |
| `apps/api/app/services/pbs_guards.py` | Safety guards re-implemented server-side (pure functions) |
| `apps/api/app/services/pbs_api_backend.py` | Speaks the PBS-agent protocol over the REST API |
| `apps/api/app/services/host_agent.py` | `get_pbs_agent_client()` returns the backend when `PBS_MODE=api` |
| `apps/api/app/core/tls.py` | Certificate fingerprint pinning (all PVE/PBS clients) |

The orchestration (`external_backup_execution.py`, `disk_handoff.py`,
`disk_eject.py`…) is identical in both modes: it calls “the PBS agent”
through `get_pbs_agent_client()`, and the backend answers with the same
payload shapes and error codes (`eject_not_finished`, …).

## Endpoints used (checked against the PBS 4.x API schema)

Source: `https://pbs.proxmox.com/docs/api-viewer/apidoc.js` (schema of the
4.x API viewer, read 2026-11-15). **[V]** = verified verbatim in the schema;
**[U]** = not stated by the schema, handled defensively. `node` is always
`localhost` ("Node name (or 'localhost')" [V]).

| Use | Method + path | Parameters sent | Returns | Privilege (schema) |
|---|---|---|---|---|
| Version / reachability | `GET /version` | – | `{version, release, repoid}` | – |
| Disk discovery, visibility, guards | `GET /nodes/{node}/disks/list` | `include-partitions=1`, `skipsmart=1` | items `name, devpath, serial, wwn, size, used, disk-type, partitions[{name, devpath, filesystem, uuid, used, mounted}]` [V] | Sys.Audit `/system/disks` |
| Wipe before formatting | `PUT /nodes/{node}/disks/wipedisk` | `disk` (e.g. `sdb`) | UPID [V] | Sys.Modify `/system/disks` |
| Format + create removable datastore | `POST /nodes/{node}/disks/directory` | `disk`, `name` (3-32 chars), `filesystem=ext4`, `add-datastore=true`, `removable-datastore=true` | UPID [V] | Sys.Modify `/system/disks` |
| List configs (incl. `backing-device`) | `GET /config/datastore` | – | items incl. `name, path, backing-device` (fs UUID), `comment`, `maintenance-mode` [V] | – (filtered by ACL) |
| PBO marker | `PUT /config/datastore/{name}` | `comment=pbo:serial=<serial>` | null [V] | Datastore.Modify `/datastore/{name}` |
| (migration) re-register a datastore | `POST /config/datastore` | `name`, `path` (relative on-device path), `backing-device`, `reuse-datastore=true` | **null** [V] (a UPID is waited for if ever returned) | Datastore.Allocate + Sys.Modify `/system/disks` |
| Mount state | `GET /admin/datastore` | – | items `store, mount-status (mounted\|notmounted\|nonremovable), maintenance` [V] | – |
| Usage | `GET /admin/datastore/{store}/status` | – | `total, used, avail` bytes [V] | Datastore.Audit/Backup |
| Mount | `POST /admin/datastore/{store}/mount` | – | UPID [V] | Datastore.Modify `/datastore/{store}` **and** Sys.Modify `/system/disks` |
| Unmount (eject) | `POST /admin/datastore/{store}/unmount` | – | UPID [V] | Datastore.Modify (partial) **and** Sys.Modify `/system/disks` |
| Local sync job (no remote) | `POST /config/sync` | `id`, `store` (target), `remote-store` (source), `remove-vanished=false`, `owner`, `comment` — **no `remote`** (optional [V] → local sync) | null [V] | Datastore.Backup on target (+ Datastore.Modify when `owner` ≠ caller) |
| Run it | `POST /admin/sync/{id}/run` | – | **null** per schema [V]; the task is then located via the task list (a UPID is used directly if one is returned) | same as above |
| Remove temp job | `DELETE /config/sync/{id}` | – | null [V] | same as above |
| List PBO jobs | `GET /config/sync` | – | sync job objects [V] | – |
| Running tasks / find the sync task | `GET /nodes/{node}/tasks` | `running=1`, `typefilter=syncjob`, `since`, `limit` | items `upid, worker_type, worker_id, starttime, status` [V] | own tasks, Sys.Audit `/system/tasks` for all |
| Follow a task to the end | `GET /nodes/{node}/tasks/{upid}/status` | – | `status` ("running"/"stopped" per description [V]), `exitstatus` ("OK", "Error: …", "unknown" [V]; "WARNINGS: n" treated as success) | own tasks or Sys.Audit |
| Task log into the run journal | `GET /nodes/{node}/tasks/{upid}/log` | `start`, `limit=500` | untyped in schema [U]: parsed as `[{n, t}]` (PBS's actual format), plain strings accepted | own tasks or Sys.Audit |

Notes:

- Datastore names are limited to 32 characters by PBS. The historical name
  `pbo-<serial>` (up to 44 chars) is shortened deterministically by
  `fit_datastore_name` (prefix + 8 hex of SHA-256); the chosen name is stored
  on the disk (`pbs_datastore_name`).
- The sync task's worker id is matched on an **exact `:`-separated segment**
  equal to the job id (`pbo-sync-<run id>`), never by substring.
- Task follow-up never stops a PBS task: on timeout the task keeps running on
  PBS and the run reports it.

### PBS token (created by the setup wizard)

The wizard (step 4, `POST /api/v1/setup/pbs/provision`) creates `pbo@pbs` and
the `pbo@pbs!pbo` token with the root@pam password typed once, and sets these
ACLs for the user **and** the token (PBS has no custom roles; a token gets the
intersection of its ACLs and its user's):

| Path | Role | Covers |
|---|---|---|
| `/system/disks` | `Admin` | `Sys.Audit` + `Sys.Modify` (list, wipe, create removable datastore, mount/unmount) |
| `/system/tasks` | `Audit` | `Sys.Audit` (follow tasks) |
| `/datastore` | `DatastoreAdmin` | `Datastore.Allocate`/`Modify`/`Backup`/`Read`/`Audit` on every store (targets are created at runtime) |

PBS caches its ACL configuration for a few seconds: right after the ACLs are
set, `GET /admin/datastore` with the new token returns a **filtered (empty)**
list and other calls may answer 403. The wizard retries the check (0.5, 1, 2,
3, 3.5 s — about 10 s) before concluding there is no datastore, and offers
*Refresh the list* (`GET /api/v1/setup/pbs/datastores`, stored token).
`GET /nodes` is never called with a token: PBS's `list_nodes` is
superuser-only (the PBS node is always `localhost`).

Reasons and the manual equivalent: [PERMISSIONS.md](PERMISSIONS.md). Exact
local-sync permissions on the *source* store are not spelled out by the schema
[U]: `DatastoreAdmin` on `/datastore` covers them.

## Flows

**Preparation** (`/prepare-dedicated-pbs-datastore`):

1. Find the disk in `disks/list` by serial (+ aliases), wwn, name or devpath —
   exactly one match or refuse.
2. Guards (`pbs_guards.plan_dedicated_preparation`), all before any write:
   - disk registered **and approved** (`trusted`) in the orchestrator;
   - a filesystem of this disk bound (`backing-device`) to a datastore:
     reused only if that datastore carries the PBO marker for this serial,
     otherwise refused (never formatted); refused too if a task runs on it;
   - target datastore name already used elsewhere → refused;
   - formatting only if: size ≥ 32 GiB (same rounding as the agent), disk
     not `mounted`/`lvm`/`zfs`/`devicemapper`, no mounted partition, no
     `lvm`/`zfs`/`efi`/`bios` partition, no `ext4`/`xfs`/`btrfs` partition
     (data without PBO marker), and explicit destructive confirmation.
3. Format path: `wipedisk` → wait → `disks/directory` (ext4, add-datastore,
   removable-datastore) → wait → PBO marker comment → mount if needed.
   Reuse path: mount if `notmounted`.
4. The datastore path is `/mnt/datastore/<name>`.

**Export** (`/run-external-export`, dedicated mode only): ensure target
mounted, source exists, target idle; remove stale `pbo-sync-*` jobs;
create the local sync job; run it; locate the UPID; follow it to the end
while copying every task log line into the run journal (progress parsing of
`percentage done` lines works as with the agent); always delete the job.

**Eject** (`/eject-dedicated-pbs-datastore`, then `disk_eject.py`):

1. Refuse if any running task references the store (→ HTTP 409 in the UI).
2. `unmount` → follow the UPID (PBS puts the store in `unmount` maintenance
   and waits for active operations).
3. Timeout (`PBS_EJECT_TIMEOUT_SECONDS`, default 1800) or store still
   mounted → `error_code=eject_not_finished`: **the USB passthrough is not
   removed**, the run ends in `eject_not_finished` ("NE PAS débrancher").
4. Only after a successful unmount does `disk_eject.py` ask the **host
   agent** to detach the USB device from the PBS VM, then spin it down.

Not available in API mode (explicit error, use `PBS_MODE=agent`): the
coexistence / loop-image mode, `/prepare-disk` on the PBS side, PBS agent
maintenance.

## TLS

`PVE_FINGERPRINT` / `PBS_FINGERPRINT` (SHA-256, set by the setup wizard) are
enforced **inside the TLS handshake of every request** of `ProxmoxClient`,
`PBSClient` and the setup probes (`app/core/tls.py`): a mismatch aborts the
connection before any HTTP byte — hence before the API token — is sent.
With `*_VERIFY_SSL=true` the CA chain is verified as well. Without
verification nor fingerprint, the setup probe only performs an
unauthenticated request and returns the fingerprint with
`trust_required=true` (trust on first use); the runtime clients keep the
legacy `verify=false` behaviour in that case.

## What still depends on the agents

| Agent | Still needed for |
|---|---|
| Proxmox host agent (root) | QEMU USB passthrough attach/detach (`qm set`), VM USB config, disk spin-down, disk discovery reports (timer), host ntfy engine, host disk preparation endpoint |
| PBS agent | Only with `PBS_MODE=agent`: legacy (fstab) datastores, coexistence mode, PBS agent maintenance |

The host agent is installed with the two commands shown by the setup wizard
(pairing code, HTTPS with a pinned certificate in both directions, one token
per direction): see [INSTALLATION.md](INSTALLATION.md#4-host-agent-what-the-install-command-does).

Agents now serve only the routes of their role (`AGENT_ROLE=host|pbs`,
see `apps/agent/src/agent/server.py:ROUTE_ROLES`); an empty role keeps the
historical "all routes" behaviour with a startup warning.

## Migrating disks prepared by the agent ("old style")

Agent-prepared disks are **not** removable datastores: a GPT disk with one
ext4 partition, mounted by an `/etc/fstab` line (`UUID=… /mnt/pbo/<serial>/pbs-datastore ext4 defaults,nofail`)
on the PBS VM, holding a regular datastore `pbo-<serial>` whose chunk store is
the **root of that filesystem**, plus the `.pbo-datastore.json` marker. In
API mode such a store reports `mount-status: nonremovable` and is refused
(`legacy_datastore`) — nothing is touched. `PBS_MODE` is global, so migrate
every agent-prepared disk in one maintenance window, or stay in agent mode.

Per disk (disk connected and handed off to the PBS VM, no task running,
`PBS_MODE=agent` still active):

1. **Inventory**: on PBS, `proxmox-backup-manager datastore show <name>`,
   `findmnt /mnt/pbo/<serial>/pbs-datastore`, `blkid -s UUID -o value <partition>`
   → note datastore name, partition, filesystem UUID.
2. **Detach the old definition without touching data**: remove the datastore
   *configuration* only — `DELETE /config/datastore/<name>` **without**
   `destroy-data` (or `proxmox-backup-manager datastore remove <name>`);
   unmount `/mnt/pbo/<serial>/pbs-datastore`; delete the matching `/etc/fstab`
   line; `systemctl daemon-reload`.
3. **Re-register as removable**: `POST /config/datastore` with
   `name=<fit_datastore_name(name)>`, `backing-device=<fs UUID>`,
   `reuse-datastore=true`, `comment=pbo:serial=<serial>`, and `path` = the
   on-device directory holding `.chunks`. Because the agent put the store at
   the filesystem root, this needs a PBS release that accepts a datastore on
   the device root (roadmap: "creation on a device's root directory", 4.1)
   [U: exact `path` value for the root to be confirmed on a test PBS]. On older
   releases, first move the store into a sub-directory on the same filesystem
   (`mkdir /mnt/…/pbs-datastore && mv .chunks .lock … into it` — a rename, no
   copy) and use that relative path.
4. **Mount and verify**: `POST /admin/datastore/<name>/mount`, wait for the
   UPID, then list snapshots (`GET /admin/datastore/<name>/snapshots`) and
   optionally run a verify job.
5. **Update the orchestrator record**: `pbs_datastore_name` = the new name,
   `pbs_mount_path` = `/mnt/datastore/<name>` (today through SQL / API; no UI yet).
6. When every disk is migrated: `PBS_MODE=api`, then optionally retire the PBS
   agent (stop its service, close port 8091).

Rollback before step 6: unmount via the API, `DELETE /config/datastore/<name>`
(no `destroy-data`), restore the fstab line, `proxmox-backup-manager datastore create <name> <path> --reuse-datastore true`.

Steps 1–3 need root on the PBS VM; automating them is a good candidate for a
one-shot, PBS-role agent endpoint in a later phase.
