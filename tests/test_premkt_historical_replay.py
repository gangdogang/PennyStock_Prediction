from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    create_snapshot_run,
    init_database,
    insert_historical_minute_bars,
    insert_universe_candidates,
    insert_watchlist,
)
from penny_stock_radar.models import HistoricalMinuteBar, UniverseCandidate, WatchlistEntry
from penny_stock_radar.services.premkt_historical_replay import validate_replay_paths
from penny_stock_radar.services.premkt_model_training import PremktModelArtifact
from penny_stock_radar.services.premkt_training_dataset import FEATURE_VERSION

EASTERN = ZoneInfo("America/New_York")


class VolumeProbabilityModel:
    classes_ = [0, 1]

    def predict_proba(self, features):
        probabilities = []
        for _, row in features.iterrows():
            probability = min(float(row["feature_premarket_volume"]) / 1000.0, 1.0)
            probabilities.append([1.0 - probability, probability])
        return probabilities


def test_replay_cli_rejects_live_db_path(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-premkt-model-replay",
            "--start-date",
            "2026-04-10",
            "--end-date",
            "2026-04-10",
            "--db-path",
            "data/penny_stock_radar.sqlite3",
            "--export-dir",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 1
    assert "Refusing to use live DB" in result.stdout


def test_replay_rejects_paper_trading_export_dir(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"

    with pytest.raises(ValueError, match="sample_outputs/paper_trading"):
        validate_replay_paths(
            db_path=db_path,
            export_dir=Path("sample_outputs/paper_trading/replay"),
        )


def test_replay_writes_manifest_progress_summary_and_bucket_outputs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = _build_replay_fixture_db(tmp_path)
    model_path = _write_model_artifact(tmp_path)
    export_dir = tmp_path / "replays" / "run_fixture"
    monkeypatch.setattr(
        "penny_stock_radar.cli.get_settings",
        lambda: AppSettings(db_path=tmp_path / "unused.sqlite3", live_market_provider="disabled"),
    )

    result = CliRunner().invoke(
        app,
        [
            "run-premkt-model-replay",
            "--start-date",
            "2026-04-10",
            "--end-date",
            "2026-04-10",
            "--db-path",
            str(db_path),
            "--model-path",
            str(model_path),
            "--score-mode",
            "blend",
            "--ml-weight",
            "0.5",
            "--export-dir",
            str(export_dir),
        ],
    )

    assert result.exit_code == 0, result.stdout
    manifest = json.loads((export_dir / "run_manifest.json").read_text(encoding="utf-8"))
    progress = json.loads((export_dir / "progress.json").read_text(encoding="utf-8"))
    summary = json.loads((export_dir / "replay_summary.json").read_text(encoding="utf-8"))
    predictions = list(csv.DictReader((export_dir / "premkt_predictions_replay.csv").open(encoding="utf-8")))
    kpis = list(csv.DictReader((export_dir / "paper_backtest_kpis.csv").open(encoding="utf-8")))
    pair_diff = list(csv.DictReader((export_dir / "paper_bucket_pair_diff.csv").open(encoding="utf-8")))
    trade_rows = list(csv.DictReader((export_dir / "paper_trade_log.csv").open(encoding="utf-8")))
    setup_features = list(csv.DictReader((export_dir / "paper_setup_features.csv").open(encoding="utf-8")))
    setup_kpis = list(csv.DictReader((export_dir / "paper_setup_state_kpis.csv").open(encoding="utf-8")))
    setup_transitions = list(
        csv.DictReader((export_dir / "paper_setup_transition_matrix.csv").open(encoding="utf-8"))
    )
    add_trim_runner = list(
        csv.DictReader((export_dir / "paper_add_trim_runner_diagnostics.csv").open(encoding="utf-8"))
    )

    assert manifest["local_only"] is True
    assert manifest["db_path"] == str(db_path)
    assert "Future bars are used only" in " ".join(manifest["leakage_guard"])
    date_progress = progress["dates"]["2026-04-10"]
    assert date_progress["status"] == "completed"
    assert date_progress["elapsed_seconds"] >= 0
    assert date_progress["loaded_bar_rows"] == 4
    assert date_progress["loaded_symbol_count"] == 1
    assert date_progress["simulated_time_count"] == 2
    assert summary["conclusion_status"] == "smoke_only"
    assert summary["conclusion_status"] != "comparable"
    assert set(summary["bucket_results"]) == {
        "predictor_weighted",
        "momentum_only",
        "watchlist_blind_momentum",
    }
    assert summary["bucket_results"]["predictor_weighted"]["trade_count"] == 1
    assert summary["bucket_results"]["predictor_weighted"]["total_net_pnl"] == pytest.approx(32.68255)
    assert summary["bucket_results"]["momentum_only"]["trade_count"] == 1
    assert summary["bucket_results"]["momentum_only"]["total_net_pnl"] == pytest.approx(28.60225)
    assert summary["bucket_results"]["watchlist_blind_momentum"]["trade_count"] == 1
    assert summary["bucket_results"]["watchlist_blind_momentum"]["total_net_pnl"] == pytest.approx(18.4015)
    assert summary["diagnostic_artifacts"]["exit_path_diagnostics"] == "paper_exit_path_diagnostics.csv"
    assert summary["diagnostic_artifacts"]["setup_features"] == "paper_setup_features.csv"
    assert {row["bucket"] for row in kpis} == {
        "predictor_weighted",
        "momentum_only",
        "watchlist_blind_momentum",
    }
    assert {
        (row["left_bucket"], row["right_bucket"])
        for row in pair_diff
    } >= {
        ("predictor_weighted", "momentum_only"),
        ("predictor_weighted", "watchlist_blind_momentum"),
    }
    assert [row["symbol"] for row in predictions] == ["AAA"]
    session_exit = next(
        row
        for row in trade_rows
        if row["bucket"] == "predictor_weighted" and row["event"] == "EXIT"
    )
    assert session_exit["exit_reason"] == "session_end"
    assert session_exit["entry_analysis_label"] == "OPENING_RANGE_CANDIDATE"
    assert session_exit["exit_analysis_label"] == "OPENING_RANGE_CANDIDATE"
    assert session_exit["entry_setup_state"]
    assert session_exit["entry_action_bias"] in {"starter_ok", "watch", "no_trade"}
    assert session_exit["exit_setup_state"]
    assert float(session_exit["net_pnl"]) == pytest.approx(32.68255)
    assert session_exit["intrabar_stop_touched"] == "1"
    assert session_exit["stop_trigger_basis"] == "intrabar_low_only"
    assert any(
        row["bucket"] == "predictor_weighted"
        and row["symbol"] == "AAA"
        and row["setup_state"]
        and row["judge_json"]
        for row in setup_features
    )
    assert any(row["bucket"] == "predictor_weighted" and row["entry_setup_state"] for row in setup_kpis)
    assert any(
        row["bucket"] == "predictor_weighted"
        and row["entry_setup_state"]
        and row["exit_setup_state"]
        for row in setup_transitions
    )
    assert any(
        row["bucket"] == "predictor_weighted"
        and row["setup_state"]
        and int(row["observation_count"]) >= 1
        for row in add_trim_runner
    )


def test_replay_model_scoring_uses_only_pre_cutoff_and_not_future_dates(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = _build_replay_fixture_db(tmp_path)
    model_path = _write_model_artifact(tmp_path)
    export_dir = tmp_path / "replays" / "run_cutoff"
    monkeypatch.setattr(
        "penny_stock_radar.cli.get_settings",
        lambda: AppSettings(db_path=tmp_path / "unused.sqlite3", live_market_provider="disabled"),
    )

    result = CliRunner().invoke(
        app,
        [
            "run-premkt-model-replay",
            "--start-date",
            "2026-04-10",
            "--end-date",
            "2026-04-10",
            "--db-path",
            str(db_path),
            "--model-path",
            str(model_path),
            "--score-mode",
            "ml",
            "--export-dir",
            str(export_dir),
        ],
    )

    assert result.exit_code == 0, result.stdout
    rows = list(csv.DictReader((export_dir / "premkt_predictions_replay.csv").open(encoding="utf-8")))

    assert [row["symbol"] for row in rows] == ["AAA"]
    assert float(rows[0]["ml_score"]) == 30.0
    assert float(rows[0]["final_score"]) == 30.0
    assert rows[0]["score_mode"] == "ml"


def test_replay_resume_skips_completed_dates(monkeypatch, tmp_path: Path) -> None:
    db_path = _build_replay_fixture_db(tmp_path)
    export_dir = tmp_path / "replays" / "run_resume"
    monkeypatch.setattr(
        "penny_stock_radar.cli.get_settings",
        lambda: AppSettings(db_path=tmp_path / "unused.sqlite3", live_market_provider="disabled"),
    )
    base_args = [
        "run-premkt-model-replay",
        "--start-date",
        "2026-04-10",
        "--end-date",
        "2026-04-10",
        "--db-path",
        str(db_path),
        "--score-mode",
        "rule",
        "--export-dir",
        str(export_dir),
    ]

    first = CliRunner().invoke(app, base_args)
    second = CliRunner().invoke(app, [*base_args, "--resume"])

    assert first.exit_code == 0, first.stdout
    assert second.exit_code == 0, second.stdout
    progress = json.loads((export_dir / "progress.json").read_text(encoding="utf-8"))
    trade_rows = list(csv.DictReader((export_dir / "paper_trade_log.csv").open(encoding="utf-8")))
    setup_rows = list(csv.DictReader((export_dir / "paper_setup_features.csv").open(encoding="utf-8")))
    assert progress["dates"]["2026-04-10"]["status"] == "completed"
    assert len(trade_rows) > 0
    assert len(trade_rows) == len({(row["bucket"], row["symbol"], row["event"]) for row in trade_rows})
    assert len(setup_rows) > 0
    assert len(setup_rows) == len(
        {
            (row["bucket"], row["symbol"], row["observed_at"], row["position_state"])
            for row in setup_rows
        }
    )


def test_replay_trade_log_uses_entry_label_for_exit_attribution(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = _build_replay_fixture_db(tmp_path, exit_close=1.0)
    export_dir = tmp_path / "replays" / "run_entry_label"
    monkeypatch.setattr(
        "penny_stock_radar.cli.get_settings",
        lambda: AppSettings(db_path=tmp_path / "unused.sqlite3", live_market_provider="disabled"),
    )

    result = CliRunner().invoke(
        app,
        [
            "run-premkt-model-replay",
            "--start-date",
            "2026-04-10",
            "--end-date",
            "2026-04-10",
            "--db-path",
            str(db_path),
            "--score-mode",
            "rule",
            "--export-dir",
            str(export_dir),
        ],
    )

    assert result.exit_code == 0, result.stdout
    trade_rows = list(csv.DictReader((export_dir / "paper_trade_log.csv").open(encoding="utf-8")))
    exit_row = next(
        row
        for row in trade_rows
        if row["bucket"] == "predictor_weighted" and row["event"] == "EXIT"
    )
    label_kpis = list(csv.DictReader((export_dir / "paper_entry_label_kpis.csv").open(encoding="utf-8")))
    label_matrix = list(csv.DictReader((export_dir / "paper_entry_exit_label_matrix.csv").open(encoding="utf-8")))
    stop_diagnostics = list(csv.DictReader((export_dir / "paper_stop_out_diagnostics.csv").open(encoding="utf-8")))
    symbol_losses = list(csv.DictReader((export_dir / "paper_symbol_loss_concentration.csv").open(encoding="utf-8")))
    hold_buckets = list(csv.DictReader((export_dir / "paper_hold_bucket_kpis.csv").open(encoding="utf-8")))
    path_diagnostics = list(csv.DictReader((export_dir / "paper_exit_path_diagnostics.csv").open(encoding="utf-8")))

    assert exit_row["exit_reason"] == "stop_loss"
    assert exit_row["analysis_label"] == "OPENING_RANGE_CANDIDATE"
    assert exit_row["entry_analysis_label"] == "OPENING_RANGE_CANDIDATE"
    assert exit_row["exit_analysis_label"] == "WAIT_PULLBACK"
    assert float(exit_row["holding_minutes"]) == pytest.approx(1.0)
    assert exit_row["fill_reference_price"]
    assert exit_row["fill_slippage_pct"]
    assert exit_row["estimated_capacity_at_1pct_volume"]
    assert float(exit_row["entry_stop_price"]) < float(exit_row["entry_price"])
    assert float(exit_row["risk_per_share"]) > 0
    assert float(exit_row["mfe_pct_intrabar"]) > 0
    assert float(exit_row["mae_pct_intrabar"]) < 0
    assert float(exit_row["max_r_multiple"]) > 1.0
    assert float(exit_row["min_r_multiple"]) < 0
    assert exit_row["reached_0_5r"] == "1"
    assert exit_row["reached_1r"] == "1"
    assert float(exit_row["first_1r_minutes"]) == pytest.approx(1.0)
    assert exit_row["intrabar_stop_touched"] == "1"
    assert exit_row["stop_trigger_basis"] == "close"
    assert float(exit_row["giveback_from_mfe_pct"]) > 0
    assert any(
        row["bucket"] == "predictor_weighted"
        and row["analysis_label"] == "OPENING_RANGE_CANDIDATE"
        and row["stop_loss_count"] == "1"
        and row["quick_stop_3m_count"] == "1"
        for row in label_kpis
    )
    assert any(
        row["bucket"] == "predictor_weighted"
        and row["entry_analysis_label"] == "OPENING_RANGE_CANDIDATE"
        and row["exit_analysis_label"] == "WAIT_PULLBACK"
        and row["exit_reason"] == "stop_loss"
        and row["trade_count"] == "1"
        for row in label_matrix
    )
    assert any(
        row["bucket"] == "predictor_weighted"
        and row["entry_analysis_label"] == "OPENING_RANGE_CANDIDATE"
        and row["exit_analysis_label"] == "WAIT_PULLBACK"
        and row["hold_bucket"] == "00_0_3m"
        and row["entry_score_bucket"] == "04_4_5_plus"
        and row["stop_loss_count"] == "1"
        and row["quick_stop_3m_count"] == "1"
        for row in stop_diagnostics
    )
    assert any(
        row["bucket"] == "predictor_weighted"
        and row["symbol"] == "AAA"
        and row["stop_loss_count"] == "1"
        and row["dominant_entry_analysis_label"] == "OPENING_RANGE_CANDIDATE"
        and row["dominant_exit_analysis_label"] == "WAIT_PULLBACK"
        for row in symbol_losses
    )
    assert any(
        row["bucket"] == "predictor_weighted"
        and row["hold_bucket"] == "00_0_3m"
        and row["stop_loss_count"] == "1"
        for row in hold_buckets
    )
    assert any(
        row["bucket"] == "predictor_weighted"
        and row["entry_analysis_label"] == "OPENING_RANGE_CANDIDATE"
        and row["exit_reason"] == "stop_loss"
        and row["hold_bucket"] == "00_0_3m"
        and row["reached_1r_count"] == "1"
        and row["stop_after_1r_count"] == "1"
        and row["intrabar_stop_touched_count"] == "1"
        for row in path_diagnostics
    )


def test_replay_cli_supports_label_and_predictor_effect_overrides(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = _build_replay_fixture_db(tmp_path)
    export_dir = tmp_path / "replays" / "run_overrides"
    monkeypatch.setattr(
        "penny_stock_radar.cli.get_settings",
        lambda: AppSettings(db_path=tmp_path / "unused.sqlite3", live_market_provider="disabled"),
    )

    result = CliRunner().invoke(
        app,
        [
            "run-premkt-model-replay",
            "--start-date",
            "2026-04-10",
            "--end-date",
            "2026-04-10",
            "--db-path",
            str(db_path),
            "--score-mode",
            "rule",
            "--export-dir",
            str(export_dir),
            "--entry-label",
            "CONDITIONAL_ENTRY",
            "--predictor-k1",
            "0",
            "--predictor-k2",
            "0",
            "--require-l1-quotes-for-entries",
            "--entry-score-upper-bound",
            "4.5",
            "--min-entry-time",
            "08:02",
            "--exit-label",
            "WAIT_PULLBACK",
            "--breakeven-stop-after-r",
            "1.0",
            "--max-entries-per-symbol-per-day",
            "1",
            "--cooldown-after-stop-minutes",
            "30",
        ],
    )

    assert result.exit_code == 0, result.stdout
    manifest = json.loads((export_dir / "run_manifest.json").read_text(encoding="utf-8"))
    trade_log = export_dir / "paper_trade_log.csv"

    assert manifest["settings_snapshot"]["paper_predictor_weight_k1"] == 0
    assert manifest["settings_snapshot"]["paper_predictor_weight_k2"] == 0
    assert manifest["entry_label_policy"]["include"] == ["CONDITIONAL_ENTRY"]
    assert manifest["entry_label_policy"]["require_l1_quotes_for_entries"] is True
    assert manifest["replay_ablation_policy"]["entry_score_upper_bound"] == 4.5
    assert manifest["replay_ablation_policy"]["min_entry_time"] == "08:02"
    assert manifest["replay_ablation_policy"]["exit_labels"] == ["WAIT_PULLBACK"]
    assert manifest["replay_ablation_policy"]["breakeven_stop_after_r"] == 1.0
    assert manifest["replay_ablation_policy"]["max_entries_per_symbol_per_day"] == 1
    assert manifest["replay_ablation_policy"]["cooldown_after_stop_minutes"] == 30.0
    assert trade_log.read_text(encoding="utf-8") == ""


def test_replay_breakeven_stop_after_r_moves_stop_to_entry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = _build_breakeven_fixture_db(tmp_path)
    export_dir = tmp_path / "replays" / "run_breakeven"
    monkeypatch.setattr(
        "penny_stock_radar.cli.get_settings",
        lambda: AppSettings(db_path=tmp_path / "unused.sqlite3", live_market_provider="disabled"),
    )

    result = CliRunner().invoke(
        app,
        [
            "run-premkt-model-replay",
            "--start-date",
            "2026-04-10",
            "--end-date",
            "2026-04-10",
            "--db-path",
            str(db_path),
            "--score-mode",
            "rule",
            "--export-dir",
            str(export_dir),
            "--breakeven-stop-after-r",
            "1.0",
        ],
    )

    assert result.exit_code == 0, result.stdout
    trade_rows = list(csv.DictReader((export_dir / "paper_trade_log.csv").open(encoding="utf-8")))
    path_diagnostics = list(csv.DictReader((export_dir / "paper_exit_path_diagnostics.csv").open(encoding="utf-8")))
    exit_row = next(
        row
        for row in trade_rows
        if row["bucket"] == "predictor_weighted" and row["event"] == "EXIT"
    )

    assert exit_row["exit_reason"] == "stop_loss"
    assert exit_row["breakeven_stop_active"] == "1"
    assert float(exit_row["breakeven_stop_after_r"]) == pytest.approx(1.0)
    assert float(exit_row["breakeven_stop_activated_minutes"]) == pytest.approx(1.0)
    assert float(exit_row["entry_stop_price"]) < float(exit_row["entry_price"])
    assert float(exit_row["initial_stop_price"]) == pytest.approx(float(exit_row["entry_stop_price"]))
    assert float(exit_row["final_stop_price"]) == pytest.approx(float(exit_row["entry_price"]))
    assert float(exit_row["risk_per_share"]) == pytest.approx(
        float(exit_row["entry_price"]) - float(exit_row["initial_stop_price"])
    )
    assert any(
        row["bucket"] == "predictor_weighted"
        and row["exit_reason"] == "stop_loss"
        and row["breakeven_stop_active_count"] == "1"
        and float(row["avg_breakeven_stop_activated_minutes"]) == pytest.approx(1.0)
        for row in path_diagnostics
    )


def test_replay_exit_label_ablation_closes_on_label_transition(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = _build_replay_fixture_db(tmp_path, exit_close=1.05)
    export_dir = tmp_path / "replays" / "run_label_exit"
    monkeypatch.setattr(
        "penny_stock_radar.cli.get_settings",
        lambda: AppSettings(
            db_path=tmp_path / "unused.sqlite3",
            live_market_provider="disabled",
            paper_stop_loss_pct=60.0,
        ),
    )

    result = CliRunner().invoke(
        app,
        [
            "run-premkt-model-replay",
            "--start-date",
            "2026-04-10",
            "--end-date",
            "2026-04-10",
            "--db-path",
            str(db_path),
            "--score-mode",
            "rule",
            "--export-dir",
            str(export_dir),
            "--exit-label",
            "WAIT_PULLBACK",
        ],
    )

    assert result.exit_code == 0, result.stdout
    trade_rows = list(csv.DictReader((export_dir / "paper_trade_log.csv").open(encoding="utf-8")))
    exit_row = next(
        row
        for row in trade_rows
        if row["bucket"] == "predictor_weighted" and row["event"] == "EXIT"
    )

    assert exit_row["exit_reason"] == "label_exit"
    assert exit_row["entry_analysis_label"] == "OPENING_RANGE_CANDIDATE"
    assert exit_row["exit_analysis_label"] == "WAIT_PULLBACK"


def _build_replay_fixture_db(tmp_path: Path, *, exit_close: float = 2.00) -> Path:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    snapshot = create_snapshot_run(
        db_path,
        source="historical",
        symbol_count=1,
        market_date="2026-04-10",
        snapshot_role="point_in_time",
        point_in_time_tag="replay_fixture",
    )
    future_snapshot = create_snapshot_run(
        db_path,
        source="historical",
        symbol_count=1,
        market_date="2026-04-11",
        snapshot_role="point_in_time",
        point_in_time_tag="future_fixture",
    )
    insert_universe_candidates(
        db_path,
        snapshot.snapshot_id,
        [_candidate("AAA", price=1.0)],
    )
    insert_universe_candidates(
        db_path,
        future_snapshot.snapshot_id,
        [_candidate("FUTR", price=1.0)],
    )
    insert_watchlist(
        db_path,
        snapshot.snapshot_id,
        [
            WatchlistEntry(
                symbol="AAA",
                total_score=4.0,
                catalyst_score=1.0,
                technical_score=1.0,
                sympathy_score=0.5,
                market_context_score=0.0,
                low_float_bonus=1.0,
                reasons=["fixture_watchlist"],
            )
        ],
    )
    insert_watchlist(
        db_path,
        future_snapshot.snapshot_id,
        [
            WatchlistEntry(
                symbol="FUTR",
                total_score=10.0,
                catalyst_score=2.0,
                technical_score=2.0,
                sympathy_score=1.0,
                low_float_bonus=1.0,
                reasons=["future_watchlist"],
            )
        ],
    )
    insert_historical_minute_bars(
        db_path,
        [
            _bar("AAA", "2026-04-10T07:58:00-04:00", 1.00, 1.08, 0.99, 1.05, 100),
            _bar("AAA", "2026-04-10T07:59:00-04:00", 1.05, 1.12, 1.04, 1.10, 200),
            _bar("AAA", "2026-04-10T08:00:00-04:00", 1.10, 2.00, 1.09, 1.90, 100_000),
            _bar("AAA", "2026-04-10T08:01:00-04:00", 1.90, 2.10, min(exit_close, 1.80), exit_close, 50_000),
            _bar("FUTR", "2026-04-11T07:59:00-04:00", 1.00, 4.00, 1.00, 4.00, 900_000),
        ],
    )
    return db_path


def _build_breakeven_fixture_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "breakeven.sqlite3"
    init_database(db_path)
    snapshot = create_snapshot_run(
        db_path,
        source="historical",
        symbol_count=1,
        market_date="2026-04-10",
        snapshot_role="point_in_time",
        point_in_time_tag="breakeven_fixture",
    )
    insert_universe_candidates(
        db_path,
        snapshot.snapshot_id,
        [_candidate("AAA", price=1.0)],
    )
    insert_watchlist(
        db_path,
        snapshot.snapshot_id,
        [
            WatchlistEntry(
                symbol="AAA",
                total_score=4.0,
                catalyst_score=1.0,
                technical_score=1.0,
                sympathy_score=0.5,
                market_context_score=0.0,
                low_float_bonus=1.0,
                reasons=["fixture_watchlist"],
            )
        ],
    )
    insert_historical_minute_bars(
        db_path,
        [
            _bar("AAA", "2026-04-10T07:58:00-04:00", 1.00, 1.08, 0.99, 1.05, 100),
            _bar("AAA", "2026-04-10T07:59:00-04:00", 1.05, 1.12, 1.04, 1.10, 200),
            _bar("AAA", "2026-04-10T08:00:00-04:00", 1.10, 1.95, 1.09, 1.90, 100_000),
            _bar("AAA", "2026-04-10T08:01:00-04:00", 1.90, 2.12, 1.90, 2.10, 90_000),
            _bar("AAA", "2026-04-10T08:02:00-04:00", 2.10, 2.11, 1.84, 1.88, 90_000),
        ],
    )
    return db_path


def _candidate(symbol: str, *, price: float) -> UniverseCandidate:
    return UniverseCandidate(
        symbol=symbol,
        company_name=f"{symbol} Corp",
        exchange="Q",
        price=price,
        market_cap=75_000_000,
        float_shares=6_000_000,
        sector="Biotech",
        passed_filters=True,
        filter_reasons=[],
    )


def _bar(
    symbol: str,
    timestamp: str,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    volume: float,
) -> HistoricalMinuteBar:
    bar_at = datetime.fromisoformat(timestamp).astimezone(EASTERN)
    return HistoricalMinuteBar(
        symbol=symbol,
        market_date=bar_at.date().isoformat(),
        market_phase="premarket",
        timestamp=bar_at,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        volume=volume,
        source="fixture",
    )


def _write_model_artifact(tmp_path: Path) -> Path:
    joblib = pytest.importorskip("joblib")
    model_path = tmp_path / "premkt_model.joblib"
    artifact = PremktModelArtifact(
        model=VolumeProbabilityModel(),
        feature_columns=["feature_premarket_volume"],
        target_column="label_winner",
        fill_values={"feature_premarket_volume": 0.0},
        model_type="fixture",
        created_at="2026-04-10T00:00:00+00:00",
        notes=["fixture"],
        feature_version=FEATURE_VERSION,
    )
    joblib.dump(artifact, model_path)
    return model_path
