"""JSON-RPC client for the Arc RPC endpoint (SPEC §2 / §4 / §13 / Phase 2.5).

Wraps ``httpx.AsyncClient`` with the three calls the Indexer Worker needs:

* ``eth_blockNumber``            — current chain head
* ``eth_getBlockByNumber``       — one block, full transactions
* ``eth_getLogs``                — filtered by address + Transfer topic + range

Hard requirements implemented here (SPEC §13 + Phase 2.5 + §20.7):

* **Exponential backoff retries** — delays follow ``base → base*2 → base*4 → …``
  capped at ``backoff_max_seconds`` (1s → 2s → 4s → … → 60s by default).
* **Rate-limit backoff (429)** — HTTP 429 is transient provider throttling, not
  an outage (SPEC §20.7). It uses its own, longer and steeper retry schedule
  (``rate_limit_backoff_schedule``: 15s → 30s → 60s → … by default) and is
  tracked in a separate ``rate_limit_strikes`` counter. 429s are retryable but
  **never** count toward ``consecutive_failures`` / ``RpcProviderDown``, so a
  free-tier CU storm cannot fire a false ``internal_rpc_down`` alert.
* **Timeouts** — every request is bounded by ``timeout``.
* **Consecutive-failure counter** — persists across calls and raises
  :class:`RpcProviderDown` once the threshold is crossed (429s excluded) so the
  worker can fire the internal ``internal_rpc_down`` alert instead of silently
  hanging.
* **dRPC fallback (Phase 2.5)** — when the primary URL fails
  ``max_consecutive_failures`` times in a row the client automatically fails
  over to ``fallback_url``. After the fallback has served
  ``probe_after_successes`` successful calls it probes the primary again and
  switches back as soon as it recovers. The probe only fires after a minimum
  ``min_fallback_stay_seconds`` window (extended when the failover followed a
  429 storm) so provider ping-ponging is avoided. If the fallback also fails
  the client raises :class:`RpcProviderDown` exactly as before (single-provider
  behaviour).
* **Recognizable errors** — a small exception hierarchy so callers can classify
  transport / HTTP / JSON-RPC failures precisely.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_RETRIES = 5
DEFAULT_BACKOFF_BASE = 1.0
DEFAULT_BACKOFF_MAX = 60.0
DEFAULT_RATE_LIMIT_BACKOFF_BASE = 15.0
DEFAULT_MAX_CONSECUTIVE_FAILURES = 5
DEFAULT_PROBE_AFTER_SUCCESSES = 5
DEFAULT_MIN_FALLBACK_STAY_SECONDS = 120.0
#: Upper bound for the 429-storm fallback-stay extension (multiples of the
#: configured ``min_fallback_stay_seconds``); keeps the extension bounded.
MAX_FALLBACK_STAY_MULTIPLIER = 10

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
        fallback_url: str | None = None,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE,
        backoff_max_seconds: float = DEFAULT_BACKOFF_MAX,
        rate_limit_backoff_base_seconds: float = DEFAULT_RATE_LIMIT_BACKOFF_BASE,
        max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
        probe_after_successes: int = DEFAULT_PROBE_AFTER_SUCCESSES,
        min_fallback_stay_seconds: float = DEFAULT_MIN_FALLBACK_STAY_SECONDS,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        now: Callable[[], float] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._fallback_url = fallback_url or None
        self._max_retries = max_retries
        self._backoff_base = backoff_base_seconds
        self._backoff_max = backoff_max_seconds
        self._rate_limit_backoff_base = rate_limit_backoff_base_seconds
        self.max_consecutive_failures = max_consecutive_failures
        self._probe_after_successes = max(1, probe_after_successes)
        self._min_fallback_stay_seconds = min_fallback_stay_seconds
        self._sleep = sleep or asyncio.sleep
        self._now = now or time.monotonic
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._request_id = 0

        #: True while the primary URL is the active provider.
        self._active_is_primary = True
        #: Consecutive successful calls served by the fallback (drives probing).
        self._fallback_success_streak = 0
        #: Set once the fallback is healthy long enough to probe the primary.
        self._pending_primary_probe = False
        #: ``time.monotonic()`` when the fallback became active (probe gate).
        self._fallback_since: float | None = None
        #: How many 429 strikes preceded the current failover — extends the
        #: minimum fallback stay so a primary in a rate-limit storm is not
        #: probed back too eagerly (SPEC §20.7).
        self._fallback_rate_limit_strikes = 0

        #: Public counter of consecutive failed *calls* (reset on success,
        #: never incremented by HTTP 429).
        self.consecutive_failures = 0
        #: Public counter of HTTP 429 rate-limit responses (reset on success).
        #: Kept separate from ``consecutive_failures`` so a 429 storm cannot
        #: trigger :class:`RpcProviderDown` (SPEC §20.7).
        self.rate_limit_strikes = 0

    @classmethod
    def from_settings(cls, settings: Any) -> RpcClient:
        """Build a client from :class:`app.config.Settings`."""
        return cls(
            url=settings.rpc_url,
            fallback_url=settings.rpc_fallback_url or None,
            timeout=settings.rpc_timeout_seconds,
            max_retries=settings.rpc_max_retries,
            backoff_base_seconds=settings.rpc_backoff_base_seconds,
            backoff_max_seconds=settings.rpc_backoff_max_seconds,
            rate_limit_backoff_base_seconds=settings.rpc_rate_limit_backoff_base_seconds,
            max_consecutive_failures=settings.rpc_max_consecutive_failures,
            min_fallback_stay_seconds=settings.rpc_min_fallback_stay_seconds,
        )

    # ------------------------------------------------------------------
    # Provider selection (Phase 2.5 — dRPC fallback)
    # ------------------------------------------------------------------
    @property
    def active_url(self) -> str:
        """The URL the next request will hit (primary or fallback)."""
        return self._url if self._active_is_primary else (self._fallback_url or self._url)

    @property
    def using_fallback(self) -> bool:
        """True once the client failed over to the backup provider."""
        return not self._active_is_primary

    # ------------------------------------------------------------------
    # Backoff schedule (kept as a property so tests can assert the policy)
    # ------------------------------------------------------------------
    @property
    def backoff_schedule(self) -> list[float]:
        """The exact retry delays used for generic transient errors, in seconds
        (``base*2**i`` capped at ``backoff_max_seconds``)."""
        return [
            min(self._backoff_base * (2.0**i), self._backoff_max)
            for i in range(self._max_retries)
        ]

    @property
    def rate_limit_backoff_schedule(self) -> list[float]:
        """The exact retry delays used for HTTP 429 rate limiting, in seconds.

        Independent of :attr:`backoff_schedule`: starts at
        ``rate_limit_backoff_base_seconds`` (15s) and doubles harder (15s →
        30s → 60s → …), capped at ``backoff_max_seconds``. A free-tier provider
        recovers its CU quota far slower than a generic server hiccup, so 429s
        deserve a much longer, steeper schedule (SPEC §20.7).
        """
        return [
            min(self._rate_limit_backoff_base * (2.0**i), self._backoff_max)
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

    async def eth_call(self, *, to: str, data: str, block: str = "latest") -> str:
        """Execute an eth_call (read-only) against the given contract."""
        result = await self._call(
            "eth_call", [{"to": to, "data": data}, block]
        )
        return str(result or "0x")

    async def eth_get_balance(self, address: str, block: str = "latest") -> int:
        """Return the native balance of *address* at *block* (wei, 18 dp)."""
        result = await self._call("eth_getBalance", [address, block])
        return _to_int(result)

    async def eth_get_code(self, address: str, block: str = "latest") -> str:
        """Return the deployed code at *address* ('' when it is an EOA)."""
        result = await self._call("eth_getCode", [address, block])
        return str(result or "0x")

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
            # When the fallback has been healthy, probe the primary on the
            # next call so we automatically switch back once it recovers — but
            # only after the minimum fallback-stay window has elapsed
            # (SPEC §20.7), so a primary in a 429 storm does not cause provider
            # ping-ponging.
            if (
                self.using_fallback
                and self._pending_primary_probe
                and self._fallback_stay_elapsed()
            ):
                await self._probe_primary()

            error = await self._attempt(method, params)
            if error is None:
                self._record_success()
                return self._last_result  # type: ignore[attr-defined]
            if not _is_retryable(error):
                raise error

            if self._is_rate_limit(error):
                # HTTP 429 = transient provider throttling, not an outage.
                # Track it separately so it can never drive the
                # provider-down / failover decision, and back off much harder
                # than for generic server errors (SPEC §20.7). A 429 storm
                # keeps retrying (with ever-longer delays) even past
                # max_consecutive_failures — it must NOT raise RpcProviderDown.
                self.rate_limit_strikes += 1
                if attempt >= self._max_retries:
                    raise error
                delay = self.rate_limit_backoff_schedule[attempt]
                attempt += 1
                logger.warning(
                    "RPC %s rate-limited (%s); retrying %d/%d in %.1fs",
                    method,
                    error,
                    attempt,
                    self._max_retries,
                    delay,
                )
                await self._sleep(delay)
                continue

            self.consecutive_failures += 1
            if self.consecutive_failures >= self.max_consecutive_failures:
                # Phase 2.5: fail over to the fallback provider, or declare down.
                if self.using_fallback or not self._fallback_url:
                    logger.error(
                        "RPC consecutive failures=%d >= %d — provider(s) down",
                        self.consecutive_failures,
                        self.max_consecutive_failures,
                    )
                    raise RpcProviderDown(
                        f"{method}: {self.consecutive_failures} consecutive failures "
                        f"(>= {self.max_consecutive_failures})"
                    ) from error

                logger.error(
                    "RPC primary failed %d times — failing over to fallback %s",
                    self.consecutive_failures,
                    self._fallback_url,
                )
                self._active_is_primary = False
                self._fallback_success_streak = 0
                self.consecutive_failures = 0
                # Remember *when* we left the primary and how badly it was
                # rate-limited, to gate the probe-return (SPEC §20.7).
                self._fallback_since = self._now()
                self._fallback_rate_limit_strikes = self.rate_limit_strikes
                attempt = 0  # start a fresh retry budget on the fallback
                continue

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

    def _record_success(self) -> None:
        """Reset failure counters and schedule a primary probe when needed."""
        self.consecutive_failures = 0
        self.rate_limit_strikes = 0
        if self.using_fallback:
            self._fallback_success_streak += 1
            if self._fallback_success_streak >= self._probe_after_successes:
                self._pending_primary_probe = True
                self._fallback_success_streak = 0

    @staticmethod
    def _is_rate_limit(error: RpcError) -> bool:
        """True when the failure is an HTTP 429 (provider throttling)."""
        return isinstance(error, RpcHttpError) and error.status_code == 429

    def _fallback_stay_elapsed(self) -> bool:
        """True when the primary may be probed again.

        Governed by ``min_fallback_stay_seconds`` — a plain timestamp gate, not
        success counts — so flipping back happens on our schedule, not the
        provider's. When the current failover followed a 429 storm the window
        is extended (up to ``MAX_FALLBACK_STAY_MULTIPLIER`` x) so a
        rate-limited primary is not re-adopted until it has had time to drain
        its CU quota (SPEC §20.7).
        """
        if self._fallback_since is None:
            return True
        multiplier = 1 + self._fallback_rate_limit_strikes // 10
        multiplier = min(multiplier, MAX_FALLBACK_STAY_MULTIPLIER)
        required = self._min_fallback_stay_seconds * multiplier
        return self._now() - self._fallback_since >= required

    async def _probe_primary(self) -> None:
        """Try one lightweight call on the primary; switch back on success."""
        self._pending_primary_probe = False
        if self._active_is_primary or not self._fallback_url:
            return
        try:
            response = await self._client.post(
                self._url, json=self._payload("eth_blockNumber", [])
            )
            if response.status_code == 200 and "error" not in response.json():
                logger.info("RPC primary recovered — switching back to %s", self._url)
                self._active_is_primary = True
                self._fallback_success_streak = 0
                self._fallback_since = None
                self._fallback_rate_limit_strikes = 0
        except (httpx.HTTPError, ValueError):
            logger.debug("RPC primary probe failed; staying on fallback")

    async def _attempt(self, method: str, params: list[Any]) -> RpcError | None:
        """Run one HTTP round-trip against the active provider; error or None."""
        try:
            response = await self._client.post(
                self.active_url, json=self._payload(method, params)
            )
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
