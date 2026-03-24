from __future__ import annotations

from collections import Counter, defaultdict

from ..config import AppSettings
from ..db import (
    fetch_latest_passed_universe,
    fetch_latest_social_signals,
    insert_filings,
    insert_watchlist,
    update_universe_filing_summaries,
)
from ..models import FilingMatch, SetupSignal, WatchlistEntry
from .filing_scanner import FilingScanner
from .setup_scanner import SetupScanner
from ..utils.theme import dominant_themes, extract_themes


def _row_value(row: object, key: str) -> object | None:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return None


class WatchlistBuilder:
    def __init__(
        self,
        settings: AppSettings,
        filing_scanner: FilingScanner | None = None,
        setup_scanner: SetupScanner | None = None,
    ) -> None:
        self.settings = settings
        self.filing_scanner = filing_scanner or FilingScanner(settings)
        self.setup_scanner = setup_scanner or SetupScanner()

    def build(
        self,
        limit: int | None = None,
        lookback_hours: int | None = None,
    ) -> tuple[list[WatchlistEntry], list[FilingMatch], dict[str, SetupSignal]]:
        universe_rows = fetch_latest_passed_universe(self.settings.database_path)
        if not universe_rows:
            return [], [], {}

        scan_id = universe_rows[0]["scan_id"]
        symbols = [row["symbol"] for row in universe_rows]

        filings = self.filing_scanner.scan_symbols(
            symbols, lookback_hours=lookback_hours or self.settings.filings_lookback_hours
        )
        setup_signals = self.setup_scanner.scan_symbols(symbols)

        insert_filings(self.settings.database_path, scan_id, filings)
        summaries = self._build_filing_summaries(filings)
        update_universe_filing_summaries(self.settings.database_path, scan_id, summaries)

        rows_by_symbol = {row["symbol"]: row for row in universe_rows}
        filings_by_symbol: dict[str, list[FilingMatch]] = defaultdict(list)
        for filing in filings:
            filings_by_symbol[filing.symbol].append(filing)

        social_by_symbol = {
            row["symbol"]: row for row in fetch_latest_social_signals(self.settings.database_path)
        }

        themes_by_symbol: dict[str, list[str]] = {}
        for symbol, row in rows_by_symbol.items():
            filing_themes = []
            for filing in filings_by_symbol.get(symbol, []):
                filing_themes.extend(filing.themes)
            row_themes = extract_themes(
                _row_value(row, "company_name"),
                _row_value(row, "sector"),
                _row_value(row, "industry"),
                _row_value(row, "recent_filings_summary"),
            )
            themes_by_symbol[symbol] = sorted(set(filing_themes + row_themes))

        theme_counter = dominant_themes(list(themes_by_symbol.values()))

        sector_counter = Counter()
        for symbol, row in rows_by_symbol.items():
            sector = row["sector"]
            filing_score = self._catalyst_score(filings_by_symbol.get(symbol, []))
            technical_score = setup_signals.get(symbol, SetupSignal(symbol=symbol)).setup_score
            if sector and (filing_score > 0 or technical_score >= 1.0):
                sector_counter[sector] += 1

        entries: list[WatchlistEntry] = []
        for symbol, row in rows_by_symbol.items():
            filing_score = self._catalyst_score(filings_by_symbol.get(symbol, []))
            signal = setup_signals.get(symbol, SetupSignal(symbol=symbol))
            technical_score = signal.setup_score
            low_float_bonus = self._low_float_bonus(row["float_shares"])
            has_sector_breadth = bool(row["sector"] and sector_counter[row["sector"]] >= 2)
            has_theme_breadth = any(
                theme_counter[theme] >= 2 for theme in themes_by_symbol.get(symbol, [])
            )
            sympathy_score = 0.5 if has_sector_breadth else 0.0
            if has_theme_breadth:
                sympathy_score += 0.5

            social_row = social_by_symbol.get(symbol)
            social_score = float(social_row["social_score"]) if social_row else 0.0

            narrative_gate = filing_score > 0 or technical_score >= 1.0
            if not narrative_gate:
                continue

            total_score = (
                low_float_bonus + filing_score + technical_score + sympathy_score + social_score
            )
            reasons = []
            if low_float_bonus:
                reasons.append("low_float")
            reasons.extend(signal.notes)
            reasons.extend(filing.summary for filing in filings_by_symbol.get(symbol, []))
            if has_sector_breadth:
                reasons.append("sector_breadth")
            if has_theme_breadth:
                reasons.append("theme_breadth")
            if social_score:
                reasons.append("social_velocity")

            entries.append(
                WatchlistEntry(
                    symbol=symbol,
                    total_score=total_score,
                    catalyst_score=filing_score,
                    technical_score=technical_score,
                    sympathy_score=sympathy_score,
                    low_float_bonus=low_float_bonus,
                    social_score=social_score,
                    themes=themes_by_symbol.get(symbol, []),
                    reasons=reasons,
                )
            )

        entries.sort(key=lambda entry: (-entry.total_score, entry.symbol))
        entries = entries[: limit or self.settings.watchlist_limit]
        insert_watchlist(self.settings.database_path, scan_id, entries)
        return entries, filings, setup_signals

    def _build_filing_summaries(self, filings: list[FilingMatch]) -> dict[str, str]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for filing in filings:
            grouped[filing.symbol].append(filing.summary)
        return {symbol: "; ".join(values[:3]) for symbol, values in grouped.items()}

    def _catalyst_score(self, filings: list[FilingMatch]) -> float:
        score = 0.0
        for filing in filings:
            if filing.form == "RW":
                score += 1.5
            elif filing.form == "8-K":
                score += 1.25
            elif filing.form in {"S-1", "S-3", "424B5"}:
                score -= 0.75
        return score

    def _low_float_bonus(self, float_shares: int | None) -> float:
        if float_shares is None:
            return 0.0
        if float_shares <= 10_000_000:
            return 1.5
        if float_shares <= 20_000_000:
            return 0.75
        return 0.0
