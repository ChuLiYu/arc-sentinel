"""API integration: write-scope auth + read routes against Postgres (SPEC §8, Phase 4).

Spins up the same throwaway Postgres fixture as the indexer integration test,
seeds one agent, then exercises the FastAPI app through httpx TestClient with:
* write routes reject missing/invalid API keys and accept a valid one,
* read routes return the seeded data without any key,
* /health reports indexer state (mocked RPC),
* dashboard HTML renders.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import _hash_api_key
from app.config import Settings
from app.db.models import Agent, ApiKey, IndexerState, Transaction
from app.main import app

API_KEY = "test-write-key-123456"
SALT = "test-salt-for-api-keys"
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


async def test_db_backed_api_key_auth(
    client: httpx.AsyncClient, migrated_db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
):
    """Phase 4 / SPEC §17: SENTINEL_API_KEY_SALT + api_keys table auth path."""
    from app.db import session as db_session

    db_key = "db-backed-key-987654"
    test_settings = Settings(api_key_salt=SALT)
    for mod in ("app.config", "app.main", "app.api.deps", "app.api.routes_transactions"):
        monkeypatch.setattr(f"{mod}.get_settings", lambda: test_settings)
    monkeypatch.setattr(db_session, "settings", test_settings)

    # seed the API key (sha256(salt + key)) exactly like scripts/gen_api_key.py
    with migrated_db() as session:
        session.add(ApiKey(key_hash=_hash_api_key(db_key, SALT), label="test-key"))
        session.commit()

    # without / with wrong key → not authorized
    resp = await client.post(
        "/api/v1/agents", json={"name": "x", "wallet_address": "0x" + "1" * 40}
    )
    assert resp.status_code in (401, 403)
    resp = await client.post(
        "/api/v1/agents",
        json={"name": "x", "wallet_address": "0x" + "1" * 40},
        headers={"X-API-Key": "wrong"},
    )
    assert resp.status_code == 403

    # with the seeded key → works
    resp = await client.post(
        "/api/v1/agents",
        json={"name": "db-auth", "wallet_address": "0x" + "6" * 40},
        headers={"X-API-Key": db_key},
    )
    assert resp.status_code == 201


async def test_db_backed_key_auth_ignores_wrong_salt(
    client: httpx.AsyncClient, migrated_db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
):
    """A key hashed with a different salt must NOT authenticate."""
    from app.db import session as db_session

    db_key = "db-backed-key-999999"
    test_settings = Settings(api_key_salt="different-salt")
    for mod in ("app.config", "app.main", "app.api.deps", "app.api.routes_transactions"):
        monkeypatch.setattr(f"{mod}.get_settings", lambda: test_settings)
    monkeypatch.setattr(db_session, "settings", test_settings)

    # seed with the ORIGINAL salt — but deps is configured with a different salt
    with migrated_db() as session:
        session.add(ApiKey(key_hash=_hash_api_key(db_key, SALT), label="test-key"))
        session.commit()

    resp = await client.post(
        "/api/v1/agents",
        json={"name": "x", "wallet_address": "0x" + "7" * 40},
        headers={"X-API-Key": db_key},
    )
    assert resp.status_code == 403


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


@pytest.mark.parametrize(("reason", "rpc_down"), [
    (None, False), ("checkpoint_fork", False), ("rollback_window_exceeded", True),
])
async def test_health_exposes_durable_recovery_even_at_zero_lag(
    client, migrated_db, monkeypatch, reason, rpc_down,
):
    settings = Settings(
        _env_file=None, rpc_url=RPC_URL, rpc_fallback_url="", rpc_max_retries=0, chain_id=2,
    )
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    detected = datetime(2026, 10, 30, tzinfo=UTC) if reason else None
    ancestor = 99 if reason == "checkpoint_fork" else None
    with migrated_db() as session:
        session.merge(IndexerState(
            chain_id=2, last_indexed_block=100, recovery_reason=reason,
            recovery_ancestor_block=ancestor, recovery_detected_at=detected,
        ))
        session.commit()
    with respx.mock() as router:
        response = (httpx.Response(503) if rpc_down else httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": "0x64"},
        ))
        router.post(RPC_URL).mock(return_value=response)
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == ("degraded" if reason else "ok")
    assert body["rpc_ok"] is not rpc_down
    assert body["indexer"]["lag_blocks"] == (None if rpc_down else 0)
    assert body["indexer"]["recovery_required"] is (reason is not None)
    assert body["indexer"]["recovery_reason"] == reason
    assert body["indexer"]["common_ancestor_block"] == ancestor
    assert bool(body["indexer"]["recovery_detected_at"]) is bool(detected)


async def test_dashboard_renders(
    client: httpx.AsyncClient, migrated_db: sessionmaker[Session]
):
    """Dashboard HTML renders KPI cards + 7-day line chart (SPEC §8, dashboard-viz decision)."""
    import uuid
    from datetime import date, timedelta

    from app.db.models import DailyAgentSummary

    agent_id = uuid.uuid4()
    with migrated_db() as session:
        session.add(
            Agent(id=agent_id, name="test-agent", wallet_address="0x" + "11" * 20)
        )
        session.add(
            DailyAgentSummary(
                agent_id=agent_id, day=date.today(), outbound_usdc=Decimal("12.40")
            )
        )
        session.add(
            DailyAgentSummary(
                agent_id=agent_id,
                day=date.today() - timedelta(days=1),
                outbound_usdc=Decimal("8.00"),
            )
        )
        session.commit()

    resp = await client.get("/api/v1/dashboard")
    assert resp.status_code == 200
    assert "Arc Sentinel" in resp.text
    # KPI cards
    assert "Tracked agents" in resp.text
    assert "Open alerts" in resp.text
    assert "USDC out (7d)" in resp.text
    assert "$20.40" in resp.text  # 12.40 + 8.00
    # line chart section rendered
    assert "USDC outbound" in resp.text
    assert "line chart" in resp.text
    assert "<svg" in resp.text
