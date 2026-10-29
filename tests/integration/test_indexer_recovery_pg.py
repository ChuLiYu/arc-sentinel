"""Failure/recovery contracts against migrated PostgreSQL and mocked JSON-RPC.

No live chain, credentials or funds are used. A failed batch must leave both
the ledger and checkpoint untouched; recovery replays from the same position.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import func, select, text

from app.db.models import Agent, IndexerState, TokenTransfer, Transaction
from app.indexer.decode import TRANSFER_EVENT_SIG
from app.indexer.rpc_client import RpcClient, RpcError
from app.indexer.state import set_last_indexed_block, try_indexer_lock
from app.indexer.worker import IndexerBusyError, IndexerWorker

CHAIN = 5042002
URL = "https://recovery.invalid"
WALLET = "0x" + "aa" * 20
OTHER = "0x" + "bb" * 20
TOKEN = "0x" + "36" * 20
TX = "0x" + "11" * 32


@pytest.fixture
def db(migrated_db):
    # Only this module's disposable container is touched.
    with migrated_db() as session:
        session.execute(text("TRUNCATE agents, indexer_state CASCADE"))
        session.add(Agent(name="recovery-wallet", wallet_address=WALLET))
        set_last_indexed_block(session, CHAIN, 100)
        session.commit()
    return migrated_db


def rpc_handler(fault):
    def handler(request):
        payload = json.loads(request.content)
        method, params = payload["method"], payload["params"]
        result: Any
        if method == "eth_chainId":
            result = hex(fault.get("chain", CHAIN))
        elif method == "eth_blockNumber":
            result = hex(103)
        elif method == "eth_getBlockByNumber":
            result = {
                "number": params[0], "timestamp": hex(1750000101),
                "transactions": [{"hash": TX, "from": WALLET, "to": OTHER, "input": "0x"}],
            }
            if fault.get("missing_block"):
                result = None
        elif method == "eth_getTransactionReceipt":
            result = {"status": "0x1", "gasUsed": "0x5208", "effectiveGasPrice": "0x1"}
            if fault.get("missing_receipt"):
                result = None
        elif method == "eth_getLogs":
            result = [{
                "address": TOKEN, "transactionHash": TX, "logIndex": "0x0",
                "topics": [TRANSFER_EVENT_SIG, "0x" + WALLET[2:].rjust(64, "0"),
                           "0x" + OTHER[2:].rjust(64, "0")],
                "data": "0x" + format(1000000, "064x"),
            }]
            if fault.get("bad_log"):
                result[0]["data"] = "0xinvalid"
        else:
            raise AssertionError(method)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})
    return handler


def worker(db, rpc, **kwargs):
    return IndexerWorker(
        session_factory=db, rpc=rpc, chain_id=CHAIN, usdc_address=TOKEN, **kwargs
    )


def assert_state(db, checkpoint, transactions, transfers):
    with db() as session:
        state = session.get(IndexerState, CHAIN)
        assert (state.last_indexed_block if state else None) == checkpoint
        assert session.scalar(select(func.count()).select_from(Transaction)) == transactions
        assert session.scalar(select(func.count()).select_from(TokenTransfer)) == transfers


@pytest.mark.parametrize("failure", ["missing_block", "missing_receipt", "bad_log"])
@respx.mock
async def test_incomplete_batch_retries_without_losing_events(db, failure):
    fault = {failure: True}
    respx.post(URL).mock(side_effect=rpc_handler(fault))
    async with RpcClient(url=URL, max_retries=0) as rpc:
        instance = worker(db, rpc)
        with pytest.raises(RpcError):
            await instance.poll_once()
        assert_state(db, 100, 0, 0)
        fault.clear()
        result = await instance.poll_once()
        assert result.from_block == 101
        assert_state(db, 101, 1, 1)


@respx.mock
async def test_database_failure_rolls_back_rows_and_checkpoint(db, monkeypatch):
    respx.post(URL).mock(side_effect=rpc_handler({}))
    async with RpcClient(url=URL, max_retries=0) as rpc:
        instance = worker(db, rpc)

        def fail_checkpoint(*args):
            raise RuntimeError("simulated failure after ledger inserts")

        with monkeypatch.context() as patch:
            patch.setattr("app.indexer.worker.set_last_indexed_block", fail_checkpoint)
            with pytest.raises(RuntimeError, match="simulated failure"):
                await instance.poll_once()
        assert_state(db, 100, 0, 0)
        await instance.poll_once()
        assert_state(db, 101, 1, 1)


@respx.mock
async def test_competing_worker_cannot_write_and_lock_releases_on_rollback(db):
    respx.post(URL).mock(side_effect=rpc_handler({}))
    async with RpcClient(url=URL, max_retries=0) as rpc:
        with db() as owner:
            assert try_indexer_lock(owner, CHAIN)
            with pytest.raises(IndexerBusyError):
                await worker(db, rpc).poll_once()
            assert_state(db, 100, 0, 0)
            owner.rollback()
            await worker(db, rpc).poll_once()
        assert_state(db, 101, 1, 1)


@respx.mock
async def test_wrong_chain_cannot_change_checkpoint(db):
    respx.post(URL).mock(side_effect=rpc_handler({"chain": 1}))
    async with RpcClient(url=URL, max_retries=0) as rpc:
        with pytest.raises(RpcError, match="chain mismatch"):
            await worker(db, rpc).poll_once()
    assert_state(db, 100, 0, 0)


@pytest.mark.parametrize("start", [0, 101, 200])
@respx.mock
async def test_explicit_start_is_inclusive_and_future_start_does_not_skip(db, start):
    with db() as session:
        session.execute(text("TRUNCATE indexer_state"))
        session.execute(text("TRUNCATE agents CASCADE"))
        session.commit()
    respx.post(URL).mock(side_effect=rpc_handler({}))
    async with RpcClient(url=URL, max_retries=0) as rpc:
        result = await worker(db, rpc, start_block=start, max_blocks_per_cycle=2).poll_once()
    if start > 101:
        assert result.to_block is None
        assert result.last_indexed_block is None
        assert_state(db, None, 0, 0)
    else:
        assert result.from_block == start
        assert result.to_block == min(start + 1, 101)
        assert_state(db, result.to_block, 0, 0)
