"""FastAPI entrypoint: REST API + dashboard (SPEC §8)."""

from __future__ import annotations

import csv
import io
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import routes_agents, routes_alerts, routes_stats, routes_transactions
from app.api.deps import require_api_key
from app.config import get_settings
from app.db.models import Agent, Alert, IndexerState, Transaction
from app.db.session import get_session
from app.indexer.rpc_client import RpcClient, RpcError

app = FastAPI(
    title="Arc Sentinel",
    description="Non-custodial, read-only monitoring of Agent wallet USDC flows on Arc mainnet.",
    version="0.1.0",
)

api = APIRouter(prefix="/api/v1")

TEMPLATES_DIR = Path(__file__).resolve().parent / "dashboard" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@api.get("/health")
async def health(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Liveness check: indexer lag in blocks vs. current RPC head (SPEC §8)."""
    settings = get_settings()

    row = session.get(IndexerState, settings.chain_id)
    last = row.last_indexed_block if row else None
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

    indexer_ok = last is not None and lag is not None and lag <= 20
    return {
        "status": "ok" if (rpc_ok and indexer_ok) else "degraded",
        "chain_id": settings.chain_id,
        "indexer": {"last_indexed_block": last, "rpc_head": head, "lag_blocks": lag},
        "rpc_ok": rpc_ok,
    }


# ---------------------------------------------------------------------------
# Dashboard (server-side rendered, SPEC §8)
# ---------------------------------------------------------------------------
@api.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    agents = list(
        session.scalars(select(Agent).order_by(Agent.created_at.desc())).all()
    )
    open_alerts = list(
        session.scalars(
            select(Alert)
            .where(Alert.acked_at.is_(None))
            .order_by(Alert.created_at.desc())
            .limit(20)
        ).all()
    )
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "agents": agents,
            "open_alerts": open_alerts,
            "chain_id": get_settings().chain_id,
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
