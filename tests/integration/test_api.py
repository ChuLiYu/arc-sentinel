"""API integration: write-scope auth + read routes against Postgres (SPEC §8, Phase 4).

Spins up the same throwaway Postgres fixture as the indexer integration test,
seeds one agent, then exercises the FastAPI app through httpx TestClient with:
* write routes reject missing/invalid API keys and accept a valid one,
* read routes return the seeded data without any key,
* /health reports indexer state (mocked RPC),
* dashboard HTML renders.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import _hash_api_key
from app.config import Settings
from app.db.models import Agent, Transaction
from app.main import app

API_KEY = "test-write-key-123456"
RPC_URL = "https://rpc.testnet.invalid"


@pytest.fixture()
def client(
    migrated_db: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> httpx.Client:
    from app.db import session as db_session

    test_settings = Settings(api_key_hash=_hash_api_key(API_KEY))
    for mod in ("app.config", "app.main", "app.api.deps", "app.api.routes_transactions"):
        monkeypatch.setattr(f"{mod}.get_settings", lambda: test_settings)
    monkeypatch.setattr(db_session, "settings", test_settings)

    # point the global session factory at the test DB before app startup
    engine = migrated_db.kw["bind"]
    db_session._engine = engine
    db_session._session_factory = migrated_db
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_write_route_requires_api_key(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/agents", json={"name": "x", "wallet_address": "0x" + "1" * 40}
    )
    assert resp.status_code == 401 or resp.status_code == 503
    resp2 = await client.post(
        "/api/v1/agents",
        json={"name": "x", "wallet_address": "0x" + "1" * 40},
        headers={"X-API-Key": "wrong"},
    )
    assert resp2.status_code in (401, 403, 503)


async def test_create_agent_and_list(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/agents",
        json={"name": "claude-code", "framework": "Claude Code", "wallet_address": "0x" + "2" * 40},
        headers={"X-API-Key": API_KEY},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["wallet_address"] == "0x" + "2" * 40
    assert data["is_active"] is True

    listed_resp = await client.get("/api/v1/agents")
    listed = listed_resp.json()
    assert any(a["name"] == "claude-code" for a in listed)


async def test_duplicate_wallet_conflict(client: httpx.AsyncClient):
    payload = {"name": "dup", "wallet_address": "0x" + "3" * 40}
    headers = {"X-API-Key": API_KEY}
    r1 = await client.post("/api/v1/agents", json=payload, headers=headers)
    r2 = await client.post("/api/v1/agents", json=payload, headers=headers)
    assert r1.status_code == 201
    assert r2.status_code == 409


async def test_agent_summary_and_transactions(
    client: httpx.AsyncClient, migrated_db: sessionmaker[Session]
):
    wallet = "0x" + "4" * 40
    with migrated_db() as session:
        agent = Agent(name="tracked", wallet_address=wallet)
        session.add(agent)
        session.flush()
        agent_id = agent.id
        session.add(
            Transaction(
                tx_hash="0x" + "a" * 64,
                block_number=1,
                block_time=__import__("datetime").datetime.now(),
                from_address=wallet,
                to_address="0x" + "5" * 40,
                agent_id=agent_id,
                direction="outbound",
                status="success",
                gas_fee_usdc=Decimal("0.000021"),
            )
        )
        session.commit()

    summary_resp = await client.get(f"/api/v1/agents/{agent_id}/summary?period=30d")
    summary = summary_resp.json()
    assert summary["period"] == "30d"

    txs_resp = await client.get(f"/api/v1/agents/{agent_id}/transactions", params={"limit": 10})
    txs = txs_resp.json()
    assert len(txs) == 1
    assert txs[0]["direction"] == "outbound"


async def test_health_reports_lag(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch):
    settings = Settings(rpc_url=RPC_URL, chain_id=1)
    monkeypatch.setattr("app.main.get_settings", lambda: settings)

    with respx.mock() as router:
        router.post(RPC_URL).mock(
            return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x64"})
        )
        resp = await client.get("/api/v1/health")
    body = resp.json()
    assert "status" in body
    assert body["rpc_ok"] is True


async def test_dashboard_renders(client: httpx.AsyncClient):
    resp = await client.get("/api/v1/dashboard")
    assert resp.status_code == 200
    assert "Arc Sentinel" in resp.text
