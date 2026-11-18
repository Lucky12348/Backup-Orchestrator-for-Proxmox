"""Inbox items, and the parameters of each notification delivery (B55).

Revision ID: 0016_inbox
Revises: 0015_notification_channels
Create Date: 2026-11-18

`notification_deliveries.params` keeps the event parameters so a message is
rendered in PBO's current language when it is sent and when the journal shows
it. `inbox_items` = the top-bar inbox (every notification event, whatever the
channels, plus the alert toasts of the web UI).
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_inbox"
down_revision = "0015_notification_channels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notification_deliveries", sa.Column("params", sa.JSON(), nullable=True))
    op.create_table(
        "inbox_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=8), nullable=False, server_default="event"),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column("dedup_key", sa.String(length=255), nullable=False),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("link", sa.String(length=255), nullable=True),
        sa.Column("disk_id", sa.Integer(), nullable=True),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_inbox_items_dedup_key", "inbox_items", ["dedup_key"])
    op.create_index("ix_inbox_items_updated_at", "inbox_items", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_inbox_items_updated_at", table_name="inbox_items")
    op.drop_index("ix_inbox_items_dedup_key", table_name="inbox_items")
    op.drop_table("inbox_items")
    op.drop_column("notification_deliveries", "params")
