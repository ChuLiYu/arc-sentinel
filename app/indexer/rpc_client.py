"""JSON-RPC client for the Arc RPC endpoint (SPEC §2 / §4 / §13).

Wraps ``httpx.AsyncClient`` with the three calls the Indexer Worker needs:

* ``eth_blockNumber``            — current chain head
* ``eth_getBlockByNumber``       — one block, full transactions
* ``eth_getLogs``                — filtered by address + Transfer topic + range

Hard requirements implemented here (SPEC §13):

* **Exponential backoff retries** — delays follow ``base → base*2 → base*4 → …``
  capped at ``backoff_max_seconds`` (1s → 2s → 4s → … → 30s by default).
* **Timeouts** — every request is bounded by ``timeout``.
* **Consecutive-failure counter** — persists across calls and raises
  :class:`RpcProviderDown` once the threshold is crossed so the worker can fire
  the internal ``internal_rpc_down`` alert instead of silently hanging.
* **Recognizable errors** — a small exception hierarchy so callers can classify
  transport / HTTP / JSON-RPC failures precisely.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_RETRIES = 5
DEFAULT_BACKOFF_BASE = 1.0
DEFAULT_BACKOFF_MAX = 30.0
DEFAULT_MAX_CONSECUTIVE_FAILURES = 5

# JSON-RPC error codes that indicate a provider-side hiccup worth retrying
# (generic server error range -32000..-32099 plus "internal error" -32603).
_RETRYABLE_JSONRPC_CODES = frozenset(
    {-32603, *(code for code in range(-32099, -32000 + 1))}
)
_RETRYABLE_HTTP_CODES = frozenset({429, 500, 502, 503, 504})


class RpcError(Exception):
    """Base class for every RPC-related failure."""


class RpcTransportError(RpcError):
    """Network-level failure (connect refused, reset, DNS, malformed body)."""


class RpcTimeoutError(RpcTransportError):
    """The provider did not answer within the configured timeout."""


class RpcHttpError(RpcTransportError):
    """The provider answered with a non-2xx HTTP status."""

    def __init__(self, status_code: int, body: str = "") -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"RPC HTTP {status_code}: {body[:200]!r}")


class RpcJsonRpcError(RpcError):
    """The provider answered 200 but with a JSON-RPC ``error`` object."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"JSON-RPC error {code}: {message}")


class RpcProviderDown(RpcError):  # noqa: N818 — name pinned by SPEC §13 (`internal_rpc_down` hook)
    """Raised when consecutive failures cross ``max_consecutive_failures``.

    This is the recognizable error the worker hooks into for the internal
    ``internal_rpc_down`` alert (SPEC §13).
    """


def _is_retryable(error: RpcError) -> bool:
    """Return True when a failure is transient enough to back off and retry."""
    if isinstance(error, RpcJsonRpcError):
        return error.code in _RETRYABLE_JSONRPC_CODES
    if isinstance(error, RpcHttpError):
        return error.status_code in _RETRYABLE_HTTP_CODES
    return isinstance(error, RpcTransportError)


class RpcClient:
    """Async JSON-RPC client with exponential backoff retries."""

    def __init__(
        self,
        *,
        url: str,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE,
        backoff_max_seconds: float = DEFAULT_BACKOFF_MAX,
        max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._max_retries = max_retries
        self._backoff_base = backoff_base_seconds
        self._backoff_max = backoff_max_seconds
        self.max_consecutive_failures = max_consecutive_failures
        self._sleep = sleep or asyncio.sleep
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._request_id = 0

        #: Public counter of consecutive failed *calls* (reset on success).
        self.consecutive_failures = 0

    @classmethod
    def from_settings(cls, settings: Any) -> RpcClient:
        """Build a client from :class:`app.config.Settings`."""
        return cls(
            url=settings.rpc_url,
            timeout=settings.rpc_timeout_seconds,
            max_retries=settings.rpc_max_retries,
            backoff_base_seconds=settings.rpc_backoff_base_seconds,
            backoff_max_seconds=settings.rpc_backoff_max_seconds,
            max_consecutive_failures=settings.rpc_max_consecutive_failures,
        )

    # ------------------------------------------------------------------
    # Backoff schedule (kept as a property so tests can assert the policy)
    # ------------------------------------------------------------------
    @property
    def backoff_schedule(self) -> list[float]:
        """The exact retry delays used, in seconds (``base*2**i`` capped)."""
        return [
            min(self._backoff_base * (2.0**i), self._backoff_max)
            for i in range(self._max_retries)
        ]

    # ------------------------------------------------------------------
    # Public RPC calls
    # ------------------------------------------------------------------
    async def eth_block_number(self) -> int:
        """Return the current head block number as an int."""
        result = await self._call("eth_blockNumber", [])
        return _to_int(result)

    async def eth_get_block_by_number(
        self,
        block_number: int | str,
        *,
        full_transactions: bool = True,
    ) -> dict[str, Any] | None:
        """Return one block (None when the provider has no such block)."""
        return await self._call(
            "eth_getBlockByNumber", [_block_tag(block_number), full_transactions]
        )

    async def eth_get_transaction_receipt(self, tx_hash: str) -> dict[str, Any] | None:
        """Return the receipt for one transaction (None if missing)."""
        return await self._call("eth_getTransactionReceipt", [tx_hash])

    async def eth_get_transaction_by_hash(self, tx_hash: str) -> dict[str, Any] | None:
        """Return one transaction by hash (None if missing/pending)."""
        return await self._call("eth_getTransactionByHash", [tx_hash])

    async def eth_get_logs(
        self,
        *,
        from_block: int | str,
        to_block: int | str,
        addresses: Sequence[str] | None = None,
        topics: Sequence[Sequence[str] | str | None] | None = None,
    ) -> list[dict[str, Any]]:
        """Return logs matching the address/topic filter in the block range."""
        params: dict[str, Any] = {
            "fromBlock": _block_tag(from_block),
            "toBlock": _block_tag(to_block),
        }
        if addresses:
            params["address"] = addresses[0] if len(addresses) == 1 else list(addresses)
        if topics:
            params["topics"] = list(topics)
        result = await self._call("eth_getLogs", [params])
        return list(result or [])

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()

    async def __aenter__(self) -> RpcClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _payload(self, method: str, params: list[Any]) -> dict[str, Any]:
        self._request_id += 1
        return {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }

    async def _call(self, method: str, params: list[Any]) -> Any:
        attempt = 0
        while True:
            error = await self._attempt(method, params)
            if error is None:
                return self._last_result  # type: ignore[attr-defined]
            if not _is_retryable(error):
                raise error

            self.consecutive_failures += 1
            if self.consecutive_failures >= self.max_consecutive_failures:
                logger.error(
                    "RPC consecutive failures=%d >= %d — provider considered down",
                    self.consecutive_failures,
                    self.max_consecutive_failures,
                )
                raise RpcProviderDown(
                    f"{method}: {self.consecutive_failures} consecutive failures "
                    f"(>= {self.max_consecutive_failures})"
                ) from error

            if attempt >= self._max_retries:
                raise error

            delay = self.backoff_schedule[attempt]
            attempt += 1
            logger.warning(
                "RPC %s failed (%s); retrying %d/%d in %.1fs",
                method,
                error,
                attempt,
                self._max_retries,
                delay,
            )
            await self._sleep(delay)

    _last_result: Any

    async def _attempt(self, method: str, params: list[Any]) -> RpcError | None:
        """Run one HTTP round-trip; return an error or None on success."""
        try:
            response = await self._client.post(self._url, json=self._payload(method, params))
        except httpx.TimeoutException as exc:
            return RpcTimeoutError(f"{method} timed out: {exc}")
        except httpx.HTTPError as exc:
            return RpcTransportError(f"{method} request failed: {exc}")

        if response.status_code != 200:
            return RpcHttpError(response.status_code, response.text or "")

        try:
            payload = response.json()
        except ValueError as exc:
            return RpcTransportError(f"{method}: provider returned invalid JSON: {exc}")

        if "error" in payload and payload["error"] is not None:
            err = payload["error"]
            code = err.get("code") if isinstance(err, dict) else -32000
            message = err.get("message", "") if isinstance(err, dict) else str(err)
            return RpcJsonRpcError(int(code), str(message))

        self.consecutive_failures = 0
        self._last_result = payload.get("result")
        return None


def _block_tag(block_number: int | str) -> str:
    """Format a block number/tag for a JSON-RPC payload."""
    if isinstance(block_number, str):
        return block_number
    return hex(block_number)


def _to_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 16)
        except ValueError as exc:
            raise RpcTransportError(f"expected a hex block number, got {value!r}") from exc
    raise RpcTransportError(f"expected a hex block number, got {value!r}")
