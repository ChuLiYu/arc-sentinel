"""Unit tests for app.indexer.decode (SPEC §10).

Covers the two decimal conversion helpers (18-dp native gas, 6-dp USDC) and
ERC-20 Transfer log decoding, including boundary values, integer overflow
beyond NUMERIC(38,0) capacity, and invalid input.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.indexer.decode import (
    NUMERIC_38_MAX,
    TRANSFER_EVENT_SIG,
    DecodeError,
    InvalidHexError,
    TransferLogError,
    decode_transfer_log,
    raw_6dp_to_usdc,
    wei_to_eth,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
FROM_ADDR = "0x1111111111111111111111111111111111111111"
TO_ADDR = "0x2222222222222222222222222222222222222222"


def _topic(address: str) -> str:
    """Left-pad a 20-byte address to a 32-byte indexed topic word."""
    return "0x" + address[2:].rjust(64, "0")


def _word(value: int) -> str:
    """Encode an integer as a 32-byte hex word."""
    return "0x" + format(value, "064x")


# ---------------------------------------------------------------------------
# wei_to_eth  (18 decimals, native gas)
# ---------------------------------------------------------------------------
class TestWeiToEth:
    def test_zero(self) -> None:
        assert wei_to_eth(0) == Decimal("0")

    def test_one_wei(self) -> None:
        assert wei_to_eth(1) == Decimal("0.000000000000000001")

    def test_one_eth(self) -> None:
        assert wei_to_eth(10**18) == Decimal("1")

    def test_fractional_precision(self) -> None:
        assert wei_to_eth(1_234_567_890_123_456_789) == Decimal("1.234567890123456789")

    def test_accepts_decimal_and_str(self) -> None:
        assert wei_to_eth(Decimal(10**18)) == Decimal("1")
        assert wei_to_eth(str(10**18)) == Decimal("1")

    def test_returns_decimal_never_float(self) -> None:
        result = wei_to_eth(10**18)
        assert isinstance(result, Decimal)
        assert not isinstance(result, float)

    def test_no_rounding_up(self) -> None:
        # 1 wei must never round up to a visible positive amount
        assert wei_to_eth(1) == Decimal("0.000000000000000001")

    def test_max_numeric_38(self) -> None:
        result = wei_to_eth(NUMERIC_38_MAX)
        assert result == (NUMERIC_38_MAX.scaleb(-18)).quantize(Decimal(1).scaleb(-18))
        assert result <= NUMERIC_38_MAX

    def test_overflow_beyond_numeric_38(self) -> None:
        with pytest.raises(DecodeError):
            wei_to_eth(10**38)

    def test_negative_rejected(self) -> None:
        with pytest.raises(InvalidHexError):
            wei_to_eth(-1)

    def test_non_integer_string_rejected(self) -> None:
        with pytest.raises(InvalidHexError):
            wei_to_eth("1.5")

    def test_hex_string_rejected(self) -> None:
        with pytest.raises(InvalidHexError):
            wei_to_eth("0x10")

    def test_garbage_string_rejected(self) -> None:
        with pytest.raises(InvalidHexError):
            wei_to_eth("abc")

    def test_float_rejected(self) -> None:
        # float is forbidden for currency math -> TypeError, not silent coercion
        with pytest.raises(TypeError):
            wei_to_eth(1.5)  # type: ignore[arg-type]

    def test_none_rejected(self) -> None:
        with pytest.raises(TypeError):
            wei_to_eth(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# raw_6dp_to_usdc  (6 decimals, USDC ERC-20)
# ---------------------------------------------------------------------------
class TestRaw6dpToUsdc:
    def test_zero(self) -> None:
        assert raw_6dp_to_usdc(0) == Decimal("0")

    def test_one_micro_usdc(self) -> None:
        assert raw_6dp_to_usdc(1) == Decimal("0.000001")

    def test_one_usdc(self) -> None:
        assert raw_6dp_to_usdc(10**6) == Decimal("1")

    def test_fractional(self) -> None:
        assert raw_6dp_to_usdc(1_234_567) == Decimal("1.234567")

    def test_accepts_decimal_and_str(self) -> None:
        assert raw_6dp_to_usdc(Decimal(1_000_000)) == Decimal("1")
        assert raw_6dp_to_usdc("1000000") == Decimal("1")

    def test_returns_decimal_never_float(self) -> None:
        result = raw_6dp_to_usdc(1_000_000)
        assert isinstance(result, Decimal)
        assert not isinstance(result, float)

    def test_exact_six_dp(self) -> None:
        assert raw_6dp_to_usdc(1234567).as_tuple().exponent == -6

    def test_max_numeric_38(self) -> None:
        assert raw_6dp_to_usdc(NUMERIC_38_MAX) > Decimal(0)

    def test_overflow_beyond_numeric_38(self) -> None:
        with pytest.raises(DecodeError):
            raw_6dp_to_usdc(10**38)

    def test_negative_rejected(self) -> None:
        with pytest.raises(InvalidHexError):
            raw_6dp_to_usdc(-1)

    def test_non_integer_rejected(self) -> None:
        with pytest.raises(InvalidHexError):
            raw_6dp_to_usdc(Decimal("0.5"))

    def test_float_rejected(self) -> None:
        with pytest.raises(TypeError):
            raw_6dp_to_usdc(1.5)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# decode_transfer_log
# ---------------------------------------------------------------------------
class TestDecodeTransferLog:
    def test_valid_transfer(self) -> None:
        log = decode_transfer_log(
            topics=[TRANSFER_EVENT_SIG, _topic(FROM_ADDR), _topic(TO_ADDR)],
            data=_word(1_000_000),
        )
        assert log.from_address == FROM_ADDR
        assert log.to_address == TO_ADDR
        assert log.amount_raw == Decimal(1_000_000)
        assert isinstance(log.amount_raw, Decimal)

    def test_amount_conversion_from_decoded_raw(self) -> None:
        log = decode_transfer_log(
            topics=[TRANSFER_EVENT_SIG, _topic(FROM_ADDR), _topic(TO_ADDR)],
            data=_word(1_234_567),
        )
        assert raw_6dp_to_usdc(log.amount_raw) == Decimal("1.234567")

    def test_zero_amount(self) -> None:
        log = decode_transfer_log(
            topics=[TRANSFER_EVENT_SIG, _topic(FROM_ADDR), _topic(TO_ADDR)],
            data=_word(0),
        )
        assert log.amount_raw == Decimal(0)

    def test_uppercase_hex_and_prefix(self) -> None:
        log = decode_transfer_log(
            topics=[
                TRANSFER_EVENT_SIG.upper().replace("0X", "0X"),
                _topic(FROM_ADDR).upper().replace("0X", "0X"),
                _topic(TO_ADDR).upper().replace("0X", "0X"),
            ],
            data="0X" + format(42, "064x").upper(),
        )
        assert log.from_address == FROM_ADDR
        assert log.amount_raw == Decimal(42)

    def test_addresses_lowercased(self) -> None:
        upper_addr = "0x" + "AB" * 20
        log = decode_transfer_log(
            topics=[TRANSFER_EVENT_SIG, _topic(upper_addr), _topic(upper_addr)],
            data=_word(1),
        )
        assert log.from_address == "0x" + "ab" * 20

    def test_max_numeric_38_amount(self) -> None:
        log = decode_transfer_log(
            topics=[TRANSFER_EVENT_SIG, _topic(FROM_ADDR), _topic(TO_ADDR)],
            data=_word(int(NUMERIC_38_MAX)),
        )
        assert log.amount_raw == NUMERIC_38_MAX

    def test_uint256_max_amount_overflows(self) -> None:
        # 2**256 - 1 far exceeds NUMERIC(38,0) capacity
        with pytest.raises(DecodeError):
            decode_transfer_log(
                topics=[TRANSFER_EVENT_SIG, _topic(FROM_ADDR), _topic(TO_ADDR)],
                data=_word(2**256 - 1),
            )

    def test_wrong_event_signature(self) -> None:
        with pytest.raises(TransferLogError):
            decode_transfer_log(
                topics=["0x" + "ab" * 32, _topic(FROM_ADDR), _topic(TO_ADDR)],
                data=_word(1),
            )

    def test_too_few_topics(self) -> None:
        with pytest.raises(TransferLogError):
            decode_transfer_log(topics=[TRANSFER_EVENT_SIG], data=_word(1))

    def test_empty_topics(self) -> None:
        with pytest.raises(TransferLogError):
            decode_transfer_log(topics=[], data=_word(1))

    def test_topics_not_a_list(self) -> None:
        with pytest.raises(TransferLogError):
            decode_transfer_log(topics="not-a-list", data=_word(1))  # type: ignore[arg-type]

    def test_short_topic_word(self) -> None:
        with pytest.raises(InvalidHexError):
            decode_transfer_log(
                topics=[TRANSFER_EVENT_SIG, "0x01", _topic(TO_ADDR)],
                data=_word(1),
            )

    def test_odd_length_topic(self) -> None:
        with pytest.raises(InvalidHexError):
            decode_transfer_log(
                topics=[TRANSFER_EVENT_SIG, "0x1", _topic(TO_ADDR)],
                data=_word(1),
            )

    def test_empty_data(self) -> None:
        with pytest.raises(InvalidHexError):
            decode_transfer_log(
                topics=[TRANSFER_EVENT_SIG, _topic(FROM_ADDR), _topic(TO_ADDR)],
                data="0x",
            )

    def test_short_data_word(self) -> None:
        with pytest.raises(InvalidHexError):
            decode_transfer_log(
                topics=[TRANSFER_EVENT_SIG, _topic(FROM_ADDR), _topic(TO_ADDR)],
                data="0x01",
            )

    def test_non_hex_data(self) -> None:
        with pytest.raises(InvalidHexError):
            decode_transfer_log(
                topics=[TRANSFER_EVENT_SIG, _topic(FROM_ADDR), _topic(TO_ADDR)],
                data="0x" + "zz" * 32,
            )

    def test_data_none(self) -> None:
        with pytest.raises(InvalidHexError):
            decode_transfer_log(
                topics=[TRANSFER_EVENT_SIG, _topic(FROM_ADDR), _topic(TO_ADDR)],
                data=None,  # type: ignore[arg-type]
            )

    def test_decoded_log_is_immutable(self) -> None:
        log = decode_transfer_log(
            topics=[TRANSFER_EVENT_SIG, _topic(FROM_ADDR), _topic(TO_ADDR)],
            data=_word(7),
        )
        with pytest.raises(FrozenInstanceError):
            log.amount_raw = Decimal(8)  # type: ignore[misc]
