"""Application settings loaded from environment variables / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for Arc Sentinel.

    Values may be provided via environment variables or a local ``.env`` file
    (see ``.env.example``). Never commit real secrets.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database -------------------------------------------------------
    database_url: str = "postgresql+psycopg://arc_sentinel:arc_sentinel@localhost:5432/arc_sentinel"

    # --- Indexer (defaults point at Arc Testnet, chain 5042002) ---------
    rpc_url: str = "https://rpc.testnet.arc.io"
    chain_id: int = 5042002
    poll_interval_seconds: float = 3.0
    confirmations: int = 2  # only index up to head - N blocks

    # --- Indexer RPC client (SPEC §2/§4/§13) ---------------------------
    rpc_timeout_seconds: float = 15.0
    rpc_max_retries: int = 5
    rpc_backoff_base_seconds: float = 1.0  # delays: 1s → 2s → 4s → … capped
    rpc_backoff_max_seconds: float = 30.0
    rpc_max_consecutive_failures: int = 5  # → internal_rpc_down alert (§13)
    rpc_logs_max_range_size: int = 2000  # eth_getLogs chunk width (blocks)
    usdc_address: str = "0x3600000000000000000000000000000000000000"

    # --- API ------------------------------------------------------------
    api_key_hash: str = ""  # sha256 hex of the write-scope X-API-Key (empty => disabled)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (importable as ``settings``)."""
    return Settings()


settings = get_settings()
