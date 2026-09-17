"""Deterministic rule engine (SPEC §9).

All rules are pure functions producing :class:`AlertDraft` records. The engine
itself only aggregates them; it never makes probabilistic judgements.
"""

from __future__ import annotations

from app.rules.anomaly import check_failed_tx_spike, check_gas_spike
from app.rules.budget import check_budget
from app.rules.common import AlertDraft
from app.rules.recipient import check_blocked_recipient, check_unknown_recipient

__all__ = [
    "AlertDraft",
    "check_blocked_recipient",
    "check_budget",
    "check_failed_tx_spike",
    "check_gas_spike",
    "check_unknown_recipient",
]
