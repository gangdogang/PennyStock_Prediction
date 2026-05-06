from __future__ import annotations

from functools import lru_cache
import os
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

    market_scope: str = "penny"
    universe_price_min: float = 0.30
    universe_price_max: float = 5.00
    universe_max_market_cap: int = 250_000_000
    universe_max_float_shares: int = 30_000_000
    small_cap_price_max: float = 25.00
    small_cap_max_market_cap: int = 2_000_000_000
    small_cap_max_float_shares: int = 150_000_000
    universe_max_seed_symbols: int = 250
    universe_metadata_workers: int = 8
    filings_lookback_hours: int = 48
    watchlist_limit: int = 10
    watchlist_market_context_limit: int = 20
    replay_dir: Path = Path("data/replay")
    market_data_mode: str = "replay"
    live_market_provider: str = "kis"
    live_market_timeout_seconds: float = 10.0
    live_observability_enabled: bool = True
    live_observability_path: Path = Path("automation/logs/live_metrics.jsonl")
    backtest_coverage_report_dir: Path = Path("automation/state/backtest_coverage")
    backtest_coverage_gate_path: Path = Path("automation/state/backtest_coverage_gate_status.json")
    backtest_coverage_gate_pct: float = 60.0
    broker_adapter: str = "null"
    kis_base_url: str = "https://openapi.koreainvestment.com:9443"
    kis_mock_base_url: str = "https://openapivts.koreainvestment.com:29443"
    kis_websocket_url: str = "ws://ops.koreainvestment.com:21000"
    kis_market_div_code: str = "J"
    kis_custtype: str = "P"
    kis_mock_account_number: str | None = None
    kis_mock_account_product_code: str | None = "01"
    kis_mock_contact_phone: str = "01000000000"
    kis_mock_order_server_code: str = "0"
    kis_mock_order_type: str = "00"
    kis_mock_balance_exchange_code: str = "NASD"
    kis_mock_balance_currency_code: str = "USD"
    kis_mock_inquiry_exchange_code: str = ""
    kis_nasdaq_master_path: Path = Path("data/kis_master/NASMST.COD")
    kis_nyse_master_path: Path = Path("data/kis_master/NYSMST.COD")
    kis_amex_master_path: Path = Path("data/kis_master/AMSMST.COD")
    premarket_min_dollar_volume: float = 100_000.0
    premarket_min_trade_count: int = 25
    premarket_max_spread_pct: float = 0.08
    regular_opening_range_minutes: int = 5
    regular_extension_z_threshold: float = 2.0
    paper_trading_enabled: bool = True
    paper_use_setup_registry: bool = True
    paper_use_pyramid_v2: bool = True
    paper_trade_dir: Path = Path("sample_outputs/paper_trading")
    paper_initial_capital: float = 10_000.0
    paper_max_open_positions: int = 3
    paper_entry_size_fraction: float = 0.20
    paper_add_size_fraction: float = 0.10
    multiday_starter_entry_fraction: float = 0.05
    multiday_winner_add_fraction: float = 0.04
    multiday_hold_score_threshold: float = 55.0
    multiday_day2_survival_threshold: float = 45.0
    multiday_replacement_min_edge: float = 15.0
    multiday_max_adds: int = 2
    paper_add_trigger_pct: float = 3.0
    paper_max_adds_per_position: int = 1
    paper_entry_score_min: float = 2.75
    paper_news_entry_score_min: float = 2.75
    paper_stop_loss_pct: float = 5.0
    paper_adaptive_max_open_positions: int = 5
    paper_adaptive_predicted_entry_size_fraction: float = 0.06
    paper_adaptive_live_exception_entry_size_fraction: float = 0.04
    paper_adaptive_time_stop_minutes: int = 30
    paper_adaptive_time_stop_min_progress_pct: float = 3.0
    paper_adaptive_partial_take_profit_pct: float = 10.0
    paper_adaptive_partial_exit_fraction: float = 0.50
    paper_profit_activation_pct: float = 6.0
    paper_trailing_stop_pct: float = 3.0
    paper_fill_slippage_pct: float = 0.15
    paper_stop_gap_slippage_pct: float = 0.50
    paper_premarket_spread_slippage_multiplier: float = 1.50
    paper_regular_spread_slippage_multiplier: float = 1.00
    paper_halt_resume_spread_slippage_multiplier: float = 2.00
    paper_halt_resume_decay_minutes: float = 3.0
    paper_min_trade_fee: float = 1.0
    paper_notional_fee_rate_pct: float = 0.10
    paper_volume_cap_large_dollar_volume: float = 10_000_000.0
    paper_volume_cap_mid_dollar_volume: float = 2_000_000.0
    paper_volume_cap_large_pct: float = 10.0
    paper_volume_cap_mid_pct: float = 5.0
    paper_volume_cap_small_pct: float = 2.0
    paper_volume_cap_small_market_cap_threshold: int = 500_000_000
    paper_volume_cap_small_market_cap_scale: float = 0.5
    paper_capacity_limited_pct: float = 2.0
    paper_participation_slippage_enabled: bool = True
    paper_participation_slippage_soft_pct: float = 1.0
    paper_participation_slippage_mid_pct: float = 2.0
    paper_participation_slippage_hard_pct: float = 5.0
    paper_participation_slippage_mid_penalty_pct: float = 0.10
    paper_participation_slippage_hard_penalty_pct: float = 0.75
    paper_participation_slippage_extreme_penalty_pct: float = 2.75
    paper_ai_consensus_enabled: bool = True
    paper_ai_consensus_limit: int = 3
    paper_ai_refresh_seconds: int = 180
    paper_ai_escalation_enabled: bool = True
    paper_ai_escalation_model: str = "gemini-3.1-pro-preview"
    paper_ai_escalation_limit: int = 1
    paper_ai_escalation_min_confidence: float = 45.0
    paper_ai_escalation_max_confidence: float = 75.0
    paper_predictor_weight_k1: float = 1.0
    paper_predictor_weight_k2: float = 1.0
    predictor_weight_total_score: float = 11.0
    predictor_weight_catalyst: float = 8.0
    predictor_weight_technical: float = 4.0
    predictor_weight_sympathy: float = 3.0
    predictor_weight_market_context: float = 4.0
    predictor_weight_social: float = 2.0
    predictor_weight_low_float_bonus: float = 3.0
    predictor_weight_filing_bonus: float = 6.0
    predictor_weight_multi_theme_bonus: float = 4.0
    predictor_max_hold_days_tier3_total: float = 6.5
    predictor_max_hold_days_tier3_catalyst: float = 1.0
    predictor_max_hold_days_tier2_total: float = 4.5
    trade_plan_per_trade_risk_pct: float = 0.35
    trade_plan_starter_notional_cap_pct: float = 8.0
    trade_plan_add_notional_cap_pct: float = 5.0
    trade_plan_daily_loss_lock_pct: float = 2.0
    trade_plan_max_concurrent_open_risk_pct: float = 1.0
    trade_plan_stale_data_seconds: int = 15
    trade_plan_halt_suspected_seconds: int = 60

    exclude_etfs: bool = True
    exclude_preferred: bool = True
    exclude_warrants: bool = True
    exclude_rights: bool = True
    exclude_spacs: bool = True

    kis_app_key: str | None = Field(default=None, repr=False)
    kis_app_secret: str | None = Field(default=None, repr=False)
    alpaca_api_key: str | None = Field(default=None, repr=False)
    alpaca_secret_key: str | None = Field(default=None, repr=False)
    alpaca_data_base_url: str = "https://data.alpaca.markets"
    alpaca_market_data_feed: str = "iex"
    kis_mock_app_key: str | None = Field(default=None, repr=False)
    kis_mock_app_secret: str | None = Field(default=None, repr=False)
    reddit_client_id: str | None = Field(default=None, repr=False)
    reddit_client_secret: str | None = Field(default=None, repr=False)
    stocktwits_access_token: str | None = Field(default=None, repr=False)
    x_bearer_token: str | None = Field(default=None, repr=False)
    gemini_api_key: str | None = Field(default=None, repr=False)
    gemini_model: str = "gemini-3-flash-preview"
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    @property
    def database_path(self) -> Path:
        return self.db_path

    @property
    def universe_min_price(self) -> float:
        return self.universe_price_min

    @property
    def universe_max_price(self) -> float:
        return self.universe_price_max

    @property
    def market_scope_label(self) -> str:
        labels = {
            "penny": "Penny",
            "small_cap": "Small Cap",
            "all": "All Market",
        }
        return labels.get(self.market_scope, self.market_scope.title())

    @property
    def scope_price_max(self) -> float | None:
        if self.market_scope == "all":
            return None
        if self.market_scope == "small_cap":
            return self.small_cap_price_max
        return self.universe_price_max

    @property
    def scope_market_cap_max(self) -> int | None:
        if self.market_scope == "all":
            return None
        if self.market_scope == "small_cap":
            return self.small_cap_max_market_cap
        return self.universe_max_market_cap

    @property
    def scope_float_shares_max(self) -> int | None:
        if self.market_scope == "all":
            return None
        if self.market_scope == "small_cap":
            return self.small_cap_max_float_shares
        return self.universe_max_float_shares

    def in_price_scope(self, price: float | None) -> bool:
        if price is None or price < self.universe_price_min:
            return False
        upper_bound = self.scope_price_max
        return upper_bound is None or price <= upper_bound

    def market_cap_in_scope(self, market_cap: int | None) -> bool:
        limit = self.scope_market_cap_max
        return market_cap is None or limit is None or market_cap <= limit

    def float_shares_in_scope(self, float_shares: int | None) -> bool:
        limit = self.scope_float_shares_max
        return float_shares is None or limit is None or float_shares <= limit

    def supports_news_driven_entries(self) -> bool:
        return self.market_scope in {"small_cap", "all"}

    def can_escalate_ai_review(self) -> bool:
        return (
            self.paper_ai_escalation_enabled
            and bool(self.paper_ai_escalation_model)
            and self.paper_ai_escalation_model != self.gemini_model
        )

    def setup_registry_enabled(self) -> bool:
        value = os.getenv("PSR_USE_SETUP_REGISTRY")
        if value is None:
            return self.paper_use_setup_registry
        return value.strip().lower() not in {"0", "false", "no", "off"}

    def pyramid_v2_enabled(self) -> bool:
        value = os.getenv("PSR_USE_PYRAMID")
        if value is None:
            return self.paper_use_pyramid_v2
        return value.strip().lower() not in {"0", "false", "no", "off"}

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
        "watchlist_market_context_limit",
        "premarket_min_trade_count",
        "regular_opening_range_minutes",
        "small_cap_max_market_cap",
        "small_cap_max_float_shares",
        "paper_max_open_positions",
        "paper_adaptive_max_open_positions",
        "paper_max_adds_per_position",
        "paper_ai_consensus_limit",
        "paper_ai_refresh_seconds",
        "paper_ai_escalation_limit",
        "paper_adaptive_time_stop_minutes",
        "trade_plan_stale_data_seconds",
        "trade_plan_halt_suspected_seconds",
        "paper_volume_cap_small_market_cap_threshold",
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
        "paper_initial_capital",
        "paper_entry_size_fraction",
        "paper_add_size_fraction",
        "paper_add_trigger_pct",
        "paper_entry_score_min",
        "paper_news_entry_score_min",
        "paper_stop_loss_pct",
        "paper_adaptive_predicted_entry_size_fraction",
        "paper_adaptive_live_exception_entry_size_fraction",
        "paper_adaptive_time_stop_min_progress_pct",
        "paper_adaptive_partial_take_profit_pct",
        "paper_adaptive_partial_exit_fraction",
        "paper_profit_activation_pct",
        "paper_trailing_stop_pct",
        "paper_fill_slippage_pct",
        "paper_stop_gap_slippage_pct",
        "paper_premarket_spread_slippage_multiplier",
        "paper_regular_spread_slippage_multiplier",
        "paper_halt_resume_spread_slippage_multiplier",
        "paper_halt_resume_decay_minutes",
        "paper_min_trade_fee",
        "paper_notional_fee_rate_pct",
        "paper_volume_cap_large_dollar_volume",
        "paper_volume_cap_mid_dollar_volume",
        "paper_volume_cap_large_pct",
        "paper_volume_cap_mid_pct",
        "paper_volume_cap_small_pct",
        "paper_volume_cap_small_market_cap_scale",
        "small_cap_price_max",
        "paper_ai_escalation_min_confidence",
        "paper_ai_escalation_max_confidence",
        "trade_plan_per_trade_risk_pct",
        "trade_plan_starter_notional_cap_pct",
        "trade_plan_add_notional_cap_pct",
        "trade_plan_daily_loss_lock_pct",
        "trade_plan_max_concurrent_open_risk_pct",
        "backtest_coverage_gate_pct",
    )
    @classmethod
    def _positive_floats(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Expected a positive float.")
        return value

    @field_validator(
        "paper_predictor_weight_k1",
        "paper_predictor_weight_k2",
        "multiday_starter_entry_fraction",
        "multiday_winner_add_fraction",
        "multiday_hold_score_threshold",
        "multiday_day2_survival_threshold",
        "multiday_replacement_min_edge",
        "predictor_weight_total_score",
        "predictor_weight_catalyst",
        "predictor_weight_technical",
        "predictor_weight_sympathy",
        "predictor_weight_market_context",
        "predictor_weight_social",
        "predictor_weight_low_float_bonus",
        "predictor_weight_filing_bonus",
        "predictor_weight_multi_theme_bonus",
        "predictor_max_hold_days_tier3_total",
        "predictor_max_hold_days_tier3_catalyst",
        "predictor_max_hold_days_tier2_total",
    )
    @classmethod
    def _nonnegative_floats(cls, value: float) -> float:
        if value < 0:
            raise ValueError("Expected a non-negative float.")
        return value

    @field_validator("multiday_max_adds")
    @classmethod
    def _nonnegative_integers(cls, value: int) -> int:
        if value < 0:
            raise ValueError("Expected a non-negative integer.")
        return value

    @field_validator(
        "market_scope",
        "live_market_provider",
        "broker_adapter",
        "kis_base_url",
        "kis_mock_base_url",
        "kis_websocket_url",
        "gemini_model",
        "paper_ai_escalation_model",
        "gemini_api_base_url",
    )
    @classmethod
    def _nonempty_strings(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Expected a non-empty string.")
        return value.strip().lower()

    @field_validator("market_scope")
    @classmethod
    def _known_market_scope(cls, value: str) -> str:
        normalized = value.strip().lower()
        allowed = {"penny", "small_cap", "all"}
        if normalized not in allowed:
            raise ValueError(f"Expected one of {sorted(allowed)}.")
        return normalized

    @field_validator("broker_adapter")
    @classmethod
    def _known_broker_adapter(cls, value: str) -> str:
        normalized = value.strip().lower()
        allowed = {"null", "kis_mock"}
        if normalized not in allowed:
            raise ValueError(f"Expected one of {sorted(allowed)}.")
        return normalized

    @field_validator("live_market_provider")
    @classmethod
    def _known_live_market_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        allowed = {"kis", "disabled", "auto"}
        if normalized not in allowed:
            raise ValueError(f"Expected one of {sorted(allowed)}.")
        return normalized

    @field_validator("kis_market_div_code")
    @classmethod
    def _known_kis_market_div_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        allowed = {"J", "US", "NAS", "NYS", "AMS"}
        if normalized not in allowed:
            raise ValueError(f"Expected one of {sorted(allowed)}.")
        return normalized

    @field_validator("kis_custtype")
    @classmethod
    def _known_kis_custtype(cls, value: str) -> str:
        normalized = value.strip().upper()
        allowed = {"P", "B"}
        if normalized not in allowed:
            raise ValueError(f"Expected one of {sorted(allowed)}.")
        return normalized

    def ensure_directories(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.replay_dir.mkdir(parents=True, exist_ok=True)
        self.paper_trade_dir.mkdir(parents=True, exist_ok=True)
        self.kis_nasdaq_master_path.parent.mkdir(parents=True, exist_ok=True)
        self.live_observability_path.parent.mkdir(parents=True, exist_ok=True)
        self.backtest_coverage_report_dir.mkdir(parents=True, exist_ok=True)
        self.backtest_coverage_gate_path.parent.mkdir(parents=True, exist_ok=True)


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
