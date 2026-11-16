"""Operator decision on a detected disk (ignored / rejected) and the
partitions the host agent saw on it (shown before approval).

`external_disks` is still created by `create_all()` (legacy table): on a fresh
install the columns already exist when this runs, on an upgrade they do not.

Revision ID: 0006_disk_decision_partitions
Revises: 0005_disk_usb_location
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_disk_decision_partitions"
down_revision = "0005_disk_usb_location"
branch_labels = None
depends_on = None

TABLE = "external_disks"


def _columns() -> set[str] | None:
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return None
    return {column["name"] for column in inspector.get_columns(TABLE)}


def upgrade() -> None:
    existing = _columns()
    if existing is None:
        return
    with op.batch_alter_table(TABLE) as batch:
        if "operator_decision" not in existing:
            batch.add_column(sa.Column("operator_decision", sa.String(length=16), nullable=True))
        if "partitions" not in existing:
            batch.add_column(sa.Column("partitions", sa.JSON(), nullable=True))


def downgrade() -> None:
    existing = _columns()
    if existing is None:
        return
    with op.batch_alter_table(TABLE) as batch:
        if "partitions" in existing:
            batch.drop_column("partitions")
        if "operator_decision" in existing:
            batch.drop_column("operator_decision")
