from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ListingRecord(BaseModel):
    symbol: str
    company_name: str
    exchange: str
    realtime_symbol: str | None = None
    name_ko: str | None = None
    name_en: str | None = None
    currency: str | None = None
    security_type: str | None = None
    security_subtype_code: str | None = None
    base_price: float | None = None
    is_dr: bool = False
    industry_code: str | None = None
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
    market_date: str | None = None
    snapshot_role: str = "live"
    point_in_time_tag: str | None = None
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
    market_context_score: float = 0.0
    low_float_bonus: float
    social_score: float = 0.0
    themes: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class PremktPrediction(BaseModel):
    symbol: str
    score: float
    max_hold_days: int
    entry_rationale: str
    themes: list[str] = Field(default_factory=list)
    filing_summary: str = ""
    generated_at: datetime = Field(default_factory=utc_now)
    cutoff_at: datetime | None = None
    source: str = "premkt_prediction"
    market_date: str | None = None
    rule_score: float | None = None
    ml_score: float | None = None
    final_score: float | None = None
    score_mode: str = "rule"
    model_path: str | None = None
    model_id: str | None = None
    model_feature_version: str | None = None
    model_missing_feature_count: int = 0
    model_missing_features: list[str] = Field(default_factory=list)
    scoring_notes: list[str] = Field(default_factory=list)


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


class MarketActivity(BaseModel):
    symbol: str
    market_phase: str
    source: str
    market_cap: int | None = None
    last_price: float | None = None
    bid_price: float | None = None
    ask_price: float | None = None
    bar_high_price: float | None = None
    bar_low_price: float | None = None
    previous_close: float | None = None
    pct_change: float | None = None
    volume: float | None = None
    dollar_volume: float | None = None
    trade_size: int | None = None
    spread_pct: float | None = None
    market_status: str | None = None
    market_data_at: datetime | None = None
    data_age_seconds: float | None = None
    resume_elapsed_seconds: float | None = None
    has_live_trade: bool = False
    has_live_quote: bool = False
    pct_rank: int = 0
    volume_rank: int = 0
    watchlist_rank: int | None = None
    watchlist_score: float | None = None
    predicted: bool = False
    leader_persistence_score: float = 0.0
    pullback_absorption_score: float = 0.0
    trap_score: float = 0.0
    behavioral_score: float = 0.0
    analysis_label: str
    analysis_score: float
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class HistoricalL1Quote(BaseModel):
    symbol: str
    market_date: str
    timestamp: datetime
    bid_price: float | None = None
    ask_price: float | None = None
    last_price: float | None = None
    source: str
    created_at: datetime = Field(default_factory=utc_now)


class HistoricalMinuteBar(BaseModel):
    symbol: str
    market_date: str
    market_phase: str
    timestamp: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    bid_price: float | None = None
    ask_price: float | None = None
    spread_pct: float | None = None
    source: str
    created_at: datetime = Field(default_factory=utc_now)


class HistoricalHaltEvent(BaseModel):
    symbol: str
    market_date: str
    start_at: datetime
    end_at: datetime | None = None
    reason: str | None = None
    resume_price: float | None = None
    source: str
    inferred: bool = False
    notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class HistoricalCoverageReport(BaseModel):
    market_date: str
    dataset_kind: str
    source: str
    expected_symbol_count: int
    covered_symbol_count: int
    symbol_coverage_pct: float
    expected_interval_count: int
    covered_interval_count: int
    interval_coverage_pct: float
    notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class CoverageGateStatus(BaseModel):
    updated_at: datetime = Field(default_factory=utc_now)
    status: str
    gate_name: str
    market_date: str
    session: str
    dataset_kind: str
    source: str
    threshold_pct: float
    decision_basis: str
    expected_symbol_count: int
    covered_symbol_count: int
    symbol_coverage_pct: float
    expected_interval_count: int
    covered_interval_count: int
    interval_coverage_pct: float
    symbol_gate_passed: bool
    interval_gate_passed: bool
    gate_passed: bool
    report_created_at: datetime
    report_path: str | None = None
    notes: list[str] = Field(default_factory=list)
    last_error: str | None = None


class PredictionOutcome(BaseModel):
    symbol: str
    market_phase: str
    predicted: bool
    watchlist_rank: int | None = None
    watchlist_score: float | None = None
    pct_rank: int | None = None
    volume_rank: int | None = None
    pct_change: float | None = None
    volume: float | None = None
    dollar_volume: float | None = None
    analysis_label: str = ""
    outcome: str
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class PaperTradingRun(BaseModel):
    run_id: str
    strategy_name: str
    bucket: str = "predictor_weighted"
    status: str = "ACTIVE"
    initial_capital: float
    cash_balance: float
    equity: float
    equity_peak: float
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    closed_trade_count: int = 0
    winning_trade_count: int = 0
    losing_trade_count: int = 0
    win_rate: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    total_transaction_cost: float = 0.0
    profit_factor: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    reward_risk_ratio: float = 0.0
    last_phase: str | None = None
    last_market_date: str | None = None
    notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None


class PaperPosition(BaseModel):
    position_id: str
    run_id: str
    symbol: str
    status: str = "OPEN"
    entry_phase: str
    entry_label: str | None = None
    exit_reason: str | None = None
    quantity: int
    average_entry_price: float
    last_price: float | None = None
    cost_basis: float
    market_value: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0
    stop_price: float | None = None
    planned_stop_price: float | None = None
    planned_risk_pct: float | None = None
    highest_price: float | None = None
    add_count: int = 0
    partial_exit_count: int = 0
    strategy_bucket: str = ""
    fill_reference_price: float | None = None
    fill_slippage_pct: float | None = None
    fees_paid_total: float = 0.0
    day_regime: str | None = None
    watchlist_rank_at_entry: int | None = None
    entry_reasons: list[str] = Field(default_factory=list)
    exit_reasons: list[str] = Field(default_factory=list)
    opened_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    closed_at: datetime | None = None


class PaperOrder(BaseModel):
    order_id: str
    run_id: str
    position_id: str | None = None
    symbol: str
    market_phase: str
    action: str
    intent: str
    quantity: int
    requested_quantity: int | None = None
    remaining_quantity: int = 0
    fill_status: str = "FILLED"
    price: float
    notional: float
    transaction_cost: float = 0.0
    strategy_bucket: str = ""
    analysis_label: str | None = None
    analysis_score: float | None = None
    planned_stop_price: float | None = None
    planned_risk_pct: float | None = None
    fill_reference_price: float | None = None
    fill_slippage_pct: float | None = None
    bar_volume: float | None = None
    bar_dollar_volume: float | None = None
    shares_pct_of_bar_volume: float | None = None
    notional_pct_of_bar_dollar_volume: float | None = None
    estimated_capacity_at_1pct_volume: float | None = None
    estimated_capacity_at_2pct_volume: float | None = None
    capacity_limited: bool = False
    participation_slippage_pct: float = 0.0
    day_regime: str | None = None
    watchlist_rank_at_entry: int | None = None
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    realized_pnl: float | None = None
    realized_pnl_pct: float | None = None


class TradePlanCandidate(BaseModel):
    symbol: str
    market_phase: str
    bucket: str
    actionability: str
    entry_reference: float | None = None
    stop: float | None = None
    risk_per_share: float | None = None
    suggested_size: int = 0
    max_dollar_risk: float | None = None
    spread_pct: float | None = None
    data_age_seconds: float | None = None
    regime: str
    day_loss_locked: bool = False
    reasons: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class PaperRunSnapshot(BaseModel):
    run_id: str
    market_phase: str
    cash_balance: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    total_return_pct: float
    max_drawdown_pct: float
    open_position_count: int
    closed_trade_count: int
    win_rate: float
    profit_factor: float
    reward_risk_ratio: float
    notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
