from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from penny_stock_radar.db import init_database, insert_historical_l1_quotes
from penny_stock_radar.models import HistoricalL1Quote
from penny_stock_radar.services.external_data_validator import (
    ExternalDataValidator,
    SourceLicenseInfo,
    is_cost_eligible_source,
)
from penny_stock_radar.services.falsification_research import (
    FalsificationAuditOptions,
    FalsificationResearchAuditor,
)

EASTERN = ZoneInfo("America/New_York")


def test_validator_keeps_non_redistributable_license_diagnostic_only(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    insert_historical_l1_quotes(
        db_path,
        _quote_rows(source="ibkr_nbbo", exchange_mode="diverse", count=40),
    )
    validator = ExternalDataValidator()

    result = validator.validate(
        source_name="ibkr_nbbo",
        sample_db_path=db_path,
        license_info=SourceLicenseInfo(
            license_type="personal_use",
            redistribution_allowed=False,
            citation_required=True,
            documented_at="https://example.test/license",
        ),
    )
    validator.register_to_policy(result, policy_file=tmp_path / "config" / "cost_source_policy.json")

    policy = json.loads((tmp_path / "config" / "cost_source_policy.json").read_text(encoding="utf-8"))
    assert result.decision == "diagnostic_only"
    assert not is_cost_eligible_source(
        "ibkr_nbbo",
        policy_file=tmp_path / "config" / "cost_source_policy.json",
    )
    assert any(entry["name"] == "ibkr_nbbo" for entry in policy["diagnostic_only_sources"])
    assert not any(entry["name"] == "ibkr_nbbo" for entry in policy["cost_eligible_sources"])


def test_validator_registers_nbbo_consolidated_license_ok_source_as_cost_eligible(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    insert_historical_l1_quotes(
        db_path,
        _quote_rows(source="ibkr_nbbo", exchange_mode="diverse", count=40),
    )
    validator = ExternalDataValidator()

    result = validator.validate(
        source_name="ibkr_nbbo",
        sample_db_path=db_path,
        license_info=SourceLicenseInfo(
            license_type="commercial_redistribution_prohibited",
            redistribution_allowed=True,
            citation_required=True,
            documented_at="https://example.test/vendor-terms",
        ),
    )
    validator.register_to_policy(result, policy_file=tmp_path / "config" / "cost_source_policy.json")

    policy = json.loads((tmp_path / "config" / "cost_source_policy.json").read_text(encoding="utf-8"))
    assert result.decision == "cost_eligible"
    assert result.nbbo_consolidation_verdict is not None
    assert result.nbbo_consolidation_verdict.classification == "nbbo_consolidated"
    assert is_cost_eligible_source(
        "ibkr_nbbo",
        policy_file=tmp_path / "config" / "cost_source_policy.json",
    )
    assert any(entry["name"] == "ibkr_nbbo" for entry in policy["cost_eligible_sources"])


def test_falsification_audit_reflects_policy_file_changes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    insert_historical_l1_quotes(
        db_path,
        _quote_rows(source="ibkr_nbbo", exchange_mode="diverse", count=40),
    )
    monkeypatch.chdir(tmp_path)
    policy_file = tmp_path / "config" / "cost_source_policy.json"
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    _write_policy(policy_file, cost_sources=["ibkr_nbbo"], diagnostic_sources=[])

    positive = FalsificationResearchAuditor()._cost_audit(  # noqa: SLF001
        FalsificationAuditOptions(db_path=db_path, export_root=tmp_path / "research_runs")
    )
    assert positive["l1_spread"]["count"] == 40
    assert positive["l1_cost_eligible_source_counts"] == {"ibkr_nbbo": 40}

    _write_policy(policy_file, cost_sources=[], diagnostic_sources=["ibkr_nbbo"])
    negative = FalsificationResearchAuditor()._cost_audit(  # noqa: SLF001
        FalsificationAuditOptions(db_path=db_path, export_root=tmp_path / "research_runs")
    )
    assert negative["l1_spread"]["count"] == 0
    assert negative["l1_cost_eligible_source_counts"] == {}
    assert negative["l1_diagnostic_only_source_counts"] == {"ibkr_nbbo": 40}
    assert "ibkr_nbbo" in negative["excluded_l1_sources"]


def test_invalid_policy_falls_back_to_default_whitelist(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    insert_historical_l1_quotes(
        db_path,
        _quote_rows(source="kis_l1_snapshot", exchange_mode="diverse", count=40),
    )
    monkeypatch.chdir(tmp_path)
    _write_latest_kis_verdict(tmp_path)
    policy_file = tmp_path / "config" / "cost_source_policy.json"
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    policy_file.write_text('{"cost_eligible_sources": [{"name": "bogus"}]}', encoding="utf-8")

    audit = FalsificationResearchAuditor()._cost_audit(  # noqa: SLF001
        FalsificationAuditOptions(db_path=db_path, export_root=tmp_path / "research_runs")
    )

    assert audit["l1_spread"]["count"] == 40
    assert audit["l1_cost_eligible_source_counts"] == {"kis_l1_snapshot": 40}
    assert audit["cost_source_policy"]["policy_load_status"] == "fallback_default_invalid"


def _quote_rows(
    *,
    source: str,
    exchange_mode: str,
    count: int,
) -> list[HistoricalL1Quote]:
    start = datetime(2026, 5, 1, 9, 30, tzinfo=EASTERN)
    rows: list[HistoricalL1Quote] = []
    for index in range(count):
        timestamp = start + timedelta(seconds=index)
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
                source=source,
            )
        )
    return rows


def _write_policy(
    path: Path,
    *,
    cost_sources: list[str],
    diagnostic_sources: list[str],
) -> None:
    license_payload = {
        "license_type": "commercial_redistribution_prohibited",
        "redistribution_allowed": True,
        "citation_required": True,
        "documented_at": "https://example.test/vendor-terms",
    }
    path.write_text(
        json.dumps(
            {
                "cost_eligible_sources": [
                    {
                        "name": source,
                        "validated_at": "2026-05-06T00:00:00Z",
                        "verdict_path": "automation/state/source_validation/latest_fixture.json",
                        "license": license_payload,
                    }
                    for source in cost_sources
                ],
                "diagnostic_only_sources": [
                    {
                        "name": source,
                        "validated_at": "2026-05-06T00:00:00Z",
                        "verdict_path": "automation/state/source_validation/latest_fixture.json",
                        "license": license_payload,
                    }
                    for source in diagnostic_sources
                ],
                "rejected_sources": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_latest_kis_verdict(base_dir: Path) -> None:
    path = base_dir / "automation" / "state" / "source_validation" / "latest_kis_consolidation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "source_name": "kis_l1_snapshot",
                "classification": "nbbo_consolidated",
                "reason": "fixture verdict",
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
