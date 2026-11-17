"""USB passthrough mappings PBO puts on the PBS VM (usbN: host=…), tracked so
orphans can be removed (Phase 5f.2).

Revision ID: 0011_pbs_usb_mappings
Revises: 0010_disk_pbs_marker_source
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_pbs_usb_mappings"
down_revision = "0010_disk_pbs_marker_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pbs_usb_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("vmid", sa.Integer(), nullable=False),
        sa.Column("slot", sa.String(length=16), nullable=False),
        sa.Column("host_value", sa.String(length=128), nullable=False),
        sa.Column("disk_id", sa.Integer(), sa.ForeignKey("external_disks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("disk_serial", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.Column("release_reason", sa.String(length=32), nullable=True),
    )
    op.create_index("ix_pbs_usb_mappings_open", "pbs_usb_mappings", ["released_at"])


def downgrade() -> None:
    op.drop_index("ix_pbs_usb_mappings_open", table_name="pbs_usb_mappings")
    op.drop_table("pbs_usb_mappings")
