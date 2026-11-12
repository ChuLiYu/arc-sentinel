"""Unit tests for ``app.indexer.worker`` — the DB-free pure helpers (SPEC §10).

Covers transaction parsing (direction, agent, method selector, gas fees),
USDC transfer log decoding, ``raw_input`` truncation and the 6-dp
``ROUND_DOWN`` boundaries, plus the relayer-transfer dedup regression.
No database and no network are involved.
"""

from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import sessionmaker

from app.indexer.decode import TRANSFER_EVENT_SIG, DecodeError
from app.indexer.rpc_client import RpcError
from app.indexer.worker import (
    IndexerWorker,
    ParsedTransfer,
    ParsedTx,
    _block_time,
    _compute_gas,
    _decode_log,
    _method_selector,
    _parse_tx,
    _raw_input_head,
    _status_from_receipt,
    _topic_address,
    _tx_touches_wallet,
    hex_to_int,
)

WALLET = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
CONTRACT = "0xcccccccccccccccccccccccccccccccccccccccc"
TOKEN = "0x3600000000000000000000000000000000000000"

BLOCK_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _wallet_map(*addrs: str) -> dict[str, uuid.UUID]:
    return {addr: uuid.uuid4() for addr in addrs}


def _tx(
    *,
    sender: str = WALLET,
    to: str | None = OTHER,
    input_hex: str = "0xa9059cbb" + "00" * 100,
    **overrides: Any,
) -> dict[str, Any]:
    tx: dict[str, Any] = {
        "hash": "0x" + "11" * 32,
        "from": sender,
        "to": to,
        "input": input_hex,
    }
    tx.update(overrides)
    return tx


def _receipt(**overrides: Any) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "status": "0x1",
        "gasUsed": "0x5208",  # 21_000
        "effectiveGasPrice": "0x3b9aca00",  # 10**9 wei
    }
    receipt.update(overrides)
    return receipt


def _topic(address: str) -> str:
    return "0x" + address[2:].rjust(64, "0")


def _word(value: int) -> str:
    return "0x" + format(value, "064x")


# ---------------------------------------------------------------------------
# ParsedTx construction
# ---------------------------------------------------------------------------
class TestParseTx:
    def test_outbound_from_wallet_with_gas(self) -> None:
        wallet_map = _wallet_map(WALLET)
        parsed = _parse_tx(_tx(), 101, BLOCK_TS, wallet_map, _receipt())
        assert isinstance(parsed, ParsedTx)
        assert parsed.tx_hash == "0x" + "11" * 32
        assert parsed.block_number == 101
        assert parsed.block_time == BLOCK_TS
        assert parsed.direction == "outbound"
        assert parsed.status == "success"
        assert parsed.agent_id == wallet_map[WALLET]
        assert parsed.from_address == WALLET
        assert parsed.to_address == OTHER
        assert parsed.method_selector == "0xa9059cbb"
        assert parsed.gas_used == Decimal(21_000)
        assert parsed.gas_fee_wei == Decimal("21000000000000")  # 21k * 1 gwei
        assert parsed.gas_fee_usdc == Decimal("0.000021")  # 6-dp human readable

    def test_inbound_to_wallet(self) -> None:
        wallet_map = _wallet_map(WALLET)
        parsed = _parse_tx(
            _tx(sender=OTHER, to=WALLET, input_hex="0x"), 101, BLOCK_TS, wallet_map, _receipt()
        )
        assert parsed.direction == "inbound"
        assert parsed.agent_id == wallet_map[WALLET]
        assert parsed.to_address == WALLET

    def test_failed_receipt_status(self) -> None:
        parsed = _parse_tx(_tx(), 101, BLOCK_TS, _wallet_map(WALLET), _receipt(status="0x0"))
        assert parsed.status == "failed"

    def test_no_receipt_refuses_to_invent_success(self) -> None:
        with pytest.raises(RpcError, match="missing transaction receipt"):
            _parse_tx(_tx(), 101, BLOCK_TS, _wallet_map(WALLET), None)

    def test_to_address_none_for_contract_creation(self) -> None:
        parsed = _parse_tx(_tx(to=None), 101, BLOCK_TS, _wallet_map(WALLET), _receipt())
        assert parsed.to_address is None
        assert parsed.raw_input_head is not None  # creation input is still stored

    def test_to_row_shape(self) -> None:
        parsed = _parse_tx(_tx(), 101, BLOCK_TS, _wallet_map(WALLET), _receipt())
        row = parsed.to_row()
        assert row["tx_hash"] == parsed.tx_hash
        assert row["direction"] == "outbound"
        assert row["gas_fee_usdc"] == Decimal("0.000021")

    def test_parsed_tx_is_frozen(self) -> None:
        parsed = _parse_tx(_tx(), 101, BLOCK_TS, _wallet_map(WALLET), _receipt())
        with pytest.raises(FrozenInstanceError):
            parsed.tx_hash = "0x" + "ff" * 32  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Gas fee computation (wei-level exact Decimal math, 6-dp ROUND_DOWN)
# ---------------------------------------------------------------------------
class TestComputeGas:
    def test_exact_full_quantum(self) -> None:
        # fee = 1_000_000 * 1_000_000 wei = 10**12 wei = exactly 0.000001 ETH
        gas_used, fee_wei, fee_usdc = _compute_gas(
            _receipt(gasUsed="0xf4240", effectiveGasPrice="0xf4240"), _tx()
        )
        assert gas_used == Decimal(1_000_000)
        assert fee_wei == Decimal("1000000000000")
        assert fee_usdc == Decimal("0.000001")

    def test_sub_quantum_fee_truncates_to_zero(self) -> None:
        # fee = 500_000 * 1_000_000 wei = 5 * 10**11 = 0.0000005 ETH
        # → the 6th decimal place cannot represent it: ROUND_DOWN → 0
        _, _, fee_usdc = _compute_gas(
            _receipt(gasUsed="0x7a120", effectiveGasPrice="0xf4240"), _tx()
        )
        assert fee_usdc == Decimal("0.000000")

    def test_rounds_down_never_rounds_half_up(self) -> None:
        # fee = 0.0000015 ETH → half-up would be 0.000002, ROUND_DOWN → 0.000001
        _, _, fee_usdc = _compute_gas(
            _receipt(gasUsed="0x16e360", effectiveGasPrice="0xf4240"), _tx()
        )
        assert fee_usdc == Decimal("0.000001")

    def test_prefers_effective_gas_price(self) -> None:
        # tx.gasPrice must be ignored when the receipt carries effectiveGasPrice
        _, _, fee_usdc = _compute_gas(
            _receipt(gasUsed="0xf4240", effectiveGasPrice="0xf4240"),
            _tx(gasPrice="0x1"),
        )
        assert fee_usdc == Decimal("0.000001")

    def test_falls_back_to_tx_gas_price(self) -> None:
        receipt = {"status": "0x1", "gasUsed": "0xf4240"}  # no effectiveGasPrice
        _, _, fee_usdc = _compute_gas(receipt, _tx(gasPrice="0xf4240"))
        assert fee_usdc == Decimal("0.000001")

    def test_missing_receipt_yields_no_fee(self) -> None:
        assert _compute_gas(None, _tx()) == (None, None, None)

    def test_fee_beyond_numeric_38_is_dropped(self) -> None:
        # fee = 10**36 * 10**10 = 10**46 > NUMERIC(38,0) → store no fee at all
        gas_used, fee_wei, fee_usdc = _compute_gas(
            _receipt(gasUsed=f"{10**36:x}", effectiveGasPrice=f"{10**10:x}"), _tx()
        )
        assert (gas_used, fee_wei, fee_usdc) == (None, None, None)


# ---------------------------------------------------------------------------
# raw_input_head truncation + method selector
# ---------------------------------------------------------------------------
class TestRawInputHead:
    def test_truncates_to_200_chars(self) -> None:
        long_input = "0x" + "ab" * 2000
        head = _raw_input_head(long_input)
        assert head is not None
        assert len(head) == 200
        assert head == long_input[:200].lower()

    def test_lowercases(self) -> None:
        assert _raw_input_head("0xABCDEF1234") == "0xabcdef1234"

    def test_exactly_200_chars_preserved(self) -> None:
        exactly = "0x" + "cd" * 99  # 2 + 198 == 200
        assert _raw_input_head(exactly) == exactly

    def test_plain_eoa_transfer_is_none(self) -> None:
        assert _raw_input_head("0x") is None
        assert _raw_input_head(None) is None
        assert _raw_input_head("") is None


class TestMethodSelector:
    def test_first_four_bytes_lowercased(self) -> None:
        assert _method_selector("0xA9059CBbA9059CBbA9059CBb") == "0xa9059cbb"

    def test_short_input_returns_none(self) -> None:
        assert _method_selector("0x1234") is None

    def test_minimum_selector_is_ten_chars(self) -> None:
        assert _method_selector("0xdeadbeef") == "0xdeadbeef"

    def test_empty_returns_none(self) -> None:
        assert _method_selector(None) is None
        assert _method_selector("0x") is None


# ---------------------------------------------------------------------------
# Transfer log decoding
# ---------------------------------------------------------------------------
class TestDecodeLog:
    def test_decodes_usdc_transfer(self) -> None:
        log = {
            "address": TOKEN,
            "topics": [TRANSFER_EVENT_SIG, _topic(WALLET), _topic(OTHER)],
            "data": _word(1_000_000),
            "transactionHash": "0x" + "11" * 32,
            "logIndex": "0x3",
        }
        parsed = _decode_log(log, TOKEN)
        assert isinstance(parsed, ParsedTransfer)
        assert parsed.tx_hash == "0x" + "11" * 32
        assert parsed.log_index == 3
        assert parsed.token_address == TOKEN
        assert parsed.from_address == WALLET
        assert parsed.to_address == OTHER
        assert parsed.amount_raw == Decimal(1_000_000)
        assert parsed.amount_usdc == Decimal("1.000000")

    def test_non_usdc_token_returns_none(self) -> None:
        log = {
            "address": OTHER,
            "topics": [TRANSFER_EVENT_SIG, _topic(WALLET), _topic(OTHER)],
            "data": _word(1),
            "transactionHash": "0x" + "11" * 32,
            "logIndex": "0x0",
        }
        assert _decode_log(log, TOKEN) is None

    def test_undecodable_topics_raise_decode_error(self) -> None:
        log = {
            "address": TOKEN,
            "topics": ["0x" + "ab" * 32, _topic(WALLET), _topic(OTHER)],
            "data": _word(1),
            "transactionHash": "0x" + "11" * 32,
            "logIndex": "0x0",
        }
        with pytest.raises(DecodeError):
            _decode_log(log, TOKEN)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
class TestHelpers:
    def test_tx_touches_wallet_from(self) -> None:
        assert _tx_touches_wallet(_tx(sender=WALLET), _wallet_map(WALLET)) is True

    def test_tx_touches_wallet_to(self) -> None:
        assert _tx_touches_wallet(_tx(sender=OTHER, to=WALLET), _wallet_map(WALLET)) is True

    def test_tx_unrelated_to_wallet(self) -> None:
        assert _tx_touches_wallet(_tx(sender=OTHER, to=CONTRACT), _wallet_map(WALLET)) is False

    def test_topic_address_left_pads_to_32_bytes(self) -> None:
        assert _topic_address(WALLET) == "0x" + WALLET[2:].rjust(64, "0")

    def test_hex_to_int_variants(self) -> None:
        assert hex_to_int("0x2a") == 42
        assert hex_to_int(42) == 42
        assert hex_to_int(None) is None

    def test_block_time_from_timestamp_and_default(self) -> None:
        assert _block_time(1_750_000_000) == datetime.fromtimestamp(1_750_000_000, tz=UTC)
        assert _block_time(None).tzinfo is UTC

    def test_status_from_receipt(self) -> None:
        with pytest.raises(RpcError):
            _status_from_receipt(None)
        assert _status_from_receipt({"status": "0x1"}) == "success"
        assert _status_from_receipt({"status": 1}) == "success"
        assert _status_from_receipt({"status": "0x0"}) == "failed"
        with pytest.raises(RpcError):
            _status_from_receipt({})


# ---------------------------------------------------------------------------
# Regression: a relayer tx carrying N Transfer logs is parsed exactly once
# ---------------------------------------------------------------------------
class _StubRpc:
    def __init__(self, *, block, txs, receipts, logs):
        self._block = block
        self._txs = txs
        self._receipts = receipts
        self._logs = logs

    async def eth_get_block_by_number(self, number, *, full_transactions=True):
        return self._block

    async def eth_get_transaction_receipt(self, tx_hash):
        return self._receipts.get(tx_hash)

    async def eth_get_transaction_by_hash(self, tx_hash):
        return self._txs.get(tx_hash)

    async def eth_get_logs(self, **kwargs):
        return self._logs


class TestIndexRangeMissingTxDedup:
    @pytest.mark.parametrize("mixed_case", [False, True])
    async def test_two_logs_for_one_tx_are_parsed_once(self, mixed_case: bool) -> None:
        tx_hash = "0x" + "ab" * 32
        wire_hash = "0x" + "AB" * 32 if mixed_case else tx_hash
        # Relay-mediated: block scan finds no wallet tx, only Transfer logs do.
        tx = {"hash": wire_hash, "from": OTHER, "to": TOKEN, "input": "0x", "blockNumber": "0x64",
              "blockHash": "0x" + "64" * 32}
        block = {
            "number": "0x64",
            "timestamp": "0x655e3c00",
            "hash": "0x" + "64" * 32, "parentHash": "0x" + "63" * 32,
            "transactions": [tx],  # present, but neither tx side touches the wallet
        }

        def log(to_addr: str, index: int) -> dict[str, Any]:
            return {
                "address": TOKEN,
                "topics": [TRANSFER_EVENT_SIG, _topic(OTHER), _topic(to_addr)],
                "data": _word(1000),
                "transactionHash": wire_hash,
                "logIndex": hex(index), "blockNumber": "0x64", "blockHash": block["hash"],
            }

        logs = [log(WALLET, 0), log(WALLET, 1)]  # two transfers, same tx
        rpc = _StubRpc(
            block=block,
            txs={tx_hash: tx},
            receipts={tx_hash: dict(_receipt(), transactionHash=tx_hash,
                                    blockNumber="0x64", blockHash=block["hash"])},
            logs=logs,
        )
        worker = IndexerWorker(
            session_factory=sessionmaker(), rpc=rpc, chain_id=1, usdc_address=TOKEN
        )
        wallet_map = _wallet_map(WALLET)

        parsed, transfers = await worker._index_range(
            from_block=100, to_block=100, wallet_map=wallet_map
        )

        assert len(transfers) == 2
        assert len(parsed) == 1, "two Transfer logs of one tx must not duplicate the row"
        assert parsed[0].tx_hash == tx_hash
        assert parsed[0].direction == "inbound"
        # Regression (SPEC §4): the agent is the log-side receiver; the relayer
        # submitted the tx, so tx.from is NOT the agent. agent_id must still be
        # resolved from the Transfer log, not from tx.from/tx.to.
        assert parsed[0].agent_id is not None
        assert parsed[0].agent_id == wallet_map[WALLET]

    async def test_relayer_outbound_log_resolves_agent_and_direction(self) -> None:
        tx_hash = "0x" + "98" * 32
        # Agent's sends are submitted by a relayer: tx.from=OTHER, tx.to=TOKEN;
        # the USDC log says from=WALLET(agent) -> OTHER. Must be indexed as
        # outbound with the agent attached (this is how budget rules fire).
        tx = {"hash": tx_hash, "from": OTHER, "to": TOKEN, "input": "0x", "blockNumber": "0x64",
              "blockHash": "0x" + "64" * 32}
        block = {
            "number": "0x64",
            "timestamp": "0x655e3c00",
            "hash": "0x" + "64" * 32, "parentHash": "0x" + "63" * 32,
            "transactions": [tx],
        }
        log = {
            "address": TOKEN,
            "topics": [TRANSFER_EVENT_SIG, _topic(WALLET), _topic(OTHER)],
            "data": _word(500),
            "transactionHash": tx_hash,
            "logIndex": "0x0", "blockNumber": "0x64", "blockHash": block["hash"],
        }
        rpc = _StubRpc(
            block=block,
            txs={tx_hash: tx},
            receipts={tx_hash: dict(_receipt(), transactionHash=tx_hash,
                                    blockNumber="0x64", blockHash=block["hash"])},
            logs=[log],
        )
        worker = IndexerWorker(
            session_factory=sessionmaker(), rpc=rpc, chain_id=1, usdc_address=TOKEN
        )
        wallet_map = _wallet_map(WALLET)

        parsed, transfers = await worker._index_range(
            from_block=100, to_block=100, wallet_map=wallet_map
        )

        assert len(parsed) == 1
        assert parsed[0].tx_hash == tx_hash
        assert parsed[0].direction == "outbound"
        assert parsed[0].agent_id == wallet_map[WALLET]
        # tx-level sides still reflect the on-chain submitter, not the USDC sides
        assert parsed[0].from_address == OTHER
        assert parsed[0].to_address == TOKEN


@pytest.mark.parametrize("change", [
    {"logIndex": None}, {"logIndex": "-0x1"},
    {"transactionHash": ""}, {"removed": True},
])
def test_transfer_identity_must_be_present_and_not_removed(change):
    log = {
        "address": TOKEN, "transactionHash": "0x" + "11" * 32, "logIndex": "0x0",
        "topics": [TRANSFER_EVENT_SIG, _topic(WALLET), _topic(OTHER)], "data": _word(1),
    }
    log.update(change)
    with pytest.raises(DecodeError):
        _decode_log(log, TOKEN)


@pytest.mark.parametrize("block", [
    None, {}, {"number": "0x65", "timestamp": "0x1", "transactions": []},
    {"number": "0x64", "timestamp": "0x1"},
    {"number": "0x64", "transactions": []},
])
async def test_missing_or_wrong_block_cannot_be_treated_as_empty(block):
    rpc = _StubRpc(block=block, txs={}, receipts={}, logs=[])
    worker = IndexerWorker(
        session_factory=sessionmaker(), rpc=rpc, chain_id=1, usdc_address=TOKEN
    )
    with pytest.raises(RpcError, match="block 100"):
        await worker._fetch_blocks(100, 100)


@pytest.mark.parametrize("change", [
    {"number": True}, {"number": "invalid"},
    {"hash": None}, {"hash": "0x1234"}, {"parentHash": "0xgg"},
    {"timestamp": -1}, {"timestamp": True}, {"timestamp": "0x" + "f" * 64},
])
async def test_malformed_header_refuses_batch(change):
    block = {
        "number": "0x64", "hash": "0x" + "01" * 32,
        "parentHash": "0x" + "02" * 32, "timestamp": "0x1", "transactions": [],
    }
    block.update(change)
    worker = IndexerWorker(
        session_factory=sessionmaker(),
        rpc=_StubRpc(block=block, txs={}, receipts={}, logs=[]),
        chain_id=1, usdc_address=TOKEN,
    )
    with pytest.raises(RpcError, match="block 100"):
        await worker._fetch_blocks(100, 100)
