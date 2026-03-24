from __future__ import annotations

import json
from pathlib import Path

from ..db import (
    fetch_latest_candidates,
    fetch_latest_premarket_signals,
    fetch_latest_replay_report,
    fetch_latest_session_decisions,
    fetch_latest_social_signals,
    fetch_latest_watchlist,
)


class ReportBuilder:
    def build_payload(self, database_path: Path, limit: int = 20) -> dict[str, object]:
        return {
            "universe": [dict(row) for row in fetch_latest_candidates(database_path, limit)],
            "watchlist": [dict(row) for row in fetch_latest_watchlist(database_path, limit)],
            "premarket": [dict(row) for row in fetch_latest_premarket_signals(database_path, limit)],
            "session": [dict(row) for row in fetch_latest_session_decisions(database_path, limit)],
            "social": [dict(row) for row in fetch_latest_social_signals(database_path, limit)],
            "report": dict(fetch_latest_replay_report(database_path) or {}),
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
                    f"- {row['symbol']}: total={row['total_score']:.2f}, catalyst={row['catalyst_score']:.2f}, technical={row['technical_score']:.2f}"
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
