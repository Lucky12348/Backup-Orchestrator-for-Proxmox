"""Verification required after an unsafe removal, damaged versions found by a
verify, repair runs (B46/B47, Phase 5f.4).

Legacy tables (`create_all()`): columns added only when missing.

Revision ID: 0013_verify_required_repair
Revises: 0012_run_interrupted
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_verify_required_repair"
down_revision = "0012_run_interrupted"
branch_labels = None
depends_on = None

COLUMNS = {
    "external_disks": (("verify_required", sa.Boolean()), ("damaged_snapshots", sa.JSON())),
    "external_backup_runs": (("repair", sa.Boolean()),),
}


def _existing(table):
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
