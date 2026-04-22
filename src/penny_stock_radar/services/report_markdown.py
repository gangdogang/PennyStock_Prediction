from __future__ import annotations

from typing import Any


def render_markdown(payload: dict[str, object]) -> str:
    lines = ["# Penny Stock Radar Summary", ""]

    watchlist = _rows(payload, "watchlist")
    lines.append("## Watchlist")
    if watchlist:
        for row in watchlist:
            lines.append(
                f"- {row['symbol']}: total={row['total_score']:.2f}, catalyst={row['catalyst_score']:.2f}, technical={row['technical_score']:.2f}, context={row.get('market_context_score', 0):.2f}"
            )
    else:
        lines.append("- No watchlist entries")
    lines.append("")

    premarket = _rows(payload, "premarket")
    lines.append("## Premarket")
    if premarket:
        for row in premarket:
            lines.append(
                f"- {row['symbol']}: quality={row['quality_score']:.2f}, rvol={row['premarket_rvol']:.2f}, dv={row['dollar_volume']:.0f}"
            )
    else:
        lines.append("- No premarket signals")
    lines.append("")

    live_premarket = _rows(payload, "live_premarket")
    lines.append("## Premarket Live Movers")
    if live_premarket:
        for row in live_premarket:
            lines.append(
                f"- {row['symbol']}: pct_rank={row['pct_rank']} vol_rank={row['volume_rank']} pct_change={row.get('pct_change', 0):.2f} volume={row.get('volume', 0):.0f} read={row.get('analysis_label', '-')}"
            )
    else:
        lines.append("- No stored live premarket movers")
    lines.append("")

    prediction_premarket = _rows(payload, "prediction_premarket")
    lines.append("## Premarket Prediction Audit")
    if prediction_premarket:
        for row in prediction_premarket:
            lines.append(
                f"- {row['symbol']}: predicted={row.get('predicted')} pct_rank={row.get('pct_rank', '-')} volume_rank={row.get('volume_rank', '-')} outcome={row.get('outcome', '-')}"
            )
    else:
        lines.append("- No premarket prediction audit")
    lines.append("")

    session = _rows(payload, "session")
    lines.append("## Session Decisions")
    if session:
        for row in session:
            lines.append(
                f"- {row['symbol']}: decision={row['decision']}, ext_z={row['extension_z']:.2f}, mfe={row['mfe_pct']:.2f}, mae={row['mae_pct']:.2f}"
            )
    else:
        lines.append("- No session decisions")
    lines.append("")

    live_regular = _rows(payload, "live_regular")
    lines.append("## Regular Live Movers")
    if live_regular:
        for row in live_regular:
            lines.append(
                f"- {row['symbol']}: pct_rank={row['pct_rank']} vol_rank={row['volume_rank']} pct_change={row.get('pct_change', 0):.2f} volume={row.get('volume', 0):.0f} read={row.get('analysis_label', '-')}"
            )
    else:
        lines.append("- No stored live regular movers")
    lines.append("")

    prediction_regular = _rows(payload, "prediction_regular")
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

    social = _rows(payload, "social")
    lines.append("## Social")
    if social:
        for row in social:
            lines.append(
                f"- {row['symbol']}: social_score={row['social_score']:.2f}, mentions={row['mention_count']}, velocity={row['mention_velocity']:.2f}"
            )
    else:
        lines.append("- No social signals")

    return "\n".join(lines) + "\n"


def _rows(payload: dict[str, object], key: str) -> list[dict[str, Any]]:
    rows = payload.get(key) or []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]
