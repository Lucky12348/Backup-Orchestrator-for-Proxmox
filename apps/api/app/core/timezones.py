"""IANA time zone names (planning, wizard). No tz database = no validation
possible: on Debian the system tzdata is used, elsewhere the `tzdata` wheel."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Fallback when nothing was chosen: the historical value (D17), so upgraded
# installs keep their behaviour. The setup wizard stores the browser's zone.
DEFAULT_TIMEZONE = "Europe/Paris"


class InvalidTimezoneError(ValueError):
    pass


def validate_timezone(name: str | None) -> str:
    value = (name or "").strip()
    if not value or len(value) > 64 or value.startswith(("/", ".")) or ".." in value:
        raise InvalidTimezoneError(f"Invalid time zone: {name!r}.")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidTimezoneError(f"Unknown time zone: {value!r}.") from exc
    return value
