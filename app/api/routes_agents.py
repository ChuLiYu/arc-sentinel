"""Agent routes (SPEC §8)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_api_key
from app.db.models import Agent
from app.db.session import get_session

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    framework: str | None = None
    wallet_address: str = Field(min_length=42, max_length=42)
    owner_label: str | None = None


class AgentOut(BaseModel):
    id: uuid.UUID
    name: str
    framework: str | None
    wallet_address: str
    owner_label: str | None
    is_active: bool
    created_at: object

    model_config = {"from_attributes": True}


@router.get("", response_model=list[AgentOut])
def list_agents(session: Session = Depends(get_session)) -> list[Agent]:
    stmt = select(Agent).order_by(Agent.created_at.desc())
    return list(session.scalars(stmt).all())


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
def create_agent(
    payload: AgentCreate,
    _: None = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> Agent:
    address = payload.wallet_address.lower()
    exists = session.scalar(select(Agent).where(Agent.wallet_address == address))
    if exists is not None:
        raise HTTPException(status_code=409, detail="wallet already tracked")
    agent = Agent(
        name=payload.name,
        framework=payload.framework,
        wallet_address=address,
        owner_label=payload.owner_label,
    )
    session.add(agent)
    session.commit()
    session.refresh(agent)
    return agent
