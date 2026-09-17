"""Alert + budget routes (SPEC §8). Network stats live in routes_stats.py."""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_api_key
from app.db.models import Alert, Budget
from app.db.session import get_session

router = APIRouter(tags=["alerts"])


class AlertOut(BaseModel):
    id: int
    agent_id: uuid.UUID | None
    tx_hash: str | None
    alert_type: str
    severity: str
    message: str
    created_at: object
    acked_at: object | None

    model_config = {"from_attributes": True}


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(
    status: str | None = "open",
    severity: str | None = None,
    session: Session = Depends(get_session),
) -> list[Alert]:
    stmt = select(Alert).order_by(Alert.created_at.desc()).limit(200)
    if status == "open":
        stmt = stmt.where(Alert.acked_at.is_(None))
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    return list(session.scalars(stmt).all())


@router.post("/alerts/{alert_id}/ack")
def ack_alert(
    alert_id: int,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    alert.acked_at = func.now()
    session.commit()
    return {"ok": True, "id": alert_id}


class BudgetCreate(BaseModel):
    agent_id: uuid.UUID
    period: str  # daily | weekly | monthly
    limit_usdc: Decimal


class BudgetOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    period: str
    limit_usdc: Decimal
    is_active: bool

    model_config = {"from_attributes": True}


@router.post("/budgets", response_model=BudgetOut, status_code=status.HTTP_201_CREATED)
def create_budget(
    payload: BudgetCreate,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> Budget:
    budget = Budget(
        agent_id=payload.agent_id,
        period=payload.period,
        limit_usdc=payload.limit_usdc,
    )
    session.add(budget)
    session.commit()
    session.refresh(budget)
    return budget
