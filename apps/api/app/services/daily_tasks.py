"""Once-a-day work (Phase 5j), started in the lifespan.

* History retention (services/run_retention.py), after 03:30 in PBO's time
  zone, once per day.
* "N days without a backup" reminder for each approved disk listed as
  rotation_stale (services/disk_guards.py); the engine's 24 h dedup makes it
  one reminder a day at most.

The last run date is stored as `meta.daily_tasks` in app_settings, so a restart
does not run them twice.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AppSetting, ExternalDisk

logger = logging.getLogger(__name__)

MARKER_KEY = "meta.daily_tasks"
RUN_AFTER = dtime(3, 30)
INTERVAL_SECONDS = 600


def _zone() -> ZoneInfo:
    try:
        return ZoneInfo(get_settings().timezone or "UTC")
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def due(db: Session, now: datetime) -> bool:
    local = now.astimezone(_zone())
    if local.time() < RUN_AFTER:
        return False
    row = db.get(AppSetting, MARKER_KEY)
    return row is None or (row.value or "") < local.date().isoformat()


def run(db: Session, now: datetime | None = None) -> dict[str, int]:
    from app.services import notifications, run_retention
    from app.services.disk_guards import disk_alerts

    now = now or datetime.now(UTC)
    result = run_retention.apply(db, now=now)
    reminders = 0
    for alert in disk_alerts(db, now=now.replace(tzinfo=None)):
        if alert.get("code") != "rotation_stale":
            continue
        disk = db.get(ExternalDisk, alert.get("disk_id"))
        if disk is not None:
            notifications.backup_stale(disk, int(alert.get("params", {}).get("days") or 0))
            reminders += 1
    row = db.get(AppSetting, MARKER_KEY) or AppSetting(key=MARKER_KEY)
    row.value = now.astimezone(_zone()).date().isoformat()
    db.add(row)
    db.commit()
    logger.info("Daily tasks: %s runs deleted, %s journals cleared, %s stale reminders", len(result.runs), len(result.logs), reminders)
    return {"runs_deleted": len(result.runs), "logs_cleared": len(result.logs), "stale_reminders": reminders}


def _tick(session_factory: Callable[[], Session]) -> None:
    with session_factory() as db:
        now = datetime.now(UTC)
        if due(db, now):
            run(db, now)


async def watch(session_factory: Callable[[], Session]) -> None:
    loop = asyncio.get_running_loop()
    while True:
        try:
            await loop.run_in_executor(None, _tick, session_factory)
        except Exception:
            logger.exception("Daily tasks failed")
        await asyncio.sleep(INTERVAL_SECONDS)
