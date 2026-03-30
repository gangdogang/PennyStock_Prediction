from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from penny_stock_radar.models import MarketActivity
from penny_stock_radar.services.momentum_advisor import GeminiMomentumAdvisor


class _FakeResponse:
    def __init__(self, text: str, *, should_raise: bool = False) -> None:
        self._text = text
        self._should_raise = should_raise

    def raise_for_status(self) -> None:
        if self._should_raise:
            raise RuntimeError("request failed")
        return None

    def json(self) -> dict[str, object]:
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": self._text,
                            }
                        ]
                    }
                }
            ]
        }


class _FakeClient:
    def __init__(self, text: str | dict[str, object]) -> None:
        self.text = text
        self.requests: list[dict[str, object]] = []

    def post(self, url: str, headers: dict[str, str], json: dict[str, object]):
        self.requests.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
            }
        )
        if isinstance(self.text, dict):
            model_name = urlparse(url).path.split("/models/")[1].split(":generateContent")[0]
            payload = self.text.get(model_name)
            if isinstance(payload, tuple):
                text, should_raise = payload
                return _FakeResponse(str(text), should_raise=bool(should_raise))
            return _FakeResponse(str(payload))
        return _FakeResponse(self.text)


def _candidate(symbol: str = "MEGA") -> MarketActivity:
    return MarketActivity(
        symbol=symbol,
        market_phase="premarket",
        source="test",
        last_price=18.4,
        previous_close=12.0,
        pct_change=53.3,
        volume=3_200_000,
        dollar_volume=58_880_000,
        trade_size=4_000,
        spread_pct=0.015,
        market_status="open",
        market_data_at=datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc),
        pct_rank=1,
        volume_rank=2,
        predicted=False,
        analysis_label="NEWS_CHECK_FIRST",
        analysis_score=3.7,
        reasons=["pct_leader", "live_dollar_volume_strong", "news_check_required"],
    )


def test_gemini_momentum_advisor_requests_structured_quant_json() -> None:
    client = _FakeClient(
        """
        {
          "ticker": "MEGA",
          "analysis_summary": "강한 갭과 거래대금이 확인된다.\\n다만 뉴스 확인 전 추격은 리스크가 있다.\\n초반 눌림 이후 재강세 여부가 핵심이다.",
          "technical_signal": "Buy",
          "confidence_score": 78,
          "key_indicators": {
            "RSI": "N/A",
            "Moving_Average": "N/A"
          },
          "risk_factors": [
            "뉴스 팩트 미확인 상태",
            "초반 과열 추격 리스크"
          ],
          "final_strategy": "첫 눌림 이후 거래대금 유지 시에만 제한적으로 진입"
        }
        """
    )
    advisor = GeminiMomentumAdvisor(
        api_key="test-key",
        model="gemini-3-flash-preview",
        base_url="https://example.test/v1beta",
        client=client,
    )

    bundle = advisor.review(
        market_phase="premarket",
        candidates=[_candidate()],
        open_positions=[],
    )

    request = client.requests[0]["json"]
    generation_config = request["generationConfig"]
    assert generation_config["responseMimeType"] == "application/json"
    assert "responseSchema" in generation_config
    assert "temperature" not in generation_config
    assert bundle.items["MEGA"].stance == "buy"
    assert bundle.items["MEGA"].conviction == 0.78
    assert "첫 눌림 이후" in bundle.items["MEGA"].note


def test_gemini_momentum_advisor_escalates_ambiguous_candidate_to_stronger_model() -> None:
    client = _FakeClient(
        {
            "gemini-3-flash-preview": """
            {
              "ticker": "MEGA",
              "analysis_summary": "강한 급등이지만 과열 여부가 애매하다.\\n거래대금은 충분하다.\\n추가 확인이 필요하다.",
              "technical_signal": "Hold",
              "confidence_score": 58,
              "key_indicators": {
                "RSI": "N/A",
                "Moving_Average": "N/A"
              },
              "risk_factors": [
                "과열 추격 가능성",
                "뉴스 사실 확인 부족"
              ],
              "final_strategy": "바로 추격하지 말고 재확인"
            }
            """,
            "gemini-2.5-pro": """
            {
              "ticker": "MEGA",
              "analysis_summary": "강한 거래대금과 상위 랭크가 유지된다.\\n다만 초기 변동성은 크다.\\n첫 눌림 이후 재강세 시나리오가 더 우세하다.",
              "technical_signal": "Buy",
              "confidence_score": 86,
              "key_indicators": {
                "RSI": "N/A",
                "Moving_Average": "N/A"
              },
              "risk_factors": [
                "뉴스 세부 내용 미확인",
                "초반 변동성 확대"
              ],
              "final_strategy": "첫 눌림 이후 거래대금 유지 시 제한적으로 진입"
            }
            """,
        }
    )
    advisor = GeminiMomentumAdvisor(
        api_key="test-key",
        model="gemini-3-flash-preview",
        base_url="https://example.test/v1beta",
        escalation_enabled=True,
        escalation_model="gemini-2.5-pro",
        escalation_limit=1,
        escalation_min_confidence=45,
        escalation_max_confidence=75,
        client=client,
    )

    bundle = advisor.review(
        market_phase="premarket",
        candidates=[_candidate()],
        open_positions=[],
    )

    assert len(client.requests) == 2
    assert "gemini-3-flash-preview" in client.requests[0]["url"]
    assert "gemini-2.5-pro" in client.requests[1]["url"]
    assert bundle.items["MEGA"].stance == "buy"
    assert bundle.items["MEGA"].conviction == 0.86
    assert "escalated_review" in bundle.items["MEGA"].risks
