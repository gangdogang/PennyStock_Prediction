from __future__ import annotations

from pathlib import Path

from penny_stock_radar import runtime_scripts


def test_full_refresh_command_uses_shell_script_on_posix(monkeypatch) -> None:
    monkeypatch.setattr(runtime_scripts, "_is_windows", lambda: False)

    command = runtime_scripts.full_refresh_command(Path("/tmp/project"))

    assert command == ["/tmp/project/scripts/run_full_pipeline.sh"]
    assert runtime_scripts.full_refresh_command_label() == "./scripts/run_full_pipeline.sh"


def test_posix_script_entrypoints_exist() -> None:
    project_root = Path(__file__).resolve().parents[1]

    assert (project_root / "scripts" / "psradar").is_file()
    assert (project_root / "scripts" / "check_quality.sh").is_file()
    macos_launchers = project_root / "launchers" / "macos"
    for launcher in (
        "launch_dashboard.command",
        "launch_snapshot.command",
        "launch_ai_supervisor.command",
        "launch_paper_trader.command",
        "live_api_setup.command",
        "start_paper_trader_background.command",
        "paper_trader_status.command",
        "stop_paper_trader_background.command",
    ):
        assert (macos_launchers / launcher).is_file()


def test_full_refresh_command_uses_powershell_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(runtime_scripts, "_is_windows", lambda: True)

    command = runtime_scripts.full_refresh_command(Path("C:/project"))

    assert command == [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "C:/project/scripts/run_full_pipeline.ps1",
    ]
    assert runtime_scripts.full_refresh_command_label() == r".\scripts\run_full_pipeline.ps1"
