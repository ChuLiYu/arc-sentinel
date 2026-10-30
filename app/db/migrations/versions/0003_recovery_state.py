"""Persist recovery diagnostics without changing checkpoints or raw rows.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("indexer_state", sa.Column("recovery_reason", sa.Text(), nullable=True))
    op.add_column(
        "indexer_state", sa.Column("recovery_ancestor_block", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "indexer_state",
        sa.Column("recovery_detected_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("indexer_state", "recovery_detected_at")
    op.drop_column("indexer_state", "recovery_ancestor_block")
    op.drop_column("indexer_state", "recovery_reason")
