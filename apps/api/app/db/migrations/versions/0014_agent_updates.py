"""Agent self-update: build/version seen on each agent, update history.

Revision ID: 0014_agent_updates
Revises: 0013_verify_required_repair
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_agent_updates"
down_revision = "0013_verify_required_repair"
branch_labels = None
depends_on = None

AGENT_COLUMNS = (
    ("agent_version", sa.String(length=64)),
    ("build_id", sa.String(length=64)),
    ("version_seen_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("agents")}
    missing = [(name, type_) for name, type_ in AGENT_COLUMNS if name not in existing]
    if missing:
        with op.batch_alter_table("agents") as batch:
            for name, type_ in missing:
                batch.add_column(sa.Column(name, type_, nullable=True))
    op.create_table(
        "agent_updates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("from_build", sa.String(length=64), nullable=True),
        sa.Column("to_build", sa.String(length=64), nullable=False),
        sa.Column("from_version", sa.String(length=64), nullable=True),
        sa.Column("to_version", sa.String(length=64), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_updates_agent_id", "agent_updates", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_updates_agent_id", table_name="agent_updates")
    op.drop_table("agent_updates")
    with op.batch_alter_table("agents") as batch:
        for name, _ in AGENT_COLUMNS:
            batch.drop_column(name)
