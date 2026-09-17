"""Rules 3 & 4 — failed_tx_spike / gas_spike (SPEC §9)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from decimal import Decimal

from app.rules.common import USDC_QUANTUM, AlertDraft

FAILED_SPIKE_THRESHOLD = 5  # failed txs in the rolling 10-min window
GAS_SPIKE_MULTIPLIER = Decimal("5")  # vs. the agent's 30-day average


def check_failed_tx_spike(
    *,
    agent_id: uuid.UUID,
    failed_tx_hashes: Iterable[str],
    threshold: int = FAILED_SPIKE_THRESHOLD,
) -> list[AlertDraft]:
    """Flag a spike of failed transactions in a rolling window.

    The caller supplies the failed tx hashes observed in the last 10 minutes;
    ``threshold`` failed txs (default 5) triggers one ``warning``.
    """
    count = len(list(failed_tx_hashes))
    if count < threshold:
        return []
    return [
        AlertDraft(
            agent_id=agent_id,
            alert_type="failed_tx_spike",
            severity="warning",
            message=f"{count} failed transactions in rolling window (>= {threshold})",
        )
    ]


def check_gas_spike(
    *,
    agent_id: uuid.UUID,
    tx_hash: str,
    gas_fee_usdc: Decimal,
    avg_30d_gas_usdc: Decimal,
    multiplier: Decimal = GAS_SPIKE_MULTIPLIER,
) -> list[AlertDraft]:
    """Flag a single tx whose gas fee far exceeds the agent's 30-day average.

    Returns ``info`` when ``gas_fee_usdc > avg_30d * multiplier``. When the
    agent has no meaningful history (``avg_30d <= 0``) there is nothing to
    compare against → no alert. Threshold comparison uses exact Decimal math
    rounded to 6 dp (ROUND_DOWN), never float.
    """
    gas = gas_fee_usdc.quantize(USDC_QUANTUM)
    avg = avg_30d_gas_usdc.quantize(USDC_QUANTUM)

    if avg <= 0:
        return []

    if gas > avg * multiplier:
        return [
            AlertDraft(
                agent_id=agent_id,
                alert_type="gas_spike",
                severity="info",
                message=(
                    f"tx {tx_hash} gas fee {gas} USDC > "
                    f"{multiplier}x 30-day average {avg} USDC"
                ),
                tx_hash=tx_hash,
            )
        ]
    return []
