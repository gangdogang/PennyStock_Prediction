from __future__ import annotations

from pathlib import Path

from penny_stock_radar import runtime_scripts


def test_full_refresh_command_uses_shell_script_on_posix(monkeypatch) -> None:
    monkeypatch.setattr(runtime_scripts, "_is_windows", lambda: False)

    command = runtime_scripts.full_refresh_command(Path("/tmp/project"))

    assert command == ["/tmp/project/scripts/run_full_pipeline.sh"]
    assert runtime_scripts.full_refresh_command_label() == "./scripts/run_full_pipeline.sh"


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
