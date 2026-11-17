"""Datastores managed by the app (Phase 6c): consistency checks after each PBS
sync, and the source datastore choice.

Checks (PBS_MODE=api, PBS configured), against `GET /config/datastore`:

a) the source datastore (`PBS_DATASTORE`, and the one of each planning) no
   longer exists → `source.state = "missing"`: backups refused
   (`source_datastore_missing`), plannings shown "blocked", "To do" alert;
b) the PBS configuration of a rotation disk's datastore (pbo-…) was deleted →
   disk `config_missing`; `recoverable` when PBO recorded its marker
   (filesystem UUID + path): the next backup re-creates it with
   reuse-datastore (services/pbs_guards.py), never by formatting;
c) a datastore carries the name a disk expects but not the PBO marker
   (`pbo:serial=…` comment) → disk `conflict`, nothing automatic.

The result is stored (non-secret JSON, `app_settings` key
`meta.datastore_health`) so pages and readiness read it without calling PBS.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.models.external_disk import ExternalDisk
from app.models.scheduled_backup import ScheduledBackupEvent
from app.services.pbs_guards import has_pbo_marker

HEALTH_KEY = "meta.datastore_health"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _client(settings: Any):
    from app.services.pbs_client import PBSClient

    return PBSClient(settings)


def _store(db: Session, value: dict[str, Any]) -> None:
    row = db.get(AppSetting, HEALTH_KEY)
    text = json.dumps(value)
    if row is None:
        db.add(AppSetting(key=HEALTH_KEY, value=text, is_secret=False))
    else:
        row.value = text
        row.is_secret = False
    db.commit()


def read_health(db: Session) -> dict[str, Any] | None:
    row = db.get(AppSetting, HEALTH_KEY)
    if row is None or not row.value:
        return None
    try:
        value = json.loads(row.value)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def expected_name(disk: ExternalDisk) -> str | None:
    if disk.pbs_datastore_name:
        return disk.pbs_datastore_name
    if disk.trusted and disk.dedicated_backup_disk:
        from app.services.external_backup_execution import build_dedicated_datastore_name

        return build_dedicated_datastore_name(disk.serial_number)
    return None


def evaluate(db: Session, configs: list[dict[str, Any]], source: str) -> dict[str, Any]:
    """Pure part of the check (configs = GET /config/datastore)."""
    by_name = {str(cfg.get("name")): cfg for cfg in configs if cfg.get("name")}
    disks: dict[str, dict[str, Any]] = {}
    for disk in db.scalars(select(ExternalDisk).where(ExternalDisk.source != "seed")):
        name = expected_name(disk)
        if not name or disk.serial_number.startswith("agent-report::"):
            continue
        config = by_name.get(name)
        prepared = bool(disk.prepared_as_pbs_datastore and disk.pbs_datastore_name)
        if config is None:
            if prepared:
                # Recoverable: PBO knows the filesystem UUID; the run checks it on
                # the plugged disk before re-creating anything (never a format).
                disks[str(disk.id)] = {
                    "state": "config_missing",
                    "datastore": name,
                    "recoverable": bool(disk.pbs_backing_device),
                    "marker_source": disk.pbs_marker_source,
                }
            continue
        if not has_pbo_marker(config, disk.serial_number):
            disks[str(disk.id)] = {"state": "conflict", "datastore": name, "comment": config.get("comment")}
        else:
            disks[str(disk.id)] = {"state": "ok", "datastore": name}
    events = list(db.scalars(select(ScheduledBackupEvent).where(ScheduledBackupEvent.deleted_at.is_(None))))
    blocked = sorted(event.id for event in events if event.datastore and event.datastore not in by_name)
    return {
        "checked": True,
        "checked_at": _now(),
        "error": None,
        "datastores": sorted(by_name),
        "source": {"name": source, "state": "ok" if source in by_name else "missing"},
        "disks": disks,
        "blocked_events": blocked,
    }


def check(db: Session, *, client: Any | None = None) -> dict[str, Any]:
    from app.services.settings_service import effective_settings

    settings = effective_settings(db)
    if settings.pbs_mode != "api" or not settings.pbs_api_url:
        value = {"checked": False, "checked_at": _now(), "error": None, "reason": "not_api_mode", "source": {"name": settings.pbs_datastore, "state": "unknown"}, "disks": {}, "blocked_events": [], "datastores": []}
    else:
        try:
            configs = (client or _client(settings)).list_datastores()
        except Exception as exc:  # noqa: BLE001 - PBS unreachable: keep the previous verdict, say why
            previous = read_health(db) or {}
            value = {**previous, "checked": bool(previous.get("checked")), "error": str(exc), "checked_at": _now()}
            value.setdefault("source", {"name": settings.pbs_datastore, "state": "unknown"})
            value.setdefault("disks", {})
            value.setdefault("blocked_events", [])
            value.setdefault("datastores", [])
        else:
            from app.services.pbs_marker import backfill

            backfill(db, configs)
            value = evaluate(db, configs, settings.pbs_datastore)
    _store(db, value)
    return value


def disk_state(health: dict[str, Any] | None, disk_id: int) -> dict[str, Any] | None:
    return ((health or {}).get("disks") or {}).get(str(disk_id))


def source_missing(health: dict[str, Any] | None) -> bool:
    return bool(health and health.get("checked") and (health.get("source") or {}).get("state") == "missing")


def list_source_candidates(db: Session, *, client: Any | None = None) -> list[dict[str, Any]]:
    """Datastores offered as the backup source: not the rotation disks' ones."""
    from app.services.settings_service import effective_settings

    settings = effective_settings(db)
    pbs = client or _client(settings)
    items = []
    for cfg in pbs.list_datastores():
        name = str(cfg.get("name") or "")
        if not name or "pbo:serial=" in str(cfg.get("comment") or ""):
            continue
        try:
            status = pbs.datastore_status(name)
        except Exception:  # noqa: BLE001 - an unmounted store has no status
            status = {}
        items.append({"name": name, "total": status.get("total"), "used": status.get("used"), "removable": bool(cfg.get("backing-device"))})
    return sorted(items, key=lambda item: item["name"])


def choose_source(db: Session, name: str, *, client: Any | None = None) -> dict[str, Any]:
    """"Choose another datastore": the setting and the plannings that used the
    old (or a missing) one switch to `name`."""
    from fastapi import HTTPException, status

    from app.services.settings_service import effective_settings, update_settings

    settings = effective_settings(db)
    pbs = client or _client(settings)
    names = {str(cfg.get("name")) for cfg in pbs.list_datastores()}
    if name not in names:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "datastore_unknown", "message": f"Datastore `{name}` does not exist on PBS."})
    old = settings.pbs_datastore
    update_settings(db, {"PBS_DATASTORE": name})
    for event in db.scalars(select(ScheduledBackupEvent).where(ScheduledBackupEvent.deleted_at.is_(None))):
        if event.datastore == old or event.datastore not in names:
            event.datastore = name
    db.commit()
    return check(db, client=pbs)
