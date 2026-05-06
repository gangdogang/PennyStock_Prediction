from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.db import (
    fetch_latest_setup_alerts,
    init_database,
    insert_setup_alerts,
)
from penny_stock_radar.services.setup_alerts import (
    ALERT_BLOCKED,
    ALERT_CANDIDATE,
    ALERT_DIAGNOSTIC,
    SetupAlertBus,
    SetupAlertClassifier,
    SetupAlertContext,
    write_setup_alert_exports,
)

EASTERN = ZoneInfo("America/New_York")


def _context(**overrides) -> SetupAlertContext:
    data = {
        "run_id": "run_setup_alerts",
        "source": "fixture",
        "market_date": "2026-05-07",
        "symbol": "ABCD",
        "observed_at": datetime(2026, 5, 7, 14, 15, tzinfo=EASTERN),
        "market_phase": "regular",
        "position_state": "flat",
        "last_price": 2.4,
        "vwap": 2.25,
        "distance_to_vwap_pct": 6.7,
        "pullback_depth_pct": 4.0,
        "volume_bar_ratio": 2.3,
        "spread_pct": None,
        "dollar_volume": 750_000.0,
        "analysis_label": "WAIT_PULLBACK",
        "analysis_score": 4.2,
        "setup_state": "VWAP_RECLAIM",
        "action_bias": "watch",
        "quality": 76,
        "risk": 42,
        "above_vwap": True,
        "vwap_reclaim": True,
        "hod_breakout": False,
        "opening_range_breakout": False,
        "failed_breakout": False,
        "pullback_hold": True,
        "volume_expansion": True,
        "true_leader": True,
        "spread_ok": True,
        "liquidity_ok": True,
        "dilution_risk_flag": False,
        "catalyst_risk_flag": False,
        "reasons": ("fixture",),
    }
    data.update(overrides)
    return SetupAlertContext(**data)


def test_afternoon_vwap_reclaim_is_diagnostic_without_execution_context() -> None:
    alerts = SetupAlertClassifier().classify(_context())

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.setup_id == "afternoon_vwap_reclaim"
    assert alert.alert_state == ALERT_DIAGNOSTIC
    assert "l1_spread_missing_cost_not_decision_grade" in alert.blocked_reasons
    assert "news_tier_missing" in alert.blocked_reasons
    assert "float_rotation_missing" in alert.blocked_reasons


def test_first_green_day_continuation_can_be_candidate_when_context_is_present() -> None:
    alerts = SetupAlertClassifier().classify(
        _context(
            observed_at=datetime(2026, 5, 7, 10, 15, tzinfo=EASTERN),
            setup_state="STARTER_VALID",
            pullback_hold=False,
            hod_breakout=True,
            day_state="D1",
            news_tier="Tier1",
            float_rotation=0.8,
            halt_code="NONE",
            spread_pct=1.2,
        )
    )

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.setup_id == "first_green_day_continuation"
    assert alert.alert_state == ALERT_CANDIDATE
    assert alert.required_data_grade == "full_setup_context_required"
    assert alert.blocked_reasons == ()


def test_day2_morning_panic_blocks_trap_news() -> None:
    alerts = SetupAlertClassifier().classify(
        _context(
            observed_at=datetime(2026, 5, 7, 10, 0, tzinfo=EASTERN),
            setup_state="PULLBACK_HOLD",
            pullback_depth_pct=14.0,
            day_state="D2",
            trap_reason="S-3 offering",
            spread_pct=1.4,
            float_rotation=0.6,
            news_tier="Tier2",
            halt_code="NONE",
        )
    )

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.setup_id == "day2_morning_panic"
    assert alert.alert_state == ALERT_BLOCKED
    assert "trap_news" in alert.blocked_reasons


def test_setup_alert_bus_reads_feature_csv_and_writes_exports(tmp_path: Path) -> None:
    features = tmp_path / "paper_setup_features.csv"
    with features.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "run_id",
                "market_date",
                "symbol",
                "observed_at",
                "market_phase",
                "position_state",
                "last_price",
                "vwap",
                "distance_to_vwap_pct",
                "pullback_depth_pct",
                "volume_bar_ratio",
                "spread_pct",
                "dollar_volume",
                "analysis_label",
                "analysis_score",
                "setup_state",
                "action_bias",
                "quality",
                "risk",
                "above_vwap",
                "vwap_reclaim",
                "hod_breakout",
                "opening_range_breakout",
                "failed_breakout",
                "pullback_hold",
                "volume_expansion",
                "true_leader",
                "spread_ok",
                "liquidity_ok",
                "dilution_risk_flag",
                "catalyst_risk_flag",
                "context_reasons",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "run_id": "csv_fixture",
                "market_date": "2026-05-07",
                "symbol": "ABCD",
                "observed_at": "2026-05-07T14:15:00-04:00",
                "market_phase": "regular",
                "position_state": "flat",
                "last_price": "2.4",
                "vwap": "2.25",
                "distance_to_vwap_pct": "6.7",
                "pullback_depth_pct": "4.0",
                "volume_bar_ratio": "2.3",
                "spread_pct": "",
                "dollar_volume": "750000",
                "analysis_label": "WAIT_PULLBACK",
                "analysis_score": "4.2",
                "setup_state": "VWAP_RECLAIM",
                "action_bias": "watch",
                "quality": "76",
                "risk": "42",
                "above_vwap": "1",
                "vwap_reclaim": "1",
                "hod_breakout": "0",
                "opening_range_breakout": "0",
                "failed_breakout": "0",
                "pullback_hold": "1",
                "volume_expansion": "1",
                "true_leader": "1",
                "spread_ok": "1",
                "liquidity_ok": "1",
                "dilution_risk_flag": "0",
                "catalyst_risk_flag": "0",
                "context_reasons": "fixture",
            }
        )

    alerts = SetupAlertBus().build_from_feature_csv(features)
    result = write_setup_alert_exports(alerts, output_root=tmp_path / "alerts")

    assert len(alerts) == 1
    assert result.csv_path.exists()
    assert result.json_path.exists()
    assert result.summary["metadata"]["decision_grade"] is False
    assert result.summary["alert_state_counts"] == {ALERT_DIAGNOSTIC: 1}


def test_setup_alerts_persist_to_database(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    alerts = SetupAlertClassifier().classify(_context())

    insert_setup_alerts(db_path, alerts, replace_run=True)
    rows = fetch_latest_setup_alerts(db_path, run_id="run_setup_alerts")

    assert len(rows) == 1
    assert rows[0]["setup_id"] == "afternoon_vwap_reclaim"
    assert rows[0]["alert_state"] == ALERT_DIAGNOSTIC


def test_build_setup_alerts_from_features_cli(tmp_path: Path) -> None:
    features = tmp_path / "paper_setup_features.csv"
    features.write_text(
        "\n".join(
            [
                "run_id,market_date,symbol,observed_at,market_phase,position_state,last_price,vwap,pullback_depth_pct,volume_bar_ratio,spread_pct,dollar_volume,analysis_label,analysis_score,setup_state,action_bias,quality,risk,above_vwap,vwap_reclaim,hod_breakout,opening_range_breakout,failed_breakout,pullback_hold,volume_expansion,true_leader,spread_ok,liquidity_ok,dilution_risk_flag,catalyst_risk_flag,context_reasons",
                "cli_fixture,2026-05-07,ABCD,2026-05-07T14:15:00-04:00,regular,flat,2.4,2.25,4.0,2.3,,750000,WAIT_PULLBACK,4.2,VWAP_RECLAIM,watch,76,42,1,1,0,0,0,1,1,1,1,1,0,0,fixture",
                "",
            ]
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "build-setup-alerts-from-features",
            "--features-csv",
            str(features),
            "--output-root",
            str(tmp_path / "cli_alerts"),
        ],
    )

    assert result.exit_code == 0
    assert "decision_grade=false" in result.stdout
    exported = list((tmp_path / "cli_alerts").glob("*/setup_alert_summary.json"))
    assert len(exported) == 1
    summary = json.loads(exported[0].read_text(encoding="utf-8"))
    assert summary["alert_count"] == 1
