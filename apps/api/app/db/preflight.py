"""Database checks that must run before SQLAlchemy touches the database.

A PostgreSQL database in SQL_ASCII (cluster initialised under a locale missing
from the container, e.g. LANG=en_US.UTF-8 forwarded by SSH) makes psycopg return
bytes, and SQLAlchemy crashes on its very first connection with
"TypeError: cannot use a string pattern on a bytes-like object". The encoding
is therefore read from the `server_encoding` parameter the server sends at
connection time (no query, no decoding involved) with plain psycopg.

Also usable as `python -m app.db.preflight` (ExecStartPre of pbo.service):
prints the explanation and exits 1 instead of a Python traceback.
"""

from __future__ import annotations

import logging
import sys

from sqlalchemy.engine import make_url

logger = logging.getLogger(__name__)

UTF8_NAMES = {"UTF8", "UTF-8"}


class DatabaseEncodingError(RuntimeError):
    pass


def _libpq_conninfo(database_url: str) -> str | None:
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        return None
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def server_encoding(database_url: str, *, connect=None) -> str | None:  # noqa: ANN001
    """`server_encoding` of the PostgreSQL server, None for other backends."""
    conninfo = _libpq_conninfo(database_url)
    if conninfo is None:
        return None
    if connect is None:
        import psycopg

        connect = psycopg.connect
    with connect(conninfo, connect_timeout=10) as conn:
        raw = conn.pgconn.parameter_status(b"server_encoding")
    if raw is None:
        return None
    return raw.decode("ascii", "replace") if isinstance(raw, bytes) else str(raw)


def encoding_error_message(database: str, encoding: str) -> str:
    return (
        f"PostgreSQL database '{database}' uses encoding {encoding}; "
        "Proxmox Backup Orchestrator requires UTF8 and will not start.\n"
        "Cause: the cluster was initialised under a locale missing from the system "
        "(e.g. LANG=en_US.UTF-8 forwarded by SSH).\n"
        "Fix: re-run the installer (an empty database is recreated in UTF8), or convert it:\n"
        f"  runuser -u postgres -- pg_dump -Fc {database} > /root/pbo.dump\n"
        f"  runuser -u postgres -- dropdb {database}\n"
        f"  runuser -u postgres -- createdb -O pbo -E UTF8 --locale=C.UTF-8 -T template0 {database}\n"
        f"  runuser -u postgres -- pg_restore -d {database} --no-owner --role pbo /root/pbo.dump\n"
        "See docs/INSTALLATION.md, Troubleshooting."
    )


def check_database_encoding(database_url: str, *, connect=None) -> None:  # noqa: ANN001
    """Raise DatabaseEncodingError when the PostgreSQL database is not UTF8.

    Connection failures are not this check's business: they propagate to the
    caller (the normal startup path reports them).
    """
    encoding = server_encoding(database_url, connect=connect)
    if encoding is not None and encoding.upper() not in UTF8_NAMES:
        database = make_url(database_url).database or "?"
        raise DatabaseEncodingError(encoding_error_message(database, encoding))


def main() -> int:
    from app.core.config import get_env_settings

    try:
        check_database_encoding(get_env_settings().database_url)
    except DatabaseEncodingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # unreachable database: let the service start and report it
        print(f"WARNING: database encoding not checked ({exc.__class__.__name__}: {exc})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
