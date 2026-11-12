"""Indexer worker; current contracts and tradeoffs live in this module's README.

Design decisions:

* **HTTP polling, not WebSocket** — every 2–3 s the worker asks for the head,
  then indexes only up to ``head - confirmations`` (head-2 safety buffer).
* **Only registered wallets** — a transaction is indexed iff ``from`` or ``to``
  matches a wallet of an ``is_active`` agent in ``agents``.
* **Transactions + USDC Transfer logs** — regular blocks (full txs) are scanned
  for wallet-related transactions; ``eth_getLogs`` (USDC contract + Transfer
  topic + agent wallets) feeds ``token_transfers``. A transfer whose transaction
  was not already picked up by wallet matching (e.g. a relayer-mediated
  receive) is resolved from the same full block and indexed as well.
* **Idempotent writes** — ``transactions.tx_hash`` (PK) and
  ``token_transfers (tx_hash, log_index)`` (UNIQUE) power PostgreSQL
  ``ON CONFLICT DO NOTHING`` upserts, so resumes never duplicate rows.
* **Durable breakpoint** — block lineage and ``indexer_state.last_indexed_block``
  advance in the same transaction as the rows; a SIGTERM/SIGINT between cycles loses nothing
  because each cycle is already committed.
* **Batched catch-up (SPEC §20.7)** — a cycle indexes at most
  ``max_blocks_per_cycle`` blocks, so a large backlog is drained gradually
  instead of exhausting a free-tier provider's rate-limit budget in one go.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any, Literal, NoReturn

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Agent, IndexedBlock, IndexerState, TokenTransfer, Transaction
from app.indexer.decode import (
    NUMERIC_38_MAX,
    TRANSFER_EVENT_SIG,
    DecodeError,
    decode_transfer_log,
    raw_6dp_to_usdc,
    wei_to_eth,
)
from app.indexer.lineage import BlockHeader, hash_value, quantity
from app.indexer.rpc_client import RpcClient, RpcError, RpcProviderDown
from app.indexer.state import get_last_indexed_block, set_last_indexed_block, try_indexer_lock

logger = logging.getLogger(__name__)

RAW_INPUT_HEAD_MAX = 200  # SPEC §7: store at most the first 200 chars of input
USDC_6DP = Decimal("0.000001")  # NUMERIC(20,6) quantum
BLOCK_FETCH_CONCURRENCY = 8

Direction = Literal["outbound", "inbound"]
Status = Literal["success", "failed"]


class IndexerBusyError(RuntimeError):
    """Another worker owns this chain's indexing transaction."""


# ---------------------------------------------------------------------------
# Parsed value objects (pure, unit-testable)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ParsedTx:
    """A wallet-related transaction ready to be persisted in ``transactions``."""

    tx_hash: str
    block_number: int
    block_time: datetime
    from_address: str
    to_address: str | None
    agent_id: uuid.UUID | None
    direction: Direction
    status: Status
    method_selector: str | None
    gas_used: Decimal | None
    gas_fee_wei: Decimal | None
    gas_fee_usdc: Decimal | None
    raw_input_head: str | None

    def to_row(self) -> dict[str, Any]:
        return {
            "tx_hash": self.tx_hash,
            "block_number": self.block_number,
            "block_time": self.block_time,
            "from_address": self.from_address,
            "to_address": self.to_address,
            "agent_id": self.agent_id,
            "direction": self.direction,
            "status": self.status,
            "method_selector": self.method_selector,
            "gas_used": self.gas_used,
            "gas_fee_wei": self.gas_fee_wei,
            "gas_fee_usdc": self.gas_fee_usdc,
            "raw_input_head": self.raw_input_head,
        }


@dataclass(frozen=True, slots=True)
class ParsedTransfer:
    """A decoded USDC Transfer log ready for ``token_transfers``."""

    tx_hash: str
    log_index: int
    token_address: str
    from_address: str
    to_address: str
    amount_raw: Decimal
    amount_usdc: Decimal

    def to_row(self) -> dict[str, Any]:
        return {
            "tx_hash": self.tx_hash,
            "log_index": self.log_index,
            "token_address": self.token_address,
            "from_address": self.from_address,
            "to_address": self.to_address,
            "amount_raw": self.amount_raw,
            "amount_usdc": self.amount_usdc,
        }


@dataclass(frozen=True, slots=True)
class PollResult:
    """Outcome of a single poll cycle."""

    chain_id: int
    head: int
    safe_head: int
    from_block: int | None  # None ⇒ nothing new (or first-run initialisation)
    to_block: int | None
    transactions_inserted: int
    transfers_inserted: int
    last_indexed_block: int | None


# ---------------------------------------------------------------------------
# Pure parsing / conversion helpers
# ---------------------------------------------------------------------------
def hex_to_int(value: Any) -> int | None:
    """Convert a JSON-RPC hex quantity (or int) to an int."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return int(str(value), 16)


def _block_time(timestamp: int | None) -> datetime:
    if timestamp is None:
        return datetime.now(UTC)
    return datetime.fromtimestamp(timestamp, tz=UTC)


def _topic_address(address: str) -> str:
    """Left-pad a 20-byte address to a 32-byte indexed-topic value."""
    return "0x" + address[2:].rjust(64, "0")


def _tx_touches_wallet(tx: dict[str, Any], wallet_map: dict[str, uuid.UUID]) -> bool:
    addrs = {str(tx.get("from") or "").lower()}
    if tx.get("to"):
        addrs.add(str(tx["to"]).lower())
    return bool(addrs & wallet_map.keys())


def _method_selector(input_hex: str | None) -> str | None:
    """First 4 bytes (8 hex chars + 0x) of call data, lowercased."""
    if not input_hex or len(input_hex) < 10:
        return None
    return input_hex[:10].lower()


def _raw_input_head(input_hex: str | None) -> str | None:
    """First 200 chars of raw input (None for plain EOA transfers)."""
    if not input_hex or input_hex == "0x":
        return None
    return input_hex[:RAW_INPUT_HEAD_MAX].lower()


def _hex_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    return Decimal(int(str(value), 16))


def _compute_gas(
    receipt: dict[str, Any] | None,
    tx: dict[str, Any],
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """Return ``(gas_used, gas_fee_wei, gas_fee_usdc)``.

    ``gas_fee_wei`` = ``gasUsed * effectiveGasPrice`` (exact Decimal integer
    math, never float). ``gas_fee_usdc`` converts the 18-dp native figure to a
    human-readable 6-dp USDC amount, truncating (ROUND_DOWN).
    """
    gas_used = _hex_decimal(receipt.get("gasUsed")) if receipt else None
    price: Decimal | None = None
    if receipt is not None and receipt.get("effectiveGasPrice") is not None:
        price = _hex_decimal(receipt["effectiveGasPrice"])
    elif tx.get("gasPrice") is not None:
        price = _hex_decimal(tx["gasPrice"])
    elif tx.get("maxFeePerGas") is not None:
        price = _hex_decimal(tx["maxFeePerGas"])

    if gas_used is None or price is None:
        return None, None, None
    fee_wei = gas_used * price
    if fee_wei > NUMERIC_38_MAX:
        logger.warning("gas fee %s exceeds NUMERIC(38,0); storing no fee", fee_wei)
        return None, None, None
    return (
        gas_used,
        fee_wei,
        wei_to_eth(fee_wei).quantize(USDC_6DP, rounding=ROUND_DOWN),
    )


def _status_from_receipt(receipt: dict[str, Any] | None) -> Status:
    if receipt is None:
        raise RpcError("missing transaction receipt; refusing to advance checkpoint")
    status = receipt.get("status")
    if status in ("0x1", "1", 1):
        return "success"
    if status in ("0x0", "0", 0):
        return "failed"
    raise RpcError("missing or invalid receipt status; refusing to advance checkpoint")


def _parse_tx(
    tx: dict[str, Any],
    block_number: int,
    block_time: datetime,
    wallet_map: dict[str, uuid.UUID],
    receipt: dict[str, Any] | None,
) -> ParsedTx:
    """Build a :class:`ParsedTx` from a full transaction + its receipt."""
    from_addr = str(tx.get("from") or "").lower()
    to_addr = str(tx.get("to") or "").lower() if tx.get("to") else None

    agent_id = wallet_map.get(from_addr)
    if agent_id is None and to_addr is not None:
        agent_id = wallet_map.get(to_addr)
    direction: Direction = "outbound" if from_addr in wallet_map else "inbound"

    gas_used, fee_wei, fee_usdc = _compute_gas(receipt, tx)
    input_hex = tx.get("input") or "0x"
    return ParsedTx(
        tx_hash=str(tx["hash"]).lower(),
        block_number=block_number,
        block_time=block_time,
        from_address=str(tx.get("from") or ""),
        to_address=to_addr,
        agent_id=agent_id,
        direction=direction,
        status=_status_from_receipt(receipt),
        method_selector=_method_selector(input_hex),
        gas_used=gas_used,
        gas_fee_wei=fee_wei,
        gas_fee_usdc=fee_usdc,
        raw_input_head=_raw_input_head(input_hex),
    )


def _decode_log(log: dict[str, Any], usdc_address: str) -> ParsedTransfer | None:
    """Decode one USDC Transfer log (None for non-USDC / non-Transfer events)."""
    token_address = str(log.get("address") or "").lower()
    if token_address != usdc_address.lower():
        return None
    if log.get("removed"):
        raise DecodeError("removed log cannot be committed as canonical")
    log_index = hex_to_int(log["logIndex"])
    if log_index is None or log_index < 0 or not log.get("transactionHash"):
        raise DecodeError("missing transaction hash or invalid log index")
    decoded = decode_transfer_log(log["topics"], str(log.get("data") or ""))
    return ParsedTransfer(
        tx_hash=str(log["transactionHash"]).lower(),
        log_index=log_index,
        token_address=token_address,
        from_address=decoded.from_address,
        to_address=decoded.to_address,
        amount_raw=decoded.amount_raw,
        amount_usdc=raw_6dp_to_usdc(decoded.amount_raw),
    )


# ---------------------------------------------------------------------------
# Idempotent PostgreSQL upsert builders (SPEC §15: UNIQUE-guaranteed)
# ---------------------------------------------------------------------------
def build_transaction_upsert(rows: list[dict[str, Any]]):
    """``INSERT ... ON CONFLICT (tx_hash) DO NOTHING`` for ``transactions``."""
    return (
        pg_insert(Transaction)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["tx_hash"])
    )


def build_transfer_upsert(rows: list[dict[str, Any]]):
    """``INSERT ... ON CONFLICT (tx_hash, log_index) DO NOTHING``."""
    return (
        pg_insert(TokenTransfer)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["tx_hash", "log_index"])
    )


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------
class IndexerWorker:
    """Poll the Arc RPC, index wallet-related activity, persist state."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        rpc: RpcClient,
        chain_id: int,
        usdc_address: str,
        confirmations: int = 2,
        poll_interval_seconds: float = 3.0,
        logs_max_range_size: int = 2000,
        max_blocks_per_cycle: int = 80,
        start_block: int | None = None,
        reorg_max_depth: int = 64,
    ) -> None:
        if not 1 <= chain_id <= 2**31 - 1:
            raise ValueError("chain_id must fit the positive PostgreSQL INTEGER range")
        if confirmations < 0 or (start_block is not None and start_block < 0):
            raise ValueError("confirmations and start_block must be nonnegative")
        if min(logs_max_range_size, max_blocks_per_cycle, poll_interval_seconds) <= 0:
            raise ValueError("batch sizes and poll interval must be positive")
        if not 1 <= reorg_max_depth <= 10000:
            raise ValueError("reorg_max_depth must be between 1 and 10000")
        self._factory = session_factory
        self._rpc = rpc
        self._chain_id = chain_id
        self._usdc_address = usdc_address
        self._confirmations = confirmations
        self._poll_interval_seconds = poll_interval_seconds
        self._logs_max_range_size = logs_max_range_size
        self._max_blocks_per_cycle = max_blocks_per_cycle
        self._start_block = start_block
        self._reorg_max_depth = reorg_max_depth

    @classmethod
    def from_settings(
        cls, settings: Any, rpc: RpcClient | None = None
    ) -> IndexerWorker:
        """Build a worker bound to the configured DB and RPC endpoint."""
        from app.db.session import get_session_factory

        return cls(
            session_factory=get_session_factory(),
            rpc=rpc or RpcClient.from_settings(settings),
            chain_id=settings.chain_id,
            usdc_address=settings.usdc_address,
            confirmations=settings.confirmations,
            poll_interval_seconds=settings.poll_interval_seconds,
            logs_max_range_size=settings.rpc_logs_max_range_size,
            max_blocks_per_cycle=settings.max_blocks_per_cycle,
            start_block=settings.start_block,
            reorg_max_depth=settings.reorg_max_depth,
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        """Poll until *stop_event* is set (SIGTERM/SIGINT => clean shutdown).

        Every cycle commits ``indexer_state`` together with the indexed rows,
        so nothing is lost between the last commit and a signal.
        """
        stop = stop_event or asyncio.Event()
        while not stop.is_set():
            cycle_started = time.monotonic()
            try:
                result = await self.poll_once()
                if result.to_block is not None:
                    logger.info(
                        "indexed blocks %s..%s (head %s): +%d txs, +%d transfers",
                        result.from_block,
                        result.to_block,
                        result.head,
                        result.transactions_inserted,
                        result.transfers_inserted,
                    )
                else:
                    logger.debug(
                        "up to date at block %s (head %s)", result.safe_head, result.head
                    )
            except IndexerBusyError:
                logger.info("indexer cycle deferred: another writer owns chain %s", self._chain_id)
            except RpcProviderDown as exc:
                # The recognizable hook for the internal_rpc_down alert (§13).
                logger.error("internal_rpc_down: %s", exc)
            except RpcError as exc:
                logger.warning("rpc error during poll: %s", exc)
            except Exception:
                logger.exception("unexpected indexer error in poll cycle")

            elapsed = time.monotonic() - cycle_started
            remaining = max(0.0, self._poll_interval_seconds - elapsed)
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=remaining)

    # ------------------------------------------------------------------
    # One poll cycle
    # ------------------------------------------------------------------
    async def poll_once(self) -> PollResult:
        """Fetch head, index ``[last+1 .. to_block]`` (batched catch-up), persist state.

        ``to_block`` is capped at ``max_blocks_per_cycle`` blocks per cycle
        (SPEC §20.7); a backlog larger than that is caught up over several
        cycles instead of hammering the provider in one backfill.
        """
        generation = self._rpc.provider_generation
        actual_chain_id = await self._rpc.eth_chain_id()
        if actual_chain_id != self._chain_id:
            raise RpcError(
                f"RPC chain mismatch: expected {self._chain_id}, received {actual_chain_id}"
            )
        head = await self._rpc.eth_block_number()
        safe_head = max(0, head - self._confirmations)

        with self._factory() as session:
            if not try_indexer_lock(session, self._chain_id):
                raise IndexerBusyError("another indexer owns this chain")
            last = get_last_indexed_block(session, self._chain_id)

            previous = None
            if last is not None:
                state = session.get(IndexerState, self._chain_id)
                if state.recovery_reason is not None:
                    raise RpcError(f"recovery required: {state.recovery_reason}")
                saved = session.get(IndexedBlock, (self._chain_id, last))
                if saved is None:
                    await self._validate_view(generation, None)
                    self._require_recovery(session, "legacy_checkpoint")
                previous = await self._read_header(last)
                if previous.hash != saved.block_hash:
                    reason, ancestor = await self._find_ancestor(session, previous)
                    await self._validate_view(generation, previous)
                    self._require_recovery(session, reason, ancestor)

            if last is None and self._start_block is None:
                anchor = await self._read_header(safe_head)
                await self._validate_view(generation, anchor)
                self._journal(session, [anchor], anchor=True)
                set_last_indexed_block(session, self._chain_id, safe_head)
                session.commit()
                return PollResult(
                    chain_id=self._chain_id,
                    head=head,
                    safe_head=safe_head,
                    from_block=None,
                    to_block=None,
                    transactions_inserted=0,
                    transfers_inserted=0,
                    last_indexed_block=safe_head,
                )

            checkpoint = last
            if last is None:
                # -1 is an in-memory sentinel, never persisted. START_BLOCK=0
                # includes genesis; a future start must not skip unseen blocks.
                last = self._start_block - 1
            from_block = last + 1
            if from_block > safe_head:  # already up to date
                await self._validate_view(generation, previous)
                return PollResult(
                    chain_id=self._chain_id,
                    head=head,
                    safe_head=safe_head,
                    from_block=from_block,
                    to_block=None,
                    transactions_inserted=0,
                    transfers_inserted=0,
                    last_indexed_block=checkpoint,
                )

            # Catch-up batching (SPEC §20.7): index at most
            # ``max_blocks_per_cycle`` blocks per cycle so a large backlog
            # never exhausts the provider's rate-limit budget in one giant
            # backfill. The remainder is picked up on subsequent cycles and
            # ``last_indexed_block`` advances only as far as ``to_block``.
            to_block = min(safe_head, from_block + self._max_blocks_per_cycle - 1)

            if previous is None and from_block > 0:
                previous = await self._read_header(from_block - 1)
            blocks = await self._fetch_blocks(from_block, to_block)
            headers = [BlockHeader.parse(block, from_block + i) for i, block in enumerate(blocks)]
            if previous is not None and headers[0].parent_hash != previous.hash:
                raise RpcError("recovery required: batch does not extend checkpoint")

            agents = self._load_active_agents(session)
            wallet_map = {
                agent.wallet_address.lower(): agent.id for agent in agents
            }

            tx_rows: list[dict[str, Any]] = []
            transfer_rows: list[dict[str, Any]] = []
            if wallet_map:
                parsed_txs, parsed_transfers = await self._index_range(
                    from_block=from_block,
                    to_block=to_block,
                    wallet_map=wallet_map,
                    blocks=blocks,
                )
                tx_rows = [tx.to_row() for tx in parsed_txs]
                transfer_rows = [t.to_row() for t in parsed_transfers]

            await self._validate_view(generation, headers[-1])
            if checkpoint is None and previous is not None:
                self._journal(session, [previous], anchor=True)
            self._journal(session, headers)

            transactions_inserted = 0
            transfers_inserted = 0
            if tx_rows:
                res = session.execute(
                    build_transaction_upsert(tx_rows).returning(Transaction.tx_hash)
                )
                transactions_inserted = len(res.all())
            if transfer_rows:
                res = session.execute(
                    build_transfer_upsert(transfer_rows).returning(TokenTransfer.id)
                )
                transfers_inserted = len(res.all())

            set_last_indexed_block(session, self._chain_id, to_block)
            session.commit()

            return PollResult(
                chain_id=self._chain_id,
                head=head,
                safe_head=safe_head,
                from_block=from_block,
                to_block=to_block,
                transactions_inserted=transactions_inserted,
                transfers_inserted=transfers_inserted,
                last_indexed_block=to_block,
            )

    async def _read_header(self, number: int) -> BlockHeader:
        block = await self._rpc.eth_get_block_by_number(number, full_transactions=False)
        return BlockHeader.parse(block, number)

    async def _find_ancestor(
        self, session: Session, tip: BlockHeader
    ) -> tuple[str, int | None]:
        """Walk a contiguous provider branch; never search past known provenance."""
        child = tip
        for depth in range(self._reorg_max_depth + 1):
            saved = session.get(IndexedBlock, (self._chain_id, child.number))
            if saved is None:
                return "journal_gap", None
            if saved.block_hash == child.hash:
                return "checkpoint_fork", child.number
            if saved.is_anchor or child.number == 0:
                return "history_boundary", None
            if depth == self._reorg_max_depth:
                break
            parent = await self._read_header(child.number - 1)
            if child.parent_hash != parent.hash:
                raise RpcError("inconsistent ancestor search; batch must be retried")
            child = parent
        return "rollback_window_exceeded", None

    def _require_recovery(
        self, session: Session, reason: str, ancestor: int | None = None
    ) -> NoReturn:
        """Latch only validated diagnostics, under the writer lock before any raw writes."""
        state = session.get(IndexerState, self._chain_id)
        state.recovery_reason = reason
        state.recovery_ancestor_block = ancestor
        state.recovery_detected_at = datetime.now(UTC)
        session.commit()
        detail = f"; common ancestor {ancestor}" if ancestor is not None else ""
        raise RpcError(f"recovery required: {reason}{detail}")

    async def _validate_view(self, generation: int, boundary: BlockHeader | None) -> None:
        if boundary is not None and await self._read_header(boundary.number) != boundary:
            raise RpcError("batch boundary changed; batch must be retried")
        chain_id = await self._rpc.eth_chain_id()
        if chain_id != self._chain_id or self._rpc.provider_generation != generation:
            raise RpcError("RPC provider/chain changed during batch; batch must be retried")

    def _journal(
        self, session: Session, headers: list[BlockHeader], *, anchor: bool = False
    ) -> None:
        provider = "fallback" if self._rpc.using_fallback else "primary"
        for header in headers:
            saved = session.get(IndexedBlock, (self._chain_id, header.number))
            if saved is not None and saved.block_hash != header.hash:
                raise RpcError("recovery required: journal conflicts with fetched history")
        session.execute(pg_insert(IndexedBlock).values([
            {"chain_id": self._chain_id, "block_number": header.number,
             "block_hash": header.hash, "parent_hash": header.parent_hash,
             "block_time": header.timestamp, "observed_provider": provider, "is_anchor": anchor}
            for header in headers
        ]).on_conflict_do_nothing(index_elements=["chain_id", "block_number"]))

    def _load_active_agents(self, session: Session) -> list[Agent]:
        stmt = select(Agent).where(Agent.is_active.is_(True))
        return list(session.scalars(stmt).all())

    # ------------------------------------------------------------------
    # Range processing
    # ------------------------------------------------------------------
    async def _index_range(
        self,
        *,
        from_block: int,
        to_block: int,
        wallet_map: dict[str, uuid.UUID],
        blocks: list[dict[str, Any]] | None = None,
    ) -> tuple[list[ParsedTx], list[ParsedTransfer]]:
        """Bind every transaction, receipt and log to the same fetched lineage."""
        if blocks is None:
            blocks = await self._fetch_blocks(from_block, to_block)
        headers = {from_block + i: BlockHeader.parse(block, from_block + i)
                   for i, block in enumerate(blocks)}
        transactions = {}
        candidates = []
        for block in blocks:
            header = headers[quantity(block["number"])]
            for tx in block["transactions"]:
                tx_hash = hash_value(tx.get("hash") if isinstance(tx, dict) else None)
                header.validate_member(tx, tx_hash)
                if tx_hash in transactions:
                    raise RpcError("duplicate transaction in fetched blocks")
                transactions[tx_hash] = (tx, header)
                if _tx_touches_wallet(tx, wallet_map):
                    candidates.append(tx_hash)

        logs = await self._fetch_transfers(from_block, to_block, wallet_map)
        transfers = []
        seen = {}
        for log in logs:
            if not isinstance(log, dict):
                raise RpcError("invalid log object")
            number = quantity(log.get("blockNumber"))
            tx_hash = hash_value(log.get("transactionHash"))
            header = headers.get(number)
            if (header is None or hash_value(log.get("blockHash")) != header.hash
                    or tx_hash not in transactions or transactions[tx_hash][1] != header):
                raise RpcError("log identity is outside the fetched block range")
            try:
                transfer = _decode_log(log, self._usdc_address)
            except (DecodeError, KeyError, ValueError, TypeError) as exc:
                raise RpcError("undecodable Transfer log; batch must be retried") from exc
            if transfer is None:
                continue
            key = (tx_hash, transfer.log_index)
            if key in seen:
                if seen[key] != log:
                    raise RpcError("conflicting duplicate Transfer log")
                continue
            seen[key] = log
            transfers.append(transfer)

        required = list(dict.fromkeys([*candidates, *(t.tx_hash for t in transfers)]))
        receipts = await self._fetch_receipts(required)
        for tx_hash in required:
            transactions[tx_hash][1].validate_member(receipts.get(tx_hash), tx_hash, receipt=True)
        parsed = [
            _parse_tx(transactions[h][0], transactions[h][1].number,
                      transactions[h][1].timestamp, wallet_map, receipts[h])
            for h in candidates
        ]
        parsed_hashes = set(candidates)
        for transfer in transfers:
            if _status_from_receipt(receipts[transfer.tx_hash]) != "success":
                raise RpcError("Transfer log belongs to a failed transaction")
            if transfer.tx_hash not in parsed_hashes:
                tx, header = transactions[transfer.tx_hash]
                parsed_tx = _parse_tx(tx, header.number, header.timestamp,
                                       wallet_map, receipts[transfer.tx_hash])
                sender = transfer.from_address.lower()
                recipient = transfer.to_address.lower()
                parsed.append(replace(
                    parsed_tx, agent_id=wallet_map.get(sender, wallet_map.get(recipient)),
                    direction="outbound" if sender in wallet_map else "inbound",
                ))
                parsed_hashes.add(transfer.tx_hash)
        return parsed, transfers

    async def _fetch_blocks(
        self, from_block: int, to_block: int
    ) -> list[dict[str, Any]]:
        sem = asyncio.Semaphore(BLOCK_FETCH_CONCURRENCY)

        async def fetch(number: int) -> dict[str, Any]:
            async with sem:
                block = await self._rpc.eth_get_block_by_number(
                    number, full_transactions=True
                )
                BlockHeader.parse(block, number)
                if not isinstance(block.get("transactions"), list):
                    raise RpcError(
                        f"incomplete or mismatched block {number}; batch must be retried"
                    )
                return block

        results = await asyncio.gather(
            *(fetch(n) for n in range(from_block, to_block + 1))
        )
        ordered = sorted(results, key=lambda b: quantity(b["number"]))
        for parent, child in zip(ordered, ordered[1:], strict=False):
            if hash_value(child["parentHash"]) != hash_value(parent["hash"]):
                raise RpcError("noncontiguous parent hashes; batch must be retried")
        return ordered

    async def _fetch_receipts(
        self, tx_hashes: list[str]
    ) -> dict[str, dict[str, Any]]:
        unique = list(dict.fromkeys(tx_hashes))
        if not unique:
            return {}
        sem = asyncio.Semaphore(BLOCK_FETCH_CONCURRENCY)

        async def fetch(tx_hash: str) -> tuple[str, dict[str, Any] | None]:
            async with sem:
                return tx_hash, await self._rpc.eth_get_transaction_receipt(tx_hash)

        pairs = await asyncio.gather(*(fetch(h) for h in unique))
        return {hash_: receipt for hash_, receipt in pairs if receipt is not None}

    async def _fetch_transfers(
        self,
        from_block: int,
        to_block: int,
        wallet_map: dict[str, uuid.UUID],
    ) -> list[dict[str, Any]]:
        """Fetch candidate logs; validate identities before deduplicating."""
        wallet_addrs = sorted(wallet_map)
        outbound_topics: list[list[str] | None] = [
            [TRANSFER_EVENT_SIG],
            [_topic_address(a) for a in wallet_addrs],
            None,
        ]
        inbound_topics: list[list[str] | None] = [
            [TRANSFER_EVENT_SIG],
            None,
            [_topic_address(a) for a in wallet_addrs],
        ]
        sem = asyncio.Semaphore(BLOCK_FETCH_CONCURRENCY)

        async def fetch_chunk(start: int) -> list[dict[str, Any]]:
            end = min(start + self._logs_max_range_size - 1, to_block)
            async with sem:
                outbound = await self._rpc.eth_get_logs(
                    from_block=start,
                    to_block=end,
                    addresses=[self._usdc_address],
                    topics=outbound_topics,
                )
                inbound = await self._rpc.eth_get_logs(
                    from_block=start,
                    to_block=end,
                    addresses=[self._usdc_address],
                    topics=inbound_topics,
                )
            return [*outbound, *inbound]

        chunks = await asyncio.gather(
            *(
                fetch_chunk(start)
                for start in range(from_block, to_block + 1, self._logs_max_range_size)
            )
        )
        # Validate every response before deduplicating; an inbound duplicate
        # must not conceal an outbound response from another fork.
        return [log for chunk in chunks for log in chunk]


# ---------------------------------------------------------------------------
# Entrypoint: python -m app.indexer.worker
# ---------------------------------------------------------------------------
def main() -> None:
    """Run the worker until SIGTERM/SIGINT, saving state every committed cycle."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    async def _run() -> None:
        settings = get_settings()
        rpc = RpcClient.from_settings(settings)
        worker = IndexerWorker.from_settings(settings, rpc=rpc)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        logger.info(
            "indexer worker starting (chain_id=%s rpc=%s confirmations=%d)",
            settings.chain_id,
            settings.rpc_url,
            settings.confirmations,
        )
        try:
            await worker.run_forever(stop)
        finally:
            await rpc.close()
        logger.info("indexer worker stopped cleanly; state is durable per cycle")

    asyncio.run(_run())


if __name__ == "__main__":
    main()
