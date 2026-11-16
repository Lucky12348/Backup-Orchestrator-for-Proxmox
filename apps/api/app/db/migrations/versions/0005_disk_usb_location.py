"""USB location of external disks (deterministic USB passthrough).

`external_disks` is still created by `create_all()` (legacy table): on a fresh
install the columns already exist when this runs, on an upgrade they do not.
Both cases are handled, and a database without the table is left alone.

Revision ID: 0005_disk_usb_location
Revises: 0004_user_language
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_disk_usb_location"
down_revision = "0004_user_language"
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
        if "usb_location" not in existing:
            batch.add_column(sa.Column("usb_location", sa.JSON(), nullable=True))
        if "usb_location_seen_at" not in existing:
            batch.add_column(sa.Column("usb_location_seen_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    existing = _columns()
    if existing is None:
        return
    with op.batch_alter_table(TABLE) as batch:
        if "usb_location_seen_at" in existing:
            batch.drop_column("usb_location_seen_at")
        if "usb_location" in existing:
            batch.drop_column("usb_location")
