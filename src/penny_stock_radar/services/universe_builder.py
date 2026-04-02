from __future__ import annotations

from pathlib import Path

from ..config import AppSettings
from ..db import create_snapshot_run, init_database, insert_universe_candidates
from ..models import ListingRecord, MetadataRecord, SnapshotRun, UniverseCandidate
from ..providers.listings import NasdaqTraderListingProvider
from ..providers.metadata import YFinanceMetadataProvider
from ..universe import filter_universe


class UniverseBuilder:
    def __init__(
        self,
        settings: AppSettings,
        listing_provider: NasdaqTraderListingProvider | None = None,
        metadata_provider: YFinanceMetadataProvider | None = None,
    ) -> None:
        self.settings = settings
        cache_dir = getattr(settings, "cache_dir", Path("data/cache"))
        self.listing_provider = listing_provider or NasdaqTraderListingProvider(
            settings.nasdaq_trader_url,
            cache_path=cache_dir / "nasdaqtraded.txt",
        )
        self.metadata_provider = metadata_provider or YFinanceMetadataProvider()

    def run(self, max_symbols: int | None = None) -> tuple[SnapshotRun, list[UniverseCandidate]]:
        init_database(self.settings.database_path)
        seed_limit = max_symbols or self.settings.universe_max_seed_symbols
        listings = self.listing_provider.fetch(limit=seed_limit)
        filtered_listings = [listing for listing in listings if not self._drop_listing(listing)]

        price_map = self.metadata_provider.fetch_prices(
            [listing.symbol for listing in filtered_listings]
        )

        price_candidates = []
        rejected_by_price = []
        for listing in filtered_listings:
            price = price_map.get(listing.symbol)
            if price is None:
                rejected_by_price.append(self._candidate_from_listing(listing, ["missing_price"]))
                continue
            if not self._price_in_range(price):
                rejected_by_price.append(
                    self._candidate_from_listing(
                        listing, [f"price_out_of_range:{price:.2f}"], price=price
                    )
                )
                continue
            price_candidates.append(listing)

        metadata_map = self.metadata_provider.fetch_metadata(
            [listing.symbol for listing in price_candidates],
            max_workers=self.settings.universe_metadata_workers,
            prices=price_map,
        )

        enriched_candidates = [
            self._build_candidate(listing, metadata_map.get(listing.symbol))
            for listing in price_candidates
        ]
        final_candidates = self._apply_pass_fail(enriched_candidates)
        final_candidates.extend(rejected_by_price)

        snapshot = create_snapshot_run(
            self.settings.database_path,
            source="nasdaq_trader+yfinance",
            symbol_count=len(final_candidates),
        )
        insert_universe_candidates(
            self.settings.database_path,
            snapshot.snapshot_id,
            final_candidates,
        )
        return snapshot, final_candidates

    def export_json(self, candidates: list[UniverseCandidate], export_path: Path) -> None:
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text(
            "[\n"
            + ",\n".join(candidate.model_dump_json(indent=2) for candidate in candidates)
            + "\n]\n",
            encoding="utf-8",
        )

    def _drop_listing(self, listing: ListingRecord) -> bool:
        if listing.is_test_issue or listing.is_nextshares:
            return True
        if self.settings.exclude_etfs and listing.is_etf:
            return True
        return False

    def _price_in_range(self, price: float) -> bool:
        return self.settings.in_price_scope(price)

    def _build_candidate(
        self,
        listing: ListingRecord,
        metadata: MetadataRecord | None,
    ) -> UniverseCandidate:
        reasons: list[str] = []
        quote_type = metadata.quote_type if metadata else None
        market_cap = metadata.market_cap if metadata else None
        float_shares = metadata.float_shares if metadata else None
        shares_outstanding = metadata.shares_outstanding if metadata else None
        price = metadata.price if metadata else None

        security_name = listing.company_name.lower()
        if self.settings.exclude_preferred and "preferred" in security_name:
            reasons.append("preferred_security")
        if self.settings.exclude_warrants and any(
            keyword in security_name for keyword in ("warrant", "warrants")
        ):
            reasons.append("warrant_security")
        if self.settings.exclude_rights and any(
            keyword in security_name for keyword in (" right", " rights")
        ):
            reasons.append("rights_security")
        if self.settings.exclude_spacs and any(
            keyword in security_name
            for keyword in ("acquisition corp", "acquisition corporation", "blank check")
        ):
            reasons.append("spac_security")
        if quote_type and quote_type.upper() not in {"EQUITY", "MUTUALFUND"}:
            reasons.append(f"quote_type:{quote_type}")
        if not self.settings.market_cap_in_scope(market_cap):
            reasons.append("market_cap_too_large")
        if not self.settings.float_shares_in_scope(float_shares):
            reasons.append("float_too_large")

        return UniverseCandidate(
            symbol=listing.symbol,
            company_name=listing.company_name,
            exchange=listing.exchange,
            quote_type=quote_type,
            price=price,
            market_cap=market_cap,
            float_shares=float_shares,
            shares_outstanding=shares_outstanding,
            sector=metadata.sector if metadata else None,
            industry=metadata.industry if metadata else None,
            recent_filings_summary=None,
            passed_filters=not reasons,
            filter_reasons=reasons,
        )

    def _apply_pass_fail(
        self, candidates: list[UniverseCandidate]
    ) -> list[UniverseCandidate]:
        gate_ready_records = []
        for candidate in candidates:
            gate_ready_records.append(
                {
                    "ticker": candidate.symbol,
                    "price": candidate.price,
                    "market_cap": candidate.market_cap,
                    "float_shares": candidate.float_shares,
                    "is_etf": candidate.quote_type == "ETF",
                    "is_preferred": "preferred_security" in candidate.filter_reasons,
                    "is_warrant": "warrant_security" in candidate.filter_reasons,
                    "is_right": "rights_security" in candidate.filter_reasons,
                    "is_spac": "spac_security" in candidate.filter_reasons,
                }
            )
        allowed = {
            row["ticker"]
            for row in filter_universe(
                gate_ready_records,
                type(
                    "UniverseGateConfig",
                    (),
                    {
                        "price_min": self.settings.universe_min_price,
                        "price_max": self.settings.scope_price_max,
                        "market_cap_max": self.settings.scope_market_cap_max,
                        "float_shares_max": self.settings.scope_float_shares_max,
                        "exclude_etf": self.settings.exclude_etfs,
                        "exclude_preferred": self.settings.exclude_preferred,
                        "exclude_warrants": self.settings.exclude_warrants,
                        "exclude_rights": self.settings.exclude_rights,
                        "exclude_spac": self.settings.exclude_spacs,
                    },
                )(),
            )
        }
        final: list[UniverseCandidate] = []
        for candidate in candidates:
            passed = candidate.symbol in allowed and not candidate.filter_reasons
            if not passed and not candidate.filter_reasons:
                candidate.filter_reasons.append("rules_filter_rejected")
            candidate.passed_filters = passed
            final.append(candidate)
        return final

    def _candidate_from_listing(
        self,
        listing: ListingRecord,
        reasons: list[str],
        price: float | None = None,
    ) -> UniverseCandidate:
        return UniverseCandidate(
            symbol=listing.symbol,
            company_name=listing.company_name,
            exchange=listing.exchange,
            price=price,
            passed_filters=False,
            filter_reasons=reasons,
        )
