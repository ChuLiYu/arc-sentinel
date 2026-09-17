"""Transaction / wallet / summary routes (SPEC §8)."""

from __future__ import annotations

import time
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Agent, TokenTransfer, Transaction
from app.db.session import get_session
from app.indexer.rpc_client import RpcClient

router = APIRouter(tags=["transactions"])

PERIODS: dict[str, int] = {"today": 1, "7d": 7, "30d": 30}


class TxOut(BaseModel):
    tx_hash: str
    block_number: int
    block_time: object
    from_address: str
    to_address: str | None
    direction: str
    status: str
    method_selector: str | None
    gas_fee_usdc: Decimal | None

    model_config = {"from_attributes": True}


class SummaryOut(BaseModel):
    agent_id: uuid.UUID
    period: str
    tx_count: int
    outbound_usdc: Decimal
    inbound_usdc: Decimal
    gas_fee_usdc: Decimal
    failed_tx_count: int


def _get_agent_or_404(session: Session, agent_id: uuid.UUID) -> Agent:
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent


@router.get("/agents/{agent_id}/summary", response_model=SummaryOut)
def agent_summary(
    agent_id: uuid.UUID,
    period: Literal["today", "7d", "30d"] = "7d",
    session: Session = Depends(get_session),
) -> SummaryOut:
    _get_agent_or_404(session, agent_id)

    if period == "today":
        start = func.date_trunc("day", func.now())
    else:
        start = text("now() - make_interval(days => :days)").bindparams(
            days=PERIODS[period]
        )

    stmt = select(
        func.count(Transaction.tx_hash),
        func.coalesce(
            func.sum(
                case(
                    (Transaction.direction == "outbound", Transaction.gas_fee_usdc),
                    else_=0,
                )
            ),
            Decimal("0"),
        ),
        func.coalesce(
            func.sum(
                case(
                    (Transaction.direction == "inbound", Transaction.gas_fee_usdc),
                    else_=0,
                )
            ),
            Decimal("0"),
        ),
    ).where(
        Transaction.agent_id == agent_id,
        Transaction.block_time >= start,
    )
    # NOTE: daily_agent_summary is the materialised source for the dashboard in
    # production; here we aggregate on the fly for correctness of arbitrary periods.
    outbound = session.scalar(
        select(func.coalesce(func.sum(TokenTransfer.amount_usdc), Decimal("0"))).where(
            TokenTransfer.from_address.in_(
                select(Agent.wallet_address).where(Agent.id == agent_id)
            ),
            TokenTransfer.tx_hash.in_(
                select(Transaction.tx_hash).where(
                    Transaction.agent_id == agent_id,
                    Transaction.block_time >= start,
                )
            ),
        )
    ) or Decimal("0")
    inbound = session.scalar(
        select(func.coalesce(func.sum(TokenTransfer.amount_usdc), Decimal("0"))).where(
            TokenTransfer.to_address.in_(
                select(Agent.wallet_address).where(Agent.id == agent_id)
            ),
            TokenTransfer.tx_hash.in_(
                select(Transaction.tx_hash).where(
                    Transaction.agent_id == agent_id,
                    Transaction.block_time >= start,
                )
            ),
        )
    ) or Decimal("0")

    failed = session.scalar(
        select(func.count(Transaction.tx_hash)).where(
            Transaction.agent_id == agent_id,
            Transaction.status == "failed",
            Transaction.block_time >= start,
        )
    ) or 0

    gas, out_gas, in_gas = session.execute(stmt).one()
    return SummaryOut(
        agent_id=agent_id,
        period=period,
        tx_count=gas or 0,
        outbound_usdc=Decimal(outbound),
        inbound_usdc=Decimal(inbound),
        gas_fee_usdc=Decimal(out_gas or 0) + Decimal(in_gas or 0),
        failed_tx_count=failed or 0,
    )


@router.get("/agents/{agent_id}/transactions", response_model=list[TxOut])
def agent_transactions(
    agent_id: uuid.UUID,
    limit: int = 50,
    cursor: str | None = None,
    session: Session = Depends(get_session),
) -> list[Transaction]:
    _get_agent_or_404(session, agent_id)
    limit = max(1, min(limit, 200))
    stmt = select(Transaction).where(Transaction.agent_id == agent_id)
    if cursor:
        stmt = stmt.where(Transaction.tx_hash < cursor)
    stmt = stmt.order_by(Transaction.block_time.desc(), Transaction.tx_hash.desc()).limit(limit)
    return list(session.scalars(stmt).all())


# --- live wallet balance (RPC, cached) -------------------------------------
USDC_BALANCE_SELECTOR = "0x70a08231000000000000000000000000"  # balanceOf(address) head
_BALANCE_CACHE: dict[str, tuple[float, Decimal]] = {}


@router.get("/wallets/{address}/balance")
async def wallet_balance(address: str) -> dict[str, object]:
    """Live ARC-native + USDC (ERC-20, 6 dp) balance via RPC, 10 s cache."""
    key = address.lower()
    now = time.monotonic()
    cached = _BALANCE_CACHE.get(key)
    if cached is not None and now - cached[0] < 10:
        return {"address": address, "native_wei": str(cached[1][0]), "usdc": str(cached[1][1])}

    settings = get_settings()
    async with RpcClient.from_settings(settings) as rpc:
        native_wei_raw = await rpc.eth_get_balance(address)
        usdc_selector = USDC_BALANCE_SELECTOR + address[2:].zfill(64)
        usdc_raw_hex = await rpc.eth_call(to=settings.usdc_address, data=usdc_selector)
        usdc_raw = int(usdc_raw_hex, 16)
        usdc = Decimal(usdc_raw) / Decimal(10**6)

    _BALANCE_CACHE[key] = (now, (Decimal(native_wei_raw), usdc))
    return {
        "address": address,
        "native_wei": str(native_wei_raw),
        "usdc": str(usdc),
    }
