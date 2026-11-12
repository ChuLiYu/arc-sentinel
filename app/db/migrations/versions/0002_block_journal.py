"""Add observed block lineage without fabricating legacy hash provenance.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "indexed_blocks",
        sa.Column("chain_id", sa.Integer(), primary_key=True),
        sa.Column("block_number", sa.BigInteger(), primary_key=True),
        sa.Column("block_hash", sa.Text(), nullable=False),
        sa.Column("parent_hash", sa.Text(), nullable=False),
        sa.Column("block_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_provider", sa.Text(), nullable=False),
        sa.Column("is_anchor", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_table("indexed_blocks")
