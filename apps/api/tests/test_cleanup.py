"""B29: the cleanup endpoints never fail with a 500 — foreign keys enforced, as
on PostgreSQL (a planned run points at the external backup run it started)."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.base import Base
from app.db.session import get_db
from app.models import (
    BackupRun,
    BackupRunStatus,
    DiskPreparationMode,
    DiskPreparationRun,
    ExternalBackupMode,
    ExternalBackupRun,
    ExternalDisk,
    ScheduledBackupEvent,
    ScheduledBackupRecurrenceType,
    ScheduledBackupRun,
    ScheduledBackupRunStatus,
    ScheduledBackupStartMode,
    VirtualMachine,
)
from app.models.virtual_machine import VMType

ADMIN = {"username": "admin", "password": "correct horse battery"}
NOW = datetime(2026, 11, 16, 12, 0)


@pytest.fixture
def fk_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):  # noqa: ANN001
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
    engine.dispose()


@pytest.fixture
def fk_client(fk_factory, secret_key):
    from app.auth import reset_login_attempts
    from app.main import app

    def _get_db():
        with fk_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    reset_login_attempts()
    client = TestClient(app, base_url="https://testserver")
    assert client.post("/api/v1/setup/admin", json=ADMIN).status_code == 201
    yield client
    app.dependency_overrides.pop(get_db, None)


def _external_runs(db):
    disk = ExternalDisk(serial_number="WD-A", display_name="WD", capacity_gb=3726, source="agent")
    db.add(disk)
    db.commit()
    event_ = ScheduledBackupEvent(
        title="Rotation", enabled=True, disk_serial="WD-A", datastore="main",
        recurrence_type=ScheduledBackupRecurrenceType.WEEKLY, timezone="UTC", window_starts_at=NOW,
        window_duration_minutes=60, notify_before_minutes=0, start_mode=ScheduledBackupStartMode.MANUAL_CONFIRMATION,
        created_at=NOW, updated_at=NOW,
    )
    db.add(event_)
    db.commit()
    runs = []
    for index in range(15):
        status = [BackupRunStatus.SUCCESS, BackupRunStatus.FAILED][index % 2]
        start = NOW - timedelta(days=index)
        run = ExternalBackupRun(disk_id=disk.id, status=status, started_at=start, finished_at=start + timedelta(minutes=5),
                                target_path="/x", datastore_name="main", mode=ExternalBackupMode.DEDICATED, created_at=start)
        db.add(run)
        db.commit()
        runs.append(run)
    # Old failed runs started by the planning: the planned run points at them.
    for run in runs[11:15:2]:
        db.add(ScheduledBackupRun(event_id=event_.id, scheduled_for=run.started_at, window_starts_at=run.started_at,
                                  window_ends_at=run.started_at + timedelta(hours=1), status=ScheduledBackupRunStatus.FAILURE,
                                  backup_run_id=run.id, created_at=NOW, updated_at=NOW))
    db.commit()
    return runs


def test_external_runs_cleanup_with_planned_runs(fk_client, fk_factory):
    with fk_factory() as db:
        runs = _external_runs(db)
        failed_old = [run.id for run in runs[10:] if run.status == BackupRunStatus.FAILED]
    response = fk_client.delete("/api/v1/external-backups/runs/cleanup", params={"keep_last": 10})
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == len(failed_old)
    with fk_factory() as db:
        left = {run.id for run in db.query(ExternalBackupRun)}
        assert not left & set(failed_old)
        assert len(left) == 15 - len(failed_old)
        planned = db.query(ScheduledBackupRun).all()
        # The planning history is kept, without the deleted run.
        assert len(planned) == 2 and all(run.backup_run_id is None for run in planned)


def test_single_run_delete_with_a_planned_run(fk_client, fk_factory):
    with fk_factory() as db:
        runs = _external_runs(db)
        linked = runs[11].id
    assert fk_client.delete(f"/api/v1/external-backups/runs/{linked}").status_code in {200, 204}


def test_other_cleanups(fk_client, fk_factory):
    with fk_factory() as db:
        vm = VirtualMachine(name="vm", vm_type=VMType.VM, source="proxmox", external_id="100")
        disk = ExternalDisk(serial_number="D", display_name="D", capacity_gb=100, source="agent")
        db.add_all([vm, disk])
        db.commit()
        for index in range(14):
            start = NOW - timedelta(days=index)
            status = BackupRunStatus.FAILED if index % 2 else BackupRunStatus.SUCCESS
            db.add(BackupRun(status=status, started_at=start, finished_at=start, triggered_by="test", summary="x"))
            db.add(DiskPreparationRun(disk_id=disk.id, status=status, started_at=start, finished_at=start,
                                      mode=DiskPreparationMode.DEDICATED_BACKUP, mount_path="/m", message="x", created_at=start))
        db.commit()
    for path in ("/api/v1/backup-runs/cleanup", "/api/v1/disks/preparation-runs/cleanup"):
        response = fk_client.delete(path, params={"keep_last": 10})
        assert response.status_code == 200, (path, response.text)
        assert response.json()["deleted"] == 2, path
