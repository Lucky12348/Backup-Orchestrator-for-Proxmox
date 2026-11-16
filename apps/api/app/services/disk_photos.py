"""Disk photos (Phase 5f): uploaded by the operator, stored locally.

The upload is never stored as sent: it is decoded with Pillow (JPEG, PNG or
WebP only, whatever the declared content type says), rotated by its EXIF
orientation, reduced to at most MAX_SIDE px and re-encoded as WebP — which
also drops EXIF/GPS metadata. Files live in `<data dir>/disk-photos`
(LXC: /var/lib/pbo via systemd StateDirectory=pbo), named after the disk id
plus a random suffix (the URL changes with each upload: no stale cache).
"""

from __future__ import annotations

import io
import os
import secrets
from pathlib import Path

from fastapi import HTTPException, status

from app.core.config import get_settings
from app.models import ExternalDisk

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_SIDE = 1024
MAX_PIXELS = 40_000_000  # decompression-bomb guard (≈ 7300 × 5500)
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
PHOTO_SUBDIR = "disk-photos"


def data_dir() -> Path:
    configured = get_settings().data_dir or (os.getenv("STATE_DIRECTORY") or "").split(":")[0]
    return Path(configured or "/var/lib/pbo")


def photo_dir() -> Path:
    return data_dir() / PHOTO_SUBDIR


def _bad(code: str, message: str, http_status: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=http_status, detail={"code": code, "message": message})


def photo_path(disk: ExternalDisk) -> Path | None:
    name = disk.photo_file
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    path = photo_dir() / name
    return path if path.is_file() else None


def encode_photo(data: bytes) -> bytes:
    from PIL import Image, ImageOps, UnidentifiedImageError

    if len(data) > MAX_UPLOAD_BYTES:
        raise _bad("photo_too_large", "The photo must be 5 MB or less.", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    if not data:
        raise _bad("photo_unreadable", "The file is empty.")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise _bad("photo_invalid_type", "Only JPEG, PNG or WebP photos are accepted.")
            probe.verify()
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGBA" if image.mode in {"RGBA", "LA", "P"} else "RGB")
            image.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            image.save(out, format="WEBP", quality=82, method=4)
            return out.getvalue()
    except HTTPException:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError) as exc:
        raise _bad("photo_unreadable", "The file is not a readable JPEG, PNG or WebP image.") from exc


def save_photo(disk: ExternalDisk, data: bytes) -> str:
    encoded = encode_photo(data)
    directory = photo_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        name = f"disk-{disk.id}-{secrets.token_hex(6)}.webp"
        tmp = directory / f".{name}.tmp"
        tmp.write_bytes(encoded)
        tmp.replace(directory / name)
    except OSError as exc:
        raise _bad("photo_storage_failed", f"The photo could not be stored ({exc.strerror}).", status.HTTP_500_INTERNAL_SERVER_ERROR) from exc
    remove_photo_file(disk)
    disk.photo_file = name
    return name


def remove_photo_file(disk: ExternalDisk) -> None:
    path = photo_path(disk)
    if path is not None:
        try:
            path.unlink()
        except OSError:
            pass
    disk.photo_file = None
