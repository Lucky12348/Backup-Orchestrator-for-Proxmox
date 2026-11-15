"""Session authentication for the operator UI.

* Accounts live in the `users` table (see app/services/users.py).
* Login sets a signed JWT in an HttpOnly, SameSite=Strict cookie (Secure unless
  AUTH_COOKIE_SECURE=false). The browser never sees the token, so it cannot be
  read by injected scripts; SameSite=Strict keeps it off cross-site requests.
* There is no switch to disable authentication.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import get_secret_key, validate_secret_key
from app.db.session import get_db
from app.services.users import authenticate, get_user

SESSION_COOKIE_NAME = "pbo_session"
AUTH_ALGORITHM = "HS256"
MAX_AUTH_TOKEN_EXPIRE_MINUTES = 180

router = APIRouter(prefix="/auth", tags=["auth"])

# Lightweight in-process login lockout. Deployments run one uvicorn worker, so
# a module-level dict is enough to slow down brute force on the admin account.
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300
_login_attempts_lock = Lock()
_login_attempts: dict[str, list[float]] = defaultdict(list)


def _login_client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _prune_attempts(key: str, now: float) -> list[float]:
    attempts = [attempt for attempt in _login_attempts[key] if now - attempt < LOGIN_WINDOW_SECONDS]
    _login_attempts[key] = attempts
    return attempts


def _assert_login_not_locked_out(key: str) -> None:
    now = time.monotonic()
    with _login_attempts_lock:
        if len(_prune_attempts(key, now)) >= LOGIN_MAX_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts. Try again later.",
            )


def _register_failed_login(key: str) -> None:
    now = time.monotonic()
    with _login_attempts_lock:
        _prune_attempts(key, now).append(now)


def reset_login_attempts() -> None:
    with _login_attempts_lock:
        _login_attempts.clear()


class SessionInfo(BaseModel):
    username: str
    expires_at: datetime | None = None


def validate_auth_settings() -> None:
    """Fail startup early when the signing/encryption key is unsafe."""
    validate_secret_key()


def _token_lifetime() -> timedelta:
    minutes = get_settings().auth_token_expire_minutes
    return timedelta(minutes=max(5, min(int(minutes), MAX_AUTH_TOKEN_EXPIRE_MINUTES)))


def _create_access_token(username: str, lifetime: timedelta) -> tuple[str, datetime]:
    expires_at = datetime.now(timezone.utc) + lifetime
    token = jwt.encode({"sub": username, "exp": expires_at}, get_secret_key(), algorithm=AUTH_ALGORITHM)
    return token, expires_at


def start_session(response: Response, username: str) -> SessionInfo:
    lifetime = _token_lifetime()
    token, expires_at = _create_access_token(username, lifetime)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=int(lifetime.total_seconds()),
        httponly=True,
        secure=get_settings().auth_cookie_secure,
        samesite="strict",
        path="/",
    )
    return SessionInfo(username=username, expires_at=expires_at)


def end_session(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=get_settings().auth_cookie_secure,
        samesite="strict",
    )


credentials_exception = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
)


def get_current_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> str:
    """Validate the session cookie and return the username of an existing account."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise credentials_exception
    try:
        payload = jwt.decode(token, get_secret_key(), algorithms=[AUTH_ALGORITHM])
    except JWTError:
        raise credentials_exception from None
    username = payload.get("sub")
    if not isinstance(username, str) or get_user(db, username) is None:
        raise credentials_exception
    return username


CurrentUser = Annotated[str, Depends(get_current_user)]


@router.post("/token", response_model=SessionInfo)
def login(
    request: Request,
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
) -> SessionInfo:
    """Check username/password and open a cookie session."""
    client_key = _login_client_key(request)
    _assert_login_not_locked_out(client_key)

    user = authenticate(db, form_data.username, form_data.password)
    if user is None:
        _register_failed_login(client_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")

    return start_session(response, user.username)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> Response:
    end_session(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=SessionInfo)
def me(username: CurrentUser) -> SessionInfo:
    return SessionInfo(username=username)
