from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3

from penny_stock_radar.ai_supervisor import AISupervisor, CommandResult
from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import create_snapshot_run, init_database


class FakeReviewer:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate_markdown(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "## Overview\nready\n\n## Risks\nnone\n\n## Next Action\nhold"


def _seed_scan(db_path: Path, created_at: datetime) -> None:
    snapshot = create_snapshot_run(db_path, source="test", symbol_count=1)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE scan_runs SET created_at = ? WHERE scan_id = ?",
            (created_at.isoformat(), snapshot.snapshot_id),
        )
        connection.commit()


def test_ai_supervisor_refreshes_when_scan_is_stale(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 3, 25, 1, 0, tzinfo=timezone.utc)
    _seed_scan(db_path, now - timedelta(minutes=45))

    commands: list[list[str]] = []
    snapshot_output = tmp_path / "dashboard.html"
    review_output = tmp_path / "gemini_review.md"
    prompt_path = tmp_path / "gemini_prompt.md"
    log_path = tmp_path / "ai_supervisor.log"
    state_path = tmp_path / "ai_supervisor_state.json"
    reviewer = FakeReviewer()

    def fake_command_runner(command: list[str], cwd: Path) -> CommandResult:
        commands.append(command)
        return CommandResult(returncode=0, stdout="ok", stderr="")

    def fake_snapshot_builder(output_path: Path) -> Path:
        output_path.write_text("<html>snapshot</html>", encoding="utf-8")
        return output_path

    prompt_path.write_text("scan=$scan_id actions=$actions\n$watchlist", encoding="utf-8")

    supervisor = AISupervisor(
        AppSettings(db_path=db_path, live_market_provider="disabled"),
        refresh_if_older_than_minutes=15,
        snapshot_output=snapshot_output,
        review_output=review_output,
        prompt_path=prompt_path,
        log_path=log_path,
        state_path=state_path,
        command_runner=fake_command_runner,
        snapshot_builder=fake_snapshot_builder,
        reviewer=reviewer,
        now_fn=lambda: now,
        git_status_provider=lambda root: "clean",
    )

    result = supervisor.run_once()

    assert result.ok is True
    assert "full_refresh" in result.actions
    assert "snapshot_export" in result.actions
    assert "gemini_review" in result.actions
    assert commands and commands[0][0].endswith("run_full_pipeline.sh")
    assert snapshot_output.exists()
    assert review_output.exists()
    assert state_path.exists()
    assert reviewer.prompts


def test_ai_supervisor_skips_refresh_when_scan_is_fresh(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 3, 25, 1, 0, tzinfo=timezone.utc)
    _seed_scan(db_path, now - timedelta(minutes=3))

    commands: list[list[str]] = []
    snapshot_output = tmp_path / "dashboard.html"
    review_output = tmp_path / "gemini_review.md"
    snapshot_output.write_text("<html>snapshot</html>", encoding="utf-8")
    review_output.write_text("existing", encoding="utf-8")

    def fake_command_runner(command: list[str], cwd: Path) -> CommandResult:
        commands.append(command)
        return CommandResult(returncode=0, stdout="ok", stderr="")

    supervisor = AISupervisor(
        AppSettings(db_path=db_path, live_market_provider="disabled"),
        refresh_if_older_than_minutes=15,
        snapshot_output=snapshot_output,
        review_output=review_output,
        prompt_path=tmp_path / "gemini_prompt.md",
        log_path=tmp_path / "ai_supervisor.log",
        state_path=tmp_path / "ai_supervisor_state.json",
        command_runner=fake_command_runner,
        reviewer=None,
        now_fn=lambda: now,
        git_status_provider=lambda root: "clean",
    )

    result = supervisor.run_once()

    assert result.ok is True
    assert result.actions == ["noop"]
    assert commands == []


def test_ai_supervisor_rebuilds_snapshot_when_database_is_newer(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 3, 25, 1, 0, tzinfo=timezone.utc)
    _seed_scan(db_path, now - timedelta(minutes=3))

    commands: list[list[str]] = []
    snapshot_output = tmp_path / "dashboard.html"
    review_output = tmp_path / "gemini_review.md"
    snapshot_output.write_text("<html>stale</html>", encoding="utf-8")
    review_output.write_text("existing", encoding="utf-8")

    old_ts = (now - timedelta(minutes=10)).timestamp()
    new_ts = (now - timedelta(minutes=1)).timestamp()
    os.utime(snapshot_output, (old_ts, old_ts))
    os.utime(db_path, (new_ts, new_ts))

    def fake_command_runner(command: list[str], cwd: Path) -> CommandResult:
        commands.append(command)
        return CommandResult(returncode=0, stdout="ok", stderr="")

    def fake_snapshot_builder(output_path: Path) -> Path:
        output_path.write_text("<html>fresh</html>", encoding="utf-8")
        return output_path

    supervisor = AISupervisor(
        AppSettings(db_path=db_path, live_market_provider="disabled"),
        refresh_if_older_than_minutes=15,
        snapshot_output=snapshot_output,
        review_output=review_output,
        prompt_path=tmp_path / "gemini_prompt.md",
        log_path=tmp_path / "ai_supervisor.log",
        state_path=tmp_path / "ai_supervisor_state.json",
        command_runner=fake_command_runner,
        snapshot_builder=fake_snapshot_builder,
        reviewer=None,
        now_fn=lambda: now,
        git_status_provider=lambda root: "clean",
    )

    result = supervisor.run_once()

    assert result.ok is True
    assert result.actions == ["snapshot_export"]
    assert snapshot_output.read_text(encoding="utf-8") == "<html>fresh</html>"
    assert commands == []


def test_ai_supervisor_skips_duplicate_gemini_review_when_nothing_changed(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 3, 25, 1, 0, tzinfo=timezone.utc)
    _seed_scan(db_path, now - timedelta(minutes=2))

    snapshot_output = tmp_path / "dashboard.html"
    snapshot_output.write_text("<html>snapshot</html>", encoding="utf-8")
    review_output = tmp_path / "gemini_review.md"
    prompt_path = tmp_path / "gemini_prompt.md"
    prompt_path.write_text("scan=$scan_id actions=$actions", encoding="utf-8")
    reviewer = FakeReviewer()

    supervisor = AISupervisor(
        AppSettings(db_path=db_path, live_market_provider="disabled"),
        refresh_if_older_than_minutes=15,
        snapshot_output=snapshot_output,
        review_output=review_output,
        prompt_path=prompt_path,
        log_path=tmp_path / "ai_supervisor.log",
        state_path=tmp_path / "ai_supervisor_state.json",
        command_runner=lambda command, cwd: CommandResult(0, "ok", ""),
        reviewer=reviewer,
        now_fn=lambda: now,
        git_status_provider=lambda root: "clean",
    )

    first = supervisor.run_once()
    second = supervisor.run_once()

    assert first.ok is True
    assert "gemini_review" in first.actions
    assert second.ok is True
    assert "gemini_review" not in second.actions
    assert reviewer.prompts and len(reviewer.prompts) == 1
