from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Iterable

import httpx

from ..config import AppSettings
from ..models import MarketActivity, PaperPosition


@dataclass(slots=True)
class MomentumAdvice:
    symbol: str
    stance: str = "watch"
    conviction: float = 0.5
    note: str = ""
    risks: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MomentumAdviceBundle:
    summary: str = ""
    items: dict[str, MomentumAdvice] = field(default_factory=dict)

    @property
    def notes(self) -> list[str]:
        notes: list[str] = []
        if self.summary:
            notes.append(f"gemini_summary:{self.summary[:120]}")
        for symbol, advice in self.items.items():
            note_text = advice.note[:96] if advice.note else advice.stance
            notes.append(f"gemini:{symbol}:{advice.stance}:{advice.conviction:.2f}:{note_text}")
        return notes


@dataclass(slots=True)
class _QuantReview:
    ticker: str
    analysis_summary: str
    technical_signal: str
    confidence_score: int
    key_indicators: dict[str, str]
    risk_factors: list[str]
    final_strategy: str
    review_model: str = ""
    escalated: bool = False


class GeminiMomentumAdvisor:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        max_candidates: int = 3,
        min_refresh_seconds: int = 180,
        escalation_enabled: bool = True,
        escalation_model: str | None = None,
        escalation_limit: int = 1,
        escalation_min_confidence: float = 45.0,
        escalation_max_confidence: float = 75.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_candidates = max(int(max_candidates), 1)
        self.min_refresh_seconds = max(int(min_refresh_seconds), 0)
        self.escalation_enabled = escalation_enabled
        self.escalation_model = escalation_model or None
        self.escalation_limit = max(int(escalation_limit), 0)
        self.escalation_min_confidence = float(escalation_min_confidence)
        self.escalation_max_confidence = float(escalation_max_confidence)
        self._client = client or httpx.Client(timeout=20.0)
        self._cached_fingerprint: str | None = None
        self._cached_at: datetime | None = None
        self._cached_bundle: MomentumAdviceBundle | None = None

    def review(
        self,
        *,
        market_phase: str,
        candidates: Iterable[MarketActivity],
        open_positions: Iterable[PaperPosition],
    ) -> MomentumAdviceBundle:
        target_candidates = list(candidates)[: self.max_candidates]
        open_position_rows = [row for row in open_positions if row.status == "OPEN"]
        if not target_candidates:
            return MomentumAdviceBundle()

        now = datetime.now(timezone.utc)
        fingerprint = self._fingerprint(
            market_phase=market_phase,
            candidates=target_candidates,
            open_positions=open_position_rows,
        )
        if (
            self._cached_bundle is not None
            and self._cached_fingerprint == fingerprint
            and self._cached_at is not None
            and (now - self._cached_at).total_seconds() < self.min_refresh_seconds
        ):
            return self._cached_bundle

        quant_reviews_by_symbol: dict[str, _QuantReview] = {}
        for candidate in target_candidates:
            quant_review = self._review_candidate(
                market_phase=market_phase,
                candidate=candidate,
                open_positions=open_position_rows,
                model_name=self.model,
            )
            quant_reviews_by_symbol[quant_review.ticker] = quant_review

        if self._can_escalate():
            escalated_count = 0
            for candidate in target_candidates:
                review = quant_reviews_by_symbol.get(candidate.symbol)
                if review is None or not self._is_ambiguous(candidate, review):
                    continue
                if escalated_count >= self.escalation_limit:
                    break
                try:
                    escalated_review = self._review_candidate(
                        market_phase=market_phase,
                        candidate=candidate,
                        open_positions=open_position_rows,
                        model_name=self.escalation_model or self.model,
                        prior_review=review,
                    )
                    escalated_review.escalated = True
                    quant_reviews_by_symbol[escalated_review.ticker] = escalated_review
                    escalated_count += 1
                except Exception:
                    continue

        quant_reviews = [
            quant_reviews_by_symbol[candidate.symbol]
            for candidate in target_candidates
            if candidate.symbol in quant_reviews_by_symbol
        ]
        items = {
            review.ticker: self._to_momentum_advice(review)
            for review in quant_reviews
        }

        bundle = MomentumAdviceBundle(
            summary=self._bundle_summary(quant_reviews),
            items=items,
        )
        self._cached_fingerprint = fingerprint
        self._cached_at = now
        self._cached_bundle = bundle
        return bundle

    def _fingerprint(
        self,
        *,
        market_phase: str,
        candidates: list[MarketActivity],
        open_positions: Iterable[PaperPosition],
    ) -> str:
        payload = {
            "market_phase": market_phase,
            "candidates": [
                {
                    "symbol": row.symbol,
                    "price": round(float(row.last_price or 0.0), 2),
                    "pct_change": round(float(row.pct_change or 0.0), 2),
                    "pct_rank": int(row.pct_rank or 0),
                    "volume_rank": int(row.volume_rank or 0),
                    "analysis_label": row.analysis_label,
                    "analysis_score": round(float(row.analysis_score or 0.0), 2),
                }
                for row in candidates
            ],
            "open_positions": [
                {
                    "symbol": row.symbol,
                    "entry_price": round(float(row.average_entry_price), 2),
                    "quantity": int(row.quantity),
                }
                for row in open_positions
                if row.status == "OPEN"
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()

    def _build_prompt(
        self,
        *,
        market_phase: str,
        candidate: MarketActivity,
        open_positions: Iterable[PaperPosition],
        prior_review: _QuantReview | None = None,
    ) -> str:
        open_lines = []
        for row in open_positions:
            open_lines.append(
                f"- {row.symbol}: avg_entry={row.average_entry_price}, last={row.last_price}, qty={row.quantity}, add_count={row.add_count}"
            )

        prior_block = ""
        if prior_review is not None:
            prior_block = (
                "1차 모델 판단:\n"
                f"- model: {prior_review.review_model}\n"
                f"- technical_signal: {prior_review.technical_signal}\n"
                f"- confidence_score: {prior_review.confidence_score}\n"
                f"- final_strategy: {prior_review.final_strategy}\n"
                f"- risk_factors: {', '.join(prior_review.risk_factors[:4]) or 'none'}\n\n"
                "위 1차 판단을 비판적으로 재검토하고, 최종 판정을 더 엄격하게 내려라.\n\n"
            )

        return "".join(
            [
                "당신은 20년 경력의 베테랑 퀀트 분석가이자 금융 데이터 전문가다.\n",
                "감정적 추측을 배제하고 오직 제공된 수치와 문맥만 사용한다.\n",
                "기술적/뉴스 정보가 부족하면 억지로 채우지 말고 N/A 또는 insufficient_data로 표시한다.\n",
                "급등주 추격 여부를 매우 냉정하게 판단하되, 값싼 모델의 과신을 피하기 위해 리스크를 분명히 적는다.\n",
                f"현재 장 상태: {market_phase}\n\n",
                prior_block,
                "분석 대상 데이터:\n",
                f"- ticker: {candidate.symbol}\n",
                f"- price: {candidate.last_price}\n",
                f"- previous_close: {candidate.previous_close}\n",
                f"- pct_change: {candidate.pct_change}\n",
                f"- pct_rank: {candidate.pct_rank}\n",
                f"- volume_rank: {candidate.volume_rank}\n",
                f"- dollar_volume: {candidate.dollar_volume}\n",
                f"- spread_pct: {candidate.spread_pct}\n",
                f"- analysis_label: {candidate.analysis_label}\n",
                f"- analysis_score: {candidate.analysis_score}\n",
                f"- predicted_context: {candidate.predicted}\n",
                f"- reasons: {', '.join(candidate.reasons[:6]) or 'none'}\n\n",
                "열린 포지션:\n",
                ("\n".join(open_lines) if open_lines else "- none"),
                "\n\n",
                "반드시 JSON 객체 하나만 반환한다. 마크다운 금지.\n",
                "JSON 구조:\n",
                '{',
                '"ticker":"종목 코드",',
                '"analysis_summary":"3줄 요약",',
                '"technical_signal":"Buy / Sell / Hold / Neutral",',
                '"confidence_score":0,',
                '"key_indicators":{"RSI":"값 또는 N/A","Moving_Average":"상태 또는 N/A"},',
                '"risk_factors":["리스크1","리스크2"],',
                '"final_strategy":"최종 투자 전략 권고"',
                '}\n',
            ]
        )

    def _review_candidate(
        self,
        *,
        market_phase: str,
        candidate: MarketActivity,
        open_positions: list[PaperPosition],
        model_name: str,
        prior_review: _QuantReview | None = None,
    ) -> _QuantReview:
        prompt = self._build_prompt(
            market_phase=market_phase,
            candidate=candidate,
            open_positions=open_positions,
            prior_review=prior_review,
        )
        text = self._generate_json(prompt, use_schema=True, model_name=model_name)
        return self._parse_quant_review(text, model_name=model_name)

    def _generate_json(self, prompt: str, *, use_schema: bool, model_name: str) -> str:
        generation_config: dict[str, object] = {
            "maxOutputTokens": 800,
            "responseMimeType": "application/json",
        }
        if not model_name.startswith("gemini-3"):
            generation_config["temperature"] = 0.2
        if use_schema:
            generation_config["responseSchema"] = self._response_schema()

        response = self._client.post(
            f"{self.base_url}/models/{model_name}:generateContent",
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": generation_config,
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if use_schema:
                return self._generate_json(prompt, use_schema=False, model_name=model_name)
            detail = exc.response.text[:1000] if exc.response is not None else str(exc)
            raise RuntimeError(f"Gemini momentum advisor failed: {detail}") from exc

        payload = response.json()
        text_fragments: list[str] = []
        for candidate in payload.get("candidates", []):
            content = candidate.get("content", {})
            for part in content.get("parts", []):
                text = part.get("text")
                if text:
                    text_fragments.append(str(text))
        if not text_fragments:
            raise RuntimeError("Gemini momentum advisor returned no text.")
        return "".join(text_fragments)

    def _response_schema(self) -> dict[str, object]:
        return {
            "type": "OBJECT",
            "required": [
                "ticker",
                "analysis_summary",
                "technical_signal",
                "confidence_score",
                "key_indicators",
                "risk_factors",
                "final_strategy",
            ],
            "properties": {
                "ticker": {"type": "STRING"},
                "analysis_summary": {"type": "STRING"},
                "technical_signal": {"type": "STRING"},
                "confidence_score": {"type": "INTEGER"},
                "key_indicators": {
                    "type": "OBJECT",
                    "required": ["RSI", "Moving_Average"],
                    "properties": {
                        "RSI": {"type": "STRING"},
                        "Moving_Average": {"type": "STRING"},
                    },
                },
                "risk_factors": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                },
                "final_strategy": {"type": "STRING"},
            },
        }

    def _parse_quant_review(self, raw_text: str, *, model_name: str) -> _QuantReview:
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3:
                text = "\n".join(lines[1:-1]).strip()
        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        data = json.loads(text)
        ticker = str(data.get("ticker") or "").upper().strip()
        if not ticker:
            raise RuntimeError("Gemini momentum advisor JSON did not include a ticker.")
        technical_signal = str(data.get("technical_signal") or "Neutral").strip()
        try:
            confidence_score = int(float(data.get("confidence_score", 0)))
        except (TypeError, ValueError):
            confidence_score = 0
        confidence_score = min(max(confidence_score, 0), 100)
        indicators = data.get("key_indicators") or {}
        if not isinstance(indicators, dict):
            indicators = {}
        risk_factors = data.get("risk_factors") or []
        if not isinstance(risk_factors, list):
            risk_factors = [str(risk_factors)]
        return _QuantReview(
            ticker=ticker,
            analysis_summary=str(data.get("analysis_summary") or "").strip(),
            technical_signal=technical_signal,
            confidence_score=confidence_score,
            key_indicators={
                "RSI": str(indicators.get("RSI") or "N/A"),
                "Moving_Average": str(indicators.get("Moving_Average") or "N/A"),
            },
            risk_factors=[str(item).strip() for item in risk_factors if str(item).strip()],
            final_strategy=str(data.get("final_strategy") or "").strip(),
            review_model=model_name,
        )

    def _to_momentum_advice(self, review: _QuantReview) -> MomentumAdvice:
        signal = review.technical_signal.strip().lower()
        conviction = review.confidence_score / 100.0
        if signal == "sell":
            stance = "avoid"
        elif signal == "buy" and conviction >= 0.60:
            stance = "buy"
        else:
            stance = "watch"
        note_parts = [
            review.final_strategy,
            review.analysis_summary.splitlines()[0] if review.analysis_summary else "",
        ]
        note = " | ".join(part for part in note_parts if part).strip()
        return MomentumAdvice(
            symbol=review.ticker,
            stance=stance,
            conviction=conviction,
            note=note[:160],
            risks=review.risk_factors[:4]
            + ([f"review_model:{review.review_model}"] if review.review_model else [])
            + (["escalated_review"] if review.escalated else []),
        )

    def _bundle_summary(self, reviews: list[_QuantReview]) -> str:
        if not reviews:
            return ""
        labels = []
        for review in reviews:
            labels.append(
                f"{review.ticker}:{review.technical_signal}:{review.confidence_score}:{review.review_model}"
            )
        return " / ".join(labels[:3])

    def _can_escalate(self) -> bool:
        return (
            self.escalation_enabled
            and bool(self.escalation_model)
            and self.escalation_model != self.model
            and self.escalation_limit > 0
        )

    def _is_ambiguous(self, candidate: MarketActivity, review: _QuantReview) -> bool:
        signal = review.technical_signal.strip().lower()
        if signal in {"hold", "neutral"}:
            return True
        if self.escalation_min_confidence <= review.confidence_score <= self.escalation_max_confidence:
            return True
        if candidate.analysis_label == "NEWS_CHECK_FIRST" and review.confidence_score < 85:
            return True
        return False


def build_momentum_advisor(settings: AppSettings) -> GeminiMomentumAdvisor | None:
    if not settings.paper_ai_consensus_enabled or not settings.gemini_api_key:
        return None
    return GeminiMomentumAdvisor(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        base_url=settings.gemini_api_base_url,
        max_candidates=settings.paper_ai_consensus_limit,
        min_refresh_seconds=settings.paper_ai_refresh_seconds,
        escalation_enabled=settings.paper_ai_escalation_enabled,
        escalation_model=settings.paper_ai_escalation_model,
        escalation_limit=settings.paper_ai_escalation_limit,
        escalation_min_confidence=settings.paper_ai_escalation_min_confidence,
        escalation_max_confidence=settings.paper_ai_escalation_max_confidence,
    )
