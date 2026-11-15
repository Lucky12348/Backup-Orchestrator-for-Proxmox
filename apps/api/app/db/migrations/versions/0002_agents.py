"""Registered agents and pairing codes (one-line agent install).

Revision ID: 0002_agents
Revises: 0001_users_app_settings
Create Date: 2026-11-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_agents"
down_revision = "0001_users_app_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("hostname", sa.String(length=253), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("fingerprint", sa.String(length=95), nullable=False),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("callback_token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("callback_token_hash", name="uq_agents_callback_token_hash"),
    )
    op.create_table(
        "agent_pairing_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agent_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint("code_hash", name="uq_agent_pairing_codes_code_hash"),
    )


def downgrade() -> None:
    op.drop_table("agent_pairing_codes")
    op.drop_table("agents")
