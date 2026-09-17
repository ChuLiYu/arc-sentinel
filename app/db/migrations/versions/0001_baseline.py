"""baseline schema from SPEC v1.0 §7

Creates all 9 tables (agents, transactions, token_transfers, budgets,
address_policies, alerts, daily_agent_summary, indexer_state, api_keys)
plus their indexes, matching the DDL in the specification.

Revision ID: 0001
Revises:
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- agents ----------------------------------------------------------
    op.create_table(
        "agents",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("framework", sa.Text(), nullable=True),
        sa.Column("wallet_address", sa.Text(), nullable=False),
        sa.Column("owner_label", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.UniqueConstraint("wallet_address", name="agents_wallet_address_key"),
    )

    # --- transactions -----------------------------------------------------
    op.create_table(
        "transactions",
        sa.Column("tx_hash", sa.Text(), primary_key=True, nullable=False),
        sa.Column("block_number", sa.BigInteger(), nullable=False),
        sa.Column("block_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("from_address", sa.Text(), nullable=False),
        sa.Column("to_address", sa.Text(), nullable=True),
        sa.Column(
            "agent_id",
            sa.Uuid(),
            sa.ForeignKey("agents.id"),
            nullable=True,
        ),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("method_selector", sa.Text(), nullable=True),
        sa.Column("gas_used", sa.Numeric(38, 0), nullable=True),
        sa.Column("gas_fee_wei", sa.Numeric(38, 0), nullable=True),
        sa.Column("gas_fee_usdc", sa.Numeric(20, 6), nullable=True),
        sa.Column("raw_input_head", sa.Text(), nullable=True),
        sa.Column(
            "indexed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_tx_agent_time",
        "transactions",
        ["agent_id", sa.text("block_time DESC")],
    )

    # --- token_transfers --------------------------------------------------
    op.create_table(
        "token_transfers",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "tx_hash",
            sa.Text(),
            sa.ForeignKey("transactions.tx_hash"),
            nullable=False,
        ),
        sa.Column("log_index", sa.Integer(), nullable=False),
        sa.Column("token_address", sa.Text(), nullable=False),
        sa.Column("from_address", sa.Text(), nullable=False),
        sa.Column("to_address", sa.Text(), nullable=False),
        sa.Column("amount_raw", sa.Numeric(38, 0), nullable=False),
        sa.Column("amount_usdc", sa.Numeric(20, 6), nullable=False),
        sa.UniqueConstraint("tx_hash", "log_index", name="uq_token_transfers_tx_log"),
    )
    op.create_index(
        "idx_transfer_agent_time",
        "token_transfers",
        ["from_address", "to_address"],
    )

    # --- budgets ----------------------------------------------------------
    op.create_table(
        "budgets",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            sa.Uuid(),
            sa.ForeignKey("agents.id"),
            nullable=False,
        ),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("limit_usdc", sa.Numeric(20, 6), nullable=False),
        sa.Column(
            "effective_from",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )

    # --- address_policies -------------------------------------------------
    op.create_table(
        "address_policies",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "agent_id",
            sa.Uuid(),
            sa.ForeignKey("agents.id"),
            nullable=False,
        ),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("list_type", sa.Text(), nullable=False),
    )

    # --- alerts -----------------------------------------------------------
    op.create_table(
        "alerts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "agent_id",
            sa.Uuid(),
            sa.ForeignKey("agents.id"),
            nullable=True,
        ),
        sa.Column(
            "tx_hash",
            sa.Text(),
            sa.ForeignKey("transactions.tx_hash"),
            nullable=True,
        ),
        sa.Column("alert_type", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- daily_agent_summary ----------------------------------------------
    op.create_table(
        "daily_agent_summary",
        sa.Column(
            "agent_id",
            sa.Uuid(),
            sa.ForeignKey("agents.id"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("day", sa.Date(), primary_key=True, nullable=False),
        sa.Column(
            "tx_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "outbound_usdc",
            sa.Numeric(20, 6),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "inbound_usdc",
            sa.Numeric(20, 6),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "gas_fee_usdc",
            sa.Numeric(20, 6),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "failed_tx_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )

    # --- indexer_state ----------------------------------------------------
    op.create_table(
        "indexer_state",
        sa.Column("chain_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("last_indexed_block", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # --- api_keys ---------------------------------------------------------
    op.create_table(
        "api_keys",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.UniqueConstraint("key_hash", name="api_keys_key_hash_key"),
    )


def downgrade() -> None:
    op.drop_table("api_keys")
    op.drop_table("indexer_state")
    op.drop_table("daily_agent_summary")
    op.drop_table("alerts")
    op.drop_table("address_policies")
    op.drop_table("budgets")
    op.drop_table("token_transfers")
    op.drop_table("transactions")
    op.drop_table("agents")
