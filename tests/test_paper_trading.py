from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    fetch_latest_paper_trading_run,
    fetch_latest_paper_strategy_runs,
    fetch_paper_orders,
    fetch_paper_positions,
    init_database,
)
from penny_stock_radar.models import MarketActivity
from penny_stock_radar.services.momentum_advisor import MomentumAdvice, MomentumAdviceBundle
from penny_stock_radar.services.paper_trading import PaperTradingCoordinator, PaperTradingEngine


def _activity(
    symbol: str,
    *,
    phase: str,
    price: float,
    label: str,
    score: float,
    pct_rank: int = 1,
    volume_rank: int = 1,
    predicted: bool = True,
    pct_change: float = 20.0,
    reasons: list[str] | None = None,
    leader_persistence_score: float = 0.0,
    pullback_absorption_score: float = 0.0,
    trap_score: float = 0.0,
    behavioral_score: float = 0.0,
) -> MarketActivity:
    return MarketActivity(
        symbol=symbol,
        market_phase=phase,
        source="test",
        last_price=price,
        previous_close=0.8,
        pct_change=pct_change,
        volume=500_000,
        dollar_volume=price * 500_000,
        trade_size=1_000,
        spread_pct=0.02,
        market_status="open",
        market_data_at=datetime(2026, 3, 26, 12, 0, tzinfo=timezone.utc),
        pct_rank=pct_rank,
        volume_rank=volume_rank,
        watchlist_rank=1 if predicted else None,
        watchlist_score=4.5 if predicted else None,
        predicted=predicted,
        leader_persistence_score=leader_persistence_score,
        pullback_absorption_score=pullback_absorption_score,
        trap_score=trap_score,
        behavioral_score=behavioral_score,
        analysis_label=label,
        analysis_score=score,
        reasons=reasons or ["watchlist_predicted", "live_dollar_volume_strong"],
    )


class FakeAdvisor:
    def __init__(self, stances: dict[str, tuple[str, float, str]]) -> None:
        self.stances = stances

    def review(self, *, market_phase, candidates, open_positions):
        items = {
            symbol: MomentumAdvice(
                symbol=symbol,
                stance=stance,
                conviction=conviction,
                note=note,
            )
            for symbol, (stance, conviction, note) in self.stances.items()
        }
        return MomentumAdviceBundle(summary=f"{market_phase} consensus", items=items)


def test_paper_trading_enters_adds_exits_and_exports_csv(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    export_dir = tmp_path / "paper_exports"
    init_database(db_path)

    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=export_dir,
        live_market_provider="disabled",
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    first = engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.00,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
            )
        ],
        export_csv=False,
    )
    assert first.entered_count == 1
    assert first.open_position_count == 1

    current_time = datetime(2026, 3, 26, 13, 40, tzinfo=timezone.utc)
    second = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=1.05,
                label="CONDITIONAL_ENTRY",
                score=4.8,
                pct_change=31.0,
            )
        ],
        export_csv=False,
    )
    assert second.added_count == 1

    current_time = datetime(2026, 3, 26, 13, 50, tzinfo=timezone.utc)
    engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=1.12,
                label="CONDITIONAL_ENTRY",
                score=4.9,
                pct_change=40.0,
            )
        ],
        export_csv=False,
    )

    current_time = datetime(2026, 3, 26, 14, 5, tzinfo=timezone.utc)
    final = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=1.08,
                label="NO_CHASE",
                score=2.0,
                pct_change=24.0,
            )
        ],
        export_csv=True,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    orders = fetch_paper_orders(db_path, run.run_id)

    assert final.exited_count == 1
    assert run.closed_trade_count == 1
    assert run.realized_pnl > 0
    assert run.win_rate == 100.0
    assert run.profit_factor >= 1.0
    assert run.reward_risk_ratio >= 1.0
    assert len(orders) == 3
    assert any(row.status == "CLOSED" for row in positions)

    orders_csv = export_dir / "paper_orders.csv"
    summary_csv = export_dir / "paper_run_summary.csv"
    curve_csv = export_dir / "paper_equity_curve.csv"
    assert orders_csv.exists()
    assert summary_csv.exists()
    assert curve_csv.exists()
    assert "AAA" in orders_csv.read_text(encoding="utf-8")


def test_paper_trading_closes_positions_after_session(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.00,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
            )
        ],
        export_csv=False,
    )

    current_time = datetime(2026, 3, 26, 21, 10, tzinfo=timezone.utc)
    result = engine.process_market_activity(
        market_phase="afterhours",
        activity=[],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    assert result.exited_count == 1
    assert run.closed_trade_count == 1
    assert all(row.status == "CLOSED" for row in positions)
    assert any(row.exit_reason == "session_closed" for row in positions)


def test_paper_trading_all_scope_can_enter_news_driven_leader_with_gemini_buy(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
        market_scope="all",
    )
    engine = PaperTradingEngine(
        settings,
        advisor=FakeAdvisor({"MEGA": ("buy", 0.82, "거래대금이 충분하고 아직 확장 여지 있음")}),
        now_fn=lambda: current_time,
    )

    result = engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "MEGA",
                phase="premarket",
                price=18.40,
                label="NEWS_CHECK_FIRST",
                score=3.4,
                pct_rank=1,
                volume_rank=2,
                predicted=False,
                pct_change=53.3,
            )
        ],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    orders = fetch_paper_orders(db_path, run.run_id)
    assert result.entered_count == 1
    assert any(row.status == "OPEN" and row.symbol == "MEGA" for row in positions)
    assert any("gemini_buy" in row.reasons for row in orders)


def test_paper_trading_can_enter_top5_reclaim_candidate(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    result = engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "LATE",
                phase="premarket",
                price=2.04,
                label="WAIT_PULLBACK",
                score=2.7,
                pct_rank=4,
                volume_rank=5,
                predicted=False,
                pct_change=18.0,
                reasons=["top5_live_leader", "leader_reclaim", "reclaim_entry_ready"],
                leader_persistence_score=61.0,
                pullback_absorption_score=69.0,
                trap_score=24.0,
                behavioral_score=72.0,
            )
        ],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    assert result.entered_count == 1
    assert any(row.symbol == "LATE" and row.status == "OPEN" for row in positions)


def test_paper_trading_coordinator_exports_strategy_comparison(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    export_dir = tmp_path / "paper_exports"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=export_dir,
        live_market_provider="disabled",
    )
    coordinator = PaperTradingCoordinator(settings, now_fn=lambda: current_time)

    result = coordinator.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.00,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
                pct_rank=2,
                volume_rank=3,
                leader_persistence_score=67.0,
                pullback_absorption_score=63.0,
                trap_score=18.0,
                behavioral_score=70.0,
            )
        ],
        export_csv=True,
    )

    comparison_csv = export_dir / "paper_strategy_comparison.csv"
    strategy_runs = fetch_latest_paper_strategy_runs(db_path, limit=10)
    assert result.comparison_path == comparison_csv.resolve()
    assert comparison_csv.exists()
    assert len(strategy_runs) >= 3
    csv_text = comparison_csv.read_text(encoding="utf-8")
    assert "auto_paper_v1" in csv_text
    assert "baseline_pct_leader_v1" in csv_text
