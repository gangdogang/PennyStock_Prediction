from __future__ import annotations

import csv
import json
import os
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.db import (
    create_snapshot_run,
    init_database,
    insert_filings,
    insert_historical_minute_bars,
    insert_universe_candidates,
)
from penny_stock_radar.models import FilingMatch, HistoricalMinuteBar, UniverseCandidate
from penny_stock_radar.services.multiday_catalyst_replay import (
    CatalystEntryRule,
    CatalystExitRule,
    run_multiday_catalyst_replay,
)

EASTERN = ZoneInfo("America/New_York")


def test_multiday_catalyst_replay_matches_golden_and_excludes_untradable(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    _seed_multiday_fixture(db_path)

    export_dir = run_multiday_catalyst_replay(
        db_path=db_path,
        market_date_start=date(2026, 4, 1),
        market_date_end=date(2026, 4, 7),
        entry_rule=CatalystEntryRule(
            entry_time="d_plus_1_open",
            require_sec_filing_within_days=1,
            min_dollar_volume_d=1_000_000,
        ),
        exit_rule=CatalystExitRule(
            max_holding_days=5,
            exit_on_volume_exhaustion=True,
            exit_on_follow_on_filing=True,
            structure_stop="d_minus_1_low",
        ),
        universe_filter={
            "export_root": tmp_path / "replays",
            "run_id": "fixture",
            "require_kis_tradable": True,
        },
    )

    snapshot = {
        "summary": _stable_summary(export_dir / "replay_summary.json"),
        "entries": _read_csv_rows(export_dir / "catalyst_entries.csv"),
        "trades": _read_csv_rows(export_dir / "catalyst_trades.csv"),
    }
    _assert_matches_golden("fixture_snapshot.json", snapshot)

    symbols = {row["symbol"] for row in snapshot["entries"]}
    assert symbols == {"AAA", "STOP"}
    assert "BBB" not in symbols
    assert {row["exit_reason"] for row in snapshot["trades"]} == {
        "structure_stop",
        "volume_exhaustion",
    }


def test_multiday_catalyst_replay_has_zero_entries_without_catalyst(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    _seed_multiday_fixture(db_path, include_filings=False)

    export_dir = run_multiday_catalyst_replay(
        db_path=db_path,
        market_date_start=date(2026, 4, 1),
        market_date_end=date(2026, 4, 7),
        entry_rule=CatalystEntryRule(entry_time="d_close", min_dollar_volume_d=1_000),
        exit_rule=CatalystExitRule(structure_stop="n_day_low", structure_stop_param=2),
        universe_filter={"export_root": tmp_path / "replays", "run_id": "no_catalyst"},
    )

    summary = json.loads((export_dir / "replay_summary.json").read_text(encoding="utf-8"))
    assert summary["entry_count"] == 0
    assert _read_csv_rows(export_dir / "catalyst_entries.csv") == []


def test_run_multiday_catalyst_replay_cli_writes_artifacts(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    _seed_multiday_fixture(db_path)

    result = CliRunner().invoke(
        app,
        [
            "run-multiday-catalyst-replay",
            "--db-path",
            str(db_path),
            "--export-root",
            str(tmp_path / "cli_replays"),
            "--run-id",
            "cli_fixture",
            "--start-date",
            "2026-04-01",
            "--end-date",
            "2026-04-07",
            "--entry-time",
            "d_plus_1_open",
            "--structure-stop",
            "d_minus_1_low",
        ],
    )

    assert result.exit_code == 0, result.stdout
    export_dir = tmp_path / "cli_replays" / "cli_fixture"
    assert (export_dir / "run_manifest.json").exists()
    assert (export_dir / "progress.json").exists()
    assert (export_dir / "replay_summary.json").exists()
    assert "Multiday catalyst replay wrote" in result.stdout


def _seed_multiday_fixture(db_path: Path, *, include_filings: bool = True) -> None:
    init_database(db_path)
    for market_date in ("2026-04-01", "2026-04-02", "2026-04-03", "2026-04-06"):
        snapshot = create_snapshot_run(
            db_path,
            source="fixture",
            symbol_count=4,
            market_date=market_date,
            snapshot_role="point_in_time",
            point_in_time_tag=f"fixture_{market_date}",
        )
        insert_universe_candidates(
            db_path,
            snapshot.snapshot_id,
            [
                _candidate("AAA", "NASDAQ"),
                _candidate("STOP", "NASDAQ"),
                _candidate("CCC", "NASDAQ"),
                _candidate("BBB", "OTC_PINK"),
            ],
        )
        if market_date == "2026-04-01" and include_filings:
            insert_filings(
                db_path,
                snapshot.snapshot_id,
                [
                    _filing("AAA", "2026-04-01", "8-K", "catalyst filing"),
                    _filing("STOP", "2026-04-01", "8-K", "stop fixture filing"),
                    _filing("BBB", "2026-04-01", "8-K", "untradable filing"),
                ],
            )
    insert_historical_minute_bars(db_path, _fixture_bars())


def _candidate(symbol: str, exchange: str) -> UniverseCandidate:
    return UniverseCandidate(
        symbol=symbol,
        company_name=f"{symbol} Corp",
        exchange=exchange,
        price=1.0,
        passed_filters=True,
        filter_reasons=[],
    )


def _filing(symbol: str, filing_date: str, form: str, summary: str) -> FilingMatch:
    return FilingMatch(
        symbol=symbol,
        cik=f"000{symbol}",
        form=form,
        filing_date=filing_date,
        acceptance_datetime=f"{filing_date}T07:30:00-04:00",
        accession_number=f"{symbol}-{filing_date}",
        filing_url=f"https://example.test/{symbol}/{filing_date}",
        matched_keywords=["catalyst"],
        summary=summary,
    )


def _fixture_bars() -> list[HistoricalMinuteBar]:
    rows: list[HistoricalMinuteBar] = []
    prices = {
        "AAA": {
            "2026-03-31": (1.00, 1.02, 0.90, 1.00, 100_000),
            "2026-04-01": (1.00, 1.30, 1.00, 1.20, 500_000),
            "2026-04-02": (1.25, 1.34, 1.15, 1.30, 400_000),
            "2026-04-03": (1.32, 1.45, 1.25, 1.40, 300_000),
            "2026-04-06": (1.35, 1.36, 1.30, 1.34, 50_000),
        },
        "STOP": {
            "2026-03-31": (1.10, 1.14, 1.00, 1.08, 100_000),
            "2026-04-01": (1.05, 1.28, 1.04, 1.20, 500_000),
            "2026-04-02": (1.20, 1.22, 0.95, 1.05, 300_000),
        },
        "CCC": {
            "2026-03-31": (1.00, 1.02, 0.96, 1.00, 100_000),
            "2026-04-01": (1.00, 1.08, 0.98, 1.02, 500_000),
            "2026-04-02": (1.02, 1.05, 1.00, 1.04, 500_000),
        },
        "BBB": {
            "2026-03-31": (1.00, 1.02, 0.96, 1.00, 100_000),
            "2026-04-01": (1.00, 1.25, 0.98, 1.20, 500_000),
            "2026-04-02": (1.20, 1.30, 1.18, 1.25, 500_000),
        },
    }
    for symbol, by_date in prices.items():
        for market_date, (open_price, high_price, low_price, close_price, volume) in by_date.items():
            day = date.fromisoformat(market_date)
            rows.extend(
                [
                    _bar(
                        symbol,
                        day,
                        time(9, 30),
                        open_price=open_price,
                        high_price=high_price,
                        low_price=low_price,
                        close_price=open_price,
                        volume=volume,
                    ),
                    _bar(
                        symbol,
                        day,
                        time(16, 0),
                        open_price=close_price,
                        high_price=high_price,
                        low_price=low_price,
                        close_price=close_price,
                        volume=volume,
                    ),
                ]
            )
    return rows


def _bar(
    symbol: str,
    market_date: date,
    at: time,
    *,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    volume: float,
) -> HistoricalMinuteBar:
    return HistoricalMinuteBar(
        symbol=symbol,
        market_date=market_date.isoformat(),
        market_phase="regular",
        timestamp=datetime.combine(market_date, at, tzinfo=EASTERN),
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        volume=volume,
        spread_pct=0.02,
        source="yfinance",
    )


def _stable_summary(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    grade = payload["replay_grade"]
    grade.pop("stamped_at", None)
    payload["replay_grade"] = grade
    payload["cost_audit"].pop("cost_source_policy", None)
    return payload


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.startswith("#")
    ]
    return list(csv.DictReader(lines))


def _assert_matches_golden(name: str, actual: dict[str, object]) -> None:
    path = Path(__file__).parent / "golden" / "multiday_catalyst" / name
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert actual == expected
