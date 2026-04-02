from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from penny_stock_radar.cli import app


def test_automation_status_command_renders_json(monkeypatch, tmp_path: Path) -> None:
    status_path = tmp_path / "automation_status.json"
    status_payload = {
        "updated_at": "2026-04-02T01:02:03+00:00",
        "status": "ok",
        "latest_scan_id": "scan-123",
        "scan_age_minutes": 3.5,
        "snapshot_path": "/tmp/radar_dashboard.html",
        "snapshot_age_minutes": 1.0,
        "review_path": "/tmp/gemini_review.md",
        "last_refresh_action": "full_refresh",
        "last_error": None,
        "degraded_reason": None,
    }
    status_path.write_text(json.dumps(status_payload), encoding="utf-8")

    monkeypatch.setattr(
        "penny_stock_radar.cli.default_automation_status_path",
        lambda: status_path,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["automation-status", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["latest_scan_id"] == "scan-123"


def test_automation_status_command_renders_text(monkeypatch, tmp_path: Path) -> None:
    status_path = tmp_path / "automation_status.json"
    status_path.write_text(
        json.dumps(
            {
                "updated_at": "2026-04-02T01:02:03+00:00",
                "status": "degraded",
                "latest_scan_id": "scan-456",
                "scan_age_minutes": 17.25,
                "snapshot_path": "/tmp/radar_dashboard.html",
                "snapshot_age_minutes": 2.0,
                "review_path": "/tmp/gemini_review.md",
                "last_refresh_action": "full_refresh_failed",
                "last_error": "offline",
                "degraded_reason": "refresh_failed_fallback",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "penny_stock_radar.cli.default_automation_status_path",
        lambda: status_path,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["automation-status"])

    assert result.exit_code == 0
    assert "status: degraded" in result.stdout
    assert "degraded_reason: refresh_failed_fallback" in result.stdout
