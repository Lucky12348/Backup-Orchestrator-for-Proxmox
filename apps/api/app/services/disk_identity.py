from __future__ import annotations

import binascii
import string


SEPARATOR_CHARS = " _-"
VENDOR_PREFIXES = ("WDC", "WD")


def normalize_serial_for_compare(value: str | None) -> str:
    if value is None:
        return ""
    return "".join(char for char in value.strip().upper() if char not in SEPARATOR_CHARS)


def decode_hex_ascii_serial(value: str | None) -> str | None:
    clean = normalize_serial_for_compare(value)
    if len(clean) < 8 or len(clean) % 2 != 0:
        return None
    if any(char not in string.hexdigits.upper() for char in clean):
        return None
    try:
        decoded = binascii.unhexlify(clean).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if not decoded or any(char not in string.printable or char in "\r\n\t\x0b\x0c" for char in decoded):
        return None
    return decoded


def canonical_serial_number(value: str | None) -> str:
    decoded = decode_hex_ascii_serial(value)
    candidate = normalize_serial_for_compare(decoded or value)
    return _strip_vendor_prefix(candidate)


def serial_aliases(value: str | None) -> list[str]:
    aliases: list[str] = []
    for candidate in (value, decode_hex_ascii_serial(value)):
        normalized = normalize_serial_for_compare(candidate)
        if not normalized:
            continue
        _append_unique(aliases, normalized)
        _append_unique(aliases, _strip_vendor_prefix(normalized))
    canonical = canonical_serial_number(value)
    if canonical:
        _append_unique(aliases, canonical)
    return aliases


def serials_match(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    left_aliases = set(serial_aliases(left))
    right_aliases = set(serial_aliases(right))
    return bool(left_aliases & right_aliases)


def disk_serial_candidates(disk: object) -> list[str]:
    """Every serial known for a disk record: stored (canonical) serial, the one
    the agent reported, the canonical form and the recorded aliases."""
    values: list[str] = []
    for value in (
        getattr(disk, "serial_number", None),
        getattr(disk, "reported_serial_number", None),
        getattr(disk, "canonical_serial_number", None),
        *(getattr(disk, "serial_aliases", None) or []),
    ):
        if isinstance(value, str) and value.strip():
            _append_unique(values, value.strip())
    return values


def disk_matches_serial(disk: object, serial: str | None) -> bool:
    """THE way to compare a serial seen elsewhere (Proxmox USB list, PBS disk
    list, planning event…) with a disk record: case, spaces, dashes,
    underscores, hex-encoded serials and vendor prefixes ("WD-") ignored."""
    if not serial or not str(serial).strip():
        return False
    return any(serials_match(candidate, serial) for candidate in disk_serial_candidates(disk))


def _strip_vendor_prefix(value: str) -> str:
    for prefix in VENDOR_PREFIXES:
        if value.startswith(prefix):
            remainder = value[len(prefix) :]
            if len(remainder) >= 6 and remainder.startswith("W"):
                return remainder
    return value


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)
