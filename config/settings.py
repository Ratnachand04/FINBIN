from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"

    database_url: str = Field(default="postgresql+asyncpg://binfin:binfin@localhost:5432/binfin")
    redis_url: str = Field(default="redis://localhost:6379/0")

    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "binfin/1.0"

    news_api_key: str = ""
    etherscan_api_key: str = ""
    binance_api_key: str = ""
    binance_secret: str = ""
    blockcypher_api_key: str = ""

    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "mistral:7b-instruct"
    ollama_timeout_seconds: int = 30

    tracked_symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "DOGEUSDT"])

    reddit_rate_limit_per_minute: int = 60
    etherscan_rate_limit_per_second: int = 5
    news_rate_limit_per_minute: int = 30

    feature_sentiment_enabled: bool = True
    feature_prediction_enabled: bool = True
    feature_backtesting_enabled: bool = True
    feature_notifications_enabled: bool = True

    signal_min_confidence: float = 0.7
    signal_min_strength: float = 0.6

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in {"development", "staging", "production"}:
            raise ValueError("environment must be development|staging|production")
        return value

    @field_validator("tracked_symbols", mode="before")
    @classmethod
    def parse_symbols(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            symbols = [item.strip().upper() for item in value.split(",") if item.strip()]
            return symbols
        if isinstance(value, list):
            return [str(item).upper() for item in value]
        return ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]

    def is_production(self) -> bool:
        return self.environment == "production"

    def base_symbol(self, symbol: str) -> str:
        symbol = symbol.upper()
        if symbol.endswith("USDT"):
            return symbol[:-4]
        return symbol


@lru_cache
def get_settings() -> Settings:
    return Settings()
