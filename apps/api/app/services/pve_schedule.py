"""Proxmox backup job schedules (systemd-like "calendar events") → occurrences.

Read-only display in the planning and the iCal export (Phase 6). The common
forms are understood; anything else is reported as unsupported (the job is
then listed without occurrences, never guessed):

* `21:00`, `2:30`, `02:00,14:30`        every day at those times
* `sun 01:00`, `mon,wed 3:00`, `mon..fri 22:00`, `sat..sun`  (00:00)
* `daily`, `weekly` (Monday 00:00), `*-*-* 02:30`; other forms (`*/6`, dates…) → unsupported

Times are wall-clock times of the PVE host, taken in the planning time zone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.services.recurrence import _zone, to_local, to_utc

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ICAL_DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


@dataclass(frozen=True)
class PveSchedule:
    weekdays: tuple[int, ...]  # 0 = Monday
    times: tuple[tuple[int, int], ...]

    def rrule(self) -> str:
        days = ",".join(ICAL_DAYS[day] for day in self.weekdays)
        hours = ",".join(sorted({str(hour) for hour, _ in self.times}, key=int))
        minutes = ",".join(sorted({str(minute) for _, minute in self.times}, key=int))
        return f"FREQ=WEEKLY;BYDAY={days};BYHOUR={hours};BYMINUTE={minutes}"


def _days(spec: str) -> tuple[int, ...] | None:
    result: list[int] = []
    for part in spec.split(","):
        part = part.strip().lower()
        if ".." in part:
            first, last = part.split("..", 1)
            if first not in DAYS or last not in DAYS:
                return None
            start, end = DAYS.index(first), DAYS.index(last)
            span = range(start, end + 1) if start <= end else [*range(start, 7), *range(0, end + 1)]
            result.extend(span)
        elif part in DAYS:
            result.append(DAYS.index(part))
        else:
            return None
    return tuple(sorted(set(result)))


def _times(spec: str) -> tuple[tuple[int, int], ...] | None:
    times: list[tuple[int, int]] = []
    for part in spec.split(","):
        match = _TIME_RE.match(part.strip())
        if not match:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            return None
        times.append((hour, minute))
    return tuple(sorted(set(times)))


def parse(schedule: str | None) -> PveSchedule | None:
    text = (schedule or "").strip().lower()
    if not text:
        return None
    every_day = tuple(range(7))
    if text == "daily":
        return PveSchedule(every_day, ((0, 0),))
    if text == "weekly":
        return PveSchedule((0,), ((0, 0),))
    if text.startswith("*-*-* "):
        text = text[len("*-*-* "):].strip()
        times = _times(text)
        return PveSchedule(every_day, times) if times else None
    parts = text.split()
    if len(parts) == 1:
        times = _times(parts[0])
        if times:
            return PveSchedule(every_day, times)
        days = _days(parts[0])
        return PveSchedule(days, ((0, 0),)) if days else None
    if len(parts) == 2:
        days, times = _days(parts[0]), _times(parts[1])
        return PveSchedule(days, times) if days and times else None
    return None


def occurrences(schedule: PveSchedule, range_start: datetime, range_end: datetime, zone_name: str | None) -> list[datetime]:
    """Naive UTC occurrences within [range_start, range_end] (max 1000)."""
    tz = _zone(zone_name)
    day = to_local(range_start, tz).date() - timedelta(days=1)
    last = to_local(range_end, tz).date() + timedelta(days=1)
    found: list[datetime] = []
    while day <= last and len(found) < 1000:
        if day.weekday() in schedule.weekdays:
            for hour, minute in schedule.times:
                moment = to_utc(datetime(day.year, day.month, day.day, hour, minute), tz)
                if range_start <= moment <= range_end:
                    found.append(moment)
        day += timedelta(days=1)
    return sorted(found)
