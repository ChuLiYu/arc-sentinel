"""Failure/recovery contracts against migrated PostgreSQL and mocked JSON-RPC.

No live chain, credentials or funds are used. A failed batch must leave both
the ledger and checkpoint untouched; recovery replays from the same position.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import func, select, text

from app.db.models import Agent, IndexedBlock, IndexerState, TokenTransfer, Transaction
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


def block_hash(number):
    return "0x" + format(number, "064x")


@pytest.fixture
def db(migrated_db):
    # Only this module's disposable container is touched.
    with migrated_db() as session:
        session.execute(text("TRUNCATE agents, indexer_state, indexed_blocks CASCADE"))
        session.add(Agent(name="recovery-wallet", wallet_address=WALLET))
        set_last_indexed_block(session, CHAIN, 100)
        session.add(IndexedBlock(
            chain_id=CHAIN, block_number=100, block_hash=block_hash(100),
            parent_hash=block_hash(99), block_time=datetime.fromtimestamp(1750000101, UTC),
            observed_provider="primary", is_anchor=True,
        ))
        session.commit()
    return migrated_db


def rpc_handler(fault):
    block_reads = {}

    def handler(request):
        payload = json.loads(request.content)
        method, params = payload["method"], payload["params"]
        result: Any
        if method == "eth_chainId":
            result = hex(fault.get("chain", CHAIN))
        elif method == "eth_blockNumber":
            result = hex(fault.get("head", 103))
        elif method == "eth_getBlockByNumber":
            number = int(params[0], 16)
            block_reads[number] = block_reads.get(number, 0) + 1
            hash_ = block_hash(number)
            if fault.get("checkpoint_fork") and number == 101:
                hash_ = block_hash(999)
            if (fault.get("boundary_changed") and number == fault.get("head", 103) - 2
                    and block_reads[number] > 1):
                hash_ = block_hash(999)
            result = {
                "number": params[0], "timestamp": hex(1750000101),
                "hash": hash_, "parentHash": block_hash(number - 1 if number else 0),
                "transactions": [{
                    "hash": TX, "from": WALLET, "to": OTHER, "input": "0x",
                    "blockHash": hash_, "blockNumber": params[0],
                }] if number == 101 else [],
            }
            if fault.get("broken_parent") and number == 101:
                result["parentHash"] = block_hash(999)
            if fault.get("internal_parent") and number == 102:
                result["parentHash"] = block_hash(999)
            if fault.get("transaction_wrong_block") and number == 101:
                result["transactions"][0]["blockHash"] = block_hash(999)
            if fault.get("missing_block"):
                result = None
        elif method == "eth_getTransactionReceipt":
            result = {
                "status": "0x1", "gasUsed": "0x5208", "effectiveGasPrice": "0x1",
                "transactionHash": TX, "blockNumber": "0x65", "blockHash": block_hash(101),
            }
            if fault.get("receipt_mismatch"):
                result["blockHash"] = block_hash(999)
            if fault.get("receipt_wrong_tx"):
                result["transactionHash"] = block_hash(999)
            if fault.get("failed_transfer"):
                result["status"] = "0x0"
            if fault.get("missing_receipt"):
                result = None
        elif method == "eth_getLogs":
            result = [{
                "address": TOKEN, "transactionHash": TX, "logIndex": "0x0",
                "blockNumber": "0x65", "blockHash": block_hash(101),
                "topics": [TRANSFER_EVENT_SIG, "0x" + WALLET[2:].rjust(64, "0"),
                           "0x" + OTHER[2:].rjust(64, "0")],
                "data": "0x" + format(1000000, "064x"),
            }]
            if fault.get("bad_log"):
                result[0]["data"] = "0xinvalid"
            if fault.get("log_out_of_range"):
                result[0]["blockNumber"] = "0x67"
            if fault.get("log_wrong_hash"):
                result[0]["blockHash"] = block_hash(999)
            if fault.get("log_absent_tx"):
                result[0]["transactionHash"] = block_hash(999)
            if fault.get("duplicate_conflict") and params[0]["topics"][1] is None:
                result[0]["data"] = "0x" + format(2, "064x")
        else:
            raise AssertionError(method)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})
    return handler


@pytest.mark.parametrize("failure", [
    "broken_parent", "receipt_mismatch", "log_out_of_range", "boundary_changed",
    "transaction_wrong_block", "receipt_wrong_tx", "failed_transfer", "log_wrong_hash",
    "log_absent_tx", "duplicate_conflict", "internal_parent",
])
@respx.mock
async def test_inconsistent_chain_view_cannot_commit(db, failure):
    respx.post(URL).mock(side_effect=rpc_handler({failure: True, "head": 104}))
    async with RpcClient(url=URL, max_retries=0) as rpc:
        with pytest.raises(RpcError):
            await worker(db, rpc).poll_once()
    assert_state(db, 100, 0, 0)


@respx.mock
async def test_forked_checkpoint_stops_even_when_caught_up(db):
    fault = {}
    respx.post(URL).mock(side_effect=rpc_handler(fault))
    async with RpcClient(url=URL, max_retries=0) as rpc:
        instance = worker(db, rpc)
        await instance.poll_once()
        fault["checkpoint_fork"] = True
        with pytest.raises(RpcError, match="recovery required"):
            await instance.poll_once()
    assert_state(db, 101, 1, 1)


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
        assert session.scalar(select(func.count()).select_from(IndexedBlock).where(
            IndexedBlock.block_number > (checkpoint if checkpoint is not None else -1)
        )) == 0
        if checkpoint is not None:
            journal = session.get(IndexedBlock, (CHAIN, checkpoint))
            assert journal is not None
            assert journal.block_hash == block_hash(checkpoint)


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
        session.execute(text("TRUNCATE indexer_state, indexed_blocks"))
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


@respx.mock
async def test_legacy_height_only_checkpoint_requires_recovery(db):
    with db() as session:
        session.execute(text("TRUNCATE indexed_blocks"))
        session.commit()
    respx.post(URL).mock(side_effect=rpc_handler({}))
    async with RpcClient(url=URL, max_retries=0) as rpc:
        with pytest.raises(RpcError, match="legacy checkpoint"):
            await worker(db, rpc).poll_once()
    with db() as session:
        assert session.get(IndexerState, CHAIN).last_indexed_block == 100
        assert session.scalar(select(func.count()).select_from(IndexedBlock)) == 0
        assert session.scalar(select(func.count()).select_from(Transaction)) == 0


@pytest.mark.parametrize(("last", "expected"), [(100, 180), (490, 500)])
@respx.mock
async def test_no_wallet_batch_cap_still_journals_contiguous_history(db, last, expected):
    with db() as session:
        session.execute(text("TRUNCATE agents, indexer_state, indexed_blocks CASCADE"))
        set_last_indexed_block(session, CHAIN, last)
        session.add(IndexedBlock(
            chain_id=CHAIN, block_number=last, block_hash=block_hash(last),
            parent_hash=block_hash(last - 1), block_time=datetime.fromtimestamp(1750000101, UTC),
            observed_provider="primary", is_anchor=True,
        ))
        session.commit()
    respx.post(URL).mock(side_effect=rpc_handler({"head": 502}))
    async with RpcClient(url=URL, max_retries=0) as rpc:
        result = await worker(db, rpc).poll_once()
    assert result.from_block == last + 1
    assert result.to_block == expected
    assert_state(db, expected, 0, 0)
    with db() as session:
        assert session.scalar(select(func.count()).select_from(IndexedBlock)) == expected - last + 1


@pytest.mark.parametrize("fallback_chain", [CHAIN, 1])
@respx.mock
async def test_mid_batch_failover_retries_with_fresh_chain_validation(db, fallback_chain):
    fallback_url = "https://fallback.invalid"
    primary = rpc_handler({})

    def primary_handler(request):
        if json.loads(request.content)["method"] == "eth_getLogs":
            return httpx.Response(503)
        return primary(request)

    respx.post(URL).mock(side_effect=primary_handler)
    respx.post(fallback_url).mock(side_effect=rpc_handler({"chain": fallback_chain}))
    async with RpcClient(
        url=URL, fallback_url=fallback_url, max_retries=0,
        max_consecutive_failures=1, probe_after_successes=10000,
    ) as rpc:
        instance = worker(db, rpc)
        with pytest.raises(RpcError, match="provider/chain changed"):
            await instance.poll_once()
        assert rpc.provider_generation == 1
        assert_state(db, 100, 0, 0)
        if fallback_chain != CHAIN:
            with pytest.raises(RpcError, match="chain mismatch"):
                await instance.poll_once()
            assert_state(db, 100, 0, 0)
        else:
            await instance.poll_once()
            assert_state(db, 101, 1, 1)
            with db() as session:
                assert session.get(IndexedBlock, (CHAIN, 101)).observed_provider == "fallback"
