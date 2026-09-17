"""Shared value types for the rules engine (SPEC §9).

Every rule is a pure function: transaction data + aggregation in, zero or
more :class:`AlertDraft` out. No I/O, no LLM judgement — deterministic and
unit-testable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class AlertDraft:
    """A candidate alert produced by a rule (persisted later by the caller)."""

    agent_id: uuid.UUID
    alert_type: str
    severity: str  # info | warning | critical
    message: str
    tx_hash: str | None = None


USDC_QUANTUM = Decimal("0.000001")
"""NUMERIC(20,6) quantum — all comparisons are rounded to it (ROUND_DOWN)."""
