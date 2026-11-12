"""FastAPI entrypoint: REST API + dashboard (SPEC §8)."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import routes_agents, routes_alerts, routes_stats, routes_transactions
from app.api.deps import require_api_key
from app.config import get_settings
from app.db.models import Agent, Alert, DailyAgentSummary, IndexerState, Transaction
from app.db.session import get_session
from app.indexer.rpc_client import RpcClient, RpcError
from app.rules.runner import run_rules

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Start the rules-runner background loop and stop it on shutdown."""
    task = asyncio.create_task(_rules_runner_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def _rules_runner_loop() -> None:
    """Re-evaluate rules every ``rules_run_interval_seconds`` (SPEC §20.6).

    Runs independently of the indexer; a failed cycle is logged and the loop
    keeps going so one bad query never kills alert generation.
    """
    while True:
        try:
            await run_rules(database_url=get_settings().database_url)
        except Exception:
            logger.exception("rules runner cycle failed")
        await asyncio.sleep(get_settings().rules_run_interval_seconds)


app = FastAPI(
    title="Arc Sentinel",
    description="Read-only monitoring of wallet USDC flows on a configured Arc network.",
    version="0.1.0",
    lifespan=lifespan,
)

api = APIRouter(prefix="/api/v1")

TEMPLATES_DIR = Path(__file__).resolve().parent / "dashboard" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@api.get("/health")
async def health(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Report lag and durable recovery state; a near-head fork is still degraded."""
    settings = get_settings()

    row = session.get(IndexerState, settings.chain_id)
    last = row.last_indexed_block if row else None
    recovery_reason = row.recovery_reason if row else None
    ancestor = row.recovery_ancestor_block if row else None
    detected_at = row.recovery_detected_at if row else None
    session.close()

    lag: int | None = None
    rpc_ok = False
    head: int | None = None
    try:
        async with RpcClient.from_settings(settings) as rpc:
            head = await rpc.eth_block_number()
            rpc_ok = True
    except RpcError:
        rpc_ok = False

    if last is not None and head is not None:
        lag = max(0, head - last)

    indexer_ok = (last is not None and lag is not None and lag <= 20
                  and recovery_reason is None)
    return {
        "status": "ok" if (rpc_ok and indexer_ok) else "degraded",
        "chain_id": settings.chain_id,
        "indexer": {
            "last_indexed_block": last, "rpc_head": head, "lag_blocks": lag,
            "recovery_required": recovery_reason is not None,
            "recovery_reason": recovery_reason,
            "common_ancestor_block": ancestor,
            "recovery_detected_at": detected_at,
        },
        "rpc_ok": rpc_ok,
    }


# ---------------------------------------------------------------------------
# Dashboard (server-side rendered, SPEC §8)
# ---------------------------------------------------------------------------
@api.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    agents = list(session.scalars(select(Agent).order_by(Agent.created_at.desc())).all())
    open_alerts = list(
        session.scalars(
            select(Alert)
            .where(Alert.acked_at.is_(None))
            .order_by(Alert.created_at.desc())
            .limit(20)
        ).all()
    )

    # Last-7-days outbound USDC series for the dashboard line chart.
    # Aggregated from daily_agent_summary (a handful of rows/day).
    today = datetime.now(UTC).date()
    start = today - timedelta(days=6)
    rows = session.execute(
        select(
            DailyAgentSummary.day,
            func.sum(DailyAgentSummary.outbound_usdc).label("outbound_usdc"),
        )
        .where(DailyAgentSummary.day >= start)
        .group_by(DailyAgentSummary.day)
        .order_by(DailyAgentSummary.day)
    ).all()
    by_day = {r.day: Decimal(r.outbound_usdc or 0) for r in rows}
    outbound_series = [
        {
            "day": d.isoformat(),
            "outbound_usdc": float(by_day.get(d, Decimal("0"))),
        }
        for d in (start + timedelta(days=i) for i in range(7))
    ]
    total_outbound_7d = sum(
        Decimal(point["outbound_usdc"]).quantize(Decimal("0.01")) for point in outbound_series
    )

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "agents": agents,
            "open_alerts": open_alerts,
            "chain_id": get_settings().chain_id,
            "outbound_series": outbound_series,
            "total_outbound_7d": total_outbound_7d,
        },
    )


@api.get("/export/ledger.csv", response_class=HTMLResponse)
def export_ledger(
    agent_id: str,
    session: Session = Depends(get_session),
    _: None = Depends(require_api_key),
) -> str:
    """Export a CSV of raw transactions for a tracked agent (SPEC §8)."""
    try:
        agent_uuid = uuid.UUID(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid agent_id") from exc

    rows = list(
        session.scalars(
            select(Transaction)
            .where(Transaction.agent_id == agent_uuid)
            .order_by(Transaction.block_time.desc())
            .limit(5000)
        ).all()
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "tx_hash",
            "block_number",
            "block_time",
            "from",
            "to",
            "direction",
            "status",
            "gas_fee_usdc",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.tx_hash,
                r.block_number,
                r.block_time.isoformat(),
                r.from_address,
                r.to_address,
                r.direction,
                r.status,
                str(r.gas_fee_usdc or ""),
            ]
        )
    return buf.getvalue()


app.include_router(api)
app.include_router(routes_agents.router, prefix="/api/v1")
app.include_router(routes_transactions.router, prefix="/api/v1")
app.include_router(routes_alerts.router, prefix="/api/v1")
app.include_router(routes_stats.router, prefix="/api/v1")


@app.get("/", response_class=HTMLResponse)
def root(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name="index.html", context={"title": "Arc Sentinel"}
    )
