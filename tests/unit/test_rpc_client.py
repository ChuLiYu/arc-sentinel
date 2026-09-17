"""Unit tests for app.indexer.rpc_client (SPEC §13: backoff/timeout/consecutive-failure)."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.indexer.rpc_client import (
    RpcClient,
    RpcHttpError,
    RpcJsonRpcError,
    RpcProviderDown,
    RpcTimeoutError,
    RpcTransportError,
)


@pytest.fixture
def url() -> str:
    return "https://rpc.testnet.arc.io"


# ---------------------------------------------------------------------------
# Backoff schedule
# ---------------------------------------------------------------------------
def test_backoff_schedule_exponential_capped():
    c = RpcClient(
        url="https://x",
        max_retries=4,
        backoff_base_seconds=1.0,
        backoff_max_seconds=5.0,
    )
    assert c.backoff_schedule == [1.0, 2.0, 4.0, 5.0]  # 8 capped to 5


# ---------------------------------------------------------------------------
# eth_blockNumber
# ---------------------------------------------------------------------------
@respx.mock
async def test_eth_block_number_success(url: str):
    respx.post(url).mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1a2b3c"})
    )
    c = RpcClient(url=url)
    try:
        assert await c.eth_block_number() == 0x1A2B3C
        assert c.consecutive_failures == 0
    finally:
        await c.close()


@respx.mock
async def test_eth_block_number_timeout_retries_then_raises(url: str):
    route = respx.post(url).mock(side_effect=httpx.ConnectTimeout("boom"))
    # max_retries=2 ⇒ 1 initial + 2 retries = 3 attempts, then raises RpcTimeoutError
    c = RpcClient(url=url, max_retries=2, backoff_base_seconds=0.001, backoff_max_seconds=0.01)
    try:
        with pytest.raises(RpcTimeoutError):
            await c.eth_block_number()
        assert route.call_count == 3
        assert c.consecutive_failures == 3
    finally:
        await c.close()


@respx.mock
async def test_http_429_retryable(url: str):
    responses = [
        httpx.Response(429, text="rate limited"),
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}),
    ]
    route = respx.post(url).mock(side_effect=responses)
    c = RpcClient(url=url, max_retries=3, backoff_base_seconds=0.001, backoff_max_seconds=0.01)
    try:
        assert await c.eth_block_number() == 1
        assert route.call_count == 2
        assert c.consecutive_failures == 0  # success resets the counter
    finally:
        await c.close()


@respx.mock
async def test_http_500_retryable(url: str):
    respx.post(url).mock(return_value=httpx.Response(500, text="oops"))
    c = RpcClient(url=url, max_retries=1, backoff_base_seconds=0.001, backoff_max_seconds=0.01)
    try:
        with pytest.raises(RpcHttpError) as exc_info:
            await c.eth_block_number()
        assert exc_info.value.status_code == 500
    finally:
        await c.close()


@respx.mock
async def test_http_400_not_retryable(url: str):
    route = respx.post(url).mock(return_value=httpx.Response(400, text="bad request"))
    c = RpcClient(url=url, max_retries=3, backoff_base_seconds=0.001, backoff_max_seconds=0.01)
    try:
        with pytest.raises(RpcHttpError):
            await c.eth_block_number()
        assert route.call_count == 1  # 400 is not retryable → single attempt
    finally:
        await c.close()


@respx.mock
async def test_jsonrpc_retryable_code_then_success(url: str):
    responses = [
        httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32005, "message": "limit"}}
        ),
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x10"}),
    ]
    route = respx.post(url).mock(side_effect=responses)
    c = RpcClient(url=url, max_retries=3, backoff_base_seconds=0.001, backoff_max_seconds=0.01)
    try:
        assert await c.eth_block_number() == 16
        assert route.call_count == 2
    finally:
        await c.close()


@respx.mock
async def test_jsonrpc_non_retryable_code_raises_immediately(url: str):
    route = respx.post(url).mock(
        return_value=httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32601, "message": "method not found"},
            },
        )
    )
    c = RpcClient(url=url, max_retries=3, backoff_base_seconds=0.001, backoff_max_seconds=0.01)
    try:
        with pytest.raises(RpcJsonRpcError) as exc_info:
            await c.eth_block_number()
        assert exc_info.value.code == -32601
        assert route.call_count == 1
    finally:
        await c.close()


@respx.mock
async def test_consecutive_failures_crosses_threshold_raises_provider_down(url: str):
    respx.post(url).mock(return_value=httpx.Response(503, text="down"))
    # threshold=3, long retry budget: first call exhausts retries (RpcHttpError),
    # second call crosses the consecutive-failure threshold → RpcProviderDown
    c = RpcClient(
        url=url,
        max_retries=1,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        max_consecutive_failures=3,
    )
    try:
        with pytest.raises(RpcHttpError):
            await c.eth_block_number()
        with pytest.raises(RpcProviderDown):
            await c.eth_block_number()
    finally:
        await c.close()


@respx.mock
async def test_success_resets_consecutive_failures(url: str):
    respx.post(url).mock(
        side_effect=[
            httpx.Response(503, text="down"),
            httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}),
        ]
    )
    c = RpcClient(
        url=url,
        max_retries=1,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        max_consecutive_failures=5,
    )
    try:
        assert await c.eth_block_number() == 1
        assert c.consecutive_failures == 0  # retry succeeded → counter reset inside _attempt
    finally:
        await c.close()


# ---------------------------------------------------------------------------
# Lifecycle / invalid responses
# ---------------------------------------------------------------------------
@respx.mock
async def test_invalid_json_raises_transport_error(url: str):
    respx.post(url).mock(return_value=httpx.Response(200, text="not json"))
    # raise max_consecutive_failures so the underlying retryable transport error
    # surfaces first (provider-down detection needs more consecutive failures)
    c = RpcClient(
        url=url,
        max_retries=1,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        max_consecutive_failures=10,
    )
    try:
        with pytest.raises(RpcTransportError):
            await c.eth_block_number()
    finally:
        await c.close()


@respx.mock
async def test_eth_get_logs_passes_addresses_and_topics(url: str):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": [{"logIndex": "0x0", "transactionHash": "0xabc"}],
            },
        )

    respx.post(url).mock(side_effect=handler)
    c = RpcClient(url=url)
    try:
        logs = await c.eth_get_logs(
            from_block=100,
            to_block=101,
            addresses=["0x3600000000000000000000000000000000000000"],
            topics=[["0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"]],
        )
        assert len(logs) == 1
        assert '"fromBlock":"0x64"' in captured["json"]
        assert "0xddf252ad" in captured["json"]
    finally:
        await c.close()


# ---------------------------------------------------------------------------
# Phase 2.5 — dRPC fallback
# ---------------------------------------------------------------------------
@respx.mock
async def test_failover_to_fallback_after_primary_threshold(url: str):
    fallback = "https://rpc.drpc.mainnet.arc.io"
    respx.post(url).mock(return_value=httpx.Response(503, text="down"))
    respx.post(fallback).mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x64"})
    )
    c = RpcClient(
        url=url,
        fallback_url=fallback,
        max_retries=5,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        max_consecutive_failures=2,
    )
    try:
        assert await c.eth_block_number() == 100
        assert c.using_fallback is True
        assert c.active_url == fallback
        assert c.consecutive_failures == 0  # fallback success resets the counter
        assert respx.calls.call_count >= 3  # 2 primary fails + 1 fallback success
    finally:
        await c.close()


@respx.mock
async def test_fallback_also_down_raises_provider_down(url: str):
    fallback = "https://rpc.drpc.mainnet.arc.io"
    respx.post(url).mock(return_value=httpx.Response(503, text="primary down"))
    respx.post(fallback).mock(return_value=httpx.Response(502, text="fallback down"))
    c = RpcClient(
        url=url,
        fallback_url=fallback,
        max_retries=5,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        max_consecutive_failures=2,
    )
    try:
        with pytest.raises(RpcProviderDown):
            await c.eth_block_number()
    finally:
        await c.close()


@respx.mock
async def test_probe_primary_and_switch_back_after_recovery(url: str):
    fallback = "https://rpc.drpc.mainnet.arc.io"
    # Primary: 2 failures (drives failover) then recovers (probe + real call).
    primary_seq = [
        httpx.Response(503, text="down"),
        httpx.Response(503, text="down"),
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x12c"}),  # probe
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x12c"}),
    ]
    respx.post(url).mock(side_effect=primary_seq)
    respx.post(fallback).mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x12c"})
    )
    c = RpcClient(
        url=url,
        fallback_url=fallback,
        max_retries=5,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        max_consecutive_failures=2,
        probe_after_successes=1,
        # Probe-on-next-success mechanics under test; the SPEC §20.7 minimum
        # fallback-stay gate is deliberately disabled (see the dedicated tests).
        min_fallback_stay_seconds=0,
    )
    try:
        assert await c.eth_block_number() == 300
        assert c.using_fallback is True  # failover happened, fallback served it

        # Second call probes the (now recovered) primary and switches back.
        assert await c.eth_block_number() == 300
        assert c.using_fallback is False
        assert c.active_url == url
    finally:
        await c.close()


@respx.mock
async def test_probe_primary_failure_stays_on_fallback(url: str):
    fallback = "https://rpc.drpc.mainnet.arc.io"
    # Primary stays down forever (probe will keep failing).
    respx.post(url).mock(return_value=httpx.Response(503, text="still down"))
    respx.post(fallback).mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1f4"})
    )
    c = RpcClient(
        url=url,
        fallback_url=fallback,
        max_retries=5,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        max_consecutive_failures=2,
        probe_after_successes=1,
        min_fallback_stay_seconds=0,
    )
    try:
        assert await c.eth_block_number() == 500
        assert c.using_fallback is True

        # Probe fires but primary is still down → stay on fallback, call succeeds.
        assert await c.eth_block_number() == 500
        assert c.using_fallback is True
        assert c.active_url == fallback
    finally:
        await c.close()


# ---------------------------------------------------------------------------
# SPEC §20.7 — HTTP 429 rate-limit resilience
# ---------------------------------------------------------------------------
async def _noop_sleep(_seconds: float) -> None:
    """Do not actually wait; keeps 429 retry storms fast in tests."""


class _FakeClock:
    """Injectable monotonic clock so fallback-stay tests control time."""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def test_rate_limit_backoff_schedule_is_separate_and_steeper():
    c = RpcClient(
        url="https://x",
        max_retries=5,
        backoff_base_seconds=1.0,
        backoff_max_seconds=60.0,
        rate_limit_backoff_base_seconds=15.0,
    )
    # Generic errors: 1s → 2s → 4s → 8s → 16s.
    assert c.backoff_schedule == [1.0, 2.0, 4.0, 8.0, 16.0]
    # HTTP 429: 15s → 30s → 60s → capped at 60s — longer AND steeper, and it
    # is an independent schedule, never mixed with the generic one.
    assert c.rate_limit_backoff_schedule == [15.0, 30.0, 60.0, 60.0, 60.0]
    assert c.rate_limit_backoff_schedule != c.backoff_schedule


@respx.mock
async def test_429_storm_never_raises_provider_down(url: str):
    respx.post(url).mock(return_value=httpx.Response(429, text="rate limited"))
    # Tiny max_consecutive_failures on purpose: a 429 storm must NOT trip the
    # provider-down detection (SPEC §20.7) no matter how long it lasts.
    c = RpcClient(
        url=url,
        max_retries=2,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        max_consecutive_failures=2,
        rate_limit_backoff_base_seconds=15.0,
        sleep=_noop_sleep,
    )
    try:
        with pytest.raises(RpcHttpError) as exc_info:
            await c.eth_block_number()
        assert exc_info.value.status_code == 429
        assert c.consecutive_failures == 0, "429 must not count toward provider-down"
        assert c.rate_limit_strikes == 3  # 1 initial + 2 retries, all 429

        # A second call against the always-429 provider still raises 429 —
        # it must never escalate to RpcProviderDown.
        with pytest.raises(RpcHttpError):
            await c.eth_block_number()
        assert c.rate_limit_strikes == 6
        assert c.consecutive_failures == 0
    finally:
        await c.close()


@respx.mock
async def test_429_retries_use_rate_limit_backoff_schedule(url: str):
    slept: list[float] = []

    async def recording_sleep(seconds: float) -> None:
        slept.append(seconds)

    responses = [
        httpx.Response(429, text="rate limited"),
        httpx.Response(429, text="rate limited"),
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}),
    ]
    respx.post(url).mock(side_effect=responses)
    c = RpcClient(
        url=url,
        max_retries=3,
        backoff_base_seconds=1.0,
        backoff_max_seconds=60.0,
        rate_limit_backoff_base_seconds=15.0,
        sleep=recording_sleep,
    )
    try:
        assert await c.eth_block_number() == 1
        # 429s slept the rate-limit schedule (15s, 30s) — NOT backoff_schedule.
        assert slept == [15.0, 30.0]
        # The eventual 200 resets every counter.
        assert c.rate_limit_strikes == 0
        assert c.consecutive_failures == 0
    finally:
        await c.close()


# ---------------------------------------------------------------------------
# SPEC §20.7 — minimum fallback stay (no provider ping-ponging)
# ---------------------------------------------------------------------------
@respx.mock
async def test_min_fallback_stay_blocks_probe_until_window_elapses(url: str):
    fallback = "https://rpc.drpc.mainnet.arc.io"
    # Primary: 2 failures (drives failover), then recovers (probe + real call).
    primary_seq = [
        httpx.Response(503, text="down"),
        httpx.Response(503, text="down"),
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x12c"}),  # probe
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x12c"}),
    ]
    respx.post(url).mock(side_effect=primary_seq)
    respx.post(fallback).mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x12c"})
    )
    clock = _FakeClock()
    c = RpcClient(
        url=url,
        fallback_url=fallback,
        max_retries=5,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        max_consecutive_failures=2,
        probe_after_successes=1,
        min_fallback_stay_seconds=120.0,
        now=clock,
        sleep=_noop_sleep,
    )
    try:
        assert await c.eth_block_number() == 300
        assert c.using_fallback is True

        # probe_after_successes=1 is reached, but only 60s of the 120s
        # minimum stay have elapsed → the probe is postponed and the call is
        # still served by the fallback.
        clock.value += 60.0
        assert await c.eth_block_number() == 300
        assert c.using_fallback is True
        assert c.active_url == fallback

        # Past the 120s stay (total 121s) the probe fires, finds the primary
        # recovered, and switches back.
        clock.value += 61.0
        assert await c.eth_block_number() == 300
        assert c.using_fallback is False
        assert c.active_url == url
    finally:
        await c.close()


@respx.mock
async def test_fallback_stay_extends_after_rate_limit_storm(url: str):
    fallback = "https://rpc.drpc.mainnet.arc.io"
    # 12 x 429 accumulated during retries, then 2 x 503 to trigger the
    # failover, then a recovered primary (probe + real call).
    primary_seq = [
        *(httpx.Response(429, text="rate limited") for _ in range(12)),
        httpx.Response(503, text="down"),
        httpx.Response(503, text="down"),
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x12c"}),  # probe
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x12c"}),
    ]
    respx.post(url).mock(side_effect=primary_seq)
    respx.post(fallback).mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x12c"})
    )
    clock = _FakeClock()
    c = RpcClient(
        url=url,
        fallback_url=fallback,
        max_retries=2,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        max_consecutive_failures=2,
        probe_after_successes=1,
        min_fallback_stay_seconds=120.0,
        rate_limit_backoff_base_seconds=15.0,
        now=clock,
        sleep=_noop_sleep,
    )
    try:
        # max_retries=2 ⇒ 3 attempts/call; four calls burn the 12 x 429.
        for _ in range(4):
            with pytest.raises(RpcHttpError):
                await c.eth_block_number()
        assert c.rate_limit_strikes == 12
        assert c.consecutive_failures == 0

        # The remaining 503s finally trigger failover; strikes (12) are
        # snapshotted to extend the fallback stay: 120s * (1 + 12//10) = 240s.
        assert await c.eth_block_number() == 300
        assert c.using_fallback is True

        # 120s have elapsed — the base window — but not the extended 240s.
        clock.value += 120.0
        assert await c.eth_block_number() == 300
        assert c.using_fallback is True
        assert c.active_url == fallback

        # 240s total elapsed → probe fires and primary has recovered.
        clock.value += 121.0
        assert await c.eth_block_number() == 300
        assert c.using_fallback is False
        assert c.active_url == url
    finally:
        await c.close()
