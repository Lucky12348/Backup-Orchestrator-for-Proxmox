"""Planning v2 (Phase 6): rotation over several disks, VM/CT selection, the
disk expected by each planned run, and the PBS group filter of a run.

`scheduled_backup_events`, `scheduled_backup_runs` and `external_backup_runs`
are still created by `create_all()` (legacy tables): on a fresh install the
columns already exist when this runs, on an upgrade they do not.

Revision ID: 0007_planning_rotation
Revises: 0006_disk_decision_partitions
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_planning_rotation"
down_revision = "0006_disk_decision_partitions"
branch_labels = None
depends_on = None

COLUMNS = {
    "scheduled_backup_events": (
        ("rotation_disk_serials", sa.JSON()),
        ("vm_groups", sa.JSON()),
    ),
    "scheduled_backup_runs": (("disk_serial", sa.String(length=255)),),
    "external_backup_runs": (("group_filter", sa.JSON()),),
}


def _columns(inspector, table: str) -> set[str] | None:
    if table not in inspector.get_table_names():
        return None
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, columns in COLUMNS.items():
        existing = _columns(inspector, table)
        if existing is None:
            continue
        missing = [(name, type_) for name, type_ in columns if name not in existing]
        if not missing:
            continue
        with op.batch_alter_table(table) as batch:
            for name, type_ in missing:
                batch.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, columns in COLUMNS.items():
        existing = _columns(inspector, table)
        if existing is None:
            continue
        present = [name for name, _ in columns if name in existing]
        if not present:
            continue
        with op.batch_alter_table(table) as batch:
            for name in present:
                batch.drop_column(name)
