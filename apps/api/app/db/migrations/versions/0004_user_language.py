"""Per-account UI language.

Revision ID: 0004_user_language
Revises: 0003_setup_progress
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_user_language"
down_revision = "0003_setup_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("language", sa.String(length=8), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("language")
