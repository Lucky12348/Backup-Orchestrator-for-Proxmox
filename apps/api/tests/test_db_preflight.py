"""Refuse a non-UTF8 PostgreSQL database before SQLAlchemy connects."""

import pytest

from app.db import preflight
from app.db.preflight import DatabaseEncodingError, check_database_encoding, server_encoding

PG_URL = "postgresql+psycopg://pbo:s3cret@127.0.0.1:5432/pbo"


class _FakePgConn:
    def __init__(self, encoding):
        self.encoding = encoding

    def parameter_status(self, name):
        assert name == b"server_encoding"
        return self.encoding


class _FakeConnection:
    def __init__(self, encoding):
        self.pgconn = _FakePgConn(encoding)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _connect(encoding, seen=None):
    def connect(conninfo, **kwargs):
        if seen is not None:
            seen.append((conninfo, kwargs))
        return _FakeConnection(encoding)

    return connect


def test_utf8_database_passes():
    seen = []
    check_database_encoding(PG_URL, connect=_connect(b"UTF8", seen))
    # libpq conninfo: SQLAlchemy driver suffix removed, password kept.
    assert seen[0][0] == "postgresql://pbo:s3cret@127.0.0.1:5432/pbo"
    assert seen[0][1] == {"connect_timeout": 10}


def test_sql_ascii_database_is_refused_with_a_clear_message():
    with pytest.raises(DatabaseEncodingError) as info:
        check_database_encoding(PG_URL, connect=_connect(b"SQL_ASCII"))
    message = str(info.value)
    assert "'pbo' uses encoding SQL_ASCII" in message
    assert "requires UTF8" in message
    assert "-E UTF8 --locale=C.UTF-8 -T template0" in message
    assert "s3cret" not in message


def test_non_postgres_backends_are_not_checked():
    def boom(*args, **kwargs):
        raise AssertionError("must not connect")

    assert server_encoding("sqlite:///tmp/x.sqlite", connect=boom) is None
    check_database_encoding("sqlite:///:memory:", connect=boom)


def test_startup_refuses_without_traceback_noise(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "check_database_encoding", lambda url: (_ for _ in ()).throw(DatabaseEncodingError("bad encoding")))
    with pytest.raises(SystemExit, match="Startup refused: bad encoding"):
        main.ensure_database_encoding()


def test_preflight_cli_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(preflight, "check_database_encoding", lambda url: None)
    assert preflight.main() == 0

    def refuse(url):
        raise DatabaseEncodingError("database 'pbo' uses encoding SQL_ASCII")

    monkeypatch.setattr(preflight, "check_database_encoding", refuse)
    assert preflight.main() == 1
    assert "SQL_ASCII" in capsys.readouterr().err

    def unreachable(url):
        raise OSError("connection refused")

    monkeypatch.setattr(preflight, "check_database_encoding", unreachable)
    assert preflight.main() == 0  # the service starts and reports the connection error itself
