import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register tables on Base.metadata)
from app.db.base import Base
from app.db.session import get_db

TEST_SECRET_KEY = "test-secret-key-0123456789abcdef0123456789abcdef"


@pytest.fixture
def secret_key(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET_KEY", TEST_SECRET_KEY)
    return TEST_SECRET_KEY


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
    engine.dispose()


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session


@pytest.fixture
def client(session_factory, secret_key):
    """HTTPS test client (the session cookie is Secure) on an in-memory database."""
    from app.auth import reset_login_attempts
    from app.main import app

    def _get_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    reset_login_attempts()
    # Not used as a context manager on purpose: that would run the lifespan
    # (migrations against the real DATABASE_URL, scheduler...).
    yield TestClient(app, base_url="https://testserver")
    app.dependency_overrides.pop(get_db, None)
    reset_login_attempts()


@pytest.fixture(autouse=True)
def _no_background_agent_update(monkeypatch):
    """Heartbeats of an agent older than the shipped build start an update in a
    thread on the real database: recorded instead (tests/test_agent_updates.py
    runs them explicitly)."""
    from app.services import agent_updates

    started: list[int] = []
    monkeypatch.setattr(agent_updates, "_spawn", lambda run, update_id: started.append(update_id))
    return started
