# Security

This project intentionally separates normal application logic from privileged host operations. The app VM runs Docker Compose without direct root access to Proxmox or PBS internals. Root-only operations are exposed by two narrow HTTP agents and must be protected by strong tokens and firewall rules.

Do not use `code_secret`.

## Authentication Variables

Authentication cannot be disabled. The administrator account lives in the
`users` table (bcrypt hash) and is created on the first-run screen
(`POST /api/v1/setup/admin`, refused once an account exists). A legacy
`AUTH_USERNAME` / `AUTH_PASSWORD_HASH` pair in `.env` is imported once on the
first start of an upgraded install.

Sessions are a signed JWT in an `HttpOnly; Secure; SameSite=Strict` cookie
(`pbo_session`): page scripts cannot read it and cross-site requests do not
carry it. Lifetime: `AUTH_TOKEN_EXPIRE_MINUTES` setting (max 180).

```env
AUTH_SECRET_KEY=<random>     # signs sessions + derives the settings encryption key
AUTH_COOKIE_SECURE=true      # false only for plain-HTTP access on a trusted LAN
```

Proxmox/PBS API tokens, agent tokens and the ntfy password are stored in the
`app_settings` table, encrypted (Fernet, key derived from `AUTH_SECRET_KEY`
with HKDF-SHA256), and never returned by the API. Rotating `AUTH_SECRET_KEY`
makes them unreadable: re-enter them afterwards.

The UI is served by the API (same origin), so CORS is disabled.

Generate `AUTH_SECRET_KEY`:

```bash
openssl rand -hex 32
```

Keep `.env` out of git. The repository `.gitignore` excludes `.env` and `.env.*` except `.env.example`.

## Agent Tokens

### Agents installed with a pairing code (documented path)

`POST /api/v1/agents/pairing` (admin session) returns a single-use code valid
15 minutes (only its SHA-256 is stored). `install.sh` registers the agent with
it (`POST /api/v1/agents/register`, public, authenticated by the code; 10
failures within 15 minutes from one address → HTTP 429). The registration
produces, **per agent and per direction**:

| Token | Direction | Stored in the app | Stored on the agent |
|---|---|---|---|
| agent token | app → agent (`X-Agent-Token`) | Fernet-encrypted (key derived from `AUTH_SECRET_KEY`) | `AGENT_SERVER_TOKEN` |
| callback token | agent → app (heartbeat, disk reports, run logs) | SHA-256 only | `AGENT_CALLBACK_TOKEN` |

- The app pins the SHA-256 of the agent certificate sent at registration on
  **every** call (checked in the TLS handshake, before the token is sent).
- The agent pins the app certificate (`AGENT_API_FINGERPRINT`, confirmed by the
  operator when running `install.sh`) on every call to the app, and only sends
  its callback token to its own `AGENT_API_BASE_URL`.
- An agent token is not accepted as a callback credential, and a host agent's
  callback token is not accepted for PBS run logs.
- Registering a new agent of the same role revokes the previous one; **Revoke**
  (`DELETE /api/v1/agents/{id}`) disables an agent immediately. Rotation =
  re-run the install command with a new code.
- The agent listens on the `vmbr0` address only, over HTTPS, and serves only
  the routes of its role (`AGENT_ROLE`).

### Static tokens (legacy `.env`)

Still accepted for roles without a registered agent. Each root agent has its
own shared bearer-style token sent in the `X-Agent-Token` header (also used,
historically, for the agent → app direction).

Generate separate tokens:

```bash
openssl rand -hex 32  # Proxmox host agent
openssl rand -hex 32  # PBS VM agent
```

App VM:

```env
HOST_AGENT_BASE_URL=http://<proxmox-host>:8090
HOST_AGENT_TOKEN=<host-agent-token>
PBS_AGENT_BASE_URL=http://<pbs-vm>:8091
PBS_AGENT_TOKEN=<pbs-agent-token>
```

Proxmox host agent:

```env
AGENT_SERVER_PORT=8090
AGENT_SERVER_TOKEN=<host-agent-token>
```

PBS VM agent:

```env
AGENT_SERVER_PORT=8091
AGENT_SERVER_TOKEN=<pbs-agent-token>
```

Never reuse these tokens for Proxmox API, PBS API, or the web login.

## Proxmox VE and PBS API access (setup wizard)

- **Dedicated identities, minimum privileges**: `pbo@pve!pbo` (custom role
  `PBO`) and `pbo@pbs!pbo` (built-in roles on three paths). Every privilege is
  justified in [PERMISSIONS.md](PERMISSIONS.md).
- **PVE**: the token is created by the host agent with `pveum` (root on the
  host); the agent returns the secret over the pinned app ↔ agent channel, with
  the PVE certificate fingerprint read from `/etc/pve/local/` — the app pins
  it without trust-on-first-use. The agent logs the steps, never the secret.
- **PBS**: the operator compares the certificate fingerprint with *PBS ›
  Dashboard › Show Fingerprint* and confirms it; only then is the root@pam
  password sent, once, over a connection pinned on that fingerprint, to get a
  PBS ticket. The password is never stored, logged, put in an error message or
  chained in an exception (tested), and the endpoint allows 5 failures per
  client per 15 minutes.
- Re-running a step re-creates the token (the previous secret stops working).
- Token secrets are stored Fernet-encrypted in `app_settings`; the wizard state
  (`setup_progress`) only keeps non-secret results.

## Firewall Rules

Agent HTTP ports must only be reachable from the app VM IP.

Required exposure:

```text
Proxmox host agent: TCP 8090 from <app-vm-ip> only
PBS VM agent:       TCP 8091 from <app-vm-ip> only
```

No browser, LAN client, or internet host should be able to call the agent ports.

## Proxmox Firewall `host.fw` for Port 8090

**Written by the agent installer (B12).** Unless `--no-firewall` is given,
`pbo-agent-install.sh` adds, at the top of the `[RULES]` section of this
node's `host.fw`:

```ini
IN ACCEPT -source <app-ip> -p tcp -dport 8090 -log nolog # pbo-agent
IN DROP -p tcp -dport 8090 -log nolog # pbo-agent
```

`<app-ip>` is the IPv4 of the app URL used for the install (a DNS name is
resolved with `getent`; an IPv6-only app gets no rule and a warning). The
rules are tagged so a re-install replaces them and `--uninstall` removes them;
other lines of the file are left untouched. Host rules only apply when the
**datacenter** firewall is enabled (*Datacenter › Firewall › Options*); the
installer never enables it — doing so switches the cluster's default input
policy — and prints a note when it is off. Check with `pve-firewall status`
and `pve-firewall compile`.

The manual equivalent follows.

The node-local host firewall file is:

```text
/etc/pve/nodes/<node-name>/host.fw
```

Example:

```text
/etc/pve/nodes/promox/host.fw
```

Cluster-wide firewall configuration is separate and lives in:

```text
/etc/pve/firewall/cluster.fw
```

Example host rule file:

```ini
[OPTIONS]
enable: 1

[RULES]
IN ACCEPT -source <app-vm-ip> -p tcp -dport 8090 -log nolog
IN DROP -p tcp -dport 8090 -log warning
```

Reload or verify the firewall from the Proxmox UI or CLI:

```bash
pve-firewall status
pve-firewall compile
```

Keep any existing management rules required for your environment.

## PBS `nftables` for Port 8091

Simple deployed variant with default accept policy:

```nft
table inet filter {
  chain input {
    type filter hook input priority 0;
    policy accept;

    ip saddr <app-vm-ip> tcp dport 8091 accept
    tcp dport 8091 drop
  }
}
```

This keeps the rest of the PBS VM networking unchanged while restricting the PBS agent port to the app VM.

Stricter default-drop example `/etc/nftables.conf`:

```nft
#!/usr/sbin/nft -f

flush ruleset

table inet filter {
  chain input {
    type filter hook input priority 0;
    policy drop;

    iif "lo" accept
    ct state established,related accept

    ip saddr <trusted-admin-subnet> tcp dport { 22, 8007 } accept
    ip saddr <app-vm-ip> tcp dport 8091 accept

    ip protocol icmp accept
    ip6 nexthdr icmpv6 accept

    counter drop
  }

  chain forward {
    type filter hook forward priority 0;
    policy drop;
  }

  chain output {
    type filter hook output priority 0;
    policy accept;
  }
}
```

Enable and validate:

```bash
nft -c -f /etc/nftables.conf
systemctl enable --now nftables
nft list ruleset
```

Adjust SSH and PBS UI/API access for your management network before enabling a default-drop policy.

## Secrets Backup

Back up these files securely:

- app VM `/opt/proxmox_backup_orchestrator/.env`
- Proxmox host `/opt/proxmox-backup-orchestrator-agent/.env`
- PBS VM `/opt/proxmox-backup-orchestrator-pbs-agent/.env`
- Proxmox and PBS API token records, if not recoverable from the UI

Recommended handling:

- store encrypted copies in a password manager or encrypted offline vault
- restrict file permissions to root or the deployment user
- avoid screenshots and chat logs containing secrets
- rotate secrets after any accidental disclosure

## Token Rotation

Rotate agent tokens one agent at a time.

Proxmox host agent:

1. Generate a new token with `openssl rand -hex 32`.
2. Update Proxmox agent `AGENT_SERVER_TOKEN`.
3. Update app VM `HOST_AGENT_TOKEN`.
4. Restart the Proxmox agent and API.
5. Verify `/health` with the new token.

PBS agent:

1. Generate a new token.
2. Update PBS agent `AGENT_SERVER_TOKEN`.
3. Update app VM `PBS_AGENT_TOKEN`.
4. Restart the PBS agent and API.
5. Verify `/health` with the new token.

JWT/login rotation:

1. Generate a new `AUTH_SECRET_KEY`.
2. Restart the API.
3. Log in again.
4. Re-enter the stored secrets (Proxmox/PBS API token secrets, agent tokens, ntfy password): they were encrypted with a key derived from the old `AUTH_SECRET_KEY` and are now ignored (logged as unreadable).

Changing `AUTH_SECRET_KEY` invalidates existing sessions and stored secrets.

## Known Risks and Limits

- The root agents are powerful by design. If an agent token is stolen and the firewall allows access, an attacker can trigger privileged disk or QEMU operations.
- Agent authentication is a shared token, not per-user authorization.
- Dedicated disk preparation can format a disk after confirmation. Operational discipline is required when selecting disks.
- If `PVE_VERIFY_SSL=false` or `PBS_VERIFY_SSL=false`, API TLS certificates are not verified. Prefer trusted certificates or fingerprints where feasible.
- `.env` backup loss can make recovery harder because API and agent tokens may need to be recreated.
- The app database tracks activity, but PBS backup data lives in PBS datastores. Protect both.

## Troubleshooting

**Legacy `AUTH_PASSWORD_HASH` not imported on upgrade**

It is imported once, only if it is a complete bcrypt hash. Check that the runtime value starts with `$2` inside the API container. The Compose file should contain `$$2b$$12$$...`; Compose converts that to `$2b$12$...` for the container.

**`code_secret` still works**

Search and remove legacy configuration:

```bash
grep -R "code_secret" -n /opt /etc/systemd /etc/default 2>/dev/null
```

Restart the affected services after removal.

**Agent health returns 401**

This means the agent is reachable but rejected the token. Verify the exact shared secret pair and avoid trailing spaces in `.env`.

**PBS disk visible but not mounted**

The firewall is not usually the cause if the disk is visible inside PBS. Check PBS agent logs and datastore preparation output. Reused disks should already contain the expected PBS datastore structure.

**USB passthrough still attached**

The host agent may have failed during the detach step or the app may be using the wrong `PBS_EXECUTION_VM_ID`. Verify `qm config <pbs-vm-id>` and host agent logs.

**External datastore reuse vs first format**

Destructive formatting should only be needed on first preparation or when force formatting is explicitly requested. Treat unexpected format prompts as a warning to stop and verify the physical disk identity.
