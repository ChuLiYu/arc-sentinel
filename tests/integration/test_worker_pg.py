"""Integration: full indexer poll cycle against Postgres with a mocked RPC (SPEC §10 / §15).

Starts the migrated Postgres from conftest, seeds one active + one inactive
agent, and feeds the ``IndexerWorker`` a fixture block through respx:
* one USDC ``Transfer`` (event-log topics + 6-dp amount) outbound from the agent,
* one failed contract call,
* one inbound contract-style call,
* one transaction from the *inactive* wallet that must NOT be indexed.

Verifies the Phase-2 acceptance criteria:
* ``transactions`` + ``token_transfers`` rows carry the right values/agent link,
* ``indexer_state.last_indexed_block`` advances together with the writes,
* re-running the same block inserts nothing (``tx_hash`` / ``(tx_hash, log_index)``
  UNIQUE idempotency).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import respx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Agent, IndexedBlock, IndexerState, TokenTransfer, Transaction
from app.indexer.decode import TRANSFER_EVENT_SIG
from app.indexer.rpc_client import RpcClient
from app.indexer.worker import IndexerWorker

RPC_URL = "https://rpc.testnet.invalid"
CHAIN_ID = 5042002
USDC = "0x3600000000000000000000000000000000000000"

WALLET = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"  # active agent
INACTIVE_WALLET = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"  # inactive agent
FROM_EXT = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
CONTRACT = "0xcccccccccccccccccccccccccccccccccccccccc"

TX_TRANSFER = "0x" + "11" * 32
TX_FAILED = "0x" + "22" * 32
TX_INBOUND = "0x" + "33" * 32
TX_INACTIVE = "0x" + "44" * 32  # belongs to the inactive wallet → never indexed

BLOCK_TS_BASE = 1_750_000_000  # block timestamp = base + block_number
BLOCK_WITH_TXS = 101


def _topic(address: str) -> str:
    return "0x" + address[2:].rjust(64, "0")


def _word(value: int) -> str:
    return "0x" + format(value, "064x")


def _tx(tx_hash: str, sender: str, to: str | None, input_hex: str) -> dict[str, Any]:
    return {"hash": tx_hash, "from": sender, "to": to, "input": input_hex}


def _receipt(tx_hash: str, status: str) -> dict[str, Any]:
    return {
        "transactionHash": tx_hash,
        "blockHash": "0x" + format(BLOCK_WITH_TXS, "064x"),
        "blockNumber": hex(BLOCK_WITH_TXS),
        "status": status,
        "gasUsed": "0x5208",  # 21_000
        "effectiveGasPrice": "0x3b9aca00",  # 10**9 wei → fee = 0.000021 ETH
    }


def _block(number: int, txs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "number": hex(number),
        "hash": "0x" + format(number, "064x"),
        "parentHash": "0x" + format(max(0, number - 1), "064x"),
        "timestamp": hex(BLOCK_TS_BASE + number),
        "transactions": [dict(tx, blockNumber=hex(number),
                              blockHash="0x" + format(number, "064x")) for tx in txs],
    }


TRANSFER_AMOUNT = 1_000_000  # micro USDC == 1.000000 USDC
TRANSFER_LOG: dict[str, Any] = {
    "address": USDC,
    "topics": [TRANSFER_EVENT_SIG, _topic(WALLET), _topic(FROM_EXT)],
    "data": _word(TRANSFER_AMOUNT),
    "transactionHash": TX_TRANSFER,
    "logIndex": "0x0",
    "blockNumber": hex(BLOCK_WITH_TXS),
    "blockHash": "0x" + format(BLOCK_WITH_TXS, "064x"),
}

BLOCK_101: dict[str, Any] = _block(
    BLOCK_WITH_TXS,
    [
        _tx(TX_TRANSFER, WALLET, USDC, "0xa9059cbb" + "11" * 60),
        _tx(TX_FAILED, WALLET, CONTRACT, "0xdeadbeef" + "22" * 60),
        _tx(TX_INBOUND, FROM_EXT, WALLET, "0x23b872dd" + "33" * 60),
        _tx(TX_INACTIVE, INACTIVE_WALLET, CONTRACT, "0x523e1566" + "44" * 60),
    ],
)

RECEIPTS = {
    TX_TRANSFER: _receipt(TX_TRANSFER, "0x1"),
    TX_FAILED: _receipt(TX_FAILED, "0x0"),
    TX_INBOUND: _receipt(TX_INBOUND, "0x1"),
}


def _rpc_handler(head_state: dict[str, int]):
    """Serve every JSON-RPC call the worker makes from the fixtures above."""
    tx_by_hash = {tx["hash"]: tx for tx in BLOCK_101["transactions"]}

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        params = payload["params"]

        if method == "eth_chainId":
            result: Any = hex(CHAIN_ID)
        elif method == "eth_blockNumber":
            result = hex(head_state["head"])
        elif method == "eth_getBlockByNumber":
            number = int(params[0], 16)
            result = BLOCK_101 if number == BLOCK_WITH_TXS else _block(number, [])
        elif method == "eth_getTransactionReceipt":
            result = RECEIPTS.get(params[0])
        elif method == "eth_getTransactionByHash":
            result = tx_by_hash.get(params[0])
        elif method == "eth_getLogs":
            request_filter = params[0]
            result = ([TRANSFER_LOG] if int(request_filter["fromBlock"], 16)
                      <= BLOCK_WITH_TXS <= int(request_filter["toBlock"], 16) else [])
        else:
            raise AssertionError(f"unexpected RPC method {method!r}")

        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result}
        )

    return handler


@respx.mock
async def test_poll_cycle_indexes_then_stays_idempotent(
    migrated_db: sessionmaker[Session],
) -> None:
    factory = migrated_db

    # --- seed: one active agent (the tracked wallet) + one inactive agent ---
    with factory() as session:
        active = Agent(name="active-agent", wallet_address=WALLET, is_active=True)
        session.add_all(
            [
                active,
                Agent(name="inactive-agent", wallet_address=INACTIVE_WALLET, is_active=False),
            ]
        )
        session.commit()
        active_id = active.id
        assert active_id is not None

    head_state = {"head": 102}  # confirmations=2 → safe_head = 100
    respx.post(RPC_URL).mock(side_effect=_rpc_handler(head_state))
    client = RpcClient(url=RPC_URL, max_retries=0)
    worker = IndexerWorker(
        session_factory=factory,
        rpc=client,
        chain_id=CHAIN_ID,
        usdc_address=USDC,
        confirmations=2,
    )

    try:
        # --- first poll: first run initialises the breakpoint, indexes nothing ---
        first = await worker.poll_once()
        assert first.from_block is None
        assert first.last_indexed_block == 100

        # --- second poll: head 103 → safe_head 101 → block 101 is indexed ---
        head_state["head"] = 103
        second = await worker.poll_once()
        assert second.from_block == 101
        assert second.to_block == 101
        assert second.transactions_inserted == 3
        assert second.transfers_inserted == 1
        assert second.last_indexed_block == 101

        block_101_time = datetime.fromtimestamp(BLOCK_TS_BASE + BLOCK_WITH_TXS, tz=UTC)

        with factory() as session:
            txs = list(session.scalars(select(Transaction).order_by(Transaction.tx_hash)))
            assert len(txs) == 3, "only transactions touching an *active* agent wallet are indexed"
            by_hash = {t.tx_hash: t for t in txs}
            assert TX_INACTIVE not in by_hash

            transfer_tx = by_hash[TX_TRANSFER]
            assert transfer_tx.agent_id == active_id
            assert transfer_tx.direction == "outbound"
            assert transfer_tx.status == "success"
            assert transfer_tx.method_selector == "0xa9059cbb"
            assert transfer_tx.block_number == BLOCK_WITH_TXS
            assert transfer_tx.block_time == block_101_time
            assert transfer_tx.from_address == WALLET
            assert transfer_tx.to_address == USDC
            assert transfer_tx.gas_used == Decimal(21_000)
            assert transfer_tx.gas_fee_wei == Decimal("21000000000000")
            assert transfer_tx.gas_fee_usdc == Decimal("0.000021")

            failed_tx = by_hash[TX_FAILED]
            assert failed_tx.status == "failed"
            assert failed_tx.method_selector == "0xdeadbeef"
            assert failed_tx.direction == "outbound"

            inbound_tx = by_hash[TX_INBOUND]
            assert inbound_tx.direction == "inbound"
            assert inbound_tx.agent_id == active_id
            assert inbound_tx.to_address == WALLET

            transfers = list(session.scalars(select(TokenTransfer)))
            assert len(transfers) == 1
            transfer = transfers[0]
            assert transfer.tx_hash == TX_TRANSFER
            assert transfer.log_index == 0
            assert transfer.token_address == USDC
            assert transfer.from_address == WALLET
            assert transfer.to_address == FROM_EXT
            assert transfer.amount_raw == Decimal(TRANSFER_AMOUNT)
            assert transfer.amount_usdc == Decimal("1.000000")

            state = session.get(IndexerState, CHAIN_ID)
            assert state is not None
            assert state.last_indexed_block == 101
            assert session.get(IndexedBlock, (CHAIN_ID, 100)).is_anchor is True
            assert session.get(IndexedBlock, (CHAIN_ID, 101)).is_anchor is False

        # --- third poll: same head → already up to date, zero writes ---
        third = await worker.poll_once()
        assert third.to_block is None
        assert third.transactions_inserted == 0
        assert third.transfers_inserted == 0

        # --- restart from an older breakpoint → same block re-parsed, no dupes ---
        with factory() as session:
            stmt = (
                IndexerState.__table__.update()
                .where(IndexerState.chain_id == CHAIN_ID)
                .values(last_indexed_block=100)
            )
            session.execute(stmt)
            session.commit()

        rerun = await worker.poll_once()
        assert rerun.transactions_inserted == 0, "ON CONFLICT DO NOTHING must be idempotent"
        assert rerun.transfers_inserted == 0
        assert rerun.last_indexed_block == 101

        with factory() as session:
            assert session.scalar(select(func.count()).select_from(Transaction)) == 3
            assert session.scalar(select(func.count()).select_from(TokenTransfer)) == 1
            state = session.get(IndexerState, CHAIN_ID)
            assert state is not None
            assert state.last_indexed_block == 101
    finally:
        await client.close()
