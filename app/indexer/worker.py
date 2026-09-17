"""Indexer Worker — main polling loop (SPEC §4, Phase 2 / §7).

Design decisions (mirroring the spec):

* **HTTP polling, not WebSocket** — every 2–3 s the worker asks for the head,
  then indexes only up to ``head - confirmations`` (head-2 safety buffer).
* **Only registered wallets** — a transaction is indexed iff ``from`` or ``to``
  matches a wallet of an ``is_active`` agent in ``agents``.
* **Transactions + USDC Transfer logs** — regular blocks (full txs) are scanned
  for wallet-related transactions; ``eth_getLogs`` (USDC contract + Transfer
  topic + agent wallets) feeds ``token_transfers``. A transfer whose transaction
  was not already picked up by block scan (e.g. a relayer-mediated internal
  receive) is fetched by hash and indexed as well.
* **Idempotent writes** — ``transactions.tx_hash`` (PK) and
  ``token_transfers (tx_hash, log_index)`` (UNIQUE) power PostgreSQL
  ``ON CONFLICT DO NOTHING`` upserts, so resumes never duplicate rows.
* **Durable breakpoint** — ``indexer_state.last_indexed_block`` advances in the
  same transaction as the rows; a SIGTERM/SIGINT between cycles loses nothing
  because each cycle is already committed.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Agent, IndexerState, TokenTransfer, Transaction
from app.indexer.decode import (
    NUMERIC_38_MAX,
    TRANSFER_EVENT_SIG,
    DecodeError,
    decode_transfer_log,
    raw_6dp_to_usdc,
    wei_to_eth,
)
from app.indexer.rpc_client import RpcClient, RpcError, RpcProviderDown
from app.indexer.state import get_last_indexed_block, set_last_indexed_block

logger = logging.getLogger(__name__)

RAW_INPUT_HEAD_MAX = 200  # SPEC §7: store at most the first 200 chars of input
USDC_6DP = Decimal("0.000001")  # NUMERIC(20,6) quantum
BLOCK_FETCH_CONCURRENCY = 8

Direction = Literal["outbound", "inbound"]
Status = Literal["success", "failed"]


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
    last_indexed_block: int


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
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


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
        return "success"
    status = receipt.get("status")
    if status in ("0x1", "1", 1):
        return "success"
    return "failed"


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
        tx_hash=str(tx["hash"]),
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
    decoded = decode_transfer_log(log["topics"], str(log.get("data") or ""))
    return ParsedTransfer(
        tx_hash=str(log["transactionHash"]),
        log_index=hex_to_int(log.get("logIndex")) or 0,
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
    ) -> None:
        self._factory = session_factory
        self._rpc = rpc
        self._chain_id = chain_id
        self._usdc_address = usdc_address
        self._confirmations = confirmations
        self._poll_interval_seconds = poll_interval_seconds
        self._logs_max_range_size = logs_max_range_size

    @classmethod
    def from_settings(
        cls, settings: Any, rpc: RpcClient | None = None
    ) -> "IndexerWorker":
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
            except RpcProviderDown as exc:
                # The recognizable hook for the internal_rpc_down alert (§13).
                logger.error("internal_rpc_down: %s", exc)
            except RpcError as exc:
                logger.warning("rpc error during poll: %s", exc)
            except Exception:
                logger.exception("unexpected indexer error in poll cycle")

            elapsed = time.monotonic() - cycle_started
            remaining = max(0.0, self._poll_interval_seconds - elapsed)
            try:
                await asyncio.wait_for(stop.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------
    # One poll cycle
    # ------------------------------------------------------------------
    async def poll_once(self) -> PollResult:
        """Fetch head, index ``[last+1 .. head-confirmations]``, persist state."""
        head = await self._rpc.eth_block_number()
        safe_head = max(0, head - self._confirmations)

        with self._factory() as session:
            last = get_last_indexed_block(session, self._chain_id)

            if last is None:  # first run: start at the current safe head
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

            from_block = last + 1
            if from_block > safe_head:  # already up to date
                return PollResult(
                    chain_id=self._chain_id,
                    head=head,
                    safe_head=safe_head,
                    from_block=from_block,
                    to_block=None,
                    transactions_inserted=0,
                    transfers_inserted=0,
                    last_indexed_block=last,
                )

            agents = self._load_active_agents(session)
            wallet_map = {
                agent.wallet_address.lower(): agent.id for agent in agents
            }

            tx_rows: list[dict[str, Any]] = []
            transfer_rows: list[dict[str, Any]] = []
            if wallet_map:
                parsed_txs, parsed_transfers, skipped = await self._index_range(
                    from_block=from_block,
                    to_block=safe_head,
                    wallet_map=wallet_map,
                )
                if skipped:
                    logger.warning("skipped %d undecodable Transfer logs", skipped)
                tx_rows = [tx.to_row() for tx in parsed_txs]
                transfer_rows = [t.to_row() for t in parsed_transfers]

            transactions_inserted = 0
            transfers_inserted = 0
            if tx_rows:
                res = session.execute(build_transaction_upsert(tx_rows))
                transactions_inserted = max(0, res.rowcount or 0)
            if transfer_rows:
                res = session.execute(build_transfer_upsert(transfer_rows))
                transfers_inserted = max(0, res.rowcount or 0)

            set_last_indexed_block(session, self._chain_id, safe_head)
            session.commit()

            return PollResult(
                chain_id=self._chain_id,
                head=head,
                safe_head=safe_head,
                from_block=from_block,
                to_block=safe_head,
                transactions_inserted=transactions_inserted,
                transfers_inserted=transfers_inserted,
                last_indexed_block=safe_head,
            )

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
    ) -> tuple[list[ParsedTx], list[ParsedTransfer], int]:
        """Scan a block range + logs; return parsed rows (no DB writes)."""
        blocks = await self._fetch_blocks(from_block, to_block)

        candidates: list[tuple[dict[str, Any], int, datetime]] = []
        for block in blocks:
            block_number = hex_to_int(block.get("number"))
            block_time = _block_time(hex_to_int(block.get("timestamp")))
            for tx in block.get("transactions") or []:
                if _tx_touches_wallet(tx, wallet_map):
                    candidates.append((tx, block_number, block_time))

        receipts = await self._fetch_receipts([tx["hash"] for tx, _, _ in candidates])
        parsed = [
            _parse_tx(tx, block_number, block_time, wallet_map, receipts.get(tx["hash"]))
            for tx, block_number, block_time in candidates
        ]

        logs = await self._fetch_transfers(from_block, to_block, wallet_map)
        parsed_hashes = {tx.tx_hash for tx in parsed}
        transfers: list[ParsedTransfer] = []
        skipped = 0
        for log in logs:
            try:
                transfer = _decode_log(log, self._usdc_address)
            except DecodeError as exc:
                skipped += 1
                logger.warning("undecodable Transfer log %s: %s", log.get("transactionHash"), exc)
                continue
            if transfer is None:
                continue
            transfers.append(transfer)
            if transfer.tx_hash not in parsed_hashes:
                # e.g. relayer-mediated internal receive: the block scan missed it.
                parsed.append(await self._parse_missing_tx(transfer.tx_hash, wallet_map))

        return parsed, transfers, skipped

    async def _fetch_blocks(
        self, from_block: int, to_block: int
    ) -> list[dict[str, Any]]:
        sem = asyncio.Semaphore(BLOCK_FETCH_CONCURRENCY)

        async def fetch(number: int) -> dict[str, Any]:
            async with sem:
                block = await self._rpc.eth_get_block_by_number(
                    number, full_transactions=True
                )
                return block or {}

        results = await asyncio.gather(
            *(fetch(n) for n in range(from_block, to_block + 1))
        )
        return sorted(results, key=lambda b: hex_to_int(b.get("number")) or 0)

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
        """USDC Transfer logs touching any agent wallet, deduplicated."""
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
        seen: set[tuple[str, Any]] = set()
        unique: list[dict[str, Any]] = []
        for chunk in chunks:
            for log in chunk:
                key = (log.get("transactionHash"), log.get("logIndex"))
                if key in seen:
                    continue
                seen.add(key)
                unique.append(log)
        return unique

    async def _parse_missing_tx(
        self, tx_hash: str, wallet_map: dict[str, uuid.UUID]
    ) -> ParsedTx:
        """Index a transaction referenced by a Transfer log but missed by scan."""
        tx = await self._rpc.eth_get_transaction_by_hash(tx_hash)
        if tx is None:
            raise RpcError(f"transaction {tx_hash} referenced by a log not found")
        block_number = hex_to_int(tx.get("blockNumber"))
        if block_number is None:
            raise RpcError(f"transaction {tx_hash} is pending; cannot index")
        block = await self._rpc.eth_get_block_by_number(
            block_number, full_transactions=False
        )
        block_time = _block_time(
            hex_to_int(block.get("timestamp")) if block else None
        )
        receipt = await self._rpc.eth_get_transaction_receipt(tx_hash)
        return _parse_tx(tx, block_number, block_time, wallet_map, receipt)


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