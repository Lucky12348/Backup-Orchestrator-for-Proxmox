"""Disk profile and safety guards (Phase 5f).

* `external_disks`: identity (colour, location note, photo), hardware read by
  the host agent (rotational, transport, USB link speed, SMART), retention,
  cached contents, usage history, usage alert level, "verification
  recommended" after a removal without eject.
* `external_backup_runs`: verify after backup, verify-only run.
* `scheduled_backup_events`: verify after backup.

These tables are still created by `create_all()` (legacy tables): every
column is added only when missing, so the migration is idempotent.

Revision ID: 0009_disk_profile_guards
Revises: 0008_disk_pbs_marker
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_disk_profile_guards"
down_revision = "0008_disk_pbs_marker"
branch_labels = None
depends_on = None

COLUMNS: dict[str, tuple[tuple[str, sa.types.TypeEngine], ...]] = {
    "external_disks": (
        ("color", sa.String(length=16)),
        ("location_note", sa.String(length=500)),
        ("photo_file", sa.String(length=128)),
        ("rotational", sa.Boolean()),
        ("transport", sa.String(length=32)),
        ("usb_speed_mbps", sa.Integer()),
        ("smart_status", sa.String(length=16)),
        ("smart_details", sa.JSON()),
        ("smart_checked_at", sa.DateTime()),
        ("retention", sa.JSON()),
        ("contents_cache", sa.JSON()),
        ("contents_cached_at", sa.DateTime()),
        ("usage_history", sa.JSON()),
        ("usage_alert_level", sa.Integer()),
        ("verify_recommended", sa.Boolean()),
        ("unsafe_removal_at", sa.DateTime()),
    ),
    "external_backup_runs": (
        ("verify_after_backup", sa.Boolean()),
        ("verify_only", sa.Boolean()),
    ),
    "scheduled_backup_events": (("verify_after_backup", sa.Boolean()),),
}


def _existing(table: str) -> set[str] | None:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return None
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    for table, columns in COLUMNS.items():
        existing = _existing(table)
        if existing is None:
            continue
        missing = [(name, type_) for name, type_ in columns if name not in existing]
        if missing:
            with op.batch_alter_table(table) as batch:
                for name, type_ in missing:
                    batch.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for table, columns in COLUMNS.items():
        existing = _existing(table)
        if existing is None:
            continue
        present = [name for name, _ in columns if name in existing]
        if present:
            with op.batch_alter_table(table) as batch:
                for name in present:
                    batch.drop_column(name)
