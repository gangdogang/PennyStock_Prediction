from __future__ import annotations

from penny_stock_radar.providers.sec import SecDataProvider


def test_sec_provider_does_not_force_host_header_for_data_sec_requests() -> None:
    provider = SecDataProvider(
        company_tickers_url="https://www.sec.gov/files/company_tickers.json",
        submissions_url_template="https://data.sec.gov/submissions/CIK{cik}.json",
        filing_url_template="https://www.sec.gov/Archives/edgar/data/{cik_number}/{accession_no}/{document}",
        user_agent="PennyStockRadar/0.1 test@example.com",
    )

    assert "host" not in provider.client.headers
    assert provider.client.headers["user-agent"] == "PennyStockRadar/0.1 test@example.com"
