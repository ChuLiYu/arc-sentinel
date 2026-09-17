"""Indexer package: RPC client, decode helpers, worker loop, and state.

Exports the public API of Phase 2 (Indexer core): the JSON-RPC client with
exponential backoff, the ``indexer_state`` accessors, and the polling worker.
"""

from app.indexer.decode import (
    DecodeError,
    InvalidHexError,
    TransferLog,
    TransferLogError,
    amount_overflow,
    decode_transfer_log,
    raw_6dp_to_usdc,
    wei_to_eth,
)
from app.indexer.rpc_client import (
    RpcClient,
    RpcError,
    RpcHttpError,
    RpcJsonRpcError,
    RpcProviderDown,
    RpcTimeoutError,
    RpcTransportError,
)
from app.indexer.state import get_last_indexed_block, set_last_indexed_block
from app.indexer.worker import (
    IndexerWorker,
    ParsedTransfer,
    ParsedTx,
    PollResult,
)

__all__ = [
    "DecodeError",
    "IndexerWorker",
    "InvalidHexError",
    "ParsedTransfer",
    "ParsedTx",
    "PollResult",
    "RpcClient",
    "RpcError",
    "RpcHttpError",
    "RpcJsonRpcError",
    "RpcProviderDown",
    "RpcTimeoutError",
    "RpcTransportError",
    "TransferLog",
    "TransferLogError",
    "amount_overflow",
    "decode_transfer_log",
    "get_last_indexed_block",
    "raw_6dp_to_usdc",
    "set_last_indexed_block",
    "wei_to_eth",
]
