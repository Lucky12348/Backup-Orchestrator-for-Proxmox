"""Occurrences of a planned backup, in the planning's time zone (Phase 6).

`window_starts_at` is stored as naive UTC. Occurrences repeat the LOCAL wall
time of that first occurrence in `event.timezone`: "every Sunday at 09:00
Europe/Paris" stays at 09:00 across daylight saving changes (it moves by one
hour in UTC). Returned datetimes are naive UTC, like everything else stored.

`recurrence_config`:
* `interval` (1–52): every N days / weeks / months (default 1);
* `weekdays` (weekly only, 0 = Monday … 6 = Sunday): several days a week
  (Phase 6c). Without it, the weekday of the first occurrence.

Weekly = the weekday of the first occurrence; monthly = its day of month,
clamped to the last day of shorter months (31 → 30 → 28/29).

`occurrence_index` numbers the occurrences from 0: the rotation uses disk
`serials[index % len(serials)]`.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_INTERVAL = 52
_MAX_STEPS = 5000


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def interval_of(event: Any) -> int:
    config = event.recurrence_config if isinstance(getattr(event, "recurrence_config", None), dict) else {}
    try:
        value = int(config.get("interval") or 1)
    except (TypeError, ValueError):
        value = 1
    return max(1, min(MAX_INTERVAL, value))


def weekdays_of(event: Any) -> list[int]:
    config = event.recurrence_config if isinstance(getattr(event, "recurrence_config", None), dict) else {}
    days = sorted({int(day) for day in config.get("weekdays") or [] if str(day).isdigit() and 0 <= int(day) <= 6})
    return days


def _week_days(event: Any, first: datetime) -> list[int]:
    return weekdays_of(event) or [first.weekday()]


def _weekly_local(first: datetime, days: list[int], interval: int, n: int) -> datetime:
    """n-th slot (from the first week's Monday) of a multi-day weekly rule."""
    week, slot = divmod(n, len(days))
    monday = first - timedelta(days=first.weekday())
    return monday + timedelta(weeks=week * interval, days=days[slot])


def _first_slot(first: datetime, days: list[int]) -> int:
    """Slots of the first week before the first occurrence are skipped."""
    return sum(1 for day in days if day < first.weekday())


def _kind(event: Any) -> str:
    value = getattr(event.recurrence_type, "value", event.recurrence_type)
    return str(value)


def to_local(utc_naive: datetime, tz: ZoneInfo) -> datetime:
    """Naive UTC → naive local wall time."""
    return utc_naive.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None)


def to_utc(local_naive: datetime, tz: ZoneInfo) -> datetime:
    """Naive local wall time → naive UTC (a time skipped by DST moves forward)."""
    return local_naive.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def _add_months(value: datetime, months: int, day: int) -> datetime:
    total = value.year * 12 + (value.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    return value.replace(year=year, month=month, day=min(day, calendar.monthrange(year, month)[1]))


def _local_occurrence(first: datetime, kind: str, interval: int, k: int) -> datetime:
    if kind == "daily":
        return first + timedelta(days=k * interval)
    if kind == "weekly":
        return first + timedelta(weeks=k * interval)
    if kind == "monthly":
        return _add_months(first, k * interval, first.day)
    return first


def _first_index(first: datetime, kind: str, interval: int, local_target: datetime) -> int:
    """An index at or just before the occurrence closest to `local_target`."""
    if kind == "daily":
        k = (local_target.date() - first.date()).days // interval
    elif kind == "weekly":
        k = (local_target.date() - first.date()).days // (7 * interval)
    elif kind == "monthly":
        k = ((local_target.year - first.year) * 12 + local_target.month - first.month) // interval
    else:
        return 0
    return max(0, k - 1)


def expand(event: Any, range_start: datetime, range_end: datetime) -> list[datetime]:
    """Occurrences (naive UTC) within [range_start, range_end]."""
    tz = _zone(getattr(event, "timezone", None))
    kind = _kind(event)
    interval = interval_of(event)
    first_local = to_local(event.window_starts_at, tz)
    if kind == "once":
        return [event.window_starts_at] if range_start <= event.window_starts_at <= range_end else []
    if kind == "weekly" and weekdays_of(event):
        days = _week_days(event, first_local)
        weeks = max(0, (to_local(range_start, tz).date() - first_local.date()).days // (7 * interval) - 1)
        n = max(_first_slot(first_local, days), weeks * len(days))
        found: list[datetime] = []
        for _ in range(_MAX_STEPS):
            local = _weekly_local(first_local, days, interval, n)
            occurrence = to_utc(local, tz)
            if occurrence > range_end:
                break
            if occurrence >= range_start and local >= first_local:
                found.append(occurrence)
            n += 1
        return found
    k = _first_index(first_local, kind, interval, to_local(range_start, tz))
    occurrences: list[datetime] = []
    for _ in range(_MAX_STEPS):
        occurrence = to_utc(_local_occurrence(first_local, kind, interval, k), tz)
        if occurrence > range_end:
            break
        if occurrence >= range_start:
            occurrences.append(occurrence)
        k += 1
    return occurrences


def occurrence_index(event: Any, scheduled_for: datetime) -> int | None:
    """0 for the first occurrence, 1 for the next… None if not an occurrence."""
    tz = _zone(getattr(event, "timezone", None))
    kind = _kind(event)
    if kind == "once":
        return 0 if scheduled_for == event.window_starts_at else None
    interval = interval_of(event)
    first_local = to_local(event.window_starts_at, tz)
    if kind == "weekly" and weekdays_of(event):
        days = _week_days(event, first_local)
        local = to_local(scheduled_for, tz)
        week = (local.date() - (first_local.date() - timedelta(days=first_local.weekday()))).days // 7
        if week % interval or local.weekday() not in days:
            return None
        n = (week // interval) * len(days) + days.index(local.weekday())
        if to_utc(_weekly_local(first_local, days, interval, n), tz) != scheduled_for or local < first_local:
            return None
        return n - _first_slot(first_local, days)
    k = _first_index(first_local, kind, interval, to_local(scheduled_for, tz))
    for candidate in range(k, k + 4):
        if to_utc(_local_occurrence(first_local, kind, interval, candidate), tz) == scheduled_for:
            return candidate
    return None


def rotation_serials(event: Any) -> list[str]:
    serials = [str(item).strip() for item in (getattr(event, "rotation_disk_serials", None) or []) if str(item).strip()]
    return serials or [event.disk_serial]


def disk_serial_for(event: Any, scheduled_for: datetime) -> str:
    """Disk expected by this occurrence (rotation in order, then again)."""
    serials = rotation_serials(event)
    index = occurrence_index(event, scheduled_for)
    return serials[(index or 0) % len(serials)]


def local_day(utc_naive: datetime, zone_name: str | None) -> date:
    return to_local(utc_naive, _zone(zone_name)).date()
