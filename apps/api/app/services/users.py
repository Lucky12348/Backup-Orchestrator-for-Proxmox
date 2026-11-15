"""Operator accounts (`users` table).

The product is still single-admin: the first account is created by the setup
wizard (POST /setup/admin) or imported once from the legacy AUTH_USERNAME /
AUTH_PASSWORD_HASH environment values. Creating a second account through the
setup endpoint is refused.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from functools import lru_cache

from passlib.hash import bcrypt
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.models.user import User

logger = logging.getLogger(__name__)

# Primary-key sentinel: two concurrent setup requests cannot both insert it,
# so at most one "first admin" is ever created.
FIRST_ADMIN_MARKER_KEY = "meta.first_admin_created"
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._@-]{3,64}$")
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_BYTES = 72  # bcrypt limit


class SetupAlreadyDoneError(RuntimeError):
    pass


class InvalidCredentialsFormatError(ValueError):
    pass


def has_users(db: Session) -> bool:
    return bool(db.scalar(select(func.count()).select_from(User)))


def validate_new_credentials(username: str, password: str) -> None:
    if not USERNAME_PATTERN.fullmatch(username):
        raise InvalidCredentialsFormatError(
            "Username must be 3-64 characters: letters, digits, '.', '_', '@' or '-'."
        )
    if len(password) < MIN_PASSWORD_LENGTH:
        raise InvalidCredentialsFormatError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise InvalidCredentialsFormatError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes long.")


def hash_password(password: str) -> str:
    return bcrypt.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.verify(plain, hashed)
    except Exception:  # malformed hash, oversize input…
        return False


def looks_like_bcrypt_hash(value: str) -> bool:
    return value.startswith("$2") and len(value) >= 50


def _create_first_admin(db: Session, username: str, password_hash: str) -> User:
    if has_users(db):
        raise SetupAlreadyDoneError("An administrator account already exists.")
    user = User(username=username, password_hash=password_hash, is_admin=True)
    db.add(AppSetting(key=FIRST_ADMIN_MARKER_KEY, value=username, is_secret=False))
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise SetupAlreadyDoneError("An administrator account already exists.") from exc
    db.refresh(user)
    return user


def create_first_admin(db: Session, username: str, password: str) -> User:
    username = username.strip()
    validate_new_credentials(username, password)
    return _create_first_admin(db, username, hash_password(password))


def bootstrap_admin_from_env(db: Session, environ: Mapping[str, str]) -> User | None:
    """Import the legacy single account from AUTH_USERNAME / AUTH_PASSWORD_HASH.

    Only when no account exists yet, so upgraded deployments keep their login.
    """
    password_hash = (environ.get("AUTH_PASSWORD_HASH") or "").strip()
    if not password_hash or has_users(db):
        return None
    if not looks_like_bcrypt_hash(password_hash):
        logger.warning(
            "AUTH_PASSWORD_HASH is set but does not look like a bcrypt hash (in a Compose .env every '$' "
            "must be written '$$'); not importing it. Create the admin account from the setup screen."
        )
        return None
    username = (environ.get("AUTH_USERNAME") or "admin").strip() or "admin"
    try:
        user = _create_first_admin(db, username, password_hash)
    except SetupAlreadyDoneError:
        return None
    logger.info("Imported administrator account %r from AUTH_USERNAME/AUTH_PASSWORD_HASH.", username)
    return user


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return bcrypt.hash("pbo-timing-equalizer")


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        # Spend comparable time to avoid a username-existence oracle.
        verify_password(password, _dummy_hash())
        return None
    if not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return user


def get_user(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))
