"""First-run wizard progress (setup_progress, single row).

Revision ID: 0003_setup_progress
Revises: 0002_agents
Create Date: 2026-11-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_setup_progress"
down_revision = "0002_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "setup_progress",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("current_step", sa.String(length=32), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("setup_progress")
