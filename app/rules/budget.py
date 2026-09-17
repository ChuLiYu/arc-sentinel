"""Rule 1 — budget_exceeded (SPEC §9).

An agent whose ``outbound_usdc`` accumulated over a period exceeds its
``budgets.limit_usdc`` produces a ``warning``; exceeding 120% of the limit
escalates to ``critical``.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Literal

from app.rules.common import USDC_QUANTUM, AlertDraft

Period = Literal["daily", "weekly", "monthly"]
VALID_PERIODS = frozenset({"daily", "weekly", "monthly"})

CRITICAL_MULTIPLIER = Decimal("1.20")  # > 120% ⇒ critical


def check_budget(
    *,
    agent_id: uuid.UUID,
    period: Period,
    limit_usdc: Decimal,
    outbound_usdc: Decimal,
) -> list[AlertDraft]:
    """Compare accumulated outbound USDC against the budget limit.

    Returns a single alert (the most severe applicable), or ``[]`` when the
    agent stayed within budget. Amounts are rounded to 6 dp (ROUND_DOWN) so a
    raw wei fraction can never push a value past a boundary by rounding.
    """
    if period not in VALID_PERIODS:
        raise ValueError(f"period must be one of {sorted(VALID_PERIODS)}, got {period!r}")

    limit = limit_usdc.quantize(USDC_QUANTUM)
    outbound = outbound_usdc.quantize(USDC_QUANTUM)

    if outbound <= limit:
        return []

    if outbound > (limit * CRITICAL_MULTIPLIER).quantize(USDC_QUANTUM):
        severity: Literal["critical", "warning"] = "critical"
        message = (
            f"budget exceeded critically: {outbound} USDC outbound in {period} "
            f"> 120% of limit {limit} USDC"
        )
    else:
        severity = "warning"
        message = (
            f"budget exceeded: {outbound} USDC outbound in {period} "
            f"> limit {limit} USDC"
        )

    return [
        AlertDraft(
            agent_id=agent_id,
            alert_type="budget_exceeded",
            severity=severity,
            message=message,
        )
    ]
