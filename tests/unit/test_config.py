"""Reject settings that could stall indexing or skip arbitrary ranges."""
import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.mark.parametrize("overrides", [
    {"start_block": -1}, {"confirmations": -1}, {"max_blocks_per_cycle": 0},
    {"rpc_logs_max_range_size": 0}, {"poll_interval_seconds": 0},
    {"chain_id": 0}, {"chain_id": 2**31},
])
def test_invalid_indexing_bounds_fail_at_startup(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)
