from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3

from penny_stock_radar.ai_supervisor import (
    AISupervisor,
    CommandResult,
    load_automation_status,
)
from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    create_snapshot_run,
    init_database,
    insert_premarket_signals,
    insert_replay_report,
    insert_session_decisions,
    insert_universe_candidates,
    insert_watchlist,
)
from penny_stock_radar.models import (
    PremarketSignal,
    ReplayEvaluation,
    SessionDecision,
    UniverseCandidate,
    WatchlistEntry,
)
from penny_stock_radar.runtime_scripts import full_refresh_command


class FakeReviewer:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate_markdown(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "## Overview\nready\n\n## Risks\nnone\n\n## Next Action\nhold"


def _seed_complete_scan(db_path: Path, created_at: datetime, symbol: str = "TEST") -> str:
    snapshot = create_snapshot_run(db_path, source="test", symbol_count=1)
    insert_universe_candidates(
        db_path,
        snapshot.snapshot_id,
        [
            UniverseCandidate(
                symbol=symbol,
                company_name=f"{symbol} Corp",
                exchange="Q",
                price=1.25,
                float_shares=5_000_000,
                passed_filters=True,
                filter_reasons=[],
            )
        ],
    )
    insert_watchlist(
        db_path,
        snapshot.snapshot_id,
        [
            WatchlistEntry(
                symbol=symbol,
                total_score=4.2,
                catalyst_score=1.0,
                technical_score=1.0,
                sympathy_score=0.5,
                low_float_bonus=0.5,
                reasons=["seed"],
            )
        ],
    )
    insert_premarket_signals(
        db_path,
        snapshot.snapshot_id,
        [
            PremarketSignal(
                symbol=symbol,
                premarket_high=1.60,
                premarket_low=1.10,
                premarket_close=1.55,
                premarket_vwap=1.30,
                premarket_rvol=4.2,
                dollar_volume=250_000.0,
                trade_count=90,
                tps=0.02,
                size_mean=5000.0,
                size_std=400.0,
                size_cv=0.08,
                spread_pct=0.025,
                round_level_break=True,
                tape_anomaly=True,
                quality_score=5.0,
                reasons=["seed"],
            )
        ],
    )
    insert_session_decisions(
        db_path,
        snapshot.snapshot_id,
        [
            SessionDecision(
                symbol=symbol,
                decision="ENTER",
                anchored_vwap=1.40,
                opening_range_high=1.58,
                opening_range_low=1.18,
                regular_high=1.70,
                regular_low=1.15,
                regular_close=1.62,
                extension_z=1.2,
                mfe_pct=12.0,
                mae_pct=-4.0,
                reasons=["seed"],
            )
        ],
    )
    insert_replay_report(
        db_path,
        snapshot.snapshot_id,
        ReplayEvaluation(
            label="continuation",
            expectancy=1.5,
            profit_factor=1.7,
            precision_at_k=0.8,
            average_mfe_pct=10.0,
            average_mae_pct=-3.0,
            symbol_count=1,
        ),
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE scan_runs SET created_at = ? WHERE scan_id = ?",
            (created_at.isoformat(), snapshot.snapshot_id),
        )
        connection.commit()
    return snapshot.snapshot_id


def _seed_incomplete_scan(db_path: Path, created_at: datetime) -> str:
    snapshot = create_snapshot_run(db_path, source="test", symbol_count=1)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE scan_runs SET created_at = ? WHERE scan_id = ?",
            (created_at.isoformat(), snapshot.snapshot_id),
        )
        connection.commit()
    return snapshot.snapshot_id


def test_ai_supervisor_refreshes_when_scan_is_stale(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 3, 25, 1, 0, tzinfo=timezone.utc)
    _seed_complete_scan(db_path, now - timedelta(minutes=45))

    commands: list[list[str]] = []
    snapshot_output = tmp_path / "dashboard.html"
    review_output = tmp_path / "gemini_review.md"
    prompt_path = tmp_path / "gemini_prompt.md"
    log_path = tmp_path / "ai_supervisor.log"
    state_path = tmp_path / "ai_supervisor_state.json"
    automation_status_path = tmp_path / "automation_status.json"
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
        automation_status_path=automation_status_path,
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
    assert commands and commands[0] == full_refresh_command(supervisor.project_root)
    assert snapshot_output.exists()
    assert review_output.exists()
    assert state_path.exists()
    assert automation_status_path.exists()
    assert reviewer.prompts


def test_ai_supervisor_skips_refresh_when_scan_is_fresh(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 3, 25, 1, 0, tzinfo=timezone.utc)
    _seed_complete_scan(db_path, now - timedelta(minutes=3))

    commands: list[list[str]] = []
    snapshot_output = tmp_path / "dashboard.html"
    review_output = tmp_path / "gemini_review.md"
    automation_status_path = tmp_path / "automation_status.json"
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
        automation_status_path=automation_status_path,
        command_runner=fake_command_runner,
        reviewer=None,
        now_fn=lambda: now,
        git_status_provider=lambda root: "clean",
    )

    result = supervisor.run_once()

    assert result.ok is True
    assert result.actions == ["noop"]
    assert commands == []
    status = load_automation_status(automation_status_path)
    assert status["status"] == "closed_market"


def test_ai_supervisor_rebuilds_snapshot_when_database_is_newer(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 3, 25, 1, 0, tzinfo=timezone.utc)
    _seed_complete_scan(db_path, now - timedelta(minutes=3))

    commands: list[list[str]] = []
    snapshot_output = tmp_path / "dashboard.html"
    review_output = tmp_path / "gemini_review.md"
    automation_status_path = tmp_path / "automation_status.json"
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
        automation_status_path=automation_status_path,
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
    _seed_complete_scan(db_path, now - timedelta(minutes=2))

    snapshot_output = tmp_path / "dashboard.html"
    snapshot_output.write_text("<html>snapshot</html>", encoding="utf-8")
    review_output = tmp_path / "gemini_review.md"
    prompt_path = tmp_path / "gemini_prompt.md"
    prompt_path.write_text("scan=$scan_id actions=$actions", encoding="utf-8")
    automation_status_path = tmp_path / "automation_status.json"
    reviewer = FakeReviewer()

    supervisor = AISupervisor(
        AppSettings(db_path=db_path, live_market_provider="disabled"),
        refresh_if_older_than_minutes=15,
        snapshot_output=snapshot_output,
        review_output=review_output,
        prompt_path=prompt_path,
        log_path=tmp_path / "ai_supervisor.log",
        state_path=tmp_path / "ai_supervisor_state.json",
        automation_status_path=automation_status_path,
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


def test_ai_supervisor_skips_refresh_when_latest_raw_is_incomplete_but_complete_scan_is_fresh(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 3, 25, 1, 0, tzinfo=timezone.utc)
    _seed_complete_scan(db_path, now - timedelta(minutes=4), symbol="GOOD")
    _seed_incomplete_scan(db_path, now - timedelta(minutes=1))

    commands: list[list[str]] = []
    snapshot_output = tmp_path / "dashboard.html"
    review_output = tmp_path / "gemini_review.md"
    automation_status_path = tmp_path / "automation_status.json"
    snapshot_output.write_text("<html>snapshot</html>", encoding="utf-8")
    review_output.write_text("existing", encoding="utf-8")

    supervisor = AISupervisor(
        AppSettings(db_path=db_path, live_market_provider="disabled"),
        refresh_if_older_than_minutes=15,
        snapshot_output=snapshot_output,
        review_output=review_output,
        prompt_path=tmp_path / "gemini_prompt.md",
        log_path=tmp_path / "ai_supervisor.log",
        state_path=tmp_path / "ai_supervisor_state.json",
        automation_status_path=automation_status_path,
        command_runner=lambda command, cwd: commands.append(command) or CommandResult(0, "ok", ""),
        reviewer=None,
        now_fn=lambda: now,
        git_status_provider=lambda root: "clean",
    )

    result = supervisor.run_once()

    assert result.ok is True
    assert result.actions == ["noop"]
    assert commands == []
    status = load_automation_status(automation_status_path)
    assert status["latest_scan_id"] == "GOOD" or status["latest_scan_id"] is not None


def test_ai_supervisor_exports_fallback_snapshot_when_refresh_fails_with_complete_scan(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 3, 25, 1, 0, tzinfo=timezone.utc)
    _seed_complete_scan(db_path, now - timedelta(minutes=45), symbol="GOOD")
    _seed_incomplete_scan(db_path, now - timedelta(minutes=1))

    snapshot_output = tmp_path / "dashboard.html"
    review_output = tmp_path / "gemini_review.md"
    automation_status_path = tmp_path / "automation_status.json"

    def fake_snapshot_builder(output_path: Path) -> Path:
        output_path.write_text("<html>fallback</html>", encoding="utf-8")
        return output_path

    supervisor = AISupervisor(
        AppSettings(db_path=db_path, live_market_provider="disabled"),
        refresh_if_older_than_minutes=15,
        snapshot_output=snapshot_output,
        review_output=review_output,
        prompt_path=tmp_path / "gemini_prompt.md",
        log_path=tmp_path / "ai_supervisor.log",
        state_path=tmp_path / "ai_supervisor_state.json",
        automation_status_path=automation_status_path,
        command_runner=lambda command, cwd: CommandResult(1, "", "offline"),
        snapshot_builder=fake_snapshot_builder,
        reviewer=None,
        now_fn=lambda: now,
        git_status_provider=lambda root: "clean",
    )

    result = supervisor.run_once()

    assert result.ok is True
    assert result.actions == ["full_refresh_failed", "fallback_export"]
    assert result.snapshot_written is True
    assert snapshot_output.read_text(encoding="utf-8") == "<html>fallback</html>"
    assert "fallback" in review_output.read_text(encoding="utf-8").lower()
    status = load_automation_status(automation_status_path)
    assert status["status"] == "degraded"
    assert status["degraded_reason"] == "refresh_failed_fallback"


def test_ai_supervisor_records_failed_status_when_refresh_fails_without_scan(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 3, 25, 13, 0, tzinfo=timezone.utc)
    automation_status_path = tmp_path / "automation_status.json"
    review_output = tmp_path / "gemini_review.md"

    supervisor = AISupervisor(
        AppSettings(db_path=db_path, live_market_provider="disabled"),
        refresh_if_older_than_minutes=15,
        snapshot_output=tmp_path / "dashboard.html",
        review_output=review_output,
        prompt_path=tmp_path / "gemini_prompt.md",
        log_path=tmp_path / "ai_supervisor.log",
        state_path=tmp_path / "ai_supervisor_state.json",
        automation_status_path=automation_status_path,
        command_runner=lambda command, cwd: CommandResult(1, "", "offline"),
        reviewer=None,
        now_fn=lambda: now,
        git_status_provider=lambda root: "clean",
    )

    result = supervisor.run_once()

    assert result.ok is False
    assert result.actions == ["full_refresh_failed"]
    assert review_output.exists()
    status = load_automation_status(automation_status_path)
    assert status["status"] == "failed"
    assert status["last_refresh_action"] == "full_refresh_failed"
