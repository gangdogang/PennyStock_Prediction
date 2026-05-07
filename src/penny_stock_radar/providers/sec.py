from __future__ import annotations

from functools import lru_cache
from typing import Any

import httpx


class SecDataProvider:
    """Minimal SEC data client with a required user agent."""

    def __init__(
        self,
        company_tickers_url: str,
        submissions_url_template: str,
        filing_url_template: str,
        user_agent: str,
    ) -> None:
        self.company_tickers_url = company_tickers_url
        self.submissions_url_template = submissions_url_template
        self.filing_url_template = filing_url_template
        self.client = httpx.Client(
            headers={
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            follow_redirects=True,
            timeout=30.0,
        )

    @lru_cache(maxsize=1)
    def fetch_company_tickers(self) -> dict[str, dict[str, str]]:
        response = self.client.get(self.company_tickers_url)
        response.raise_for_status()
        payload = response.json()
        mapping: dict[str, dict[str, str]] = {}
        for item in payload.values():
            ticker = str(item.get("ticker", "")).upper()
            cik = str(item.get("cik_str", "")).strip()
            title = str(item.get("title", "")).strip()
            if ticker and cik:
                mapping[ticker] = {
                    "cik": cik.zfill(10),
                    "title": title,
                }
        return mapping

    @lru_cache(maxsize=256)
    def fetch_submissions(self, cik: str) -> dict[str, Any]:
        response = self.client.get(self.submissions_url_template.format(cik=cik))
        response.raise_for_status()
        return response.json()

    def build_filing_url(
        self,
        cik: str,
        accession_number: str,
        primary_document: str | None,
    ) -> str:
        document = primary_document or ""
        return self.filing_url_template.format(
            cik_number=str(int(cik)),
            accession_no=accession_number.replace("-", ""),
            document=document,
        )

    def fetch_filing_text(self, filing_url: str) -> str:
        response = self.client.get(filing_url)
        response.raise_for_status()
        return response.text
