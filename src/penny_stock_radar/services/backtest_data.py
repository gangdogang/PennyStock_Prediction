from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from ..config import AppSettings
from ..db import (
    fetch_historical_l1_quotes,
    fetch_historical_minute_bars,
    fetch_historical_minute_bars_for_symbols,
    fetch_latest_passed_universe,
    fetch_passed_universe_for_market_date,
    fetch_point_in_time_scan_id,
    insert_historical_coverage_report,
    insert_historical_halt_events,
    tag_scan_run_point_in_time,
)
from ..models import CoverageGateStatus, HistoricalCoverageReport, HistoricalHaltEvent

EASTERN = ZoneInfo("America/New_York")
_SESSION_WINDOWS = {
    "premarket": (time(4, 0), time(9, 30)),
    "regular": (time(9, 30), time(16, 0)),
    "full_day": (time(4, 0), time(20, 0)),
}


@dataclass(frozen=True, slots=True)
class UniverseDifferenceReport:
    market_date: str
    point_in_time_scan_id: str | None
    current_scan_id: str | None
    point_in_time_count: int
    current_count: int
    added_symbols: list[str]
    removed_symbols: list[str]
    common_symbols: int


class BacktestDataManager:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def premarket_cutoff(self, market_date: date | str) -> datetime:
        session_date = self._coerce_market_date(market_date)
        return datetime.combine(session_date, time(8, 0), tzinfo=EASTERN)

    def tag_universe_snapshot(
        self,
        scan_id: str,
        *,
        market_date: date | str,
        point_in_time_tag: str = "manual",
    ) -> None:
        tag_scan_run_point_in_time(
            self.settings.database_path,
            scan_id,
            market_date=self._coerce_market_date(market_date).isoformat(),
            point_in_time_tag=point_in_time_tag,
        )

    def fetch_point_in_time_universe(self, market_date: date | str):
        return fetch_passed_universe_for_market_date(
            self.settings.database_path,
            self._coerce_market_date(market_date).isoformat(),
        )

    def build_universe_difference_report(
        self,
        market_date: date | str,
    ) -> UniverseDifferenceReport:
        market_date_str = self._coerce_market_date(market_date).isoformat()
        point_in_time_scan = fetch_point_in_time_scan_id(
            self.settings.database_path,
            market_date_str,
        )
        point_in_time_rows = fetch_passed_universe_for_market_date(
            self.settings.database_path,
            market_date_str,
        )
        current_rows = fetch_latest_passed_universe(
            self.settings.database_path,
            prefer_reportable=False,
        )
        point_in_time_symbols = {str(row["symbol"]).upper() for row in point_in_time_rows}
        current_symbols = {str(row["symbol"]).upper() for row in current_rows}
        current_scan_id = str(current_rows[0]["scan_id"]) if current_rows else None
        return UniverseDifferenceReport(
            market_date=market_date_str,
            point_in_time_scan_id=(
                str(point_in_time_scan["scan_id"]) if point_in_time_scan is not None else None
            ),
            current_scan_id=current_scan_id,
            point_in_time_count=len(point_in_time_symbols),
            current_count=len(current_symbols),
            added_symbols=sorted(current_symbols - point_in_time_symbols),
            removed_symbols=sorted(point_in_time_symbols - current_symbols),
            common_symbols=len(point_in_time_symbols & current_symbols),
        )

    def build_l1_coverage_report(
        self,
        market_date: date | str,
        *,
        symbols: Iterable[str],
        session: str = "premarket",
        source: str = "historical_l1_quotes",
    ) -> HistoricalCoverageReport:
        market_date_str = self._coerce_market_date(market_date).isoformat()
        expected_symbols = sorted({symbol.upper() for symbol in symbols if symbol})
        quotes = fetch_historical_l1_quotes(
            self.settings.database_path,
            market_date=market_date_str,
        )
        start_at, end_at = self._session_window(market_date_str, session)
        expected_minutes = max(int((end_at - start_at).total_seconds() // 60), 0)
        covered_by_symbol: dict[str, set[str]] = defaultdict(set)
        for row in quotes:
            quote_at = self._parse_timestamp(row["quote_at"])
            if quote_at is None or quote_at < start_at or quote_at >= end_at:
                continue
            symbol = str(row["symbol"]).upper()
            covered_by_symbol[symbol].add(self._coverage_minute_key(quote_at))

        covered_symbols = [symbol for symbol in expected_symbols if covered_by_symbol.get(symbol)]
        covered_intervals = sum(len(covered_by_symbol.get(symbol, set())) for symbol in expected_symbols)
        expected_intervals = len(expected_symbols) * expected_minutes
        quality_notes = self._l1_quality_notes(quotes, market_date_str)
        report = HistoricalCoverageReport(
            market_date=market_date_str,
            dataset_kind=f"l1_quote_{session}",
            source=source,
            expected_symbol_count=len(expected_symbols),
            covered_symbol_count=len(covered_symbols),
            symbol_coverage_pct=self._pct(len(covered_symbols), len(expected_symbols)),
            expected_interval_count=expected_intervals,
            covered_interval_count=covered_intervals,
            interval_coverage_pct=self._pct(covered_intervals, expected_intervals),
            notes=[
                f"session={session}",
                f"window={start_at.isoformat()}->{end_at.isoformat()}",
                *quality_notes,
            ],
        )
        insert_historical_coverage_report(self.settings.database_path, report)
        return report

    def coverage_report_output_path(
        self,
        report: HistoricalCoverageReport,
    ) -> Path:
        return self.settings.backtest_coverage_report_dir / f"{report.market_date}_{report.dataset_kind}.json"

    def build_coverage_gate_status(
        self,
        report: HistoricalCoverageReport,
        *,
        gate_threshold_pct: float | None = None,
        report_path: Path | None = None,
    ) -> CoverageGateStatus:
        if not report.dataset_kind.startswith("l1_quote_"):
            raise ValueError(
                "Coverage gate status expects an L1 coverage report dataset_kind like "
                "'l1_quote_<session>'."
            )
        threshold = (
            float(gate_threshold_pct)
            if gate_threshold_pct is not None
            else float(self.settings.backtest_coverage_gate_pct)
        )
        session = report.dataset_kind.removeprefix("l1_quote_")
        symbol_gate_passed = report.symbol_coverage_pct >= threshold
        interval_gate_passed = report.interval_coverage_pct >= threshold
        quality_failures = self._coverage_quality_failures(report.notes)
        quality_gate_passed = not quality_failures
        threshold_label = f"{threshold:.1f}".rstrip("0").rstrip(".").replace(".", "_")
        return CoverageGateStatus(
            status=(
                "passed"
                if symbol_gate_passed and interval_gate_passed and quality_gate_passed
                else "failed"
            ),
            gate_name=f"step0_l1_coverage_{threshold_label}",
            market_date=report.market_date,
            session=session,
            dataset_kind=report.dataset_kind,
            source=report.source,
            threshold_pct=threshold,
            decision_basis=(
                f"symbol_coverage_pct>={threshold:.1f} and interval_coverage_pct>={threshold:.1f} "
                "and no_l1_timestamp_quality_failures"
            ),
            expected_symbol_count=report.expected_symbol_count,
            covered_symbol_count=report.covered_symbol_count,
            symbol_coverage_pct=report.symbol_coverage_pct,
            expected_interval_count=report.expected_interval_count,
            covered_interval_count=report.covered_interval_count,
            interval_coverage_pct=report.interval_coverage_pct,
            symbol_gate_passed=symbol_gate_passed,
            interval_gate_passed=interval_gate_passed,
            gate_passed=symbol_gate_passed and interval_gate_passed,
            report_created_at=report.created_at,
            report_path=str(report_path) if report_path is not None else None,
            notes=report.notes,
            last_error="; ".join(quality_failures) if quality_failures else None,
        )

    def export_coverage_report_json(
        self,
        report: HistoricalCoverageReport,
        *,
        output_path: Path | None = None,
    ) -> Path:
        resolved = (output_path or self.coverage_report_output_path(report)).resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        return resolved

    def export_coverage_gate_status(
        self,
        gate_status: CoverageGateStatus,
        *,
        output_path: Path | None = None,
    ) -> Path:
        resolved = (output_path or self.settings.backtest_coverage_gate_path).resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(
            json.dumps(gate_status.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        return resolved

    def infer_and_store_halt_events(
        self,
        market_date: date | str,
        *,
        symbols: Iterable[str] | None = None,
        min_gap_minutes: int = 5,
        min_zero_volume_minutes: int = 2,
        source: str = "minute_bar_fallback",
    ) -> list[HistoricalHaltEvent]:
        market_date_str = self._coerce_market_date(market_date).isoformat()
        symbol_filter = {symbol.upper() for symbol in symbols} if symbols is not None else None
        rows = (
            fetch_historical_minute_bars_for_symbols(
                self.settings.database_path,
                market_date=market_date_str,
                symbols=symbol_filter,
            )
            if symbol_filter is not None
            else fetch_historical_minute_bars(self.settings.database_path, market_date=market_date_str)
        )
        grouped: dict[str, list[object]] = defaultdict(list)
        for row in rows:
            symbol = str(row["symbol"]).upper()
            grouped[symbol].append(row)

        inferred: list[HistoricalHaltEvent] = []
        for symbol, symbol_rows in grouped.items():
            sorted_rows = sorted(symbol_rows, key=lambda row: str(row["bar_at"]))
            inferred.extend(
                self._infer_symbol_halts(
                    market_date=market_date_str,
                    symbol=symbol,
                    rows=sorted_rows,
                    min_gap_minutes=min_gap_minutes,
                    min_zero_volume_minutes=min_zero_volume_minutes,
                    source=source,
                )
            )
        if inferred:
            insert_historical_halt_events(self.settings.database_path, inferred)
        return inferred

    def _infer_symbol_halts(
        self,
        *,
        market_date: str,
        symbol: str,
        rows: list[object],
        min_gap_minutes: int,
        min_zero_volume_minutes: int,
        source: str,
    ) -> list[HistoricalHaltEvent]:
        events: list[HistoricalHaltEvent] = []
        zero_volume_start: datetime | None = None
        zero_volume_count = 0
        last_bar_at: datetime | None = None
        for row in rows:
            bar_at = self._parse_timestamp(row["bar_at"])
            if bar_at is None:
                continue
            volume = float(row["volume"] or 0.0)
            if last_bar_at is not None:
                gap_minutes = int((bar_at - last_bar_at).total_seconds() // 60)
                if gap_minutes >= min_gap_minutes:
                    events.append(
                        HistoricalHaltEvent(
                            symbol=symbol,
                            market_date=market_date,
                            start_at=last_bar_at + timedelta(minutes=1),
                            end_at=bar_at,
                            reason="minute_gap_detected",
                            resume_price=float(row["open_price"]),
                            source=source,
                            inferred=True,
                            notes=[f"gap_minutes={gap_minutes}"],
                        )
                    )
            if volume <= 0:
                zero_volume_count += 1
                if zero_volume_start is None:
                    zero_volume_start = bar_at
            elif zero_volume_start is not None and zero_volume_count >= min_zero_volume_minutes:
                events.append(
                    HistoricalHaltEvent(
                        symbol=symbol,
                        market_date=market_date,
                        start_at=zero_volume_start,
                        end_at=bar_at,
                        reason="zero_volume_stretch",
                        resume_price=float(row["open_price"]),
                        source=source,
                        inferred=True,
                        notes=[f"zero_volume_minutes={zero_volume_count}"],
                    )
                )
                zero_volume_start = None
                zero_volume_count = 0
            elif volume > 0:
                zero_volume_start = None
                zero_volume_count = 0
            last_bar_at = bar_at
        return events

    def _session_window(self, market_date: str, session: str) -> tuple[datetime, datetime]:
        normalized = session.lower()
        if normalized not in _SESSION_WINDOWS:
            raise ValueError(f"Unsupported session for coverage report: {session}")
        start_time, end_time = _SESSION_WINDOWS[normalized]
        session_date = self._coerce_market_date(market_date)
        return (
            datetime.combine(session_date, start_time, tzinfo=EASTERN),
            datetime.combine(session_date, end_time, tzinfo=EASTERN),
        )

    def _coerce_market_date(self, market_date: date | str) -> date:
        if isinstance(market_date, date):
            return market_date
        return date.fromisoformat(str(market_date))

    def _parse_timestamp(self, value: object) -> datetime | None:
        if value in {None, ""}:
            return None
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    def _l1_quality_notes(self, rows: list[object], market_date: str) -> list[str]:
        snapshot_mismatch_count = 0
        timestamp_drift_count = 0
        for row in rows:
            quote_at = self._parse_timestamp(self._row_value(row, "quote_at"))
            if quote_at is None:
                continue
            quote_date = quote_at.astimezone(EASTERN).date().isoformat()
            if quote_date != market_date:
                snapshot_mismatch_count += 1
            source = str(self._row_value(row, "source") or "")
            if source != "kis_l1_snapshot":
                continue
            created_at = self._parse_timestamp(self._row_value(row, "created_at"))
            if created_at is None:
                continue
            drift_minutes = abs(
                (
                    created_at.astimezone(timezone.utc)
                    - quote_at.astimezone(timezone.utc)
                ).total_seconds()
            ) / 60.0
            if drift_minutes > 120.0:
                timestamp_drift_count += 1
        notes: list[str] = []
        if snapshot_mismatch_count:
            notes.append(f"snapshot_date_mismatch_count={snapshot_mismatch_count}")
        if timestamp_drift_count:
            notes.append(f"timestamp_drift_gt_120m_count={timestamp_drift_count}")
        return notes

    def _coverage_quality_failures(self, notes: list[str]) -> list[str]:
        failures: list[str] = []
        for note in notes:
            if note.startswith("snapshot_date_mismatch_count=") and not note.endswith("=0"):
                failures.append(note)
            if note.startswith("timestamp_drift_gt_120m_count=") and not note.endswith("=0"):
                failures.append(note)
        return failures

    def _row_value(self, row: object, key: str) -> object:
        try:
            return row[key]  # type: ignore[index]
        except Exception:
            return None

    def _coverage_minute_key(self, timestamp: datetime) -> str:
        return timestamp.astimezone(timezone.utc).replace(second=0, microsecond=0).strftime(
            "%Y-%m-%dT%H:%M"
        )

    def _pct(self, numerator: int, denominator: int) -> float:
        if denominator <= 0:
            return 0.0
        return (float(numerator) / float(denominator)) * 100.0
