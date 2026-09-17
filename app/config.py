"""Application settings loaded from environment variables / .env."""

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for Arc Sentinel.

    Values may be provided via environment variables or a local ``.env`` file
    (see ``.env.example``). Variable names follow SPEC §17 credentials map:

    * ``ARC_MAINNET_RPC_URL``            — primary RPC (Alchemy mainnet)
    * ``ARC_MAINNET_RPC_FALLBACK_URL``   — dRPC mainnet fallback
    * ``ARC_TESTNET_RPC_URL``            — CI / integration tests
    * ``SENTINEL_API_KEY_SALT``          — hash salt for self-issued API keys

    The legacy ``RPC_URL`` / ``API_KEY_SALT`` names are still accepted so an
    existing ``.env`` keeps working. Never commit real secrets.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,  # accept both the field name and the env alias
    )

    # --- Database -------------------------------------------------------
    database_url: str = "postgresql+psycopg://arc_sentinel:arc_sentinel@localhost:5432/arc_sentinel"

    # --- Indexer (defaults point at Arc Testnet, chain 5042002) ---------
    # SPEC §17: primary RPC is Alchemy mainnet in production.
    rpc_url: str = Field(
        default="https://rpc.testnet.arc.io",
        validation_alias=AliasChoices("ARC_MAINNET_RPC_URL", "RPC_URL"),
    )
    # SPEC §17 / Phase 2.5: dRPC fallback, empty => no automatic failover.
    rpc_fallback_url: str = Field(
        default="",
        validation_alias=AliasChoices("ARC_MAINNET_RPC_FALLBACK_URL", "RPC_FALLBACK_URL"),
    )
    # SPEC §17: testnet URL used only by CI integration tests.
    testnet_rpc_url: str = Field(
        default="https://rpc.testnet.arc.io",
        validation_alias=AliasChoices("ARC_TESTNET_RPC_URL", "TESTNET_RPC_URL"),
    )
    chain_id: int = 5042002
    poll_interval_seconds: float = 3.0
    confirmations: int = 2  # only index up to head - N blocks

    # --- Indexer RPC client (SPEC §2/§4/§13/§20.7) ----------------------
    rpc_timeout_seconds: float = 15.0
    rpc_max_retries: int = 5
    rpc_backoff_base_seconds: float = 1.0  # delays: 1s → 2s → 4s → … capped
    rpc_backoff_max_seconds: float = 60.0  # generic + rate-limit retry ceiling
    rpc_max_consecutive_failures: int = 5  # → internal_rpc_down alert (§13)
    # SPEC §20.7 — HTTP 429 is transient throttling, not an outage: it gets a
    # longer, steeper backoff (15s → 30s → 60s → … capped) and never counts
    # toward the provider-down threshold.
    rpc_rate_limit_backoff_base_seconds: float = 15.0
    # SPEC §20.7 — minimum time on the fallback provider before the primary may
    # be probed again (extended after a 429 storm), preventing ping-ponging.
    rpc_min_fallback_stay_seconds: float = 120.0
    # Alchemy Free tier caps eth_getLogs block range at 10; use a small chunk.
    rpc_logs_max_range_size: int = 10  # eth_getLogs chunk width (blocks)
    # SPEC §20.7 — one catch-up cycle indexes at most this many blocks so a big
    # backlog never exhausts a free-tier provider's CU budget in a single poll.
    max_blocks_per_cycle: int = Field(
        default=80,
        validation_alias=AliasChoices("MAX_BLOCKS_PER_CYCLE"),
    )
    usdc_address: str = "0x3600000000000000000000000000000000000000"

    # --- Rules runner (SPEC §20.6) ---------------------------------------
    rules_run_interval_seconds: float = 60.0  # how often the rules loop re-evaluates

    # --- API ------------------------------------------------------------
    # SPEC §17: self-issued API keys are stored hashed in `api_keys` table
    # using this salt. Legacy API_KEY_HASH env remains for the preset key.
    api_key_salt: str = Field(
        default="",
        validation_alias=AliasChoices("SENTINEL_API_KEY_SALT", "API_KEY_SALT"),
    )
    api_key_hash: str = ""  # sha256 hex of the write-scope X-API-Key (empty => disabled)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (importable as ``settings``)."""
    return Settings()


settings = get_settings()
