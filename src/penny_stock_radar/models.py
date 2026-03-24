from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ListingRecord(BaseModel):
    symbol: str
    company_name: str
    exchange: str
    is_etf: bool = False
    is_test_issue: bool = False
    is_nextshares: bool = False


class MetadataRecord(BaseModel):
    symbol: str
    price: float | None = None
    market_cap: int | None = None
    float_shares: int | None = None
    shares_outstanding: int | None = None
    quote_type: str | None = None
    sector: str | None = None
    industry: str | None = None


class UniverseCandidate(BaseModel):
    symbol: str
    company_name: str
    exchange: str
    quote_type: str | None = None
    price: float | None = None
    market_cap: int | None = None
    float_shares: int | None = None
    shares_outstanding: int | None = None
    sector: str | None = None
    industry: str | None = None
    recent_filings_summary: str | None = None
    passed_filters: bool = False
    filter_reasons: list[str] = Field(default_factory=list)
    last_updated_at: datetime = Field(default_factory=utc_now)


class SnapshotRun(BaseModel):
    snapshot_id: str
    source: str
    symbol_count: int
    created_at: datetime = Field(default_factory=utc_now)


class FilingMatch(BaseModel):
    symbol: str
    cik: str
    form: str
    filing_date: str
    acceptance_datetime: str | None = None
    accession_number: str
    primary_document: str | None = None
    primary_doc_description: str | None = None
    filing_url: str
    item_numbers: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    matched_keywords: list[str] = Field(default_factory=list)
    summary: str
    created_at: datetime = Field(default_factory=utc_now)


class SetupSignal(BaseModel):
    symbol: str
    close: float | None = None
    sma20: float | None = None
    atr10: float | None = None
    atr20: float | None = None
    above_sma20: bool = False
    volatility_contraction: bool = False
    setup_score: float = 0.0
    notes: list[str] = Field(default_factory=list)


class WatchlistEntry(BaseModel):
    symbol: str
    total_score: float
    catalyst_score: float
    technical_score: float
    sympathy_score: float
    low_float_bonus: float
    social_score: float = 0.0
    themes: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class MarketTick(BaseModel):
    timestamp: datetime
    symbol: str
    session: str
    price: float
    size: int
    bid: float
    ask: float
    side: str = "unknown"
    baseline_avg_volume: float | None = None


class PremarketSignal(BaseModel):
    symbol: str
    premarket_high: float
    premarket_low: float
    premarket_close: float
    premarket_vwap: float
    premarket_rvol: float
    dollar_volume: float
    trade_count: int
    tps: float
    size_mean: float
    size_std: float
    size_cv: float
    spread_pct: float
    round_level_break: bool
    tape_anomaly: bool
    quality_score: float
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class SessionDecision(BaseModel):
    symbol: str
    decision: str
    anchored_vwap: float
    opening_range_high: float
    opening_range_low: float
    regular_high: float
    regular_low: float
    regular_close: float
    extension_z: float
    mfe_pct: float
    mae_pct: float
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class ReplayEvaluation(BaseModel):
    label: str
    expectancy: float
    profit_factor: float
    precision_at_k: float
    average_mfe_pct: float
    average_mae_pct: float
    symbol_count: int
    created_at: datetime = Field(default_factory=utc_now)


class SocialSignal(BaseModel):
    symbol: str
    mention_count: int
    mention_velocity: float
    unique_authors: int
    cross_platform_count: int
    engagement_total: float
    social_score: float
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
