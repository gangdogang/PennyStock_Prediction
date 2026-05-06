from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import init_database, insert_historical_l1_quotes
from penny_stock_radar.models import HistoricalL1Quote
from penny_stock_radar.services.falsification_research import FalsificationResearchAuditor
from penny_stock_radar.services.kis_quote_consolidation_check import (
    evaluate_kis_quote_consolidation,
)

EASTERN = ZoneInfo("America/New_York")


def test_kis_quote_consolidation_classifies_nbbo_consolidated(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    insert_historical_l1_quotes(db_path, _quote_rows(exchange_mode="diverse", count=40))

    verdict = evaluate_kis_quote_consolidation(db_path, sample_window_days=5)

    assert verdict.classification == "nbbo_consolidated"
    assert verdict.evidence.bid_exchange_distinct_count == 2
    assert verdict.evidence.ask_exchange_distinct_count == 2
    assert verdict.evidence.quote_update_frequency_hz >= 1.0
    assert verdict.evidence.sample_size == 40


def test_kis_quote_consolidation_classifies_missing_exchange_info_as_single_venue(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    insert_historical_l1_quotes(db_path, _quote_rows(exchange_mode="missing", count=40))

    verdict = evaluate_kis_quote_consolidation(db_path, sample_window_days=5)

    assert verdict.classification == "single_venue_proxy"
    assert verdict.evidence.bid_exchange_distinct_count == 0
    assert "absent" in verdict.reason


def test_kis_quote_consolidation_classifies_small_sample_as_insufficient(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    insert_historical_l1_quotes(db_path, _quote_rows(exchange_mode="diverse", count=5))

    verdict = evaluate_kis_quote_consolidation(db_path, sample_window_days=5)

    assert verdict.classification == "insufficient_evidence"
    assert verdict.evidence.sample_size == 5


def test_evaluate_kis_quote_consolidation_cli_writes_run_and_latest_json(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    insert_historical_l1_quotes(db_path, _quote_rows(exchange_mode="diverse", count=40))
    settings = AppSettings(db_path=db_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("penny_stock_radar.cli.get_settings", lambda: settings)

    out_path = tmp_path / "automation" / "state" / "source_validation" / "kis_run.json"
    result = CliRunner().invoke(
        app,
        [
            "evaluate-kis-quote-consolidation",
            "--sample-window-days",
            "5",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0
    run_payload = json.loads(out_path.read_text(encoding="utf-8"))
    latest_payload = json.loads(
        (
            tmp_path
            / "automation"
            / "state"
            / "source_validation"
            / "latest_kis_consolidation.json"
        ).read_text(encoding="utf-8")
    )
    assert run_payload["classification"] == "nbbo_consolidated"
    assert latest_payload == run_payload


def test_falsification_cost_policy_reacts_to_kis_consolidation_verdict(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    insert_historical_l1_quotes(db_path, _quote_rows(exchange_mode="diverse", count=40))
    monkeypatch.chdir(tmp_path)

    _write_latest_verdict(tmp_path, classification="nbbo_consolidated", reason="fixture pass")
    positive = FalsificationResearchAuditor()._cost_audit(  # noqa: SLF001
        _options(db_path, tmp_path)
    )
    assert positive["l1_spread"]["count"] == 40
    assert positive["l1_cost_eligible_source_counts"] == {"kis_l1_snapshot": 40}
    assert positive["cost_source_policy"]["kis_l1_snapshot_policy"]["cost_eligible"] is True

    _write_latest_verdict(
        tmp_path,
        classification="single_venue_proxy",
        reason="fixture downgrade",
    )
    negative = FalsificationResearchAuditor()._cost_audit(  # noqa: SLF001
        _options(db_path, tmp_path)
    )
    assert negative["l1_spread"]["count"] == 0
    assert negative["l1_cost_eligible_source_counts"] == {}
    assert negative["l1_diagnostic_only_source_counts"] == {"kis_l1_snapshot": 40}
    assert "kis_l1_snapshot" in negative["excluded_l1_sources"]
    assert negative["cost_source_policy"]["kis_l1_snapshot_policy"]["reason"] == "fixture downgrade"


def _quote_rows(*, exchange_mode: str, count: int) -> list[HistoricalL1Quote]:
    start = datetime(2026, 5, 1, 9, 30, tzinfo=EASTERN)
    rows: list[HistoricalL1Quote] = []
    for index in range(count):
        timestamp = start + timedelta(seconds=index)
        bid_exchange: str | None
        ask_exchange: str | None
        if exchange_mode == "diverse":
            bid_exchange = "XNAS" if index % 2 == 0 else "XNYS"
            ask_exchange = "XASE" if index % 3 == 0 else "XNAS"
        else:
            bid_exchange = None
            ask_exchange = None
        rows.append(
            HistoricalL1Quote(
                symbol="AAA" if index % 2 == 0 else "BBB",
                market_date="2026-05-01",
                timestamp=timestamp,
                bid_price=1.00 + index * 0.001,
                ask_price=1.02 + index * 0.001,
                bid_exchange=bid_exchange,
                ask_exchange=ask_exchange,
                last_price=1.01 + index * 0.001,
                source="kis_l1_snapshot",
            )
        )
    return rows


def _write_latest_verdict(tmp_path: Path, *, classification: str, reason: str) -> None:
    path = tmp_path / "automation" / "state" / "source_validation" / "latest_kis_consolidation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "source_name": "kis_l1_snapshot",
                "classification": classification,
                "reason": reason,
                "evidence": {
                    "bid_exchange_distinct_count": 2,
                    "ask_exchange_distinct_count": 2,
                    "bid_ask_exchange_differ_rate": 0.5,
                    "quote_update_frequency_hz": 1.5,
                    "spread_distribution_p50": 0.02,
                    "spread_distribution_p90": 0.02,
                    "spread_distribution_p99": 0.02,
                    "sample_size": 40,
                    "sample_window": [
                        "2026-05-01T09:30:00-04:00",
                        "2026-05-01T09:30:39-04:00",
                    ],
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _options(db_path: Path, tmp_path: Path):
    from penny_stock_radar.services.falsification_research import FalsificationAuditOptions

    return FalsificationAuditOptions(
        db_path=db_path,
        export_root=tmp_path / "research_runs",
    )
