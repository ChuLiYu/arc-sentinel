"""Unit tests for the deterministic rule engine (SPEC §9, Phase 3).

Every rule must be covered with at least one *exactly-at-the-boundary* case
and one *clearly-over* case, using ``Decimal`` throughout.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.rules.anomaly import check_failed_tx_spike, check_gas_spike
from app.rules.budget import check_budget
from app.rules.recipient import check_blocked_recipient, check_unknown_recipient

AGENT = uuid.uuid4()
TX = "0x" + "ab" * 32


# ---------------------------------------------------------------------------
# Rule 1 — budget_exceeded
# ---------------------------------------------------------------------------
class TestBudgetExceeded:
    def test_within_budget_no_alert(self):
        alerts = check_budget(
            agent_id=AGENT, period="daily", limit_usdc=Decimal("100.000000"),
            outbound_usdc=Decimal("99.999999"),
        )
        assert alerts == []

    def test_exactly_at_limit_no_alert(self):
        alerts = check_budget(
            agent_id=AGENT, period="daily", limit_usdc=Decimal("100.000000"),
            outbound_usdc=Decimal("100.000000"),
        )
        assert alerts == []

    def test_just_over_limit_warning(self):
        (a,) = check_budget(
            agent_id=AGENT, period="daily", limit_usdc=Decimal("100.000000"),
            outbound_usdc=Decimal("100.000001"),
        )
        assert a.alert_type == "budget_exceeded"
        assert a.severity == "warning"

    def test_at_120_percent_is_still_warning(self):
        (a,) = check_budget(
            agent_id=AGENT, period="weekly", limit_usdc=Decimal("100.000000"),
            outbound_usdc=Decimal("120.000000"),
        )
        assert a.severity == "warning"

    def test_over_120_percent_critical(self):
        (a,) = check_budget(
            agent_id=AGENT, period="weekly", limit_usdc=Decimal("100.000000"),
            outbound_usdc=Decimal("120.000001"),
        )
        assert a.severity == "critical"

    def test_monthly_big_overshoot_critical(self):
        (a,) = check_budget(
            agent_id=AGENT, period="monthly", limit_usdc=Decimal("500.00"),
            outbound_usdc=Decimal("5000.00"),
        )
        assert a.severity == "critical"

    @pytest.mark.parametrize("bad", ["hourly", "yearly", "every-second", ""])
    def test_invalid_period_raises(self, bad: str):
        with pytest.raises(ValueError):
            check_budget(
                agent_id=AGENT, period=bad, limit_usdc=Decimal("1"),
                outbound_usdc=Decimal("2"),
            )


# ---------------------------------------------------------------------------
# Rule 2 — unknown_recipient
# ---------------------------------------------------------------------------
class TestUnknownRecipient:
    def test_no_allow_list_no_alert(self):
        assert check_unknown_recipient(
            agent_id=AGENT, tx_hash=TX, to_address="0xbbb", allow_list=None
        ) == []

    def test_empty_allow_list_flags_everything(self):
        (a,) = check_unknown_recipient(
            agent_id=AGENT, tx_hash=TX, to_address="0xbbb", allow_list=[]
        )
        assert a.alert_type == "unknown_recipient"
        assert a.severity == "warning"
        assert a.tx_hash == TX

    def test_known_recipient_no_alert(self):
        assert check_unknown_recipient(
            agent_id=AGENT, tx_hash=TX, to_address="0xAaBbCc",
            allow_list=["0xaabbcc"],
        ) == []

    def test_unknown_recipient_warning(self):
        (a,) = check_unknown_recipient(
            agent_id=AGENT, tx_hash=TX, to_address="0xDDD",
            allow_list=["0xaabbcc"],
        )
        assert a.alert_type == "unknown_recipient"
        assert a.severity == "warning"

    def test_address_case_insensitive(self):
        assert check_unknown_recipient(
            agent_id=AGENT, tx_hash=TX, to_address="0xABCdef123",
            allow_list=["0xabcdef123"],
        ) == []

    def test_null_recipient_flagged(self):
        (a,) = check_unknown_recipient(
            agent_id=AGENT, tx_hash=TX, to_address=None, allow_list=["0xaabbcc"]
        )
        assert a.alert_type == "unknown_recipient"


class TestBlockedRecipient:
    def test_blocked_recipient_critical(self):
        (a,) = check_blocked_recipient(
            agent_id=AGENT, tx_hash=TX, to_address="0xAAA", block_list=["0xaaa"]
        )
        assert a.alert_type == "blocked_recipient"
        assert a.severity == "critical"

    def test_not_blocked_no_alert(self):
        assert check_blocked_recipient(
            agent_id=AGENT, tx_hash=TX, to_address="0xaaa", block_list=["0xbbb"]
        ) == []

    def test_no_block_list_no_alert(self):
        assert check_blocked_recipient(
            agent_id=AGENT, tx_hash=TX, to_address="0xaaa", block_list=None
        ) == []

    def test_null_recipient_no_block_alert(self):
        assert check_blocked_recipient(
            agent_id=AGENT, tx_hash=TX, to_address=None, block_list=["0xaaa"]
        ) == []


# ---------------------------------------------------------------------------
# Rule 3 — failed_tx_spike
# ---------------------------------------------------------------------------
class TestFailedTxSpike:
    def test_below_threshold_no_alert(self):
        hashes = ["h1", "h2", "h3", "h4"]
        assert check_failed_tx_spike(agent_id=AGENT, failed_tx_hashes=hashes) == []

    def test_exactly_at_threshold_warning(self):
        (a,) = check_failed_tx_spike(
            agent_id=AGENT, failed_tx_hashes=[f"h{i}" for i in range(5)]
        )
        assert a.alert_type == "failed_tx_spike"
        assert a.severity == "warning"

    def test_well_over_threshold_warning(self):
        (a,) = check_failed_tx_spike(
            agent_id=AGENT, failed_tx_hashes=[f"h{i}" for i in range(20)]
        )
        assert a.alert_type == "failed_tx_spike"
        assert a.severity == "warning"

    def test_zero_failures_no_alert(self):
        assert check_failed_tx_spike(agent_id=AGENT, failed_tx_hashes=[]) == []

    def test_custom_threshold(self):
        assert check_failed_tx_spike(
            agent_id=AGENT, failed_tx_hashes=["h1"], threshold=2
        ) == []
        (a,) = check_failed_tx_spike(
            agent_id=AGENT, failed_tx_hashes=["h1", "h2"], threshold=2
        )
        assert a.alert_type == "failed_tx_spike"


# ---------------------------------------------------------------------------
# Rule 4 — gas_spike
# ---------------------------------------------------------------------------
class TestGasSpike:
    def test_within_multiplier_no_alert(self):
        assert check_gas_spike(
            agent_id=AGENT, tx_hash=TX, gas_fee_usdc=Decimal("0.000100"),
            avg_30d_gas_usdc=Decimal("0.000050"),
        ) == []

    def test_exactly_five_times_no_alert(self):
        assert check_gas_spike(
            agent_id=AGENT, tx_hash=TX, gas_fee_usdc=Decimal("0.000250"),
            avg_30d_gas_usdc=Decimal("0.000050"),
        ) == []

    def test_just_over_five_times_info(self):
        (a,) = check_gas_spike(
            agent_id=AGENT, tx_hash=TX, gas_fee_usdc=Decimal("0.000251"),
            avg_30d_gas_usdc=Decimal("0.000050"),
        )
        assert a.alert_type == "gas_spike"
        assert a.severity == "info"
        assert a.tx_hash == TX

    def test_zero_history_no_alert(self):
        assert check_gas_spike(
            agent_id=AGENT, tx_hash=TX, gas_fee_usdc=Decimal("0.100000"),
            avg_30d_gas_usdc=Decimal("0"),
        ) == []

    def test_large_spike_info(self):
        (a,) = check_gas_spike(
            agent_id=AGENT, tx_hash=TX, gas_fee_usdc=Decimal("5.000000"),
            avg_30d_gas_usdc=Decimal("0.000001"),
        )
        assert a.alert_type == "gas_spike"
        assert a.severity == "info"

    def test_custom_multiplier(self):
        assert check_gas_spike(
            agent_id=AGENT, tx_hash=TX, gas_fee_usdc=Decimal("0.001500"),
            avg_30d_gas_usdc=Decimal("0.0005"), multiplier=Decimal("3"),
        ) == []
        (a,) = check_gas_spike(
            agent_id=AGENT, tx_hash=TX, gas_fee_usdc=Decimal("0.001501"),
            avg_30d_gas_usdc=Decimal("0.0005"), multiplier=Decimal("3"),
        )
        assert a.alert_type == "gas_spike"
