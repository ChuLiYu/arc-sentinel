"""Decoding of EVM logs and decimal conversion helpers.

All monetary values are handled as :class:`decimal.Decimal` — floating point
is forbidden for currency math in this project (SPEC §7).

Decimals & units on Arc (SPEC §2):
  * Native gas accounting: 18 decimal places (raw integers are "wei").
  * USDC ERC-20:           6 decimal places (raw integers are "micro USDC").

Column mapping for ``token_transfers`` (SPEC §7):
  * ``amount_raw``  -> the raw uint256 from the Transfer event (6-dp integer)
  * ``amount_usdc`` -> :func:`raw_6dp_to_usdc` of ``amount_raw``
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, getcontext

# 28 significant digits is the Decimal default; bump it so that converting the
# largest value storable in NUMERIC(38,0) never silently loses digits.
getcontext().prec = 50

NUMERIC_38_MAX = Decimal(10**38) - Decimal(1)
"""Largest value representable in ``NUMERIC(38,0)`` (SPEC §7)."""

WEI_DECIMALS = 18
USDC_DECIMALS = 6

# keccak256("Transfer(address,address,uint256)")
TRANSFER_EVENT_SIG = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

_HEX_WORD_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class DecodeError(ValueError):
    """Base error for any malformed log / data passed to the decoder."""


class InvalidHexError(DecodeError):
    """Raised when a hex string is empty, malformed or of the wrong length."""


class TransferLogError(DecodeError):
    """Raised when topics/data cannot be parsed into a USDC Transfer log."""


def amount_overflow(value: Decimal) -> bool:
    """Return True when *value* exceeds ``NUMERIC(38,0)`` storage capacity."""
    return value > NUMERIC_38_MAX


# ---------------------------------------------------------------------------
# Decimal conversion helpers (18-dp native gas and 6-dp USDC)
# ---------------------------------------------------------------------------
def _as_decimal(raw: str | int | Decimal, *, field: str) -> Decimal:
    """Coerce the input to a non-negative integral Decimal."""
    if isinstance(raw, Decimal):
        value = raw
    elif isinstance(raw, int):
        value = Decimal(raw)
    elif isinstance(raw, str):
        # Reject floats-as-strings ("1.5") explicitly: raw chain values are
        # integers. ``Decimal("1.50")`` would silently carry a fractional part.
        if not re.fullmatch(r"[0-9]+", raw):
            raise InvalidHexError(f"{field}: expected a non-negative integer string, got {raw!r}")
        value = Decimal(raw)
    else:
        raise TypeError(f"{field}: unsupported type {type(raw).__name__}")

    if value != value.to_integral_value():
        raise InvalidHexError(f"{field}: raw chain values must be integers, got {value}")
    if value < 0:
        raise InvalidHexError(f"{field}: raw chain values must be >= 0, got {value}")
    if amount_overflow(value):
        raise DecodeError(f"{field}: value exceeds NUMERIC(38,0) capacity: {value}")
    return value


def wei_to_eth(wei: str | int | Decimal) -> Decimal:
    """Convert a raw wei integer (18 decimals) to a human-readable ETH/Arc value.

    The result is a Decimal with at most 18 decimal places, truncated
    (``ROUND_DOWN``) — never rounded up, so a 2-wei balance never becomes a
    positive displayed amount by rounding.
    """
    value = _as_decimal(wei, field="wei")
    return value.scaleb(-WEI_DECIMALS).quantize(
        Decimal(1).scaleb(-WEI_DECIMALS), rounding=ROUND_DOWN
    )


def raw_6dp_to_usdc(raw: str | int | Decimal) -> Decimal:
    """Convert a raw USDC integer (6 decimals, "micro USDC") to human-readable USDC.

    Result is a Decimal with at most 6 decimal places, truncated
    (``ROUND_DOWN``), matching the ``NUMERIC(20,6)`` column definition.
    """
    value = _as_decimal(raw, field="amount_raw")
    return value.scaleb(-USDC_DECIMALS).quantize(
        Decimal(1).scaleb(-USDC_DECIMALS), rounding=ROUND_DOWN
    )


# ---------------------------------------------------------------------------
# ERC-20 Transfer log decoding
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TransferLog:
    """A decoded ERC-20 ``Transfer(address,address,uint256)`` event."""

    from_address: str  # checksummed EIP-55 address, lowercase hex preserved
    to_address: str
    amount_raw: Decimal  # raw uint256 value (6-dp integer for USDC)


def _strip_0x(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidHexError(f"expected a hex string, got {type(value).__name__}")
    hex_part = value[2:] if value.startswith(("0x", "0X")) else value
    if not hex_part:
        raise InvalidHexError("empty hex string")
    if len(hex_part) % 2 != 0:
        raise InvalidHexError(f"odd-length hex string: {value!r}")
    return hex_part


def _hex_word_to_int(hex_word: str, *, field: str) -> int:
    """Parse a 32-byte (64-hex-char) word into an int."""
    word = _strip_0x(hex_word)
    if not _HEX_WORD_RE.fullmatch(word):
        raise InvalidHexError(f"{field}: expected a 32-byte hex word, got {hex_word!r}")
    return int(word, 16)


def _extract_address(word: str, *, field: str) -> str:
    """Extract the 20-byte address from an indexed (padded) 32-byte topic word."""
    _ = _hex_word_to_int(word, field=field)  # length/hex validation
    address_hex = _strip_0x(word)[-40:]  # last 20 bytes, lowercase preserved
    return f"0x{address_hex.lower()}"


def decode_transfer_log(topics: list[str], data: str) -> TransferLog:
    """Decode an ERC-20 ``Transfer`` event into :class:`TransferLog`.

    ``topics`` must contain the event signature plus the two indexed
    addresses: ``[sig, from, to]``. ``data`` holds the ``uint256`` amount.

    Raises :class:`TransferLogError` for anything that cannot be losslessly
    converted into a ``token_transfers`` row (malformed hex, wrong event
    signature, amount exceeding ``NUMERIC(38,0)`` capacity …).
    """
    if not isinstance(topics, list) or len(topics) < 3:
        found = len(topics) if isinstance(topics, list) else type(topics).__name__
        raise TransferLogError(
            f"expected at least 3 topics [sig, from, to], got {found}"
        )

    sig = _strip_0x(topics[0])
    if sig.lower() != TRANSFER_EVENT_SIG[2:].lower():
        raise TransferLogError(f"topic[0] is not the Transfer signature: {topics[0]!r}")

    from_address = _extract_address(topics[1], field="topics[1] (from)")
    to_address = _extract_address(topics[2], field="topics[2] (to)")

    amount_hex = _strip_0x(data)
    if not _HEX_WORD_RE.fullmatch(amount_hex):
        raise InvalidHexError(f"data: expected a 32-byte uint256 hex word, got {data!r}")
    amount_raw = Decimal(int(amount_hex, 16))
    if amount_overflow(amount_raw):
        raise DecodeError(f"transfer amount {amount_raw} exceeds NUMERIC(38,0) capacity")

    return TransferLog(
        from_address=from_address,
        to_address=to_address,
        amount_raw=amount_raw,
    )
