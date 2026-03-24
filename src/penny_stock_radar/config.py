from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="PENNY_STOCK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_path: Path = Path("data/penny_stock_radar.sqlite3")
    cache_dir: Path = Path("data/cache")
    log_level: str = "INFO"
    use_mock_market_data: bool = True

    nasdaq_trader_url: str = (
        "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqtraded.txt"
    )
    sec_company_tickers_url: str = "https://www.sec.gov/files/company_tickers.json"
    sec_submissions_url_template: str = (
        "https://data.sec.gov/submissions/CIK{cik}.json"
    )
    sec_archive_filing_url_template: str = (
        "https://www.sec.gov/Archives/edgar/data/{cik_number}/{accession_no}/{document}"
    )
    sec_user_agent: str = "PennyStockRadar/0.1 research-tool@example.com"
    sec_contact_email: str | None = None

    universe_price_min: float = 0.30
    universe_price_max: float = 5.00
    universe_max_market_cap: int = 250_000_000
    universe_max_float_shares: int = 30_000_000
    universe_max_seed_symbols: int = 250
    universe_metadata_workers: int = 8
    filings_lookback_hours: int = 48
    watchlist_limit: int = 10
    replay_dir: Path = Path("data/replay")
    market_data_mode: str = "replay"
    live_market_provider: str = "auto"
    live_market_timeout_seconds: float = 10.0
    polygon_base_url: str = "https://api.polygon.io"
    alpaca_data_base_url: str = "https://data.alpaca.markets"
    alpaca_market_data_feed: str = "iex"
    premarket_min_dollar_volume: float = 100_000.0
    premarket_min_trade_count: int = 25
    premarket_max_spread_pct: float = 0.08
    regular_opening_range_minutes: int = 5
    regular_extension_z_threshold: float = 2.0

    exclude_etfs: bool = True
    exclude_preferred: bool = True
    exclude_warrants: bool = True
    exclude_rights: bool = True
    exclude_spacs: bool = True

    polygon_api_key: str | None = Field(default=None, repr=False)
    alpaca_api_key: str | None = Field(default=None, repr=False)
    alpaca_secret_key: str | None = Field(default=None, repr=False)
    reddit_client_id: str | None = Field(default=None, repr=False)
    reddit_client_secret: str | None = Field(default=None, repr=False)
    stocktwits_access_token: str | None = Field(default=None, repr=False)
    x_bearer_token: str | None = Field(default=None, repr=False)

    @property
    def database_path(self) -> Path:
        return self.db_path

    @property
    def universe_min_price(self) -> float:
        return self.universe_price_min

    @property
    def universe_max_price(self) -> float:
        return self.universe_price_max

    @field_validator("universe_price_min", "universe_price_max")
    @classmethod
    def _prices_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Price filters must be positive.")
        return value

    @field_validator(
        "universe_max_seed_symbols",
        "universe_metadata_workers",
        "filings_lookback_hours",
        "watchlist_limit",
        "premarket_min_trade_count",
        "regular_opening_range_minutes",
    )
    @classmethod
    def _positive_integers(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Expected a positive integer.")
        return value

    @field_validator(
        "premarket_min_dollar_volume",
        "premarket_max_spread_pct",
        "regular_extension_z_threshold",
        "live_market_timeout_seconds",
    )
    @classmethod
    def _positive_floats(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Expected a positive float.")
        return value

    @field_validator("live_market_provider", "alpaca_market_data_feed")
    @classmethod
    def _nonempty_strings(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Expected a non-empty string.")
        return value.strip().lower()

    def ensure_directories(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.replay_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    settings = AppSettings()
    settings.ensure_directories()
    return settings


Settings = AppSettings


def load_settings() -> AppSettings:
    settings = AppSettings()
    settings.ensure_directories()
    return settings
