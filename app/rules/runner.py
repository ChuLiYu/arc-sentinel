"""Rule engine executor (SPEC §20.6).

The pure rule functions in :mod:`app.rules` produce :class:`AlertDraft`
records but nothing ever called them, so ``alerts`` stayed empty. This module
is that missing execution point: walk every ``is_active`` agent, aggregate the
data each rule needs from the database, call the existing pure functions, and
persist the results to ``alerts``.

Design rules:

* **No rule logic here** — only data queries and calls. The threshold/severity
  decisions live in ``budget.py`` / ``anomaly.py`` / ``recipient.py``.
* **Dedupe (anti-spam)** — alerts are keyed per ``(agent_id, alert_type)``
  while unacked (``acked_at IS NULL``); an already-open alert is never
  re-written, otherwise a 60 s loop would fire the same budget alert every
  minute. The dedupe is checked against the DB (past cycles) *and* within a
  single run, so one pass never emits duplicate ``unknown_recipient`` alerts.
* **Rolling windows** (SPEC §9 "period"):
  ``daily = now - 1 day``, ``weekly = now - 7 days``,
  ``monthly = now - 30 days``.
* ``gas_spike`` is deliberately not wired here yet — it needs a 30-day
  per-agent gas average that this MVP cycle does not compute.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import AddressPolicy, Agent, Alert, Budget, TokenTransfer, Transaction
from app.rules.anomaly import check_failed_tx_spike
from app.rules.budget import check_budget
from app.rules.common import AlertDraft
from app.rules.recipient import check_blocked_recipient, check_unknown_recipient

logger = logging.getLogger(__name__)

FAILED_SPIKE_WINDOW = timedelta(minutes=10)
RECIPIENT_TX_WINDOW = timedelta(minutes=10)
RECIPIENT_TX_LIMIT = 50

BUDGET_WINDOWS: dict[str, timedelta] = {
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
}

# Persist session factories per database_url so the 60 s background loop never
# rebuilds the connection pool on every cycle.
_factories: dict[str, sessionmaker[Session]] = {}


async def run_rules(
    database_url: str | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> int:
    """Evaluate every rule for every active agent; persist new alerts.

    Returns the number of alerts newly inserted (0 when nothing changed).

    Either pass an existing ``session_factory`` (tests, callers already bound)
    or a ``database_url`` (the background loop); with neither, the process-wide
    factory from :mod:`app.db.session` is used.
    """
    factory = _resolve_factory(database_url, session_factory)
    now = datetime.now(UTC)

    new_alerts = 0
    with factory() as session:
        agents = list(
            session.scalars(select(Agent).where(Agent.is_active.is_(True))).all()
        )
        for agent in agents:
            drafts = _evaluate_agent(session, agent, now)
            new_alerts += _persist_drafts(session, drafts)
        session.commit()

    logger.info("rules runner: %d new alerts", new_alerts)
    return new_alerts


# ---------------------------------------------------------------------------
# Session resolution
# ---------------------------------------------------------------------------
def _resolve_factory(
    database_url: str | None,
    session_factory: sessionmaker[Session] | None,
) -> sessionmaker[Session]:
    if session_factory is not None:
        return session_factory
    if database_url is None:
        from app.db.session import get_session_factory

        return get_session_factory()
    factory = _factories.get(database_url)
    if factory is None:
        engine = create_engine(database_url, pool_pre_ping=True, future=True)
        factory = sessionmaker(
            bind=engine, class_=Session, autoflush=False, expire_on_commit=False
        )
        _factories[database_url] = factory
    return factory


# ---------------------------------------------------------------------------
# Per-agent rule evaluation
# ---------------------------------------------------------------------------
def _evaluate_agent(session: Session, agent: Agent, now: datetime) -> list[AlertDraft]:
    return [
        *_evaluate_budgets(session, agent, now),
        *_evaluate_failed_spike(session, agent, now),
        *_evaluate_recipients(session, agent, now),
    ]


def _evaluate_budgets(session: Session, agent: Agent, now: datetime) -> list[AlertDraft]:
    budgets = list(
        session.scalars(
            select(Budget).where(
                Budget.agent_id == agent.id, Budget.is_active.is_(True)
            )
        ).all()
    )
    if not budgets:
        return []

    wallet = agent.wallet_address.lower()
    drafts: list[AlertDraft] = []
    for budget in budgets:
        window = BUDGET_WINDOWS.get(budget.period)
        if window is None:
            logger.warning(
                "agent %s has budget with unsupported period %r; skipping",
                agent.id,
                budget.period,
            )
            continue
        outbound = _outbound_usdc(session, wallet, now - window)
        drafts.extend(
            check_budget(
                agent_id=agent.id,
                agent_name=agent.name,
                period=budget.period,
                limit_usdc=budget.limit_usdc,
                outbound_usdc=outbound,
            )
        )
    return drafts


def _outbound_usdc(session: Session, wallet_address: str, window_start: datetime) -> Decimal:
    """USDC flowing out of ``wallet_address`` since ``window_start``.

    ``token_transfers`` has no block timestamp (SPEC §7 doesn't denormalise it),
    so the query joins ``transactions`` on ``tx_hash`` to filter by time while
    summing only transfers whose ``from`` side is the agent's wallet.
    """
    total = session.scalar(
        select(func.coalesce(func.sum(TokenTransfer.amount_usdc), Decimal("0")))
        .join(Transaction, Transaction.tx_hash == TokenTransfer.tx_hash)
        .where(
            func.lower(TokenTransfer.from_address) == wallet_address,
            Transaction.block_time >= window_start,
        )
    )
    return Decimal(total or 0)


def _evaluate_failed_spike(session: Session, agent: Agent, now: datetime) -> list[AlertDraft]:
    failed_hashes = list(
        session.scalars(
            select(Transaction.tx_hash)
            .where(
                Transaction.agent_id == agent.id,
                Transaction.status == "failed",
                Transaction.block_time >= now - FAILED_SPIKE_WINDOW,
            )
        ).all()
    )
    return check_failed_tx_spike(
        agent_id=agent.id, agent_name=agent.name, failed_tx_hashes=failed_hashes
    )


def _evaluate_recipients(session: Session, agent: Agent, now: datetime) -> list[AlertDraft]:
    """Unknown/blocked-recipient checks — only for agents with a policy.

    ``check_unknown_recipient`` treats ``allow_list=None`` (no allow policy) as
    "no comparison possible → no alert", so the allow check only runs when the
    agent actually has allow-list rows; the block check mirrors that.
    """
    policies = list(
        session.scalars(
            select(AddressPolicy).where(AddressPolicy.agent_id == agent.id)
        ).all()
    )
    if not policies:
        return []

    allow_list = [p.address for p in policies if p.list_type == "allow"]
    block_list = [p.address for p in policies if p.list_type == "block"]
    if not allow_list and not block_list:
        return []

    recent_outbound = list(
        session.scalars(
            select(Transaction)
            .where(
                Transaction.agent_id == agent.id,
                Transaction.direction == "outbound",
                Transaction.status == "success",
                Transaction.block_time >= now - RECIPIENT_TX_WINDOW,
            )
            .order_by(Transaction.block_time.desc())
            .limit(RECIPIENT_TX_LIMIT)
        ).all()
    )

    drafts: list[AlertDraft] = []
    for tx in recent_outbound:
        if allow_list:
            drafts.extend(
                check_unknown_recipient(
                    agent_id=agent.id,
                    agent_name=agent.name,
                    tx_hash=tx.tx_hash,
                    to_address=tx.to_address,
                    allow_list=allow_list,
                )
            )
        if block_list:
            drafts.extend(
                check_blocked_recipient(
                    agent_id=agent.id,
                    agent_name=agent.name,
                    tx_hash=tx.tx_hash,
                    to_address=tx.to_address,
                    block_list=block_list,
                )
            )
    return drafts


# ---------------------------------------------------------------------------
# Persistence with dedupe
# ---------------------------------------------------------------------------
def _persist_drafts(session: Session, drafts: list[AlertDraft]) -> int:
    """Insert alert rows not already open for the same ``(agent, type)``.

    "Open" means ``acked_at IS NULL``. ``seen`` guards against duplicate drafts
    within this single run (e.g. many unknown recipients), while the DB check
    guards against alerts written by earlier cycles. Returns the insert count.
    """
    inserted = 0
    seen: set[tuple[uuid.UUID, str]] = set()
    for draft in drafts:
        key = (draft.agent_id, draft.alert_type)
        if key in seen:
            continue
        if _has_open_alert(session, draft.agent_id, draft.alert_type):
            seen.add(key)
            continue
        session.add(
            Alert(
                agent_id=draft.agent_id,
                tx_hash=draft.tx_hash,
                alert_type=draft.alert_type,
                severity=draft.severity,
                message=draft.message,
                # Explicit timestamp: SQLite tests can't evaluate now() as a
                # server default; PostgreSQL semantics are identical either way.
                created_at=datetime.now(UTC),
            )
        )
        seen.add(key)
        inserted += 1
    session.flush()
    return inserted


def _has_open_alert(session: Session, agent_id: uuid.UUID, alert_type: str) -> bool:
    row = session.scalar(
        select(Alert.id)
        .where(
            Alert.agent_id == agent_id,
            Alert.alert_type == alert_type,
            Alert.acked_at.is_(None),
        )
        .limit(1)
    )
    return row is not None
