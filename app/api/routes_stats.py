"""Network-level stats routes (SPEC §8 / §6 file layout: routes_stats.py)."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Agent, TokenTransfer, Transaction
from app.db.session import get_session

router = APIRouter(tags=["stats"])


@router.get("/stats/network")
def network_stats(session: Session = Depends(get_session)) -> dict[str, object]:
    """Global network summary for the dashboard hero (SPEC §8: /stats/network)."""
    agent_count = session.scalar(select(func.count()).select_from(Agent))
    tx_count = session.scalar(select(func.count()).select_from(Transaction))
    usdc_volume = session.scalar(
        select(func.coalesce(func.sum(TokenTransfer.amount_usdc), Decimal("0")))
    )
    return {
        "tracked_agents": int(agent_count or 0),
        "indexed_transactions": int(tx_count or 0),
        "cumulative_usdc_volume": str(usdc_volume),
    }
