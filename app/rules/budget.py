"""Rule 1 — budget_exceeded (SPEC §9).

An agent whose ``outbound_usdc`` accumulated over a period exceeds its
``budgets.limit_usdc`` produces a ``warning``; exceeding 120% of the limit
escalates to ``critical``.
"""

from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from app.rules.common import USDC_QUANTUM, AlertDraft

Period = Literal["daily", "weekly", "monthly"]
VALID_PERIODS = frozenset({"daily", "weekly", "monthly"})

CRITICAL_MULTIPLIER = Decimal("1.20")  # > 120% ⇒ critical


def check_budget(
    *,
    agent_id: uuid.UUID,
    agent_name: str,
    period: Period,
    limit_usdc: Decimal,
    outbound_usdc: Decimal,
) -> list[AlertDraft]:
    """Compare accumulated outbound USDC against the budget limit.

    Returns a single alert (the most severe applicable), or ``[]`` when the
    agent stayed within budget. Amounts are rounded to 6 dp (ROUND_DOWN) so a
    raw wei fraction can never push a value past a boundary by rounding.
    ``agent_name`` is formatted into a human-readable message (SPEC §18).
    """
    if period not in VALID_PERIODS:
        raise ValueError(f"period must be one of {sorted(VALID_PERIODS)}, got {period!r}")

    limit = limit_usdc.quantize(USDC_QUANTUM)
    outbound = outbound_usdc.quantize(USDC_QUANTUM)

    if outbound <= limit:
        return []

    period_label = {"daily": "today", "weekly": "this week", "monthly": "this month"}[period]

    if outbound > (limit * CRITICAL_MULTIPLIER).quantize(USDC_QUANTUM):
        severity: Literal["critical", "warning"] = "critical"
        message = (
            f"Agent {agent_name} spent ${outbound:,.2f} {period_label}, "
            f"which is {_percent(outbound, limit)}% of the {period} budget of "
            f"${limit:,.2f} — this critically exceeds the 120% threshold."
        )
    else:
        severity = "warning"
        message = (
            f"Agent {agent_name} spent ${outbound:,.2f} {period_label}, "
            f"which is {_percent(outbound, limit)}% of the {period} budget of "
            f"${limit:,.2f}."
        )

    return [
        AlertDraft(
            agent_id=agent_id,
            alert_type="budget_exceeded",
            severity=severity,
            message=message,
        )
    ]


def _percent(outbound: Decimal, limit: Decimal) -> Decimal:
    """Format ``outbound`` as a percentage of ``limit`` (ROUND_HALF_UP, 1 dp).

    Safe against a zero limit: 0/0 → 0%, and any positive spend against a zero
    limit returns an arbitrarily large figure (callers never hit this because
    ``outbound <= limit`` short-circuits a zero limit, but keep it total-safe).
    """
    if limit == 0:
        return Decimal("0.0")
    return (outbound * Decimal("100") / limit).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
