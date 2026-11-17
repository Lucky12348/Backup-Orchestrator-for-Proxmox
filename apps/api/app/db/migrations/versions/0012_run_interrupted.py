"""A run interrupted by the disk being pulled out (B44, Phase 5f.3).

`external_backup_runs` is still created by `create_all()` (legacy table): the
column is added only when missing.

Revision ID: 0012_run_interrupted
Revises: 0011_pbs_usb_mappings
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_run_interrupted"
down_revision = "0011_pbs_usb_mappings"
branch_labels = None
depends_on = None

TABLE = "external_backup_runs"


def _columns() -> set[str] | None:
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return None
    return {column["name"] for column in inspector.get_columns(TABLE)}


def upgrade() -> None:
    existing = _columns()
    if existing is not None and "interrupted" not in existing:
        with op.batch_alter_table(TABLE) as batch:
            batch.add_column(sa.Column("interrupted", sa.String(length=32), nullable=True))


def downgrade() -> None:
    existing = _columns()
    if existing is not None and "interrupted" in existing:
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_column("interrupted")
