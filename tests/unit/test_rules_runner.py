"""Unit tests for ``app.rules.runner`` (SPEC §20.6) against an in-memory SQLite DB.

The runner is the missing execution point for the pure rule functions; these
tests prove it *queries and calls* — they do not re-test rule thresholds (see
``test_rules.py``). SQLite needs explicit timestamps and UUID PKs because it
cannot evaluate ``now()`` / ``gen_random_uuid()`` server defaults.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import AddressPolicy, Agent, Alert, Base, Budget, TokenTransfer, Transaction
from app.rules.runner import run_rules

USDC = "0x3600000000000000000000000000000000000000"
WALLET_A = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
WALLET_B = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


@pytest.fixture()
def factory() -> sessionmaker[Session]:
    """A fresh in-memory SQLite sessionmaker with the full schema."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)


def _now() -> datetime:
    return datetime.now(UTC)


def _seed_agent(
    fac: sessionmaker[Session],
    *,
    name: str = "Test Agent",
    wallet: str,
    is_active: bool = True,
    budgets: list[tuple[str, Decimal]] | None = None,
    policies: list[tuple[str, str]] | None = None,
) -> uuid.UUID:
    aid = uuid.uuid4()
    with fac() as session:
        session.add(
            Agent(
                id=aid,
                name=name,
                wallet_address=wallet,
                is_active=is_active,
                created_at=_now(),
            )
        )
        for period, limit in budgets or []:
            session.add(
                Budget(
                    id=uuid.uuid4(),
                    agent_id=aid,
                    period=period,
                    limit_usdc=limit,
                    effective_from=_now(),
                )
            )
        for address, list_type in policies or []:
            session.add(AddressPolicy(agent_id=aid, address=address, list_type=list_type))
        session.commit()
    return aid


def _seed_outbound_tx(
    fac: sessionmaker[Session],
    agent_id: uuid.UUID,
    *,
    wallet: str,
    to: str,
    amount: Decimal | None = None,
    status: str = "success",
) -> str:
    tx_hash = "0x" + uuid.uuid4().hex
    block_time = _now()
    with fac() as session:
        session.add(
            Transaction(
                tx_hash=tx_hash,
                block_number=1,
                block_time=block_time,
                from_address=wallet,
                to_address=to,
                agent_id=agent_id,
                direction="outbound",
                status=status,
                indexed_at=block_time,
            )
        )
        if amount is not None:
            session.add(
                TokenTransfer(
                    tx_hash=tx_hash,
                    log_index=0,
                    token_address=USDC,
                    from_address=wallet,
                    to_address=to,
                    amount_raw=Decimal(0),
                    amount_usdc=amount,
                )
            )
        session.commit()
    return tx_hash


# ---------------------------------------------------------------------------
# budget_exceeded
# ---------------------------------------------------------------------------
class TestBudgetExceeded:
    async def test_over_budget_agent_gets_alert(self, factory) -> None:
        aid = _seed_agent(factory, wallet=WALLET_A, budgets=[("daily", Decimal("50"))])
        _seed_outbound_tx(factory, aid, wallet=WALLET_A, to="0xbbb", amount=Decimal("100"))

        assert await run_rules(session_factory=factory) == 1

        with factory() as session:
            (alert,) = list(session.scalars(select(Alert)))
            assert alert.agent_id == aid
            assert alert.alert_type == "budget_exceeded"
            assert alert.severity == "critical"  # 100 > 120% × 50
            assert "Test Agent" in alert.message

    async def test_within_budget_no_alert(self, factory) -> None:
        aid = _seed_agent(factory, wallet=WALLET_A, budgets=[("daily", Decimal("500"))])
        _seed_outbound_tx(factory, aid, wallet=WALLET_A, to="0xbbb", amount=Decimal("100"))

        assert await run_rules(session_factory=factory) == 0

    async def test_second_run_does_not_duplicate(self, factory) -> None:
        """Dedupe: an unacked alert is never re-inserted on the next cycle."""
        aid = _seed_agent(factory, wallet=WALLET_A, budgets=[("daily", Decimal("50"))])
        _seed_outbound_tx(factory, aid, wallet=WALLET_A, to="0xbbb", amount=Decimal("100"))

        assert await run_rules(session_factory=factory) == 1
        assert await run_rules(session_factory=factory) == 0

        with factory() as session:
            assert session.scalar(select(func.count()).select_from(Alert)) == 1


# ---------------------------------------------------------------------------
# recipient rules are gated on the agent having a policy
# ---------------------------------------------------------------------------
class TestRecipientGating:
    async def test_no_policy_means_no_recipient_rule(self, factory) -> None:
        aid = _seed_agent(factory, wallet=WALLET_A)
        _seed_outbound_tx(factory, aid, wallet=WALLET_A, to="0xunknown")

        assert await run_rules(session_factory=factory) == 0

    async def test_allow_policy_flags_unknown_recipient(self, factory) -> None:
        aid = _seed_agent(factory, wallet=WALLET_B, policies=[("0xknown", "allow")])

        # a known recipient passes the allow list → nothing
        _seed_outbound_tx(factory, aid, wallet=WALLET_B, to="0xknown")
        assert await run_rules(session_factory=factory) == 0

        # an unknown recipient on an allow-listed agent → warning
        tx_hash = _seed_outbound_tx(factory, aid, wallet=WALLET_B, to="0xunknown")
        assert await run_rules(session_factory=factory) == 1

        with factory() as session:
            (alert,) = list(session.scalars(select(Alert)))
            assert alert.alert_type == "unknown_recipient"
            assert alert.severity == "warning"
            assert alert.tx_hash == tx_hash

    async def test_blocked_recipient_is_critical(self, factory) -> None:
        aid = _seed_agent(factory, wallet=WALLET_A, policies=[("0xbad", "block")])
        tx_hash = _seed_outbound_tx(factory, aid, wallet=WALLET_A, to="0xbad")

        assert await run_rules(session_factory=factory) == 1

        with factory() as session:
            (alert,) = list(session.scalars(select(Alert)))
            assert alert.alert_type == "blocked_recipient"
            assert alert.severity == "critical"
            assert alert.tx_hash == tx_hash

    async def test_block_policy_alone_does_not_run_allow_check(self, factory) -> None:
        """An agent with ONLY a block list must not fire unknown_recipient."""
        aid = _seed_agent(factory, wallet=WALLET_A, policies=[("0xbad", "block")])
        _seed_outbound_tx(factory, aid, wallet=WALLET_A, to="0xnotblocked")

        assert await run_rules(session_factory=factory) == 0


# ---------------------------------------------------------------------------
# failed_tx_spike (rolling 10-minute window)
# ---------------------------------------------------------------------------
class TestFailedTxSpike:
    async def test_spike_of_five_failed_txs_warns(self, factory) -> None:
        aid = _seed_agent(factory, wallet=WALLET_A)
        for _ in range(5):
            _seed_outbound_tx(factory, aid, wallet=WALLET_A, to="0xbbb", status="failed")

        assert await run_rules(session_factory=factory) == 1

        with factory() as session:
            (alert,) = list(session.scalars(select(Alert)))
            assert alert.alert_type == "failed_tx_spike"
            assert alert.severity == "warning"
            assert "5 failed transactions" in alert.message

    async def test_four_failed_txs_under_threshold_no_alert(self, factory) -> None:
        aid = _seed_agent(factory, wallet=WALLET_A)
        for _ in range(4):
            _seed_outbound_tx(factory, aid, wallet=WALLET_A, to="0xbbb", status="failed")

        assert await run_rules(session_factory=factory) == 0


# ---------------------------------------------------------------------------
# agent / wiring edge cases
# ---------------------------------------------------------------------------
class TestAgentWiring:
    async def test_inactive_agent_is_skipped(self, factory) -> None:
        aid = _seed_agent(
            factory, wallet=WALLET_A, is_active=False, budgets=[("daily", Decimal("50"))]
        )
        _seed_outbound_tx(factory, aid, wallet=WALLET_A, to="0xbbb", amount=Decimal("100"))

        assert await run_rules(session_factory=factory) == 0

    async def test_accepts_database_url(self, tmp_path: Path) -> None:
        """The database_url path builds its own engine and dedupes across runs."""
        url = f"sqlite:///{tmp_path / 'rules.db'}"
        engine = create_engine(url)
        Base.metadata.create_all(engine)
        fac = sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)
        aid = _seed_agent(fac, wallet=WALLET_A, budgets=[("weekly", Decimal("50"))])
        _seed_outbound_tx(fac, aid, wallet=WALLET_A, to="0xbbb", amount=Decimal("400"))
        engine.dispose()

        assert await run_rules(database_url=url) == 1
        assert await run_rules(database_url=url) == 0
