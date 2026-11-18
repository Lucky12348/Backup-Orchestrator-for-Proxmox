"""Notification channels (Apprise engine) and their delivery queue/journal.

Revision ID: 0015_notification_channels
Revises: 0014_agent_updates
Create Date: 2026-11-18

The legacy `notification_preferences` table is left in place (read once by
the ntfy migration, services/notification_migration.py); nothing is dropped.
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_notification_channels"
down_revision = "0014_agent_updates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("secrets_encrypted", sa.Text(), nullable=True),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("min_level", sa.String(length=8), nullable=False, server_default="info"),
        sa.Column("quiet_start", sa.String(length=5), nullable=True),
        sa.Column("quiet_end", sa.String(length=5), nullable=True),
        sa.Column("digest", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("digest_time", sa.String(length=5), nullable=False, server_default="08:00"),
        sa.Column("last_digest_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel_id", sa.Integer(), nullable=True),
        sa.Column("channel_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("event", sa.String(length=48), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column("dedup_key", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_notification_deliveries_channel_id", "notification_deliveries", ["channel_id"])
    op.create_index("ix_notification_deliveries_dedup_key", "notification_deliveries", ["dedup_key"])
    op.create_index("ix_notification_deliveries_status", "notification_deliveries", ["status"])


def downgrade() -> None:
    op.drop_index("ix_notification_deliveries_status", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_dedup_key", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_channel_id", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_table("notification_channels")
