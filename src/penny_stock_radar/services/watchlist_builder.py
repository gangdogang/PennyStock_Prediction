from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..config import AppSettings
from ..db import (
    fetch_latest_passed_universe,
    fetch_recent_market_activity,
    fetch_latest_social_signals,
    insert_filings,
    insert_watchlist,
    update_universe_filing_summaries,
)
from ..models import FilingMatch, ListingRecord, MetadataRecord, SetupSignal, WatchlistEntry
from ..providers.listings import NasdaqTraderListingProvider
from ..providers.live_market import build_live_market_provider
from ..providers.metadata import YFinanceMetadataProvider
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


@dataclass(slots=True)
class _MarketContextSignal:
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    price: float | None = None


class WatchlistBuilder:
    def __init__(
        self,
        settings: AppSettings,
        filing_scanner: FilingScanner | None = None,
        setup_scanner: SetupScanner | None = None,
        listing_provider: NasdaqTraderListingProvider | None = None,
        metadata_provider: YFinanceMetadataProvider | None = None,
    ) -> None:
        self.settings = settings
        self.filing_scanner = filing_scanner or FilingScanner(settings)
        self.setup_scanner = setup_scanner or SetupScanner()
        self.listing_provider = listing_provider or NasdaqTraderListingProvider(
            self._setting("nasdaq_trader_url", "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqtraded.txt")
        )
        self.metadata_provider = metadata_provider or YFinanceMetadataProvider()

    def build(
        self,
        limit: int | None = None,
        lookback_hours: int | None = None,
    ) -> tuple[list[WatchlistEntry], list[FilingMatch], dict[str, SetupSignal]]:
        universe_rows = fetch_latest_passed_universe(self.settings.database_path)
        if not universe_rows:
            return [], [], {}

        scan_id = universe_rows[0]["scan_id"]
        rows_by_symbol: dict[str, object] = {
            str(row["symbol"]).upper(): row for row in universe_rows
        }
        market_context = self._build_market_context()
        dynamic_rows = self._build_dynamic_rows(
            [symbol for symbol in market_context if symbol not in rows_by_symbol],
            market_context,
            scan_id=scan_id,
        )
        rows_by_symbol.update(dynamic_rows)
        symbols = list(rows_by_symbol)

        filings = self.filing_scanner.scan_symbols(
            symbols, lookback_hours=lookback_hours or self.settings.filings_lookback_hours
        )
        setup_signals = self.setup_scanner.scan_symbols(symbols)

        insert_filings(self.settings.database_path, scan_id, filings)
        summaries = self._build_filing_summaries(filings)
        update_universe_filing_summaries(self.settings.database_path, scan_id, summaries)

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
            if sector and (filing_score > 0 or technical_score >= 1.0 or market_context.get(symbol, _MarketContextSignal()).score >= 1.0):
                sector_counter[sector] += 1

        entries: list[WatchlistEntry] = []
        for symbol, row in rows_by_symbol.items():
            filing_score = self._catalyst_score(filings_by_symbol.get(symbol, []))
            signal = setup_signals.get(symbol, SetupSignal(symbol=symbol))
            technical_score = signal.setup_score
            market_context_score = market_context.get(symbol, _MarketContextSignal()).score
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

            narrative_gate = filing_score > 0 or technical_score >= 1.0 or market_context_score >= 1.0
            if not narrative_gate:
                continue

            total_score = (
                low_float_bonus
                + filing_score
                + technical_score
                + sympathy_score
                + market_context_score
                + social_score
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
            reasons.extend(market_context.get(symbol, _MarketContextSignal()).reasons)
            if social_score:
                reasons.append("social_velocity")

            entries.append(
                WatchlistEntry(
                    symbol=symbol,
                    total_score=total_score,
                    catalyst_score=filing_score,
                    technical_score=technical_score,
                    sympathy_score=sympathy_score,
                    market_context_score=market_context_score,
                    low_float_bonus=low_float_bonus,
                    social_score=social_score,
                    themes=themes_by_symbol.get(symbol, []),
                    reasons=list(dict.fromkeys(reasons)),
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

    def _build_market_context(self) -> dict[str, _MarketContextSignal]:
        context: dict[str, _MarketContextSignal] = {}
        self._merge_recent_market_activity(context)
        self._merge_live_screeners(context)
        return {symbol: signal for symbol, signal in context.items() if signal.score > 0}

    def _merge_recent_market_activity(
        self,
        context: dict[str, _MarketContextSignal],
    ) -> None:
        limit = max(int(self._setting("watchlist_market_context_limit", 20)), 1) * 4
        recent_rows = fetch_recent_market_activity(self.settings.database_path, limit=limit)
        seen: set[str] = set()
        for row in recent_rows:
            symbol = str(row["symbol"]).upper()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            signal = context.setdefault(symbol, _MarketContextSignal())
            pct_rank = int(row["pct_rank"]) if row["pct_rank"] is not None else None
            volume_rank = int(row["volume_rank"]) if row["volume_rank"] is not None else None
            analysis_label = str(row["analysis_label"] or "")

            if pct_rank is not None and pct_rank <= 5:
                signal.score += 1.0
                signal.reasons.append("recent_pct_leader")
            elif pct_rank is not None and pct_rank <= 10:
                signal.score += 0.5
                signal.reasons.append("recent_pct_momentum")

            if volume_rank is not None and volume_rank <= 5:
                signal.score += 0.85
                signal.reasons.append("recent_volume_leader")
            elif volume_rank is not None and volume_rank <= 10:
                signal.score += 0.4
                signal.reasons.append("recent_volume_support")

            if analysis_label in {"CONDITIONAL_ENTRY", "OPENING_RANGE_CANDIDATE"}:
                signal.score += 0.5
                signal.reasons.append("recent_trade_quality")
            elif analysis_label == "NEWS_CHECK_FIRST":
                signal.score += 0.25
                signal.reasons.append("recent_unverified_momentum")

            last_price = _row_value(row, "last_price")
            if signal.price is None and last_price is not None:
                signal.price = float(last_price)

    def _merge_live_screeners(
        self,
        context: dict[str, _MarketContextSignal],
    ) -> None:
        try:
            provider = build_live_market_provider(self.settings)
        except Exception:
            return
        if not provider.is_available():
            self._close_live_provider(provider)
            return

        limit = max(int(self._setting("watchlist_market_context_limit", 20)), 10)
        try:
            gainers = list(provider.top_gainers(limit=limit))
            actives = list(provider.most_active(limit=limit))
        except Exception:
            return
        finally:
            self._close_live_provider(provider)

        for row in gainers:
            symbol = str(row.symbol).upper()
            if not self._screenable_symbol(symbol, row.price):
                continue
            signal = context.setdefault(symbol, _MarketContextSignal())
            signal.score += 1.5 if row.rank and row.rank <= 5 else 1.0
            signal.reasons.append("live_pct_leader" if row.rank and row.rank <= 5 else "live_pct_momentum")
            if signal.price is None and row.price is not None:
                signal.price = row.price

        for row in actives:
            symbol = str(row.symbol).upper()
            if not symbol:
                continue
            signal = context.setdefault(symbol, _MarketContextSignal())
            signal.score += 1.25 if row.rank and row.rank <= 5 else 0.75
            signal.reasons.append(
                "live_volume_leader" if row.rank and row.rank <= 5 else "live_volume_support"
            )

    def _build_dynamic_rows(
        self,
        symbols: list[str],
        market_context: dict[str, _MarketContextSignal],
        *,
        scan_id: str,
    ) -> dict[str, dict[str, Any]]:
        if not symbols:
            return {}

        listing_map = self._fetch_listing_map(symbols)
        price_map = {
            symbol: signal.price
            for symbol, signal in market_context.items()
            if symbol in symbols and signal.price is not None
        }
        metadata_map = self.metadata_provider.fetch_metadata(
            symbols,
            max_workers=int(self._setting("universe_metadata_workers", 8)),
            prices=price_map,
        )

        rows: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            row = self._build_dynamic_row(
                symbol,
                listing_map.get(symbol),
                metadata_map.get(symbol),
                scan_id=scan_id,
                fallback_price=price_map.get(symbol),
            )
            if row is not None:
                rows[symbol] = row
        return rows

    def _fetch_listing_map(self, symbols: list[str]) -> dict[str, ListingRecord]:
        try:
            listings = self.listing_provider.fetch(limit=None)
        except Exception:
            return {}
        wanted = {symbol.upper() for symbol in symbols}
        return {record.symbol.upper(): record for record in listings if record.symbol.upper() in wanted}

    def _build_dynamic_row(
        self,
        symbol: str,
        listing: ListingRecord | None,
        metadata: MetadataRecord | None,
        *,
        scan_id: str,
        fallback_price: float | None,
    ) -> dict[str, Any] | None:
        company_name = listing.company_name if listing is not None else symbol
        security_name = company_name.lower()
        if self._setting("exclude_preferred", True) and "preferred" in security_name:
            return None
        if self._setting("exclude_warrants", True) and any(
            keyword in security_name for keyword in ("warrant", "warrants")
        ):
            return None
        if self._setting("exclude_rights", True) and any(
            keyword in security_name for keyword in (" right", " rights")
        ):
            return None
        if self._setting("exclude_spacs", True) and any(
            keyword in security_name
            for keyword in ("acquisition corp", "acquisition corporation", "blank check")
        ):
            return None

        price = metadata.price if metadata and metadata.price is not None else fallback_price
        if not self._price_in_scope(price):
            return None

        if metadata and metadata.quote_type and metadata.quote_type.upper() not in {"EQUITY", "MUTUALFUND"}:
            return None
        if not self.settings.market_cap_in_scope(metadata.market_cap if metadata else None):
            return None
        if not self.settings.float_shares_in_scope(metadata.float_shares if metadata else None):
            return None

        return {
            "scan_id": scan_id,
            "symbol": symbol,
            "company_name": company_name,
            "exchange": listing.exchange if listing is not None else "UNKNOWN",
            "quote_type": metadata.quote_type if metadata is not None else None,
            "price": price,
            "market_cap": metadata.market_cap if metadata is not None else None,
            "float_shares": metadata.float_shares if metadata is not None else None,
            "shares_outstanding": metadata.shares_outstanding if metadata is not None else None,
            "sector": metadata.sector if metadata is not None else None,
            "industry": metadata.industry if metadata is not None else None,
            "recent_filings_summary": None,
            "passed_filters": 1,
            "filter_reasons": "[]",
        }

    def _price_in_scope(self, price: float | None) -> bool:
        return self.settings.in_price_scope(price)

    def _screenable_symbol(self, symbol: str, price: float | None) -> bool:
        return bool(symbol) and self._price_in_scope(price)

    def _close_live_provider(self, provider: object) -> None:
        close = getattr(provider, "close", None)
        if callable(close):
            close()

    def _setting(self, name: str, default: Any) -> Any:
        return getattr(self.settings, name, default)
