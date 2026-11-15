# API

FastAPI backend for orchestration, state tracking, and integrations.

## Run locally

1. Create a virtual environment.
2. Install the package with `pip install -e .`
3. Start the server with `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`

## Authentication

Authentication cannot be disabled. Accounts live in the `users` table; on an
empty database `GET /api/v1/setup/status` reports `needs_setup: true` and
`POST /api/v1/setup/admin` creates the first administrator (refused afterwards).
Login (`POST /api/v1/auth/token`) sets an `HttpOnly; Secure; SameSite=Strict`
session cookie; `GET /api/v1/auth/me` and `POST /api/v1/auth/logout` complete the flow.

Environment (bootstrap only):

```env
AUTH_SECRET_KEY=replace-with-random-secret   # openssl rand -hex 32
AUTH_COOKIE_SECURE=true                       # false only for plain-HTTP LAN access
DEV_SEED=false                                # demo data only when true
```

Operator settings (Proxmox/PBS/agents/ntfy…) are stored in `app_settings`
(secrets encrypted) and managed through `/api/v1/setup/*` and `/api/v1/settings`.
A legacy `.env` with those keys (and `AUTH_USERNAME`/`AUTH_PASSWORD_HASH`) is
imported once on first start.

Schema: tables `users` and `app_settings` are managed by Alembic
(`app/db/migrations`, applied automatically at startup; `alembic revision`
from this directory for new migrations). Older tables are still created by
`app/db/init.py`.

Bcrypt passwords are limited to 72 bytes after UTF-8 encoding. The API pins `bcrypt<5` because `passlib==1.7.4` is not compatible with bcrypt 5.x.

To debug hash verification inside the API environment:

```powershell
py -c "from passlib.hash import bcrypt; print(bcrypt.verify('your-password', '$2b$12$paste_the_runtime_hash_here'))"
```

## Current scope

- health endpoint
- environment-based configuration
- minimal package layout for future services, models, and schemas

## Agent Execution

Disk preparation calls the Proxmox host agent over HTTP.
PBS-native export execution calls a separate PBS-side agent over HTTP.
For PBS-native export, the backend also uses the Proxmox API to hand the selected USB disk through to the PBS VM before preparation/export.

- `HOST_AGENT_BASE_URL` points to the host agent API, for example `http://proxmox-host:8081`
- `HOST_AGENT_TOKEN` is sent as the shared `X-Agent-Token` header
- `HOST_AGENT_TIMEOUT_SECONDS` controls request timeout
- `PBS_AGENT_BASE_URL` points to the PBS execution agent API, for example `http://pbs-host:8081`
- `PBS_AGENT_TOKEN` is sent as the shared `X-Agent-Token` header
- `PBS_AGENT_TIMEOUT_SECONDS` controls request timeout
- `PBS_EXECUTION_VM_ID` identifies the PBS VM on Proxmox for USB handoff
- `PBS_EXECUTION_VM_NODE` identifies the Proxmox node hosting that PBS VM
