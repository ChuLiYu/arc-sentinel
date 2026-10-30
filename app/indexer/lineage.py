"""Validated block identities at the untrusted JSON-RPC boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.indexer.rpc_client import RpcError


def hash_value(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
        raise RpcError("missing or invalid chain hash; batch must be retried")
    return value.lower()


def quantity(value: Any) -> int:
    try:
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValueError
        number = int(value, 16) if isinstance(value, str) else value
        if number < 0:
            raise ValueError
        return number
    except (ValueError, TypeError) as exc:
        raise RpcError("missing or invalid chain quantity; batch must be retried") from exc


@dataclass(frozen=True)
class BlockHeader:
    number: int
    hash: str
    parent_hash: str
    timestamp: datetime

    @classmethod
    def parse(cls, block: Any, expected_number: int) -> BlockHeader:
        try:
            if not isinstance(block, dict) or quantity(block.get("number")) != expected_number:
                raise ValueError
            return cls(
                number=expected_number,
                hash=hash_value(block.get("hash")),
                parent_hash=hash_value(block.get("parentHash")),
                timestamp=datetime.fromtimestamp(quantity(block.get("timestamp")), tz=UTC),
            )
        except (RpcError, ValueError, OverflowError, OSError) as exc:
            raise RpcError(f"incomplete or mismatched block {expected_number}") from exc

    def validate_member(self, item: Any, tx_hash: str, *, receipt: bool = False) -> None:
        if (
            not isinstance(item, dict)
            or quantity(item.get("blockNumber")) != self.number
            or hash_value(item.get("blockHash")) != self.hash
            or hash_value(item.get("transactionHash" if receipt else "hash")) != tx_hash
        ):
            raise RpcError("transaction/receipt identity disagrees with fetched block")
