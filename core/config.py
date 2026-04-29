"""Application configuration via Pydantic Settings."""

from pydantic_settings import BaseSettings


class AppConfig(BaseSettings):
    database_url: str = "postgresql://harshit@localhost:5432/trading"
    log_level: str = "INFO"

    # Bot defaults
    default_timeframe: str = "1d"
    default_stake_amount: float = 10_000.0

    model_config = {"env_prefix": "TRADING_"}


config = AppConfig()
