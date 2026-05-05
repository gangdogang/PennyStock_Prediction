from __future__ import annotations

import json
from pathlib import Path

from penny_stock_radar.db import get_connection, init_database
from penny_stock_radar.services.research_data_coverage import (
    ResearchDataCoverageAuditor,
    ResearchDataCoverageOptions,
)


def test_research_data_coverage_classifies_cost_sources_and_writes_artifacts(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "research.sqlite3"
    init_database(db_path)
    with get_connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO historical_minute_bars (
                symbol, market_date, market_phase, bar_at, open_price, high_price,
                low_price, close_price, volume, bid_price, ask_price, spread_pct,
                source, created_at
            )
            VALUES
                ('ABCD', '2026-05-01', 'regular', '2026-05-01T13:30:00+00:00',
                 1, 1.1, 0.9, 1.0, 1000, NULL, NULL, 12.5,
                 'alpaca_iex_historical_quotes', '2026-05-01T13:30:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO historical_l1_quotes (
                symbol, market_date, quote_at, bid_price, ask_price, last_price,
                source, created_at
            )
            VALUES
                ('ABCD', '2026-05-01', '2026-05-01T13:30:00+00:00',
                 1.00, 1.01, NULL, 'kis_l1_snapshot', '2026-05-01T13:30:00+00:00'),
                ('ABCD', '2026-05-01', '2026-05-01T13:31:00+00:00',
                 0.90, 1.20, NULL, 'alpaca_iex_historical_quotes',
                 '2026-05-01T13:31:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO scan_runs (
                scan_id, source, symbol_count, market_date, snapshot_role,
                point_in_time_tag, created_at
            )
            VALUES (
                'pit_20260501', 'fixture', 1, '2026-05-01',
                'point_in_time', 'fixture', '2026-05-01T12:00:00+00:00'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO universe (
                scan_id, symbol, company_name, exchange, passed_filters,
                filter_reasons, last_updated_at
            )
            VALUES (
                'pit_20260501', 'ABCD', 'ABCD Corp', 'NASDAQ', 1,
                '[]', '2026-05-01T12:00:00+00:00'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO filings (
                scan_id, symbol, cik, form, filing_date, acceptance_datetime,
                accession_number, primary_document, primary_doc_description,
                filing_url, item_numbers, themes, matched_keywords, summary, created_at
            )
            VALUES
                ('pit_20260501', 'ABCD', '0000000001', '8-K', '2026-05-01',
                 '2026-05-01T07:30:00-04:00', '0001', 'a.htm', '8-K',
                 'https://www.sec.gov/Archives/edgar/data/1/0001/a.htm',
                 '[]', '[]', '[]', 'eligible', '2026-05-01T11:30:00+00:00'),
                ('pit_20260501', 'ABCD', '0000000001', '8-K', '2026-05-01',
                 '2026-05-01T09:30:00-04:00', '0002', 'b.htm', '8-K',
                 'https://www.sec.gov/Archives/edgar/data/1/0002/b.htm',
                 '[]', '[]', '[]', 'post cutoff', '2026-05-01T13:30:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO corporate_actions (
                source, source_record_id, action_category, action_subtype, symbol,
                effective_date, raw_payload, created_at
            )
            VALUES (
                'finra_otc_daily_list', 'fixture_1', 'symbol_change',
                'symbol_change', 'ABCD', '2026-05-01', '{}',
                '2026-05-01T12:00:00+00:00'
            )
            """
        )

    trade_log = tmp_path / "paper_trade_log.csv"
    trade_log.write_text(
        "event,bucket,symbol,market_date,entry_at\n"
        "ENTRY,predictor_weighted,ABCD,2026-05-01,2026-05-01T09:30:00-04:00\n",
        encoding="utf-8",
    )

    result = ResearchDataCoverageAuditor().run(
        ResearchDataCoverageOptions(
            db_path=db_path,
            output_root=tmp_path / "runs",
            run_id="coverage_fixture",
            strategy_trade_log=trade_log,
            strategy_bucket="predictor_weighted",
        )
    )

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    row = report["by_date"][0]
    assert row["l1_cost_eligible_quote_count"] == 1
    assert row["l1_diagnostic_only_alpaca_iex_count"] == 1
    assert row["minute_spread_cost_eligible_count"] == 0
    assert row["minute_spread_diagnostic_only_count"] == 1
    assert row["pit_universe_exists"] is True
    assert row["sec_filing_cutoff_eligible_count"] == 1
    assert row["sec_filing_post_cutoff_diagnostic_count"] == 1
    assert report["summary"]["strategy_dates_cost_eligible_overlap"] is True
    assert "minute_bars_6_month_coverage_missing" in report["summary"]["blockers"]
    assert result.csv_path.exists()
    assert "Alpaca IEX" in result.summary_path.read_text(encoding="utf-8")
