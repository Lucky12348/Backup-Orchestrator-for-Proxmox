# Notifications

PBO sends its alerts through one engine built on
[Apprise](https://github.com/caronc/apprise). The dependency is pinned to
`apprise==2.1.0` (BSD-2-Clause) in `apps/api/pyproject.toml`; the channel URLs
are built for that version's syntax. You configure everything on the
**Notifications** page, or in the optional "Notifications" step of the
first-run wizard. Nothing is read from `.env` any more, except for the
[one-time migration](#migration-from-the-old-ntfy-settings).

Code:

| File | Role |
| --- | --- |
| `apps/api/app/services/notification_catalog.py` | Events, levels, messages (EN/FR) |
| `apps/api/app/services/notification_channels.py` | Forms, validation, secrets, Apprise URLs |
| `apps/api/app/services/notification_engine.py` | Queue, deduplication, quiet hours, summary, retries, journal |
| `apps/api/app/services/notifications.py` | The hooks the rest of PBO calls |
| `apps/api/app/services/notification_migration.py` | Old ntfy settings → one channel |

## Channels

You can configure several channels. For each one you choose:

- a **name**;
- the **events** it receives;
- a **minimum level**: information, warning or error;
- **quiet hours**, optional: only errors are sent inside them, everything
  else waits until they end;
- a **daily summary**, optional: information and warnings are grouped in one
  message a day at the chosen time, errors are still sent at once;
- **Send a test**: the result is shown in the form. A test ignores events,
  level and quiet hours, so it checks the channel only.

| Type | Fields | Apprise URL built |
| --- | --- | --- |
| ntfy | server (default `https://ntfy.sh`), topic, optional token *or* user + password | `ntfys://host/topic?auth=token&token=…&priority=…&click=<link>` |
| Gotify | server, application token | `gotifys://host/path/<token>?priority=…` |
| Discord | webhook URL | `discord://<id>/<token>` |
| Telegram | bot token, chat id | `tgram://<token>/<chat_id>` |
| E-mail (SMTP) | server, security (STARTTLS / TLS / none), port, user, password, from, to | `mailtos://user:pass@host:port?mode=starttls&from=…&to=…` |
| Webhook (JSON) | URL, HTTP headers | `jsons://host/path?+Header=value&:event=…` |
| Apprise URL (advanced) | any [Apprise URL](https://github.com/caronc/apprise/wiki) | as typed |

**Telegram**: send `/newbot` to @BotFather to get the bot token, then send any
message to the bot. Open `https://api.telegram.org/bot<token>/getUpdates`; the
chat id is in `"chat":{"id":…}`. A group's id is negative. A public channel can
be given as `@name`.

**ntfy.sh**: anyone who knows a topic can read it, so pick a long random one,
or use your own ntfy server with a token.

### Webhook JSON

PBO posts Apprise's JSON document, with PBO's own keys added:

```json
{
  "version": "1.0",
  "title": "Backup failed",
  "message": "🔵 Rotation A: PBS refused the sync job",
  "attachments": [],
  "type": "failure",
  "event": "run_failed",
  "level": "error",
  "link": "https://pbo.example.lan/disks/3"
}
```

- `type`: `info`, `success`, `warning` or `failure`.
- `level`: PBO's level (`info`, `warning` or `error`).
- `link`: empty when no [public address](#links) is set.
- **Headers** you add are sent as is, e.g. `Authorization: Bearer …`. Their
  values are stored as secrets.

## Events

| Group | Event | Level | Identical alerts folded for |
| --- | --- | --- | --- |
| Runs | `run_success`: backup done | info | never (one per run) |
| | `run_failed`: backup failed (also an eject that did not finish) | error | never |
| | `run_interrupted`: disk unplugged during the run | error | never |
| | `verify_failed`: verification failed | error | never |
| | `repair_completed`: repair finished | info | never |
| Disks | `unsafe_removal`: disk removed without eject | warning | 6 h |
| | `disk_to_plug`: disk expected for a planned rotation | info | 12 h |
| | `rotation_missed`: rotation missed (disk absent) | warning | 12 h |
| | `backup_stale`: N days without a backup (Settings › Backups) | warning | 24 h |
| | `disk_space`: disk 80 % (warning) / 90 % (error) full | warning / error | 24 h |
| | `smart_degraded`: SMART warning / failing | warning / error | 24 h |
| | `disk_new`: new disk detected | info | 1 h |
| Agents | `agent_offline`: host agent stops answering | error | 6 h |
| | `agent_update_failed`: agent update failed, rolled back or too old | warning | 12 h |
| Proxmox | `pve_unreachable` / `pbs_unreachable`: inventory sync failed | error | 6 h |
| | `guest_agent_missing`: PBS VM without QEMU guest agent (stale mounts unchecked) | warning | 24 h |

Messages:

- They are short, in the account's language (English or French).
- They name the disk with its colour as a dot, e.g. « 🔵 Rotation A ».

### Language

Every message follows PBO's language: the language of the admin account
(Settings › General). Titles, texts and their parameters are all
translated: a run step, a SMART state, an agent role or why a run stopped
are words of the catalog (`term()` in `notification_catalog.py`), never
English text from PVE, PBS or an agent. A failed backup says at which stage
it failed; the exact error stays in the run journal.

A message is rendered again in the current language when it is sent and
when the journal shows it (its parameters are stored), so changing the
language also changes what is still waiting.

### Links and appearance

Set the **public address of PBO** in Settings › General (for example
`https://pbo.example.lan`). Each message then carries a link to the screen
concerned: the disk sheet, Planning or Integrations. With no public address,
messages have no link.

| Channel | Link | Icon | Priority |
| --- | --- | --- | --- |
| ntfy | click on the message + an **Open** button | PBO's icon | by level (low → max) |
| Discord | the title opens the screen; posted as "PBO" | PBO's icon | — |
| Telegram | in the text (no preview card) | the bot's picture (BotFather) | — |
| Gotify | in the text | — | by level |
| e-mail, Apprise URL | in the text | — | — |
| webhook | `link` in the JSON | — | `level` in the JSON |

PBO's icon is served by PBO itself (`/favicon/web-app-manifest-192x192.png`
under the public address). The ntfy app and Discord download it: with a
self-signed certificate or an address they cannot reach, they show their
default icon instead. Apprise's own icon is never sent.

## Reliability

- **Queue**: `emit()` writes one delivery per matching channel and never
  raises into the code that called it. A worker started in the API lifespan
  sends what is due every 15 s, or at once when something is queued.
- **Retries**: a failed send is retried after 1 min, 5 min, 15 min and 1 h.
  After that it is marked *failed* with a readable error; the secrets are
  removed from the error text. **Try again** in the journal re-queues it.
- **Deduplication**: the same alert (same event and subject) inside the
  event's window is not sent again. The first message counts the duplicates
  instead, shown as "×N" in the journal.
- **Journal**: the Notifications page lists the last messages: sent, waiting,
  in the daily summary, failed (with the error) or cancelled. Entries are
  kept 30 days.
- **Disabled channel**: its messages wait until it is enabled again.
  Deleting a channel cancels its waiting messages.
- **Quiet hours changed**: saving a channel places what waits for it again
  at once. Quiet hours removed: held messages leave now; a shorter range:
  they leave at its new end; the daily summary switched on or off: they
  move into or out of it. Messages waiting for a retry keep their delay.
- **Waiting messages** show a clock and the time they will leave (quiet
  hours) or of the next attempt (retry), apart from a message being sent.

## Inbox

The inbox in the top bar (it replaces the bell) lists PBO's events whatever
the channels: every event of the catalog is stored (`inbox_items`, Alembic
`0016`), rendered in PBO's current language. An identical event inside its
window does not add a line: it counts (×N) and is unread again. The alert
toasts of the web UI (warnings and errors) are kept there too, except form
messages and what the server already recorded.

Badge: the number of unread lines; red when one of them is urgent (error).
Filters All / Unread / Urgent, "Mark all as read", a click opens the screen
concerned. Lines are kept as Settings › Retention says for the run history
("keep everything": kept; "delete after N days": N days; "keep the last N
per disk": one year), trimmed by the daily task.

## Secrets

Tokens, passwords, webhook URLs, header values and Apprise URLs are encrypted
with the same Fernet key as the secret `app_settings` (derived from
`AUTH_SECRET_KEY`).

They are never returned by the API: `GET /notifications/channels` lists only
`secrets_set`, the names of the secrets that are stored. On update:

- a value replaces the secret;
- `null` removes it;
- an absent key keeps it.

## API

All routes need a session.

| Route | Purpose |
| --- | --- |
| `GET /api/v1/notifications/catalog` | Events, groups, levels, form fields |
| `GET` / `POST /api/v1/notifications/channels` | List / create |
| `PUT` / `DELETE /api/v1/notifications/channels/{id}` | Update / delete |
| `POST /api/v1/notifications/channels/{id}/test` | Test a stored channel |
| `POST /api/v1/notifications/test` | Test the form before saving; `channel_id` reuses the stored secrets |
| `GET /api/v1/notifications/deliveries` | The journal: `channel_id`, `status`, `level`, `since`, `q` (search), `limit` / `offset`; returns `{items, total}` |
| `POST /api/v1/notifications/deliveries/{id}/retry` | Re-queue a failed message |
| `GET /api/v1/inbox?filter=all\|unread\|urgent&before=` | Inbox lines, newest first, with the counts |
| `GET /api/v1/inbox/counts` | `{unread, urgent_unread}` |
| `POST /api/v1/inbox/read` | `{ids}` or `{}` (everything) |
| `POST /api/v1/inbox/toasts` | An alert toast of the web UI (`warning` / `error`) |

The SSE stream publishes `inbox.updated` when the inbox changes.

Errors are `{"code", "field"}` objects, translated by the UI.

## Migration from the old ntfy settings

The previous engine read `NTFY_BASE_URL`, `NTFY_TOPIC`, `NTFY_USERNAME`,
`NTFY_PASSWORD`, `NOTIFICATIONS_ENABLED` and the `NOTIFY_ON_*` flags (B9:
they were also duplicated in a `notification_preferences` table). It is gone,
and so is the relay to the Proxmox host's own ntfy engine.

On the first start of this version, `migrate_legacy_ntfy()` turns an existing
configuration into one **ntfy** channel:

- **Values read**: the stored settings, the `notification_preferences` row
  when present, or the environment of an install whose `.env` was never
  imported.
- **Enabled**: as before.
- **Events**: follow the old flags. Disk space, SMART, removal without eject
  and the stale reminder were always sent before; they stay on.

It runs once (marker `meta.notifications_migrated`). The old rows and table
are left in place, so an older PBO still works after a rollback.

The Proxmox host's own ntfy engine (`/etc/ntfy-notif`, `ntfy-send`) is not
part of PBO and is left untouched. PBO no longer manages it.
