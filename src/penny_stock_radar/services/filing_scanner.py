from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from ..config import AppSettings
from ..models import FilingMatch
from ..providers.sec import SecDataProvider
from ..utils.theme import extract_themes

TARGET_FORMS = {"8-K", "S-1", "S-3", "424B5", "RW"}
KEYWORDS = (
    "minimum bid",
    "compliance",
    "deficiency",
    "contract",
    "purchase agreement",
    "merger",
    "letter of intent",
    "loi",
    "reverse stock split",
    "reverse split",
    "registration statement",
    "prospectus supplement",
    "withdrawal",
)
ITEM_PATTERN = re.compile(r"item\s+(\d+\.\d+)", re.IGNORECASE)


class FilingScanner:
    def __init__(
        self,
        settings: AppSettings,
        provider: SecDataProvider | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider or SecDataProvider(
            company_tickers_url=settings.sec_company_tickers_url,
            submissions_url_template=settings.sec_submissions_url_template,
            filing_url_template=settings.sec_archive_filing_url_template,
            user_agent=settings.sec_user_agent,
        )

    def scan_symbols(
        self,
        symbols: list[str],
        lookback_hours: int | None = None,
        filed_at_cutoff: datetime | None = None,
    ) -> list[FilingMatch]:
        ticker_map = self.provider.fetch_company_tickers()
        max_filed_at = filed_at_cutoff.astimezone(UTC) if filed_at_cutoff is not None else datetime.now(UTC)
        min_filed_at = max_filed_at - timedelta(
            hours=lookback_hours or self.settings.filings_lookback_hours
        )

        matches: list[FilingMatch] = []
        for symbol in symbols:
            company = ticker_map.get(symbol.upper())
            if not company:
                continue
            cik = company["cik"]
            try:
                submissions = self.provider.fetch_submissions(cik)
            except Exception:
                continue
            recent = submissions.get("filings", {}).get("recent", {})
            for filing in self._iter_recent_filings(
                symbol.upper(),
                cik,
                recent,
                min_filed_at=min_filed_at,
                max_filed_at=max_filed_at,
            ):
                matches.append(filing)
        return matches

    def _iter_recent_filings(
        self,
        symbol: str,
        cik: str,
        recent: dict[str, list[str]],
        *,
        min_filed_at: datetime,
        max_filed_at: datetime,
    ) -> list[FilingMatch]:
        forms = recent.get("form", [])
        results: list[FilingMatch] = []
        total = len(forms)
        for index in range(total):
            form = str(forms[index]).upper()
            if form not in TARGET_FORMS:
                continue
            filing_date = recent.get("filingDate", [None] * total)[index]
            acceptance = recent.get("acceptanceDateTime", [None] * total)[index]
            if not self._is_within_window(
                filing_date,
                acceptance,
                min_filed_at=min_filed_at,
                max_filed_at=max_filed_at,
            ):
                continue
            accession = recent.get("accessionNumber", [None] * total)[index]
            if not accession:
                continue
            primary_document = recent.get("primaryDocument", [None] * total)[index]
            description = recent.get("primaryDocDescription", [None] * total)[index]
            filing_url = self.provider.build_filing_url(cik, accession, primary_document)

            matched_keywords = self._match_keywords(description or "")
            if form == "8-K" and not matched_keywords:
                filing_text = self._fetch_filing_text(filing_url)
                matched_keywords = self._match_keywords(filing_text)
                if not matched_keywords:
                    continue
                item_numbers = self._extract_item_numbers(filing_text)
            elif form in {"S-1", "S-3", "424B5", "RW"} and not matched_keywords:
                filing_text = self._fetch_filing_text(filing_url)
                matched_keywords = self._match_keywords(filing_text)
                item_numbers = self._extract_item_numbers(filing_text)
            else:
                filing_text = description or ""
                item_numbers = self._extract_item_numbers(filing_text)

            themes = extract_themes(description, filing_text, " ".join(matched_keywords))

            summary = self._build_summary(form, description, matched_keywords, item_numbers, themes)
            results.append(
                FilingMatch(
                    symbol=symbol,
                    cik=cik,
                    form=form,
                    filing_date=str(filing_date),
                    acceptance_datetime=str(acceptance) if acceptance else None,
                    accession_number=str(accession),
                    primary_document=str(primary_document) if primary_document else None,
                    primary_doc_description=str(description) if description else None,
                    filing_url=filing_url,
                    item_numbers=item_numbers,
                    themes=themes,
                    matched_keywords=matched_keywords,
                    summary=summary,
                )
            )
        return results

    def _is_within_window(
        self,
        filing_date: str | None,
        acceptance: str | None,
        *,
        min_filed_at: datetime,
        max_filed_at: datetime,
    ) -> bool:
        dt = None
        if acceptance:
            acceptance = acceptance.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(acceptance)
            except ValueError:
                dt = None
        if dt is None and filing_date:
            try:
                dt = datetime.fromisoformat(filing_date).replace(tzinfo=UTC)
            except ValueError:
                dt = None
        return dt is not None and min_filed_at <= dt <= max_filed_at

    def _fetch_filing_text(self, filing_url: str) -> str:
        try:
            text = self.provider.fetch_filing_text(filing_url)
        except Exception:
            return ""
        return re.sub(r"<[^>]+>", " ", text).lower()

    def _match_keywords(self, text: str) -> list[str]:
        lower_text = text.lower()
        return [keyword for keyword in KEYWORDS if keyword in lower_text]

    def _extract_item_numbers(self, text: str) -> list[str]:
        matches = ITEM_PATTERN.findall(text or "")
        return sorted(set(matches))

    def _build_summary(
        self,
        form: str,
        description: str | None,
        matched_keywords: list[str],
        item_numbers: list[str],
        themes: list[str],
    ) -> str:
        parts = [form]
        if description:
            parts.append(description.strip())
        if item_numbers:
            parts.append("items=" + ", ".join(item_numbers))
        if matched_keywords:
            parts.append("keywords=" + ", ".join(sorted(set(matched_keywords))))
        if themes:
            parts.append("themes=" + ", ".join(sorted(set(themes))))
        return " | ".join(parts)
