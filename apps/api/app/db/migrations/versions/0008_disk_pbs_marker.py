"""PBO marker of a rotation disk's datastore (Phase 6c): the filesystem UUID
(`backing-device`) and the on-device path PBO recorded when it created or
reused the datastore. They allow re-creating a deleted PBS configuration with
`reuse-datastore` — never by formatting.

`external_disks` is still created by `create_all()` (legacy table).

Revision ID: 0008_disk_pbs_marker
Revises: 0007_planning_rotation
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_disk_pbs_marker"
down_revision = "0007_planning_rotation"
branch_labels = None
depends_on = None

TABLE = "external_disks"
COLUMNS = (("pbs_backing_device", sa.String(length=128)), ("pbs_datastore_path", sa.String(length=512)))


def _columns() -> set[str] | None:
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return None
    return {column["name"] for column in inspector.get_columns(TABLE)}


def upgrade() -> None:
    existing = _columns()
    if existing is None:
        return
    missing = [(name, type_) for name, type_ in COLUMNS if name not in existing]
    if missing:
        with op.batch_alter_table(TABLE) as batch:
            for name, type_ in missing:
                batch.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    existing = _columns()
    if existing is None:
        return
    present = [name for name, _ in COLUMNS if name in existing]
    if present:
        with op.batch_alter_table(TABLE) as batch:
            for name in present:
                batch.drop_column(name)
