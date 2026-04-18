from __future__ import annotations

from typing import Any

import pandas as pd

LABEL_MAP = {
    "continuation": "지속 상승형",
    "fade": "상승 실패/Fade",
    "fakeout": "가짜 돌파",
    "ENTER": "진입",
    "WATCH": "관찰",
    "AVOID": "회피",
    "OPENING_RANGE_CANDIDATE": "시초 후보",
    "CONDITIONAL_ENTRY": "조건부 진입",
    "NEWS_CHECK_FIRST": "재료 확인 전",
    "WAIT_PULLBACK": "눌림/재돌파 대기",
    "NO_CHASE": "추격 금지",
    "matched_both": "예측 적중(상승률+거래량)",
    "matched_pct": "예측 적중(상승률)",
    "matched_volume": "예측 적중(거래량)",
    "predicted_only": "예측만 됨",
    "unpredicted_leader": "새로 떠오른 리더",
    "OPEN": "보유 중",
    "CLOSED": "종료",
    "ACTIVE": "운용 중",
    "BUY": "매수",
    "SELL": "매도",
    "ENTRY": "신규 진입",
    "ADD": "불타기",
    "EXIT": "청산",
    "SESSION_END": "장 종료 청산",
}

REASON_MAP = {
    "low_float": "유통주식수가 작음",
    "volatility_contraction": "변동성이 수렴한 상태",
    "above_sma20": "20일 이동평균선 위",
    "formal_catalyst": "공시/재료가 감지됨",
    "theme_sympathy": "같은 테마 종목이 같이 움직임",
    "dollar_volume_ok": "프리장 거래대금이 충분함",
    "trade_count_ok": "체결 수가 충분함",
    "rvol_ok": "프리장 상대거래량이 강함",
    "spread_ok": "스프레드가 과도하지 않음",
    "close_above_pm_vwap": "프리장 VWAP 위에서 마감",
    "round_level_break": "라운드 가격대를 돌파함",
    "tape_anomaly": "테이프 이상 신호가 감지됨",
    "close_above_anchored_vwap": "앵커 VWAP 위를 유지함",
    "broke_pm_high": "프리장 고점을 돌파함",
    "opening_range_breakout": "시초 구간 돌파가 나옴",
    "extension_ok": "과열 이격이 허용 범위 안쪽임",
    "failed_avwap": "앵커 VWAP 회복에 실패함",
    "mention_velocity_ok": "언급 속도가 빠르게 증가함",
    "unique_author_support": "고유 작성자 수가 받쳐줌",
    "cross_platform_sync": "여러 플랫폼에서 동시에 언급됨",
    "engagement": "반응 수치가 받쳐줌",
    "pct_leader": "상승률 상위권",
    "pct_momentum": "상승률 모멘텀이 유지됨",
    "pct_fading": "상승률이 꺾이고 있음",
    "live_dollar_volume_strong": "실시간 거래대금이 매우 강함",
    "live_dollar_volume_ok": "실시간 거래대금이 기준을 넘김",
    "spread_wide": "스프레드가 넓어 추격 리스크가 큼",
    "watchlist_predicted": "프리장 전 watchlist에 이미 있었음",
    "watchlist_score_strong": "프리장 전 셋업 점수가 높았음",
    "late_extension": "이미 많이 확장돼 늦을 수 있음",
    "predicted_watchlist": "프리장 전 예측 종목",
    "top_pct_change": "실시간 상승률 상위권",
    "top_volume": "실시간 거래량 상위권",
    "live_pct_leader": "방금 실시간 상승률 리더로 포착됨",
    "live_pct_momentum": "실시간 상승률 모멘텀이 감지됨",
    "live_volume_leader": "방금 실시간 거래량 리더로 포착됨",
    "live_volume_support": "실시간 거래량 유입이 붙고 있음",
    "recent_pct_leader": "직전 실시간 스캔에서 상승률 리더였음",
    "recent_pct_momentum": "직전 실시간 스캔에서 상승률 상위권이었음",
    "recent_volume_leader": "직전 실시간 스캔에서 거래량 리더였음",
    "recent_volume_support": "직전 실시간 스캔에서 거래량 유입이 확인됨",
    "recent_trade_quality": "직전 실시간 판독에서도 매매 후보로 남았음",
    "recent_unverified_momentum": "직전에도 급등했지만 재료 검증은 미완료였음",
    "first_pullback_only": "수직 추격 말고 첫 눌림/재돌파에서만 접근",
    "news_check_required": "뉴스/공시 같은 공개 재료 확인이 먼저 필요함",
    "context_missing": "프리장 전 예측이나 재료 맥락이 부족함",
    "wait_for_reclaim": "VWAP 또는 분봉 고점 재회복 전까지 대기",
    "no_chase": "지금 자리에서 추격 매수는 금지",
    "top5_live_leader": "지금 상위 5위 리더군 안에 있음",
    "top5_leader_persistence": "최근 몇 번의 스캔 동안 상위 5위권을 유지함",
    "leader_persistence_strong": "리더 지위를 꾸준히 유지 중임",
    "leader_reclaim": "잠깐 밀린 뒤 다시 순위권으로 치고 올라오는 중임",
    "reclaim_entry_ready": "재돌파/재점화 패턴이 확인돼 재진입 감시 가치가 높음",
    "pullback_absorption": "눌림에서 매도 물량을 흡수하는 흐름이 보임",
    "spread_expanding": "스프레드가 벌어져 추격 효율이 악화되고 있음",
    "trap_warning": "상승 흐름 대비 체결/순위가 약해져 가짜 돌파 위험이 커짐",
    "stop_loss": "손절 기준에 닿아 청산됨",
    "trailing_stop": "트레일링 스탑에 닿아 이익 보호 청산됨",
    "momentum_cooldown": "모멘텀이 식어서 보수적으로 청산함",
    "session_closed": "장 종료로 포지션을 정리함",
}


def prepare_display_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame.copy()
    for column in result.columns:
        result[column] = result[column].apply(prettify_cell)
    return result


def prettify_cell(value: Any) -> Any:
    if isinstance(value, list):
        if not value:
            return "-"
        return ", ".join(translate_reason(str(item)) for item in value)
    if value is None:
        return "-"
    if isinstance(value, float) and pd.isna(value):
        return "-"
    if isinstance(value, str):
        if value in LABEL_MAP:
            return translate_label(value)
        if value in REASON_MAP:
            return translate_reason(value)
    return value


def translate_label(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return LABEL_MAP.get(str(value), str(value))


def translate_reason(reason: str) -> str:
    if not reason:
        return "-"
    if reason in REASON_MAP:
        return REASON_MAP[reason]
    if ":" in reason:
        key, suffix = reason.split(":", 1)
        if key in REASON_MAP:
            return f"{REASON_MAP[key]} ({suffix})"
    return reason.replace("_", " ")


def coerce_reason_list(value: Any) -> list[str]:
    if value is None or value == "" or value == "-":
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def metric_number(value: Any, precision: int) -> str:
    if value is None or value == "-" or (isinstance(value, float) and pd.isna(value)):
        return "-"
    try:
        return f"{float(value):.{precision}f}"
    except Exception:
        return str(value)


def metric_integer(value: Any) -> str:
    if value is None or value == "-" or (isinstance(value, float) and pd.isna(value)):
        return "-"
    try:
        return f"{int(float(value)):,}"
    except Exception:
        return str(value)
