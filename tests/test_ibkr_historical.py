from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import fetch_historical_l1_quotes, init_database, insert_historical_l1_quotes
from penny_stock_radar.models import HistoricalL1Quote
from penny_stock_radar.services import ibkr_historical
from penny_stock_radar.services.external_data_validator import is_cost_eligible_source
from penny_stock_radar.services.ibkr_historical import (
    IBKR_NBBO_SOURCE,
    IbkrBackfillSummary,
    IbkrHistoricalConfig,
    backfill_ibkr_historical_quotes,
)

EASTERN = ZoneInfo("America/New_York")


def test_ibkr_backfill_inserts_nbbo_rows_and_registers_license_policy(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ibkr_historical, "_create_ibkr_client", lambda config: _MockIbkrClient())

    summary = backfill_ibkr_historical_quotes(
        symbols=["AAA"],
        market_date_start=date(2026, 4, 10),
        market_date_end=date(2026, 4, 10),
        db_path=db_path,
        config=IbkrHistoricalConfig(),
    )

    rows = fetch_historical_l1_quotes(db_path, market_date="2026-04-10", symbol="AAA")
    assert summary.requested_symbols == 1
    assert summary.rows_inserted == 40
    assert summary.rows_duplicate == 0
    assert summary.failed_chunks == []
    assert summary.consolidation_evidence["decision"] == "cost_eligible"
    assert summary.consolidation_evidence["license"]["license_type"] == "personal_use"
    assert summary.consolidation_evidence["license"]["redistribution_allowed"] is False
    assert is_cost_eligible_source(
        IBKR_NBBO_SOURCE,
        policy_file=tmp_path / "config" / "cost_source_policy.json",
    )
    assert len(rows) == 40
    assert {row["source"] for row in rows} == {IBKR_NBBO_SOURCE}
    assert rows[0]["bid_exchange"] in {"ARCA", "ISLD"}
    assert rows[0]["subscription_continuous"] == 1


def test_ibkr_backfill_counts_existing_and_in_memory_duplicates(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    monkeypatch.chdir(tmp_path)
    first_time = datetime(2026, 4, 10, 13, 30, tzinfo=timezone.utc)
    insert_historical_l1_quotes(
        db_path,
        [
            HistoricalL1Quote(
                symbol="AAA",
                market_date="2026-04-10",
                timestamp=first_time,
                bid_price=1.0,
                ask_price=1.02,
                source=IBKR_NBBO_SOURCE,
            )
        ],
    )
    monkeypatch.setattr(
        ibkr_historical,
        "_create_ibkr_client",
        lambda config: _MockIbkrClient(
            ticks=[
                _tick(first_time, bid=1.0, ask=1.02),
                _tick(first_time, bid=1.0, ask=1.02),
                _tick(first_time + timedelta(seconds=1), bid=1.01, ask=1.03),
            ]
        ),
    )

    summary = backfill_ibkr_historical_quotes(
        symbols=["AAA"],
        market_date_start=date(2026, 4, 10),
        market_date_end=date(2026, 4, 10),
        db_path=db_path,
        config=IbkrHistoricalConfig(),
    )

    rows = fetch_historical_l1_quotes(db_path, market_date="2026-04-10", symbol="AAA")
    assert summary.rows_inserted == 1
    assert summary.rows_duplicate == 2
    assert len(rows) == 2


def test_ibkr_backfill_retries_rate_limit_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ibkr_historical.time_module, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        ibkr_historical,
        "_create_ibkr_client",
        lambda config: _MockIbkrClient(
            ticks=[_tick(datetime(2026, 4, 10, 13, 30, tzinfo=timezone.utc))],
            fail_first=True,
        ),
    )

    summary = backfill_ibkr_historical_quotes(
        symbols=["AAA"],
        market_date_start=date(2026, 4, 10),
        market_date_end=date(2026, 4, 10),
        db_path=db_path,
        config=IbkrHistoricalConfig(),
    )

    assert summary.rows_inserted == 1
    assert summary.rate_limit_hits == 1
    assert summary.failed_chunks == []


def test_backfill_ibkr_historical_quotes_cli_writes_summary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    settings = AppSettings(db_path=db_path)
    symbols_file = tmp_path / "watchlist.txt"
    symbols_file.write_text("AAA\nBBB\n", encoding="utf-8")
    out_summary = tmp_path / "automation" / "state" / "ibkr_backfill" / "run.json"

    monkeypatch.setattr("penny_stock_radar.cli.get_settings", lambda: settings)
    monkeypatch.setattr(
        "penny_stock_radar.cli.backtest.backfill_ibkr_historical_quotes",
        lambda symbols, market_date_start, market_date_end, db_path, config: IbkrBackfillSummary(
            requested_symbols=len(symbols),
            market_date_range=(market_date_start, market_date_end),
            rows_inserted=3,
            rows_duplicate=1,
            rate_limit_hits=0,
            failed_chunks=[],
            consolidation_evidence={"decision": "cost_eligible"},
        ),
    )

    result = CliRunner().invoke(
        app,
        [
            "backfill-ibkr-historical-quotes",
            "--symbols-file",
            str(symbols_file),
            "--market-date-start",
            "2025-12-01",
            "--market-date-end",
            "2026-05-01",
            "--paper-account",
            "--out-summary",
            str(out_summary),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(out_summary.read_text(encoding="utf-8"))
    assert payload["source"] == IBKR_NBBO_SOURCE
    assert payload["requested_symbols"] == 2
    assert payload["rows_inserted"] == 3
    assert payload["market_date_range"] == ["2025-12-01", "2026-05-01"]
    assert payload["config"]["paper_account"] is True


class _MockIbkrClient:
    def __init__(
        self,
        ticks: list[dict] | None = None,
        *,
        fail_first: bool = False,
    ) -> None:
        self._ticks = ticks
        self._fail_first = fail_first
        self._failed = False
        self.disconnected = False

    def reqHistoricalTicks(
        self,
        contract,
        startDateTime,
        endDateTime,
        numberOfTicks,
        whatToShow,
        useRth,
        ignoreSize,
        miscOptions,
    ):
        if self._fail_first and not self._failed:
            self._failed = True
            raise RuntimeError("IBKR pacing violation rate limit")
        start = _coerce_datetime(startDateTime)
        if self._ticks is not None:
            return self._ticks if start.hour == 13 and start.minute == 30 else []
        if start.hour == 13 and start.minute == 30:
            return [
                _tick(
                    datetime(2026, 4, 10, 13, 30, tzinfo=timezone.utc) + timedelta(seconds=index),
                    bid=1.0 + index * 0.001,
                    ask=1.02 + index * 0.001,
                    bid_exchange="ARCA" if index % 2 == 0 else "ISLD",
                    ask_exchange="BATS" if index % 3 == 0 else "EDGX",
                )
                for index in range(40)
            ]
        return []

    def disconnect(self) -> None:
        self.disconnected = True


def _tick(
    timestamp: datetime,
    *,
    bid: float = 1.0,
    ask: float = 1.02,
    bid_exchange: str = "ARCA",
    ask_exchange: str = "BATS",
) -> dict:
    return {
        "time": timestamp,
        "priceBid": bid,
        "priceAsk": ask,
        "bidExchange": bid_exchange,
        "askExchange": ask_exchange,
    }


def _coerce_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    raise AssertionError(f"unexpected datetime value: {value!r}")
