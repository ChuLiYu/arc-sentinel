"""SQLAlchemy 2.0 ORM models matching SPEC §7 (all 9 tables).

Financial ground rules (SPEC §7):
  * Every amount column is ``NUMERIC`` — never ``float``.
  * Raw chain integers (wei-level) and human-readable amounts are stored in
    separate columns and must never be mixed.
  * ``NUMERIC(38,0)``   -> raw wei-level integers (gas, USDC 6-dp raw amount)
  * ``NUMERIC(20,6)``   -> human-readable USDC (6 decimal places)
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base shared by every Arc Sentinel model."""


TIMESTAMPTZ = DateTime(timezone=True)
"""TIMESTAMPTZ -- timezone-aware timestamp, mirrors ``now()`` semantics."""

BIGINT_AUTOINCREMENT = BigInteger().with_variant(Integer, "sqlite")
"""``BigInteger`` identity PK that degrades to ``INTEGER PRIMARY KEY`` on SQLite.

PostgreSQL serialises it as ``BIGSERIAL`` exactly like a plain ``BigInteger``;
SQLite only auto-increments the *exact* ``INTEGER`` type (``BIGINT PRIMARY KEY``
is NOT a rowid alias), so the variant keeps in-memory/unit-test databases
working without changing the production DDL.
"""


def _uuid_pk() -> Mapped[uuid.UUID]:
    """PostgreSQL-native UUID primary key defaulting to ``gen_random_uuid()``.

    Requires PostgreSQL 13+ (built-in function, no pgcrypto extension).
    """
    return mapped_column(
        Uuid,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------
class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    framework: Mapped[str | None] = mapped_column(Text)
    wallet_address: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    owner_label: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=text("now()")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


# ---------------------------------------------------------------------------
# transactions
# ---------------------------------------------------------------------------
class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (Index("idx_tx_agent_time", "agent_id", text("block_time DESC")),)

    tx_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    block_time: Mapped[dt.datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    from_address: Mapped[str] = mapped_column(Text, nullable=False)
    to_address: Mapped[str | None] = mapped_column(Text)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    direction: Mapped[str] = mapped_column(Text, nullable=False)  # outbound|inbound
    status: Mapped[str] = mapped_column(Text, nullable=False)  # success|failed
    method_selector: Mapped[str | None] = mapped_column(Text)
    gas_used: Mapped[Decimal | None] = mapped_column(Numeric(38, 0))
    gas_fee_wei: Mapped[Decimal | None] = mapped_column(Numeric(38, 0))
    gas_fee_usdc: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    raw_input_head: Mapped[str | None] = mapped_column(Text)
    indexed_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=text("now()")
    )


# ---------------------------------------------------------------------------
# token_transfers
# ---------------------------------------------------------------------------
class TokenTransfer(Base):
    __tablename__ = "token_transfers"
    __table_args__ = (
        UniqueConstraint("tx_hash", "log_index", name="uq_token_transfers_tx_log"),
        Index("idx_transfer_agent_time", "from_address", "to_address"),
    )

    id: Mapped[int] = mapped_column(BIGINT_AUTOINCREMENT, primary_key=True, autoincrement=True)
    tx_hash: Mapped[str] = mapped_column(ForeignKey("transactions.tx_hash"), nullable=False)
    log_index: Mapped[int] = mapped_column(Integer, nullable=False)
    token_address: Mapped[str] = mapped_column(Text, nullable=False)
    from_address: Mapped[str] = mapped_column(Text, nullable=False)
    to_address: Mapped[str] = mapped_column(Text, nullable=False)
    amount_raw: Mapped[Decimal] = mapped_column(Numeric(38, 0), nullable=False)
    amount_usdc: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)


# ---------------------------------------------------------------------------
# budgets
# ---------------------------------------------------------------------------
class Budget(Base):
    __tablename__ = "budgets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    period: Mapped[str] = mapped_column(Text, nullable=False)  # daily|weekly|monthly
    limit_usdc: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    effective_from: Mapped[dt.datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=text("now()")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


# ---------------------------------------------------------------------------
# address_policies
# ---------------------------------------------------------------------------
class AddressPolicy(Base):
    __tablename__ = "address_policies"

    id: Mapped[int] = mapped_column(BIGINT_AUTOINCREMENT, primary_key=True, autoincrement=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    list_type: Mapped[str] = mapped_column(Text, nullable=False)  # allow|block


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------
class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(BIGINT_AUTOINCREMENT, primary_key=True, autoincrement=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    tx_hash: Mapped[str | None] = mapped_column(ForeignKey("transactions.tx_hash"))
    alert_type: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)  # info|warning|critical
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=text("now()")
    )
    acked_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMPTZ)


# ---------------------------------------------------------------------------
# daily_agent_summary
# ---------------------------------------------------------------------------
class DailyAgentSummary(Base):
    __tablename__ = "daily_agent_summary"

    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), primary_key=True)
    day: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    tx_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    outbound_usdc: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, server_default=text("0")
    )
    inbound_usdc: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, server_default=text("0")
    )
    gas_fee_usdc: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, server_default=text("0")
    )
    failed_tx_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


# ---------------------------------------------------------------------------
# indexer_state
# ---------------------------------------------------------------------------
class IndexerState(Base):
    __tablename__ = "indexer_state"

    chain_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_indexed_block: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=text("now()")
    )


# ---------------------------------------------------------------------------
# api_keys
# ---------------------------------------------------------------------------
class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = _uuid_pk()
    key_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    label: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=text("now()")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
