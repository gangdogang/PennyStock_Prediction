from __future__ import annotations

import json
from html import escape
from pathlib import Path
from string import Template
from typing import Any, Callable

from ..db import (
    fetch_scan_selection,
    fetch_latest_candidates,
    fetch_latest_market_activity,
    fetch_latest_premarket_signals,
    fetch_latest_prediction_outcomes,
    fetch_latest_replay_report,
    fetch_latest_session_decisions,
    fetch_latest_social_signals,
    fetch_latest_watchlist,
)

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
}

JSON_FIELDS = {"filter_reasons", "themes", "reasons"}
DECISION_WEIGHT = {"ENTER": 3.0, "WATCH": 1.5, "AVOID": -1.0}


class ReportBuilder:
    def build_payload(self, database_path: Path, limit: int = 20) -> dict[str, object]:
        selection = fetch_scan_selection(database_path)
        selected_scan_id = selection.get("selected_scan_id")
        return {
            "meta": {
                "scan_id": selected_scan_id,
                "selected_scan_id": selected_scan_id,
                "selected_created_at": selection.get("selected_created_at"),
                "latest_scan_id": selection.get("latest_scan_id"),
                "latest_created_at": selection.get("latest_created_at"),
                "is_fallback": bool(selection.get("is_fallback")),
                "missing_core_sections": list(selection.get("missing_core_sections") or []),
                "missing_supplemental_sections": list(
                    selection.get("missing_supplemental_sections") or []
                ),
                "market_phase": selection.get("market_phase"),
                "live_data_expected": bool(selection.get("live_data_expected")),
                "database_path": str(database_path),
                "limit": limit,
            },
            "universe": [
                dict(row)
                for row in fetch_latest_candidates(database_path, limit, scan_id=selected_scan_id)
            ],
            "watchlist": [
                dict(row)
                for row in fetch_latest_watchlist(database_path, limit, scan_id=selected_scan_id)
            ],
            "premarket": [
                dict(row)
                for row in fetch_latest_premarket_signals(
                    database_path,
                    limit,
                    scan_id=selected_scan_id,
                )
            ],
            "live_premarket": [
                dict(row)
                for row in fetch_latest_market_activity(
                    database_path,
                    "premarket",
                    limit=limit,
                    scan_id=selected_scan_id,
                )
            ],
            "prediction_premarket": [
                dict(row)
                for row in fetch_latest_prediction_outcomes(
                    database_path,
                    "premarket",
                    limit=limit,
                    scan_id=selected_scan_id,
                )
            ],
            "session": [
                dict(row)
                for row in fetch_latest_session_decisions(
                    database_path,
                    limit,
                    scan_id=selected_scan_id,
                )
            ],
            "live_regular": [
                dict(row)
                for row in fetch_latest_market_activity(
                    database_path,
                    "regular",
                    limit=limit,
                    scan_id=selected_scan_id,
                )
            ],
            "prediction_regular": [
                dict(row)
                for row in fetch_latest_prediction_outcomes(
                    database_path,
                    "regular",
                    limit=limit,
                    scan_id=selected_scan_id,
                )
            ],
            "social": [
                dict(row)
                for row in fetch_latest_social_signals(
                    database_path,
                    limit,
                    scan_id=selected_scan_id,
                )
            ],
            "report": dict(
                fetch_latest_replay_report(
                    database_path,
                    scan_id=selected_scan_id,
                )
                or {}
            ),
        }

    def export_json(self, database_path: Path, output_path: Path, limit: int = 20) -> dict[str, object]:
        payload = self.build_payload(database_path, limit=limit)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def export_markdown(self, database_path: Path, output_path: Path, limit: int = 20) -> str:
        payload = self.build_payload(database_path, limit=limit)
        lines = ["# Penny Stock Radar Summary", ""]

        watchlist = payload["watchlist"]
        lines.append("## Watchlist")
        if watchlist:
            for row in watchlist:
                lines.append(
                    f"- {row['symbol']}: total={row['total_score']:.2f}, catalyst={row['catalyst_score']:.2f}, technical={row['technical_score']:.2f}, context={row.get('market_context_score', 0):.2f}"
                )
        else:
            lines.append("- No watchlist entries")
        lines.append("")

        premarket = payload["premarket"]
        lines.append("## Premarket")
        if premarket:
            for row in premarket:
                lines.append(
                    f"- {row['symbol']}: quality={row['quality_score']:.2f}, rvol={row['premarket_rvol']:.2f}, dv={row['dollar_volume']:.0f}"
                )
        else:
            lines.append("- No premarket signals")
        lines.append("")

        live_premarket = payload["live_premarket"]
        lines.append("## Premarket Live Movers")
        if live_premarket:
            for row in live_premarket:
                lines.append(
                    f"- {row['symbol']}: pct_rank={row['pct_rank']} vol_rank={row['volume_rank']} pct_change={row.get('pct_change', 0):.2f} volume={row.get('volume', 0):.0f} read={row.get('analysis_label', '-')}"
                )
        else:
            lines.append("- No stored live premarket movers")
        lines.append("")

        prediction_premarket = payload["prediction_premarket"]
        lines.append("## Premarket Prediction Audit")
        if prediction_premarket:
            for row in prediction_premarket:
                lines.append(
                    f"- {row['symbol']}: predicted={row.get('predicted')} pct_rank={row.get('pct_rank', '-')} volume_rank={row.get('volume_rank', '-')} outcome={row.get('outcome', '-')}"
                )
        else:
            lines.append("- No premarket prediction audit")
        lines.append("")

        session = payload["session"]
        lines.append("## Session Decisions")
        if session:
            for row in session:
                lines.append(
                    f"- {row['symbol']}: decision={row['decision']}, ext_z={row['extension_z']:.2f}, mfe={row['mfe_pct']:.2f}, mae={row['mae_pct']:.2f}"
                )
        else:
            lines.append("- No session decisions")
        lines.append("")

        live_regular = payload["live_regular"]
        lines.append("## Regular Live Movers")
        if live_regular:
            for row in live_regular:
                lines.append(
                    f"- {row['symbol']}: pct_rank={row['pct_rank']} vol_rank={row['volume_rank']} pct_change={row.get('pct_change', 0):.2f} volume={row.get('volume', 0):.0f} read={row.get('analysis_label', '-')}"
                )
        else:
            lines.append("- No stored live regular movers")
        lines.append("")

        prediction_regular = payload["prediction_regular"]
        lines.append("## Regular Prediction Audit")
        if prediction_regular:
            for row in prediction_regular:
                lines.append(
                    f"- {row['symbol']}: predicted={row.get('predicted')} pct_rank={row.get('pct_rank', '-')} volume_rank={row.get('volume_rank', '-')} outcome={row.get('outcome', '-')}"
                )
        else:
            lines.append("- No regular prediction audit")
        lines.append("")

        report = payload["report"]
        lines.append("## Replay Report")
        if report:
            lines.append(
                f"- label={report.get('label')} expectancy={report.get('expectancy', 0):.2f} profit_factor={report.get('profit_factor', 0):.2f} precision_at_k={report.get('precision_at_k', 0):.2f}"
            )
        else:
            lines.append("- No replay report")
        lines.append("")

        social = payload["social"]
        lines.append("## Social")
        if social:
            for row in social:
                lines.append(
                    f"- {row['symbol']}: social_score={row['social_score']:.2f}, mentions={row['mention_count']}, velocity={row['mention_velocity']:.2f}"
                )
        else:
            lines.append("- No social signals")

        content = "\n".join(lines) + "\n"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        return content

    def export_html(self, database_path: Path, output_path: Path, limit: int = 20) -> str:
        payload = self.build_payload(database_path, limit=limit)
        content = self.render_html(payload)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        return content

    def render_html(self, payload: dict[str, object]) -> str:
        normalized = _normalize_payload(payload)
        leaderboard = _build_leaderboard(normalized)
        metrics = _build_metrics(normalized, leaderboard)
        meta = normalized["meta"]
        hero_meta = [
            ("출력 형식", "Standalone snapshot UI"),
            ("데이터베이스", Path(str(meta.get("database_path") or "-")).name),
            ("표시 스캔", str(meta.get("selected_scan_id") or "-")[:14]),
            ("최신 Raw", str(meta.get("latest_scan_id") or "-")[:14]),
            ("기준 시각", _find_generated_at(normalized)),
        ]
        live_empty_message = _live_empty_message(meta)
        prediction_empty_message = _prediction_empty_message(meta)
        template = Template(
            """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Penny Stock Radar Snapshot</title>
  <style>
    :root {
      --bg: #f3efe6;
      --surface: rgba(255, 252, 246, 0.76);
      --surface-strong: rgba(255, 251, 243, 0.95);
      --ink: #15212c;
      --muted: #5e6a76;
      --line: rgba(21, 33, 44, 0.12);
      --teal: #0f766e;
      --copper: #b35c2e;
      --navy: #123047;
      --enter: #2f855a;
      --watch: #9a6700;
      --avoid: #b42318;
      --shadow: 0 20px 60px rgba(18, 48, 71, 0.12);
      --radius-lg: 24px;
      --radius-md: 18px;
      --radius-sm: 12px;
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      font-family: "Avenir Next", "Pretendard", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(15, 118, 110, 0.18), transparent 28%),
        radial-gradient(circle at top right, rgba(179, 92, 46, 0.14), transparent 32%),
        linear-gradient(180deg, #efe8dc 0%, #f6f1e8 42%, #ede7dc 100%);
      min-height: 100vh;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image:
        linear-gradient(rgba(18, 48, 71, 0.04) 1px, transparent 1px),
        linear-gradient(90deg, rgba(18, 48, 71, 0.04) 1px, transparent 1px);
      background-size: 32px 32px;
      mask-image: linear-gradient(180deg, rgba(0, 0, 0, 0.55), transparent 80%);
    }
    .shell {
      width: min(1200px, calc(100% - 32px));
      margin: 28px auto 64px;
    }
    .topnav {
      position: sticky;
      top: 16px;
      z-index: 5;
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      padding: 12px;
      margin-bottom: 18px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(255, 249, 240, 0.78);
      backdrop-filter: blur(18px);
      box-shadow: var(--shadow);
    }
    .topnav a {
      text-decoration: none;
      color: var(--navy);
      font-size: 0.95rem;
      font-weight: 600;
      padding: 8px 14px;
      border-radius: 999px;
      transition: transform 160ms ease, background 160ms ease;
    }
    .topnav a:hover {
      background: rgba(15, 118, 110, 0.10);
      transform: translateY(-1px);
    }
    .hero, .panel, .metric-card, .symbol-card, .report-card {
      border: 1px solid var(--line);
      background: var(--surface);
      backdrop-filter: blur(16px);
      box-shadow: var(--shadow);
    }
    .hero {
      position: relative;
      overflow: hidden;
      padding: 28px;
      border-radius: 32px;
      animation: rise 500ms ease both;
    }
    .hero::after {
      content: "";
      position: absolute;
      right: -40px;
      top: -48px;
      width: 240px;
      height: 240px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(15, 118, 110, 0.26), transparent 68%);
    }
    .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 12px;
      border-radius: 999px;
      background: rgba(18, 48, 71, 0.08);
      color: var(--navy);
      font-size: 0.82rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .hero h1 {
      margin: 14px 0 8px;
      font-size: clamp(2.1rem, 5vw, 4rem);
      line-height: 0.96;
      letter-spacing: -0.04em;
    }
    .hero p {
      max-width: 760px;
      margin: 0;
      color: var(--muted);
      font-size: 1.02rem;
      line-height: 1.6;
    }
    .hero-meta {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 12px;
      margin-top: 22px;
    }
    .hero-chip {
      padding: 14px 16px;
      border-radius: var(--radius-md);
      background: rgba(255, 255, 255, 0.58);
      border: 1px solid rgba(18, 48, 71, 0.08);
    }
    .hero-chip strong {
      display: block;
      font-size: 0.78rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.06em;
      margin-bottom: 6px;
    }
    .hero-chip span {
      font-size: 0.95rem;
      font-weight: 700;
      color: var(--navy);
      word-break: break-word;
    }
    .section {
      margin-top: 22px;
      animation: rise 620ms ease both;
    }
    .section-header {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-end;
      margin-bottom: 12px;
    }
    .section-header h2 {
      margin: 0;
      font-size: clamp(1.35rem, 2.8vw, 2rem);
      letter-spacing: -0.03em;
    }
    .section-header p {
      margin: 4px 0 0;
      color: var(--muted);
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 14px;
    }
    .metric-card {
      padding: 18px;
      border-radius: var(--radius-lg);
    }
    .metric-card strong {
      display: block;
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      margin-bottom: 10px;
    }
    .metric-card .value {
      font-size: 2rem;
      line-height: 1;
      font-weight: 800;
      letter-spacing: -0.04em;
      color: var(--navy);
    }
    .metric-card .note {
      margin-top: 10px;
      color: var(--muted);
      line-height: 1.5;
      font-size: 0.95rem;
    }
    .leaderboard {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 14px;
    }
    .symbol-card {
      padding: 18px;
      border-radius: var(--radius-lg);
    }
    .symbol-top {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
      margin-bottom: 12px;
    }
    .symbol-name {
      margin: 0;
      font-size: 1.55rem;
      letter-spacing: -0.04em;
    }
    .symbol-subtitle {
      color: var(--muted);
      margin: 6px 0 0;
      line-height: 1.5;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 78px;
      padding: 8px 12px;
      border-radius: 999px;
      font-size: 0.84rem;
      font-weight: 800;
      border: 1px solid transparent;
    }
    .pill-enter {
      color: var(--enter);
      background: rgba(47, 133, 90, 0.12);
      border-color: rgba(47, 133, 90, 0.16);
    }
    .pill-watch {
      color: var(--watch);
      background: rgba(154, 103, 0, 0.11);
      border-color: rgba(154, 103, 0, 0.18);
    }
    .pill-avoid {
      color: var(--avoid);
      background: rgba(180, 35, 24, 0.10);
      border-color: rgba(180, 35, 24, 0.14);
    }
    .pill-neutral {
      color: var(--navy);
      background: rgba(18, 48, 71, 0.07);
      border-color: rgba(18, 48, 71, 0.12);
    }
    .score-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin: 12px 0 14px;
    }
    .score-item {
      padding: 12px;
      border-radius: var(--radius-md);
      background: rgba(255, 255, 255, 0.56);
      border: 1px solid rgba(18, 48, 71, 0.08);
    }
    .score-item strong {
      display: block;
      color: var(--muted);
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      margin-bottom: 6px;
    }
    .score-item span {
      font-size: 1.05rem;
      font-weight: 800;
      color: var(--navy);
    }
    .meter {
      height: 10px;
      border-radius: 999px;
      background: rgba(18, 48, 71, 0.08);
      overflow: hidden;
    }
    .meter span {
      display: block;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--teal), var(--copper));
    }
    .chip-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 14px;
    }
    .chip {
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(18, 48, 71, 0.07);
      color: var(--navy);
      font-size: 0.82rem;
      font-weight: 600;
    }
    .panel {
      padding: 18px;
      border-radius: 28px;
    }
    .panel-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 14px;
    }
    .report-card {
      padding: 18px;
      border-radius: var(--radius-lg);
    }
    .report-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 10px;
      margin-top: 14px;
    }
    .report-grid .score-item {
      min-height: 92px;
    }
    .table-wrap {
      overflow-x: auto;
      border-radius: 20px;
      border: 1px solid rgba(18, 48, 71, 0.08);
      background: rgba(255, 255, 255, 0.52);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 720px;
    }
    th, td {
      padding: 13px 14px;
      border-bottom: 1px solid rgba(18, 48, 71, 0.08);
      text-align: left;
      vertical-align: top;
    }
    th {
      color: var(--muted);
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      background: rgba(18, 48, 71, 0.04);
    }
    td strong {
      color: var(--navy);
    }
    tr:last-child td {
      border-bottom: none;
    }
    .stack {
      display: flex;
      flex-direction: column;
      gap: 5px;
    }
    .mono {
      font-family: "SFMono-Regular", "Menlo", monospace;
      font-size: 0.9rem;
    }
    .empty {
      padding: 22px;
      text-align: center;
      color: var(--muted);
    }
    .footer-note {
      margin-top: 18px;
      padding: 16px 18px;
      border-radius: 18px;
      background: rgba(18, 48, 71, 0.06);
      color: var(--muted);
      line-height: 1.6;
    }
    .status-callout {
      margin-top: 18px;
      padding: 16px 18px;
      border-radius: 18px;
      border: 1px solid rgba(18, 48, 71, 0.12);
      background: rgba(255, 251, 243, 0.82);
      color: var(--ink);
      line-height: 1.6;
    }
    .status-callout strong {
      display: block;
      margin-bottom: 6px;
      font-size: 0.95rem;
    }
    @keyframes rise {
      from { opacity: 0; transform: translateY(10px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @media (max-width: 740px) {
      .shell {
        width: min(100% - 20px, 1200px);
        margin-top: 18px;
      }
      .hero {
        padding: 22px;
      }
      .score-grid {
        grid-template-columns: 1fr;
      }
      .topnav {
        top: 10px;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <nav class="topnav">
      <a href="#overview">Overview</a>
      <a href="#leaders">Radar Board</a>
      <a href="#replay">Replay</a>
      <a href="#watchlist">Watchlist</a>
      <a href="#premarket">Premarket</a>
      <a href="#session">Session</a>
      <a href="#live-premarket">Live Premarket</a>
      <a href="#live-regular">Live Regular</a>
      <a href="#prediction">Prediction Audit</a>
      <a href="#social">Social</a>
    </nav>
    <header class="hero" id="overview">
      <div class="eyebrow">Standalone Snapshot UI</div>
      <h1>Penny Stock Radar</h1>
      <p>브라우저 탭에서 로컬 URL을 직접 띄우는 대신, 저장된 최신 스냅샷을 바탕으로 읽기 좋은 형태로 정리한 독립형 대시보드입니다. 실시간 폴링은 제외하고, 후보 선별부터 정규장 판단까지 흐름을 한 화면에서 빠르게 읽도록 재구성했습니다.</p>
      <div class="hero-meta">
        $hero_meta
      </div>
      $status_callout
    </header>

    <section class="section" aria-labelledby="metric-title">
      <div class="section-header">
        <div>
          <h2 id="metric-title">Signal Overview</h2>
          <p>지금 저장된 데이터에서 가장 먼저 봐야 할 핵심 수치입니다.</p>
        </div>
      </div>
      <div class="metrics">
        $metrics
      </div>
    </section>

    <section class="section" id="leaders">
      <div class="section-header">
        <div>
          <h2>Radar Board</h2>
          <p>셋업 점수, 프리장 품질, 소셜 속도, 정규장 판단을 합쳐 본 우선순위입니다.</p>
        </div>
      </div>
      <div class="leaderboard">
        $leaderboard
      </div>
    </section>

    <section class="section" id="replay">
      <div class="section-header">
        <div>
          <h2>Replay Read</h2>
          <p>최근 리플레이 리포트를 한 장짜리 카드로 요약했습니다.</p>
        </div>
      </div>
      $report
    </section>

    <section class="section panel" id="watchlist">
      <div class="section-header">
        <div>
          <h2>Watchlist</h2>
          <p>프리장 전에 압축해 둔 관심 종목입니다.</p>
        </div>
      </div>
      $watchlist_table
    </section>

    <section class="section panel" id="premarket">
      <div class="section-header">
        <div>
          <h2>Premarket</h2>
          <p>프리장 강세가 실제로 의미 있는지 확인하는 구간입니다.</p>
        </div>
      </div>
      $premarket_table
    </section>

    <section class="section panel" id="session">
      <div class="section-header">
        <div>
          <h2>Session</h2>
          <p>정규장 continuation / fade 해석에 가까운 의사결정 영역입니다.</p>
        </div>
      </div>
      $session_table
    </section>

    <section class="section panel" id="live-premarket">
      <div class="section-header">
        <div>
          <h2>Live Premarket Movers</h2>
          <p>실시간 API 기준으로 프리장 중 상승률/거래량 상위 market momentum leaders 입니다.</p>
        </div>
      </div>
      $live_premarket_table
    </section>

    <section class="section panel" id="live-regular">
      <div class="section-header">
        <div>
          <h2>Live Regular Movers</h2>
          <p>정규장 중 실제로 거래가 몰리는 종목과 추세 상태를 함께 봅니다.</p>
        </div>
      </div>
      $live_regular_table
    </section>

    <section class="section panel" id="prediction">
      <div class="section-header">
        <div>
          <h2>Prediction Audit</h2>
          <p>프리장 전 watchlist가 실제 프리장/정규장 리더를 얼마나 맞췄는지 비교합니다.</p>
        </div>
      </div>
      $prediction_table
    </section>

    <section class="section panel" id="social">
      <div class="section-header">
        <div>
          <h2>Social</h2>
          <p>언급 속도와 플랫폼 확산으로 과열 여부를 점검합니다.</p>
        </div>
      </div>
      $social_table
      <div class="footer-note">
        이 HTML 대시보드는 로컬 파일로 열 수 있는 스냅샷 UI입니다. 실시간 API 폴링과 즉시 새로고침은 기존 Streamlit 대시보드가 더 적합하고, 보기 좋은 요약 화면이 필요할 때는 이 HTML export가 더 편합니다.
      </div>
    </section>
  </div>
</body>
</html>
"""
        )
        return template.substitute(
            hero_meta=_render_hero_meta(hero_meta),
            status_callout=_render_status_callout(meta),
            metrics=_render_metric_cards(metrics),
            leaderboard=_render_leaderboard(leaderboard),
            report=_render_report_card(normalized["report"]),
            watchlist_table=_render_table(
                normalized["watchlist"],
                [
                    ("심볼", lambda row: _stack_html([f"<strong>{_escape_text(row.get('symbol'))}</strong>", _theme_line(row)])),
                    ("셋업", lambda row: _escape_text(_format_float(row.get("total_score")))),
                    ("재료", lambda row: _escape_text(_format_float(row.get("catalyst_score")))),
                    ("기술", lambda row: _escape_text(_format_float(row.get("technical_score")))),
                    ("시장맥락", lambda row: _escape_text(_format_float(row.get("market_context_score")))),
                    ("소셜", lambda row: _escape_text(_format_float(row.get("social_score")))),
                    ("이유", lambda row: _reason_html(row.get("reasons"))),
                ],
                "저장된 watchlist 데이터가 없습니다.",
            ),
            premarket_table=_render_table(
                normalized["premarket"],
                [
                    ("심볼", lambda row: f"<strong>{_escape_text(row.get('symbol'))}</strong>"),
                    ("품질", lambda row: _escape_text(_format_float(row.get("quality_score")))),
                    ("RVOL", lambda row: _escape_text(_format_float(row.get("premarket_rvol")))),
                    ("거래대금", lambda row: _escape_text(_format_integer(row.get("dollar_volume")))),
                    ("스프레드", lambda row: _escape_text(_format_percent(row.get("spread_pct"), scale=100.0))),
                    ("이유", lambda row: _reason_html(row.get("reasons"))),
                ],
                "저장된 프리장 판단 결과가 없습니다.",
            ),
            session_table=_render_table(
                normalized["session"],
                [
                    ("심볼", lambda row: f"<strong>{_escape_text(row.get('symbol'))}</strong>"),
                    ("판단", lambda row: _pill_html(row.get("decision"))),
                    ("Extension Z", lambda row: _escape_text(_format_float(row.get("extension_z")))),
                    ("MFE%", lambda row: _escape_text(_format_percent(row.get("mfe_pct")))),
                    ("MAE%", lambda row: _escape_text(_format_percent(row.get("mae_pct")))),
                    ("이유", lambda row: _reason_html(row.get("reasons"))),
                ],
                "저장된 정규장 판단 결과가 없습니다.",
            ),
            live_premarket_table=_render_table(
                normalized["live_premarket"],
                [
                    ("%순위", lambda row: _escape_text(_format_integer(row.get("pct_rank")))),
                    ("거래량순위", lambda row: _escape_text(_format_integer(row.get("volume_rank")))),
                    ("심볼", lambda row: f"<strong>{_escape_text(row.get('symbol'))}</strong>"),
                    ("상승률", lambda row: _escape_text(_format_percent(row.get("pct_change"), scale=1.0))),
                    ("거래량", lambda row: _escape_text(_format_integer(row.get("volume")))),
                    ("거래대금", lambda row: _escape_text(_format_integer(row.get("dollar_volume")))),
                    ("판독", lambda row: _pill_html(row.get("analysis_label"))),
                    ("진입 근거", lambda row: _reason_html(_entry_basis_reasons(row))),
                ],
                live_empty_message,
            ),
            live_regular_table=_render_table(
                normalized["live_regular"],
                [
                    ("%순위", lambda row: _escape_text(_format_integer(row.get("pct_rank")))),
                    ("거래량순위", lambda row: _escape_text(_format_integer(row.get("volume_rank")))),
                    ("심볼", lambda row: f"<strong>{_escape_text(row.get('symbol'))}</strong>"),
                    ("상승률", lambda row: _escape_text(_format_percent(row.get("pct_change"), scale=1.0))),
                    ("거래량", lambda row: _escape_text(_format_integer(row.get("volume")))),
                    ("거래대금", lambda row: _escape_text(_format_integer(row.get("dollar_volume")))),
                    ("판독", lambda row: _pill_html(row.get("analysis_label"))),
                    ("진입 근거", lambda row: _reason_html(_entry_basis_reasons(row))),
                ],
                live_empty_message,
            ),
            prediction_table=_render_table(
                normalized["prediction_premarket"] + normalized["prediction_regular"],
                [
                    ("구간", lambda row: _escape_text(str(row.get("market_phase") or "-"))),
                    ("심볼", lambda row: f"<strong>{_escape_text(row.get('symbol'))}</strong>"),
                    ("예측", lambda row: _escape_text("Y" if row.get("predicted") else "-")),
                    ("watchlist", lambda row: _escape_text(_format_integer(row.get("watchlist_rank")))),
                    ("%순위", lambda row: _escape_text(_format_integer(row.get("pct_rank")))),
                    ("거래량순위", lambda row: _escape_text(_format_integer(row.get("volume_rank")))),
                    ("결과", lambda row: _escape_text(_translate_label(row.get("outcome")))),
                    ("판독", lambda row: _pill_html(row.get("analysis_label"))),
                ],
                prediction_empty_message,
            ),
            social_table=_render_table(
                normalized["social"],
                [
                    ("심볼", lambda row: f"<strong>{_escape_text(row.get('symbol'))}</strong>"),
                    ("점수", lambda row: _escape_text(_format_float(row.get("social_score")))),
                    ("언급 속도", lambda row: _escape_text(_format_float(row.get("mention_velocity")))),
                    ("언급 수", lambda row: _escape_text(_format_integer(row.get("mention_count")))),
                    ("플랫폼", lambda row: _escape_text(_format_integer(row.get("cross_platform_count")))),
                    ("이유", lambda row: _reason_html(row.get("reasons"))),
                ],
                "저장된 소셜 신호가 없습니다.",
            ),
        )


def _normalize_payload(payload: dict[str, object]) -> dict[str, Any]:
    result: dict[str, Any] = {"meta": dict(payload.get("meta") or {})}
    for key in (
        "universe",
        "watchlist",
        "premarket",
        "live_premarket",
        "prediction_premarket",
        "session",
        "live_regular",
        "prediction_regular",
        "social",
    ):
        rows = payload.get(key) or []
        result[key] = [_normalize_row(dict(row)) for row in rows]
    result["report"] = _normalize_row(dict(payload.get("report") or {}))
    return result


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    result = row.copy()
    for field in JSON_FIELDS:
        if field in result:
            result[field] = _decode_json_value(result[field])
    return result


def _decode_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _build_leaderboard(payload: dict[str, Any]) -> list[dict[str, Any]]:
    by_symbol: dict[str, dict[str, Any]] = {}
    for row in payload["universe"]:
        symbol = str(row.get("symbol") or "")
        if not symbol:
            continue
        entry = by_symbol.setdefault("symbol:" + symbol, {"symbol": symbol})
        entry["company_name"] = row.get("company_name")
        entry["sector"] = row.get("sector")
        entry["industry"] = row.get("industry")
        entry["price"] = row.get("price")
        entry["float_shares"] = row.get("float_shares")

    for row in payload["watchlist"]:
        symbol = str(row.get("symbol") or "")
        if not symbol:
            continue
        entry = by_symbol.setdefault("symbol:" + symbol, {"symbol": symbol})
        entry["setup_score"] = _safe_float(row.get("total_score"))
        entry["watchlist_reasons"] = _coerce_list(row.get("reasons"))
        entry["themes"] = _coerce_list(row.get("themes"))

    for row in payload["premarket"]:
        symbol = str(row.get("symbol") or "")
        if not symbol:
            continue
        entry = by_symbol.setdefault("symbol:" + symbol, {"symbol": symbol})
        entry["premarket_quality"] = _safe_float(row.get("quality_score"))
        entry["premarket_rvol"] = row.get("premarket_rvol")
        entry["dollar_volume"] = row.get("dollar_volume")
        entry["premarket_reasons"] = _coerce_list(row.get("reasons"))

    for row in payload["session"]:
        symbol = str(row.get("symbol") or "")
        if not symbol:
            continue
        entry = by_symbol.setdefault("symbol:" + symbol, {"symbol": symbol})
        entry["decision"] = str(row.get("decision") or "")
        entry["extension_z"] = row.get("extension_z")
        entry["mfe_pct"] = row.get("mfe_pct")
        entry["session_reasons"] = _coerce_list(row.get("reasons"))

    for row in payload["social"]:
        symbol = str(row.get("symbol") or "")
        if not symbol:
            continue
        entry = by_symbol.setdefault("symbol:" + symbol, {"symbol": symbol})
        entry["social_score"] = _safe_float(row.get("social_score"))
        entry["mention_velocity"] = row.get("mention_velocity")
        entry["social_reasons"] = _coerce_list(row.get("reasons"))

    leaderboard: list[dict[str, Any]] = []
    for entry in by_symbol.values():
        entry["radar_score"] = (
            _safe_float(entry.get("setup_score"))
            + _safe_float(entry.get("premarket_quality"))
            + _safe_float(entry.get("social_score"))
            + DECISION_WEIGHT.get(str(entry.get("decision") or ""), 0.0)
        )
        reasons: list[str] = []
        for field in ("watchlist_reasons", "premarket_reasons", "session_reasons", "social_reasons"):
            reasons.extend(_coerce_list(entry.get(field)))
        entry["headline_reasons"] = _unique_list(reasons)[:5]
        leaderboard.append(entry)

    leaderboard.sort(
        key=lambda row: (
            -_safe_float(row.get("radar_score")),
            -_safe_float(row.get("premarket_quality")),
            -_safe_float(row.get("setup_score")),
            str(row.get("symbol") or ""),
        )
    )
    return leaderboard[:8]


def _build_metrics(payload: dict[str, Any], leaderboard: list[dict[str, Any]]) -> list[dict[str, str]]:
    top_symbol = leaderboard[0]["symbol"] if leaderboard else "-"
    top_score = _format_float(leaderboard[0].get("radar_score")) if leaderboard else "-"
    top_decision = _translate_label(leaderboard[0].get("decision")) if leaderboard else "-"
    enter_count = sum(1 for row in payload["session"] if row.get("decision") == "ENTER")
    report = payload["report"]
    return [
        {
            "title": "Top Symbol",
            "value": top_symbol,
            "note": f"현재 스냅샷에서 가장 먼저 볼 심볼은 {top_symbol} 입니다.",
        },
        {
            "title": "Radar Score",
            "value": top_score,
            "note": f"상위 종목의 종합 점수이며 정규장 판단은 {top_decision} 입니다.",
        },
        {
            "title": "Enter Signals",
            "value": str(enter_count),
            "note": "정규장에서 실제 진입 쪽으로 해석된 종목 수입니다.",
        },
        {
            "title": "Replay Label",
            "value": _translate_label(report.get("label")),
            "note": f"Expectancy {_format_float(report.get('expectancy'))} / Precision@K {_format_float(report.get('precision_at_k'))}",
        },
    ]


def _render_hero_meta(items: list[tuple[str, str]]) -> str:
    return "".join(
        f"<div class='hero-chip'><strong>{escape(label)}</strong><span>{escape(value)}</span></div>"
        for label, value in items
    )


def _render_status_callout(meta: dict[str, Any]) -> str:
    lines = _status_lines(meta)
    if not lines:
        return ""
    content = "".join(f"<div>{escape(line)}</div>" for line in lines)
    return (
        "<div class='status-callout'>"
        "<strong>Data Status</strong>"
        f"{content}"
        "</div>"
    )


def _status_lines(meta: dict[str, Any]) -> list[str]:
    selected_scan_id = str(meta.get("selected_scan_id") or "")
    latest_scan_id = str(meta.get("latest_scan_id") or "")
    market_phase = str(meta.get("market_phase") or "-")
    missing_core = [
        _humanize_section_name(value) for value in meta.get("missing_core_sections") or []
    ]
    missing_supplemental = [
        _humanize_section_name(value)
        for value in meta.get("missing_supplemental_sections") or []
    ]

    if not selected_scan_id:
        return [
            "표시 가능한 complete scan이 아직 없습니다.",
            "Universe, watchlist, premarket, session, replay core 섹션이 모두 채워진 뒤 다시 export 하세요.",
        ]

    lines = [
        f"표시 중 scan은 {selected_scan_id[:14]} 이고, 현재 미국장 구간은 {market_phase} 입니다.",
    ]
    if meta.get("is_fallback") and latest_scan_id:
        lines.append(
            f"최신 raw scan {latest_scan_id[:14]} 이 incomplete라 마지막 complete scan으로 fallback했습니다."
        )
    if missing_core:
        lines.append("최신 raw scan의 누락 core 섹션: " + ", ".join(missing_core) + ".")
    if missing_supplemental:
        lines.append(
            "현재 상태에서 비어 있는 supplemental 섹션: "
            + ", ".join(missing_supplemental)
            + "."
        )
    if not meta.get("live_data_expected"):
        lines.append(
            "지금은 미국장 프리장/정규장이 아니어서 live movers와 prediction audit 공백이 정상일 수 있습니다."
        )
    return lines


def _live_empty_message(meta: dict[str, Any]) -> str:
    if not meta.get("live_data_expected"):
        return "지금은 미국장 프리장/정규장이 아니어서 저장된 실시간 movers 랭킹이 비어 있을 수 있습니다."
    return "저장된 실시간 랭킹이 없습니다."


def _prediction_empty_message(meta: dict[str, Any]) -> str:
    if not meta.get("live_data_expected"):
        return "지금은 미국장 프리장/정규장이 아니어서 prediction audit 결과가 비어 있을 수 있습니다."
    return "저장된 예측 비교 결과가 없습니다."


def _humanize_section_name(value: str) -> str:
    return value.replace("_", " ")


def _render_metric_cards(metrics: list[dict[str, str]]) -> str:
    return "".join(
        (
            "<article class='metric-card'>"
            f"<strong>{escape(metric['title'])}</strong>"
            f"<div class='value'>{escape(metric['value'])}</div>"
            f"<div class='note'>{escape(metric['note'])}</div>"
            "</article>"
        )
        for metric in metrics
    )


def _render_leaderboard(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<div class='panel empty'>표시할 리더보드 데이터가 없습니다.</div>"
    max_score = max(_safe_float(row.get("radar_score")) for row in rows) or 1.0
    cards: list[str] = []
    for row in rows:
        width = min(100.0, max(8.0, (_safe_float(row.get("radar_score")) / max_score) * 100.0))
        subtitle_parts = []
        if row.get("company_name"):
            subtitle_parts.append(str(row["company_name"]))
        if row.get("sector"):
            subtitle_parts.append(str(row["sector"]))
        if row.get("industry"):
            subtitle_parts.append(str(row["industry"]))
        if not subtitle_parts:
            subtitle_parts.append("핵심 종목 요약 카드")
        chips = []
        chips.extend(_render_chip_list(_coerce_list(row.get("themes"))[:3]))
        chips.extend(_render_chip_list(_translate_reason(reason) for reason in row.get("headline_reasons", [])))
        cards.append(
            (
                "<article class='symbol-card'>"
                "<div class='symbol-top'>"
                "<div>"
                f"<h3 class='symbol-name'>{_escape_text(row.get('symbol'))}</h3>"
                f"<p class='symbol-subtitle'>{_escape_text(' / '.join(subtitle_parts))}</p>"
                "</div>"
                f"{_pill_html(row.get('decision'))}"
                "</div>"
                "<div class='score-grid'>"
                f"{_score_item_html('Radar', _format_float(row.get('radar_score')))}"
                f"{_score_item_html('Setup', _format_float(row.get('setup_score')))}"
                f"{_score_item_html('Premarket', _format_float(row.get('premarket_quality')))}"
                "</div>"
                "<div class='score-grid'>"
                f"{_score_item_html('Social', _format_float(row.get('social_score')))}"
                f"{_score_item_html('RVOL', _format_float(row.get('premarket_rvol')))}"
                f"{_score_item_html('Float', _format_integer(row.get('float_shares')))}"
                "</div>"
                f"<div class='meter'><span style='width: {width:.1f}%'></span></div>"
                f"<div class='chip-row'>{''.join(chips) if chips else '<span class=\"chip\">근거 없음</span>'}</div>"
                "</article>"
            )
        )
    return "".join(cards)


def _render_report_card(report: dict[str, Any]) -> str:
    if not report:
        return "<div class='report-card empty'>저장된 리플레이 리포트가 없습니다.</div>"
    return (
        "<article class='report-card'>"
        f"<div class='eyebrow'>Replay Label · {_escape_text(_translate_label(report.get('label')))}</div>"
        "<div class='report-grid'>"
        f"{_score_item_html('Expectancy', _format_float(report.get('expectancy')))}"
        f"{_score_item_html('Profit Factor', _format_float(report.get('profit_factor')))}"
        f"{_score_item_html('Precision@K', _format_float(report.get('precision_at_k')))}"
        f"{_score_item_html('Symbols', _format_integer(report.get('symbol_count')))}"
        f"{_score_item_html('Avg MFE%', _format_percent(report.get('average_mfe_pct')))}"
        f"{_score_item_html('Avg MAE%', _format_percent(report.get('average_mae_pct')))}"
        "</div>"
        "</article>"
    )


def _render_table(
    rows: list[dict[str, Any]],
    columns: list[tuple[str, Callable[[dict[str, Any]], str]]],
    empty_message: str,
) -> str:
    if not rows:
        return f"<div class='empty'>{escape(empty_message)}</div>"
    head = "".join(f"<th>{escape(label)}</th>" for label, _ in columns)
    body = []
    for row in rows:
        cells = "".join(f"<td>{renderer(row)}</td>" for _, renderer in columns)
        body.append(f"<tr>{cells}</tr>")
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def _score_item_html(label: str, value: str) -> str:
    return (
        "<div class='score-item'>"
        f"<strong>{escape(label)}</strong>"
        f"<span>{escape(value)}</span>"
        "</div>"
    )


def _stack_html(lines: list[str]) -> str:
    return "<div class='stack'>" + "".join(f"<div>{line}</div>" for line in lines if line) + "</div>"


def _theme_line(row: dict[str, Any]) -> str:
    themes = _coerce_list(row.get("themes"))
    if not themes:
        return "<span class='mono'>theme: -</span>"
    joined = ", ".join(str(theme) for theme in themes[:3])
    return f"<span class='mono'>theme: {escape(joined)}</span>"


def _reason_html(value: Any) -> str:
    reasons = [_translate_reason(reason) for reason in _coerce_list(value)[:3]]
    if not reasons:
        return "<span class='mono'>-</span>"
    return _stack_html([escape(reason) for reason in reasons])


def _entry_basis_reasons(row: dict[str, Any]) -> list[str]:
    reasons = _coerce_list(row.get("reasons"))
    label = str(row.get("analysis_label") or "")
    selected: list[str] = []
    for key in (
        "live_dollar_volume_strong",
        "live_dollar_volume_ok",
        "spread_ok",
        "pct_leader",
        "pct_momentum",
        "watchlist_predicted",
        "context_missing",
        "late_extension",
        "spread_wide",
        "first_pullback_only",
        "news_check_required",
        "wait_for_reclaim",
        "no_chase",
    ):
        if key in reasons:
            selected.append(key)

    if label == "CONDITIONAL_ENTRY" and "first_pullback_only" not in selected:
        selected.append("first_pullback_only")
    if label == "NEWS_CHECK_FIRST" and "news_check_required" not in selected:
        selected.append("news_check_required")
    if label == "WAIT_PULLBACK" and "wait_for_reclaim" not in selected:
        selected.append("wait_for_reclaim")
    if label == "NO_CHASE" and "no_chase" not in selected:
        selected.append("no_chase")
    return selected[:4]


def _pill_html(value: Any) -> str:
    decision = str(value or "")
    label = _translate_label(decision) if decision else "NO SIGNAL"
    klass = "pill-neutral"
    if decision in {"ENTER", "CONDITIONAL_ENTRY", "OPENING_RANGE_CANDIDATE"}:
        klass = "pill-enter"
    elif decision in {"WATCH", "WAIT_PULLBACK", "NEWS_CHECK_FIRST"}:
        klass = "pill-watch"
    elif decision in {"AVOID", "NO_CHASE"}:
        klass = "pill-avoid"
    return f"<span class='pill {klass}'>{escape(label)}</span>"


def _render_chip_list(values: Any) -> list[str]:
    return [f"<span class='chip'>{escape(str(value))}</span>" for value in values if value]


def _find_generated_at(payload: dict[str, Any]) -> str:
    meta = payload.get("meta") or {}
    report = payload.get("report") or {}
    if report.get("created_at"):
        return str(report["created_at"])
    for key in (
        "session",
        "live_regular",
        "premarket",
        "live_premarket",
        "watchlist",
        "prediction_premarket",
        "prediction_regular",
        "social",
        "universe",
    ):
        rows = payload.get(key) or []
        if rows and rows[0].get("created_at"):
            return str(rows[0]["created_at"])
    if meta.get("selected_created_at"):
        return str(meta["selected_created_at"])
    return "-"


def _translate_label(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return LABEL_MAP.get(str(value), str(value))


def _translate_reason(reason: str) -> str:
    if not reason:
        return "-"
    if reason in REASON_MAP:
        return REASON_MAP[reason]
    if ":" in reason:
        key, suffix = reason.split(":", 1)
        if key in REASON_MAP:
            return f"{REASON_MAP[key]} ({suffix})"
    return reason.replace("_", " ")


def _coerce_list(value: Any) -> list[str]:
    if value is None or value == "" or value == "-":
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _unique_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = str(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def _safe_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _format_float(value: Any, precision: int = 2) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value):.{precision}f}"
    except Exception:
        return str(value)


def _format_percent(value: Any, precision: int = 2, scale: float = 1.0) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value) * scale:.{precision}f}%"
    except Exception:
        return str(value)


def _format_integer(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{int(float(value)):,}"
    except Exception:
        return str(value)


def _escape_text(value: Any) -> str:
    return escape(str(value if value not in {None, ""} else "-"))
