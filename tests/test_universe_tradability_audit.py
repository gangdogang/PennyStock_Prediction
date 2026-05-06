from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.db import create_snapshot_run, init_database, insert_universe_candidates
from penny_stock_radar.models import UniverseCandidate
from penny_stock_radar.services.falsification_research import (
    FalsificationAuditOptions,
    FalsificationResearchAuditor,
)
from penny_stock_radar.services.universe_tradability_audit import audit_universe, write_report


def test_nasdaq_only_universe_is_not_blocked(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    _insert_pit_universe(
        db_path,
        market_date="2025-06-02",
        exchanges={"ABCD": "NASDAQ", "EFGH": "Q"},
    )
    replay_dir = _write_replay_log(
        tmp_path,
        "nasdaq_replay",
        [
            ("ABCD", "2025-06-02"),
            ("EFGH", "2025-06-02"),
        ],
    )

    report = audit_universe(
        db_path,
        "replay_log",
        {"replay_dir": replay_dir},
    )

    assert report.total_symbols == 2
    assert report.tradable_count == 2
    assert report.untradable_pct == 0
    assert report.decision_blocker is False
    assert report.decision_grade is True


def test_otc_pink_half_universe_blocks_transferability(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    _insert_pit_universe(
        db_path,
        market_date="2025-06-02",
        exchanges={"ABCD": "NASDAQ", "OTCP": "OTC Pink"},
    )
    replay_dir = _write_replay_log(
        tmp_path,
        "otc_replay",
        [
            ("ABCD", "2025-06-02"),
            ("OTCP", "2025-06-02"),
        ],
    )

    report = audit_universe(db_path, "replay_log", {"replay_dir": replay_dir})

    assert report.untradable_count == 1
    assert report.untradable_pct == 50.0
    assert report.decision_blocker is True
    assert report.grade_reason == "universe_kis_untradable_pct_high"


def test_unknown_listing_exchange_counts_toward_blocker(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    replay_dir = _write_replay_log(
        tmp_path,
        "unknown_replay",
        [("MYST", "2025-06-02")],
    )

    report = audit_universe(db_path, "replay_log", {"replay_dir": replay_dir})

    assert report.unknown_count == 1
    assert report.by_symbol[0].classification == "unknown"
    assert report.decision_blocker is True


def test_scanner_universe_uses_selected_scan_exchange_as_fallback(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    snapshot = create_snapshot_run(
        db_path,
        source="scanner_fixture",
        symbol_count=1,
        market_date="2025-06-02",
        snapshot_role="live",
        point_in_time_tag=None,
    )
    insert_universe_candidates(
        db_path,
        snapshot.snapshot_id,
        [
            UniverseCandidate(
                symbol="OTCP",
                company_name="OTCP Corp",
                exchange="OTC Pink",
                price=1.0,
                passed_filters=True,
                filter_reasons=[],
            )
        ],
    )

    report = audit_universe(
        db_path,
        "scanner_universe",
        {"scan_id": snapshot.snapshot_id, "passed_only": True},
    )

    assert report.untradable_count == 1
    assert report.by_symbol[0].evidence_source == "manual"
    assert report.decision_blocker is True


def test_june_2025_replay_log_fixture_writes_regression_artifacts(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    _insert_pit_universe(
        db_path,
        market_date="2025-06-02",
        exchanges={
            "ABCD": "NASDAQ",
            "EFGH": "NYSE",
            "OTCP": "OTC Pink",
            "MYST": "",
        },
    )
    replay_dir = _write_replay_log(
        tmp_path,
        "june_2025_sec_universe",
        [
            ("ABCD", "2025-06-02"),
            ("EFGH", "2025-06-03"),
            ("OTCP", "2025-06-03"),
            ("MYST", "2025-06-04"),
        ],
    )

    report = audit_universe(db_path, "replay_log", {"replay_dir": replay_dir})
    report_path = write_report(report, tmp_path / "tradability")

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    csv_text = (tmp_path / "tradability" / "universe_tradability_by_symbol.csv").read_text(
        encoding="utf-8"
    )
    md_text = (tmp_path / "tradability" / "universe_tradability_summary.md").read_text(
        encoding="utf-8"
    )
    assert payload["universe_id"] == "replay_log:june_2025_sec_universe"
    assert payload["total_symbols"] == 4
    assert payload["tradable_count"] == 2
    assert payload["untradable_count"] == 1
    assert payload["unknown_count"] == 1
    assert payload["decision_grade"] is False
    assert payload["grade_reason"] == "universe_kis_untradable_pct_high"
    assert "decision_grade" in csv_text
    assert "grade_reason" in md_text


def test_cli_audit_universe_tradability_writes_artifacts(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    _insert_pit_universe(db_path, market_date="2025-06-02", exchanges={"ABCD": "NASDAQ"})
    replay_dir = _write_replay_log(tmp_path, "cli_replay", [("ABCD", "2025-06-02")])

    result = CliRunner().invoke(
        app,
        [
            "audit-universe-tradability",
            "--db-path",
            str(db_path),
            "--universe-source",
            "replay_log",
            "--replay-dir",
            str(replay_dir),
            "--out-dir",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0
    assert (tmp_path / "out" / "universe_tradability_report.json").exists()
    assert "KIS tradable universe" in result.stdout


def test_falsification_audit_adds_kis_untradable_blocker(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    _insert_pit_universe(
        db_path,
        market_date="2025-06-02",
        exchanges={"ABCD": "NASDAQ", "OTCP": "OTC Pink"},
    )

    result = FalsificationResearchAuditor().run(
        options=FalsificationAuditOptions(
            db_path=db_path,
            export_root=tmp_path / "runs",
            run_id="kis_blocker",
            null_sample_count=1,
        )
    )

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert "universe_kis_untradable_pct_high" in report["blockers"]
    assert report["universe_tradability_audit"]["grade_reason"] == (
        "universe_kis_untradable_pct_high"
    )


def _insert_pit_universe(
    db_path: Path,
    *,
    market_date: str,
    exchanges: dict[str, str],
) -> None:
    snapshot = create_snapshot_run(
        db_path,
        source="historical_fixture",
        symbol_count=len(exchanges),
        market_date=market_date,
        snapshot_role="point_in_time",
        point_in_time_tag="fixture",
    )
    insert_universe_candidates(
        db_path,
        snapshot.snapshot_id,
        [
            UniverseCandidate(
                symbol=symbol,
                company_name=f"{symbol} Corp",
                exchange=exchange or "UNKNOWN",
                price=1.0,
                passed_filters=True,
                filter_reasons=[],
            )
            for symbol, exchange in exchanges.items()
        ],
    )


def _write_replay_log(
    tmp_path: Path,
    run_id: str,
    rows: list[tuple[str, str]],
) -> Path:
    replay_dir = tmp_path / run_id
    replay_dir.mkdir()
    lines = ["event,bucket,symbol,market_date,entry_at"]
    for symbol, market_date in rows:
        lines.append(
            f"ENTRY,predictor_weighted,{symbol},{market_date},{market_date}T09:30:00-04:00"
        )
    (replay_dir / "paper_trade_log.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return replay_dir
