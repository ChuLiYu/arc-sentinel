"""Upgrade a populated baseline without inventing block provenance."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, inspect, select, text

from app.db.models import Agent, IndexedBlock, IndexerState, TokenTransfer, Transaction


def migrate(factory, target: str, direction: str = "upgrade") -> None:
    # This URL belongs to the disposable fixture, never the configured app DB.
    database_url = factory.kw["bind"].url.render_as_string(hide_password=False)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", direction, target],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_populated_baseline_upgrade_preserves_rows_and_leaves_journal_empty(migrated_db):
    migrate(migrated_db, "0001", "downgrade")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with migrated_db() as session:
        agent = Agent(name="legacy", wallet_address="0x" + "aa" * 20)
        session.add(agent)
        session.flush()
        session.add(Transaction(
            tx_hash="legacy-transaction", block_number=100, block_time=now,
            from_address=agent.wallet_address, agent_id=agent.id,
            direction="outbound", status="success",
        ))
        session.flush()
        session.add(TokenTransfer(
            tx_hash="legacy-transaction", log_index=0, token_address="legacy-token",
            from_address=agent.wallet_address, to_address="recipient",
            amount_raw=1000000, amount_usdc=1,
        ))
        # Insert against the historical schema, not today's expanded ORM model.
        session.execute(text(
            "INSERT INTO indexer_state (chain_id, last_indexed_block) VALUES (5042002, 100)"
        ))
        session.commit()
        agent_id = agent.id

    migrate(migrated_db, "head")
    with migrated_db() as session:
        assert session.get(Agent, agent_id).name == "legacy"
        assert session.get(Transaction, "legacy-transaction").agent_id == agent_id
        assert session.scalar(select(TokenTransfer)).amount_raw == 1000000
        assert session.get(IndexerState, 5042002).last_indexed_block == 100
        assert session.get(IndexerState, 5042002).recovery_reason is None
        assert session.get(IndexerState, 5042002).recovery_ancestor_block is None
        assert session.get(IndexerState, 5042002).recovery_detected_at is None
        assert session.scalar(select(func.count()).select_from(IndexedBlock)) == 0
        columns = {c["name"] for c in inspect(session.connection()).get_columns("indexed_blocks")}
        assert columns == set(IndexedBlock.__table__.columns.keys())
        # The new journal is chain-scoped even while legacy raw tables are not.
        for chain in (1, 5042002):
            session.add(IndexedBlock(
                chain_id=chain, block_number=100, block_hash="0x" + "11" * 32,
                parent_hash="0x" + "22" * 32, block_time=now,
                observed_provider="primary", is_anchor=True,
            ))
        session.commit()
        assert session.scalar(select(func.count()).select_from(IndexedBlock)) == 2


def test_recovery_revision_downgrade_and_upgrade_preserve_checkpoint_and_journal(migrated_db):
    with migrated_db() as session:
        session.execute(text("TRUNCATE indexer_state, indexed_blocks"))
        session.add(IndexerState(
            chain_id=7, last_indexed_block=50, recovery_reason="checkpoint_fork",
            recovery_ancestor_block=49, recovery_detected_at=datetime(2026, 10, 30, tzinfo=UTC),
        ))
        session.add(IndexedBlock(
            chain_id=7, block_number=50, block_hash="0x" + "11" * 32,
            parent_hash="0x" + "22" * 32, block_time=datetime(2026, 10, 30, tzinfo=UTC),
            observed_provider="primary", is_anchor=True,
        ))
        session.commit()
    migrate(migrated_db, "0002", "downgrade")
    with migrated_db() as session:
        assert session.scalar(text("SELECT last_indexed_block FROM indexer_state")) == 50
        assert session.scalar(select(func.count()).select_from(IndexedBlock)) == 1
        columns = {c["name"] for c in inspect(session.connection()).get_columns("indexer_state")}
        assert "recovery_reason" not in columns
    migrate(migrated_db, "head")
    with migrated_db() as session:
        state = session.get(IndexerState, 7)
        assert state.last_indexed_block == 50
        assert state.recovery_reason is None
        columns = {c["name"] for c in inspect(session.connection()).get_columns("indexer_state")}
        assert columns == set(IndexerState.__table__.columns.keys())
