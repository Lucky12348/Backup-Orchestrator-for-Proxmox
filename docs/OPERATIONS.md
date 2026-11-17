# Operations

Use this runbook for routine external backups and safe disk handling.

For asset ignore rules and existing Proxmox backup job selection management, see [Protection Management](PROTECTION.md).

## Login

Open the Web UI:

```text
https://<app-host>/        # TLS reverse proxy in front of the API port 8000
```

The API serves the UI on port `8000` (no separate web container). On a fresh
database the first screen creates the administrator account; afterwards, log
in with that account. Upgraded installs keep their former
`AUTH_USERNAME` / `AUTH_PASSWORD_HASH` login (imported once at first start).

If login succeeds but you land back on the login screen, see the
`AUTH_COOKIE_SECURE` troubleshooting entry below.

## Run an External Backup

1. Plug the dedicated USB disk into the Proxmox host.
2. Open the Web UI.
3. Go to the disk or external backup workflow.
4. Select the detected external USB disk.
5. Confirm dedicated datastore preparation.
6. Start the external backup run.
7. Watch activity until the run completes.

The workflow does three privileged operations:

- Proxmox host agent passes the USB disk to the PBS VM.
- PBS agent prepares or reuses the external datastore.
- PBS sync copies snapshots from `PBS_DATASTORE` to the external dedicated datastore.

## Dedicated Datastore Reuse

Dedicated mode is designed for one external disk that belongs to this backup process.

On first preparation, the PBS agent may format the disk and create a PBS datastore on it. This is destructive for existing disk contents.

On later runs, the PBS agent should detect and reuse the existing filesystem and datastore. Reuse means the disk can be plugged in again, mounted in PBS, and synced without recreating the datastore.

Do not mix this disk with unrelated data. Treat it as a removable PBS datastore.

## Safe Eject

Use the UI safe eject action before unplugging the disk.

Safe eject performs:

1. PBS datastore unmount inside the PBS VM.
2. USB passthrough removal from the PBS VM on the Proxmox host.
3. UI confirmation that the disk can be physically unplugged.

Only unplug the disk after the UI reports that safe eject completed.

## Disk Pulled Out Without Eject

If a disk is unplugged while it is handed to the PBS VM (passthrough), PBO
notices it within ~30 s (USB device gone from the host, or invisible to PBS
twice in a row) and, in this order: removes the `usbN` mapping from the PBS VM
(otherwise PBS would get whatever is plugged into that port next), stops a run
on that disk, unmounts its removable datastore on PBS, and marks the disk
"Removed without eject" (alert in Disks, "To do", toast, notification). Plug it
back in, then click **Verify now**: a PBS verification of its datastore, no
backup. Success clears the alert; a failure says what to do (new backup, then
verify again; replace the disk if it fails again).

At start, PBO also removes the `usbN` mappings it wrote for disks no longer in
use (tracked in `pbs_usb_mappings`; a line someone changed is never touched).

Manual cleanup, if PBO cannot reach the host agent (example: VM 100, slot
`usb0`, datastore `pbo-pbotesta`):

```bash
# Proxmox host
qm config 100 | grep ^usb
qm set 100 -delete usb0
# PBS (repeat while `mount | grep pbo-pbotesta` shows a stale mount)
proxmox-backup-manager datastore unmount pbo-pbotesta
```

### Verification required, damaged versions, repair (B46/B47)

A disk pulled out before its datastore was unmounted may have lost data that
was still in the write cache (run 30 → 3 versions with a 0-byte
`index.json.blob`). PBO then marks it **Verification required**: the next
backup starts with a full PBS verification, or click **Verify now**. Rotation
datastores write durably (`sync-level=file`), set automatically.

When a verification finds damaged versions, the disk sheet lists them (VM/CT,
version, still in the source datastore or not) and offers **Repair** (also in
the run window and "To do"): the damaged versions are deleted on the **disk
only**, copied again from the source, then verified; the disk is released at
the end, even after an error. Because PBS's sync skips a version older than the
newest one already on the disk, newer intact versions the source still holds
are deleted and copied again too; if a newer version exists only on the disk,
the older damaged one is kept and the journal says why. A damaged version the
source no longer has "only exists, damaged, on this disk": Repair asks for a
confirmation before deleting it.

### Stale mounts on PBS (B41)

A disk pulled out leaves its ext4 mount on `/mnt/datastore/<datastore>` with
the device gone (`shutdown` in `mount` output). PBS does not see it (its mount
check reads the UUID of the mount source) and mounts the next disk on top. The
PBS REST API can neither list those layers nor unmount them lazily. PBS runs in
a VM of the Proxmox node, so the **host agent** works inside it through the
**QEMU guest agent** (`qm guest exec <vmid> -- cat /proc/self/mountinfo`, then
`umount -l`), before every mount and every 30 s while a disk is handed off:
dead layers only (device gone or filesystem `shutdown`), from the top of the
stack; a live mount is never touched, and PBO refuses to mount over a live
mount it does not know. Every unmount is journalled. No PBS agent is needed.

The guest agent must be active in the PBS VM. The readiness check and the
setup wizard warn (non-blocking) when it is not; to enable it:

```bash
apt install qemu-guest-agent            # in the PBS VM
qm set <vmid> --agent enabled=1         # on the Proxmox host
qm shutdown <vmid> && qm start <vmid>   # full stop/start (a reboot from inside is not enough)
```

Without it, the run journal says the check was not possible; by hand on PBS:

```bash
findmnt /mnt/datastore/pbo-pbotesta      # one line per stacked layer
umount -l /mnt/datastore/pbo-pbotesta    # repeat while the top layer is dead
```

## Replug and Rerun Backup

For the next backup cycle:

1. Plug the same USB disk into the Proxmox host.
2. Wait for it to appear in the UI.
3. Start the external backup workflow.
4. Confirm that the UI reports reuse of the dedicated datastore, not first-time formatting.
5. Let the PBS sync finish.
6. Use safe eject before removal.

## Verify the Disk Can Be Removed

After safe eject:

On PBS:

```bash
findmnt | grep /mnt/pbo || true
proxmox-backup-manager datastore list
```

The external mount path should not appear in `findmnt`.

On Proxmox:

```bash
qm config <pbs-vm-id> | grep '^usb' || true
```

The USB passthrough entry used by the external disk should be absent.

## Cleanup Activity

Use activity cleanup from the UI when old preparation, backup, or handoff records are no longer useful. Cleanup removes application activity records; it does not delete PBS backup snapshots from the main datastore or the external datastore.

Keep recent failed activity until the cause is understood. Agent logs are often needed for root-cause analysis:

```bash
journalctl -u proxmox-backup-orchestrator-agent-http.service -n 200 --no-pager
journalctl -u proxmox-backup-orchestrator-pbs-agent-http.service -n 200 --no-pager
```

## Troubleshooting

**Login loops back to the login screen (`AUTH_COOKIE_SECURE`)**

The session cookie is `Secure` and is dropped by browsers on plain `http://<ip>`.
Serve the UI over HTTPS, or set `AUTH_COOKIE_SECURE=false` on a trusted LAN.

**Legacy `AUTH_PASSWORD_HASH` not imported on upgrade**

It is only imported (once) if it is a complete bcrypt hash. In Docker Compose `.env` files, bcrypt `$` characters must be escaped as `$$`. A valid Compose value looks like:

```env
AUTH_PASSWORD_HASH=$$2b$$12$$...
```

Regenerate with:

```bash
python scripts/generate_password_hash.py
```

Paste the complete printed line into `.env`, then restart the API:

```bash
docker compose --env-file .env -f infra/docker/docker-compose.yml up -d api
```

`--env-file .env` is required (run from the repository root) — without it,
Compose re-resolves several `environment:` entries in the compose file
(notifications config among them) against a nonexistent `infra/docker/.env`
instead of the real one, silently blanking them out even though `env_file:`
loaded them correctly moments earlier.

**`code_secret` still works**

This is not expected in the hardened deployment. Remove `code_secret` from old `.env` files, service overrides, shell profiles, browser bookmarks, and reverse proxy snippets. Restart the app and agents.

**Agent health returns 401**

The app token and the agent token do not match. Check:

- `HOST_AGENT_TOKEN` equals Proxmox agent `AGENT_SERVER_TOKEN`
- `PBS_AGENT_TOKEN` equals PBS agent `AGENT_SERVER_TOKEN`
- health checks include `X-Agent-Token`

Example:

```bash
curl -H "X-Agent-Token: <token>" http://<agent-host>:8090/health
```

**PBS disk visible but not mounted**

Check that the USB device is attached to the PBS VM and inspect PBS agent logs:

```bash
lsblk -f
journalctl -u proxmox-backup-orchestrator-pbs-agent-http.service -n 200 --no-pager
```

If this is the first run for the disk, destructive preparation may still be required. If this is a reused disk, confirm the expected PBS datastore path exists on the disk.

**USB passthrough still attached**

Run safe eject again. If it still remains, inspect the Proxmox VM config:

```bash
qm config <pbs-vm-id> | grep '^usb'
```

Do not unplug until the PBS datastore is unmounted. Manual detach is a last resort:

```bash
qm set <pbs-vm-id> -delete usbX
```

Replace `usbX` with the slot shown by `qm config`.

**External datastore reuse vs first format**

First-time dedicated preparation can format the disk. Reuse should not format. If the UI asks for destructive confirmation for a disk that should already be prepared, stop and verify you selected the expected physical disk.
