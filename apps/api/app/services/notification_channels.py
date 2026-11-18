"""Notification channels: the fields of each kind, their validation, the
secrets (encrypted like the secret app_settings, never returned), and the
Apprise URL each channel becomes (Phase 5j, Apprise 2.1.0 syntax).

A channel kind = a dedicated form in the UI. Secret fields are write-only:
the API reports which ones are set (`secrets_set`); on update a string
replaces a secret, `null` clears it, an absent key keeps it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import SecretDecryptionError, decrypt_value, encrypt_value
from app.models import NotificationChannel
from app.services.notification_catalog import EVENT_KEYS, LEVELS


class ChannelError(ValueError):
    """Invalid channel: `code` is translated by the UI, `field` names the input."""

    def __init__(self, code: str, field: str | None = None, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.field = field


@dataclass(frozen=True)
class Field:
    name: str
    secret: bool = False
    required: bool = False


KINDS: dict[str, tuple[Field, ...]] = {
    "ntfy": (Field("server_url"), Field("topic", required=True), Field("username"), Field("token", secret=True), Field("password", secret=True)),
    "gotify": (Field("server_url", required=True), Field("app_token", secret=True, required=True)),
    "discord": (Field("webhook_url", secret=True, required=True),),
    "telegram": (Field("chat_id", required=True), Field("bot_token", secret=True, required=True)),
    "email": (
        Field("host", required=True),
        Field("port"),
        Field("security"),
        Field("username"),
        Field("password", secret=True),
        Field("from_address", required=True),
        Field("to_addresses", required=True),
    ),
    # Header values may hold credentials: all of them are secrets ("header.<Name>").
    "webhook": (Field("url", required=True), Field("header_names")),
    "apprise": (Field("url", secret=True, required=True),),
}
EMAIL_SECURITY = ("starttls", "ssl", "none")
DEFAULT_NTFY_SERVER = "https://ntfy.sh"
HEADER_PREFIX = "header."
_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
_TOPIC = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DISCORD = re.compile(r"^https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/(\d+)/([A-Za-z0-9_-]+)/?$")
_TELEGRAM_TOKEN = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
_TELEGRAM_CHAT = re.compile(r"^(-?\d+|@[A-Za-z][A-Za-z0-9_]{4,})$")


def secret_fields(kind: str, config: dict[str, Any]) -> set[str]:
    names = {field.name for field in KINDS[kind] if field.secret}
    if kind == "webhook":
        names |= {HEADER_PREFIX + name for name in config.get("header_names") or []}
    return names


# --- validation ------------------------------------------------------------------------------


def _http_url(value: str, field: str) -> str:
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ChannelError("invalid_url", field)
    return value.rstrip("/")


def _time(value: Any, field: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not _TIME.match(value):
        raise ChannelError("invalid_time", field)
    return value


def clean_config(kind: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Non-secret fields of a kind, validated and normalised."""
    if kind not in KINDS:
        raise ChannelError("invalid_kind", "kind")
    config: dict[str, Any] = {}
    for field in KINDS[kind]:
        if field.secret:
            continue
        value = raw.get(field.name)
        if isinstance(value, str):
            value = value.strip()
        if value in (None, "", []):
            if field.required:
                raise ChannelError("required", field.name)
            continue
        config[field.name] = value
    if kind == "ntfy":
        config["server_url"] = _http_url(config.get("server_url") or DEFAULT_NTFY_SERVER, "server_url")
        if not _TOPIC.match(config["topic"]):
            raise ChannelError("invalid_topic", "topic")
    elif kind == "gotify":
        config["server_url"] = _http_url(config["server_url"], "server_url")
    elif kind == "telegram":
        if not _TELEGRAM_CHAT.match(str(config["chat_id"])):
            raise ChannelError("invalid_chat_id", "chat_id")
        config["chat_id"] = str(config["chat_id"])
    elif kind == "email":
        try:
            port = int(config.get("port") or 0)
        except (TypeError, ValueError):
            raise ChannelError("invalid_port", "port") from None
        security = config.get("security") or "starttls"
        if security not in EMAIL_SECURITY:
            raise ChannelError("invalid_security", "security")
        config["security"] = security
        config["port"] = port or {"starttls": 587, "ssl": 465, "none": 25}[security]
        if not 1 <= config["port"] <= 65535:
            raise ChannelError("invalid_port", "port")
        if not _EMAIL.match(config["from_address"]):
            raise ChannelError("invalid_email", "from_address")
        recipients = config["to_addresses"]
        if isinstance(recipients, str):
            recipients = [item.strip() for item in re.split(r"[,;\s]+", recipients) if item.strip()]
        if not recipients or not all(_EMAIL.match(item) for item in recipients):
            raise ChannelError("invalid_email", "to_addresses")
        config["to_addresses"] = recipients
    elif kind == "webhook":
        config["url"] = _http_url(config["url"], "url")
        names = config.get("header_names") or []
        if not isinstance(names, list) or not all(isinstance(name, str) and _HEADER_NAME.match(name) for name in names):
            raise ChannelError("invalid_header", "header_names")
        config["header_names"] = sorted(set(names))
    return config


def check_secret(kind: str, name: str, value: str) -> str:
    value = value.strip()
    if not value:
        raise ChannelError("required", name)
    if kind == "discord" and not _DISCORD.match(value):
        raise ChannelError("invalid_discord_webhook", name)
    if kind == "telegram" and name == "bot_token" and not _TELEGRAM_TOKEN.match(value):
        raise ChannelError("invalid_bot_token", name)
    if kind == "apprise":
        import apprise

        if not apprise.Apprise().add(value):
            raise ChannelError("invalid_apprise_url", name)
    return value


def merge_secrets(kind: str, config: dict[str, Any], current: dict[str, str], changes: dict[str, str | None] | None) -> dict[str, str]:
    """Stored secrets + the request's changes; drops secrets the kind no longer has."""
    allowed = secret_fields(kind, config)
    merged = {name: value for name, value in current.items() if name in allowed}
    for name, value in (changes or {}).items():
        if name not in allowed:
            raise ChannelError("unknown_secret", name)
        if value is None:
            merged.pop(name, None)
        else:
            merged[name] = check_secret(kind, name, value)
    for field in KINDS[kind]:
        if field.secret and field.required and field.name not in merged:
            raise ChannelError("required", field.name)
    if kind == "ntfy" and merged.get("password") and not config.get("username"):
        raise ChannelError("required", "username")
    if kind == "email" and config.get("username") and not merged.get("password"):
        raise ChannelError("required", "password")
    return merged


def clean_schedule(payload: dict[str, Any]) -> dict[str, Any]:
    events = payload.get("events")
    if events is None:
        events = list(EVENT_KEYS)
    if not isinstance(events, list) or not all(event in EVENT_KEYS for event in events):
        raise ChannelError("invalid_events", "events")
    level = payload.get("min_level") or "info"
    if level not in LEVELS:
        raise ChannelError("invalid_level", "min_level")
    quiet_start = _time(payload.get("quiet_start"), "quiet_start")
    quiet_end = _time(payload.get("quiet_end"), "quiet_end")
    if (quiet_start is None) != (quiet_end is None) or (quiet_start and quiet_start == quiet_end):
        raise ChannelError("invalid_quiet_hours", "quiet_start")
    digest_time = _time(payload.get("digest_time") or "08:00", "digest_time")
    return {
        "events": sorted(set(events), key=EVENT_KEYS.index),
        "min_level": level,
        "quiet_start": quiet_start,
        "quiet_end": quiet_end,
        "digest": bool(payload.get("digest")),
        "digest_time": digest_time,
    }


# --- secrets ---------------------------------------------------------------------------------


def read_secrets(channel: NotificationChannel) -> dict[str, str]:
    if not channel.secrets_encrypted:
        return {}
    try:
        value = json.loads(decrypt_value(channel.secrets_encrypted))
    except (SecretDecryptionError, ValueError):
        return {}
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def write_secrets(channel: NotificationChannel, secrets: dict[str, str]) -> None:
    channel.secrets_encrypted = encrypt_value(json.dumps(secrets, sort_keys=True)) if secrets else None


# --- CRUD ------------------------------------------------------------------------------------


def channel_view(channel: NotificationChannel) -> dict[str, Any]:
    """Client-safe: never a secret value, only which secrets are set."""
    return {
        "id": channel.id,
        "name": channel.name,
        "kind": channel.kind,
        "enabled": channel.enabled,
        "config": dict(channel.config or {}),
        "secrets_set": sorted(read_secrets(channel)),
        "events": list(channel.events or []),
        "min_level": channel.min_level,
        "quiet_start": channel.quiet_start,
        "quiet_end": channel.quiet_end,
        "digest": channel.digest,
        "digest_time": channel.digest_time,
        "created_at": channel.created_at,
        "updated_at": channel.updated_at,
    }


def list_channels(db: Session) -> list[NotificationChannel]:
    return list(db.scalars(select(NotificationChannel).order_by(NotificationChannel.id)))


def apply_payload(channel: NotificationChannel, payload: dict[str, Any], *, creating: bool) -> NotificationChannel:
    """Validate a create/update request and copy it onto `channel` (not committed)."""
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 120:
        raise ChannelError("invalid_name", "name")
    kind = payload.get("kind") if creating else channel.kind
    if not creating and payload.get("kind") not in (None, channel.kind):
        raise ChannelError("kind_locked", "kind")
    config = clean_config(str(kind), payload.get("config") or {})
    current = {} if creating else read_secrets(channel)
    secrets = merge_secrets(str(kind), config, current, payload.get("secrets"))
    schedule = clean_schedule(payload)
    channel.name = name
    channel.kind = str(kind)
    channel.enabled = bool(payload.get("enabled", True))
    channel.config = config
    write_secrets(channel, secrets)
    for key, value in schedule.items():
        setattr(channel, key, value)
    return channel


def draft_channel(payload: dict[str, Any], stored: NotificationChannel | None) -> NotificationChannel:
    """An unsaved channel for "Send a test" (reuses the stored secrets when editing)."""
    channel = NotificationChannel(id=stored.id if stored else None, secrets_encrypted=stored.secrets_encrypted if stored else None)
    if stored is not None:
        channel.kind = stored.kind
    data = dict(payload)
    data.setdefault("name", stored.name if stored else "test")
    return apply_payload(channel, data, creating=stored is None)


# --- Apprise URLs ----------------------------------------------------------------------------


def _q(value: str) -> str:
    return quote(value, safe="")


def _query(params: dict[str, str]) -> str:
    """Query string with %20 for spaces: Apprise does not read "+" as a space
    (a header "Bearer x" would arrive as "Bearer+x")."""
    return urlencode(params, quote_via=quote)


def _host_part(url: str) -> tuple[bool, str, str]:
    """(secure, "host[:port]", "/path") of an http(s) URL."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:  # IPv6
        host = f"[{host}]"
    if parts.port:
        host = f"{host}:{parts.port}"
    return parts.scheme == "https", host, parts.path.rstrip("/")


NTFY_PRIORITY = {"info": "default", "warning": "high", "error": "max"}
GOTIFY_PRIORITY = {"info": "normal", "warning": "high", "error": "emergency"}


def apprise_url(
    channel: NotificationChannel,
    secrets: dict[str, str],
    *,
    level: str = "info",
    link: str | None = None,
    extras: dict[str, str] | None = None,
    icon: str | None = None,
    open_label: str = "Open",
) -> str:
    """The Apprise URL of a channel for one message: priority by level, click
    link and "Open" action, PBO's icon (`icon`, served by PUBLIC_URL; B55)
    instead of Apprise's, JSON extras for the webhook."""
    config = channel.config or {}
    kind = channel.kind
    if kind == "ntfy":
        secure, host, path = _host_part(config.get("server_url") or DEFAULT_NTFY_SERVER)
        auth = ""
        params: dict[str, str] = {"priority": NTFY_PRIORITY.get(level, "default")}
        if secrets.get("token"):
            params.update({"auth": "token", "token": secrets["token"]})
        elif config.get("username"):
            auth = _q(config["username"]) + (":" + _q(secrets["password"]) if secrets.get("password") else "") + "@"
        if link:
            params["click"] = link
            params["actions"] = f"view, {open_label}, {link}, clear=true"
        # Never Apprise's own icon: PBO's, or none.
        params.update({"image": "yes", "avatar_url": icon} if icon else {"image": "no"})
        return f"{'ntfys' if secure else 'ntfy'}://{auth}{host}{path}/{_q(config['topic'])}?{_query(params)}"
    if kind == "gotify":
        secure, host, path = _host_part(config["server_url"])
        query = _query({"priority": GOTIFY_PRIORITY.get(level, "normal")})
        return f"{'gotifys' if secure else 'gotify'}://{host}{path}/{_q(secrets['app_token'])}?{query}"
    if kind == "discord":
        match = _DISCORD.match(secrets["webhook_url"])
        if not match:
            raise ChannelError("invalid_discord_webhook", "webhook_url")
        # Posted as "PBO" with PBO's icon; the title opens the screen.
        params = {"avatar": "yes", "avatar_url": icon} if icon else {"avatar": "no"}
        if link:
            params["href"] = link
        return f"discord://PBO@{match.group(1)}/{match.group(2)}?{_query(params)}"
    if kind == "telegram":
        # The bot's own picture is set in BotFather; Apprise's image is not sent.
        return f"tgram://{_q(secrets['bot_token'])}/{_q(config['chat_id'])}?{_query({'image': 'no', 'preview': 'no'})}"
    if kind == "email":
        security = config.get("security") or "starttls"
        auth = ""
        if config.get("username"):
            auth = _q(config["username"]) + (":" + _q(secrets["password"]) if secrets.get("password") else "") + "@"
        params = {"from": config["from_address"], "to": ",".join(config["to_addresses"])}
        if security != "none":
            params["mode"] = security
        return f"{'mailto' if security == 'none' else 'mailtos'}://{auth}{config['host']}:{config['port']}?{_query(params)}"
    if kind == "webhook":
        secure, host, path = _host_part(config["url"])
        query = urlsplit(config["url"]).query
        params = {f"+{name[len(HEADER_PREFIX):]}": value for name, value in secrets.items() if name.startswith(HEADER_PREFIX)}
        params.update({f":{key}": value for key, value in (extras or {}).items()})
        tail = _query(params)
        if query:
            tail = f"{query}&{tail}" if tail else query
        return f"{'jsons' if secure else 'json'}://{host}{path}" + (f"?{tail}" if tail else "")
    if kind == "apprise":
        return secrets["url"]
    raise ChannelError("invalid_kind", "kind")


def redact(text: str, secrets: dict[str, str]) -> str:
    """Remove secret values (raw and URL-quoted) from an error message."""
    for value in secrets.values():
        if value and len(value) >= 4:
            text = text.replace(value, "•••").replace(_q(value), "•••")
    return text
