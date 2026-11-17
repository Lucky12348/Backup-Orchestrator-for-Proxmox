"""Where the PBO marker of a rotation disk comes from (created | pbs_config |
run_log) and when it was recorded — the proof that a filesystem holds the
disk's datastore, kept by PBO independently of the PBS configuration.

`external_disks` is still created by `create_all()` (legacy table).

Revision ID: 0010_disk_pbs_marker_source
Revises: 0009_disk_profile_guards
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_disk_pbs_marker_source"
down_revision = "0009_disk_profile_guards"
branch_labels = None
depends_on = None

TABLE = "external_disks"
COLUMNS = (("pbs_marker_source", sa.String(length=16)), ("pbs_marker_recorded_at", sa.DateTime()))


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
