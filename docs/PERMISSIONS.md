# API permissions (Proxmox VE and PBS)

The setup wizard creates one dedicated identity on each side, with the
smallest set of privileges the current code needs. Nothing runs with
`root@pam` tokens.

| Side | User | Token | Created by |
|---|---|---|---|
| Proxmox VE | `pbo@pve` (PVE realm, no password) | `pbo@pve!pbo` (privilege separation on) | the host agent: `POST /pve/provision-access` (`apps/agent/src/agent/pve_access.py`) |
| PBS | `pbo@pbs` (PBS realm, no password) | `pbo@pbs!pbo` | the app, with a PBS ticket from the root@pam password typed once (`apps/api/app/services/pbs_provisioning.py`) |

Both runs are idempotent: the user and role are updated, and the token is
**deleted then re-created** (neither PVE nor PBS ever returns an existing
token secret). The new secret is stored encrypted in the app database and is
never logged.

## Proxmox VE — custom role `PBO`, ACL `/` (user and token, propagate)

Privileges were derived from every `ProxmoxClient` call
(`apps/api/app/services/proxmox_client.py`) and the setup probe, and checked
against the PVE source (`PVE/API2/Backup.pm`, `PVE/API2/Hardware/USB.pm`,
`PVE/API2/Cluster.pm`).

| Privilege | Needed by (API call → app feature) | Check in PVE |
|---|---|---|
| `Sys.Audit` | `GET /cluster/status` (Integrations › Proxmox status); `GET /cluster/backup`, `GET /cluster/backup/{id}` (backup jobs list, planning coverage) | `perm / Sys.Audit` |
| `Sys.Modify` | `GET /nodes/{node}/hardware/usb` (find the USB disk before passthrough, `disk_handoff.py`); `POST /cluster/backup`, `PUT/DELETE /cluster/backup/{id}` (create / edit / delete vzdump jobs from the UI) | `perm / Sys.Modify` |
| `VM.Audit` | `GET /nodes/{node}/qemu`, `GET /nodes/{node}/lxc` (inventory sync; PVE only lists guests with `VM.Audit`), `GET /nodes/{node}/qemu/{vmid}/config` | `perm /vms/{vmid} VM.Audit` |
| `Datastore.Allocate` | vzdump jobs: PVE checks it on `/storage/<job storage>` when a job is created or its storage changed | `Backup.pm: assert_param_permission_create/update` |

Not granted, on purpose:

- `VM.Config.HWType` / `VM.Config.*`: the USB passthrough (`qm set -usbN`) is
  done by the **host agent** as root (raw `host=` USB passthrough is
  restricted to `root@pam` by PVE anyway).
- `VM.GuestAgent.Audit` (PVE 9) / `VM.Monitor` (PVE 8): the PBS VM address
  shown by the wizard is read by the host agent (`qm guest cmd <vmid>
  network-get-interfaces`, root, local), not through the API token.
- `VM.Backup`: only checked by PVE for jobs that target a **pool**; the app
  creates jobs on explicit VM lists.

`GET /version` and `GET /nodes` need no privilege.

## PBS — built-in roles (PBS has no custom roles)

ACLs are set for the user **and** the token: a PBS token gets the
intersection of its own ACLs and its user's. Privileges per endpoint are
listed in [PBS_API.md](PBS_API.md#endpoints-used-checked-against-the-pbs-4x-api-schema).

| Path | Role | Privileges used | Why this role |
|---|---|---|---|
| `/system/disks` | `Admin` | `Sys.Audit` (disk list, guards), `Sys.Modify` (wipe, create removable datastore, mount / unmount) | `Admin` is the only built-in role with `Sys.Modify`; the ACL is limited to the disks subtree |
| `/system/tasks` | `Audit` | `Sys.Audit` (list and follow PBS tasks the token did not start, e.g. sync jobs) | read-only |
| `/datastore` | `Admin` | `Datastore.Allocate` (`POST /config/datastore`: re-create a deleted configuration with reuse-datastore), `Datastore.Modify` (PBO marker, mount), `Datastore.Backup` (local sync jobs), `Datastore.Read`/`Audit` (source datastore, snapshot inventory, disk contents), `Datastore.Prune`/`Modify` (retention: prune-datastore, then gc — Phase 5f), `Datastore.Verify` (verify of the copied snapshots — Phase 5f) | `Admin` is the only built-in role with `Datastore.Allocate` (`DatastoreAdmin` = Audit, Modify, Read, Verify, Backup, Prune); target datastores do not exist yet when the ACL is set, so it sits on the parent path |

### Privileges per call (PBS 4.x source, `access:` blocks + in-handler checks)

| Call (disk path) | Privileges |
|---|---|
| `GET /nodes/{node}/disks/list` | `Sys.Audit` on `/system/disks` |
| `PUT …/disks/wipedisk`, `POST …/disks/directory` (first creation, removable) | `Sys.Modify` on `/system/disks` |
| `POST /config/datastore` (re-create, `backing-device`, `reuse-datastore`) | `Datastore.Allocate` on `/datastore` **and** `Sys.Modify` on `/system/disks` |
| `PUT /config/datastore/{name}` (PBO marker) | `Datastore.Modify` |
| `POST /admin/datastore/{store}/mount` / `unmount` | `Datastore.Modify` + `Sys.Modify` on `/system/disks` |
| status, snapshots | `Datastore.Audit` |
| local sync job (`/config/sync`, `/admin/sync/{id}/run`) | `Datastore.Backup` (target), `Datastore.Read`/`Audit` (source) |
| `prune-datastore` | `Datastore.Modify` or `Datastore.Prune` |
| `gc` | `Datastore.Modify` |
| `verify` | `Datastore.Verify` |
| following tasks of other users (sync job) | `Sys.Audit` on `/system/tasks` |

The first creation goes through `disks/directory` (`Sys.Modify` only): a token
with `DatastoreAdmin` on `/datastore` could create a datastore but not
re-create a deleted configuration (403 "permission check failed", real test
5f.1). `datastore remove` deletes the ACLs of `/datastore/<name>` (unless
`--keep-job-configs`); PBO never set any — its ACL is on `/datastore` and
survives — so nothing has to be re-applied.

**Checked before every run**: `GET /access/permissions?path=…` (a token may
read its own privileges, `services/pbs_permissions.py`). A missing privilege
= no run, readiness reason `pbs_token_cannot_create_datastore` /
`pbs_token_missing_privileges` with the exact command to run on PBS; a 403
during a run is reported the same way (`pbs_permission_denied` when PBS cannot
tell which one).

**Existing installs** (provisioned with `DatastoreAdmin`), on PBS:

```bash
proxmox-backup-manager acl update /datastore Admin --auth-id pbo@pbs
proxmox-backup-manager acl update /datastore Admin --auth-id 'pbo@pbs!pbo'
```

The wizard refuses PBS older than **3.3** (no removable datastores, so no API
mode) before creating anything.

## Manual setup

The wizard's "Manual configuration (advanced)" sections print the exact
commands (`GET /api/v1/setup/manual-commands`), for example on PVE:

```bash
pveum role add PBO --privs "Sys.Audit,Sys.Modify,VM.Audit,Datastore.Allocate"
pveum user add pbo@pve --comment "Proxmox Backup Orchestrator"
pveum user token add pbo@pve pbo --privsep 1
pveum acl modify / --users pbo@pve --roles PBO
pveum acl modify / --tokens 'pbo@pve!pbo' --roles PBO
```

and on PBS:

```bash
proxmox-backup-manager user create pbo@pbs --comment "Proxmox Backup Orchestrator"
proxmox-backup-manager user generate-token pbo@pbs pbo
proxmox-backup-manager acl update /system/disks Admin --auth-id pbo@pbs
proxmox-backup-manager acl update /system/disks Admin --auth-id 'pbo@pbs!pbo'
proxmox-backup-manager acl update /system/tasks Audit --auth-id pbo@pbs
proxmox-backup-manager acl update /system/tasks Audit --auth-id 'pbo@pbs!pbo'
proxmox-backup-manager acl update /datastore Admin --auth-id pbo@pbs
proxmox-backup-manager acl update /datastore Admin --auth-id 'pbo@pbs!pbo'
```

The PVE privilege list is defined twice (agent and API, they cannot import
each other); `test_manual_commands_match_the_agent_privileges` fails if they
diverge.

## Removing the identities

On the PVE host: `bash pbo-agent-install.sh --uninstall --purge-pve-access`
(deletes `pbo@pve!pbo`, `pbo@pve` and the `PBO` role). On PBS:
`proxmox-backup-manager user remove pbo@pbs` (its tokens and ACLs go with it).
