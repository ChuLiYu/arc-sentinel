"""Integration: rules runner end-to-end against real Postgres (SPEC §20.6).

Uses the throwaway migrated Postgres from ``conftest.py``: seeds an active
agent with a daily budget + outbound USDC transfer, runs ``run_rules`` twice
and asserts the alert appears exactly once (dedupe across cycles).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Agent, Alert, Budget, TokenTransfer, Transaction
from app.rules.runner import run_rules

WALLET = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
RECIPIENT = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
USDC = "0x3600000000000000000000000000000000000000"


async def test_rules_runner_persists_and_dedupes(migrated_db: sessionmaker[Session]) -> None:
    factory = migrated_db

    with factory() as session:
        agent = Agent(name="pg-rules-agent", wallet_address=WALLET)
        session.add(agent)
        session.flush()
        agent_id = agent.id
        session.add(Budget(agent_id=agent_id, period="daily", limit_usdc=Decimal("50")))
        tx_hash = "0x" + "7" * 64
        session.add(
            Transaction(
                tx_hash=tx_hash,
                block_number=1,
                block_time=datetime.now(UTC),
                from_address=WALLET,
                to_address=RECIPIENT,
                agent_id=agent_id,
                direction="outbound",
                status="success",
            )
        )
        # PG enforces the token_transfers.tx_hash FK immediately: fully emit the
        # parent row before inserting the child (SQLAlchemy's flush order is not
        # guaranteed to match FK dependency).
        session.flush()
        session.add(
            TokenTransfer(
                tx_hash=tx_hash,
                log_index=0,
                token_address=USDC,
                from_address=WALLET,
                to_address=RECIPIENT,
                amount_raw=Decimal("100000000"),
                amount_usdc=Decimal("100.000000"),
            )
        )
        session.commit()

    first = await run_rules(session_factory=factory)
    assert first == 1, "over-budget agent must produce exactly one alert"

    second = await run_rules(session_factory=factory)
    assert second == 0, "unacked alerts must not be re-inserted"

    with factory() as session:
        alerts = list(session.scalars(select(Alert)))
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.agent_id == agent_id
        assert alert.alert_type == "budget_exceeded"
        assert alert.severity == "critical"  # 100 > 120% × 50
        assert alert.tx_hash is None  # budget alerts are not tx-scoped
        assert "pg-rules-agent" in alert.message
        assert alert.acked_at is None
        assert alert.created_at is not None
