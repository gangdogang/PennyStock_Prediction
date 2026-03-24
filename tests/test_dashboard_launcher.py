from __future__ import annotations

from types import SimpleNamespace


def test_dashboard_launcher_builds_streamlit_command(monkeypatch, tmp_path) -> None:
    dashboard_module = __import__("penny_stock_radar.dashboard", fromlist=["launch_dashboard"])

    captured: dict[str, list[str]] = {}

    monkeypatch.setattr(dashboard_module.importlib.util, "find_spec", lambda name: object())

    def fake_run(command, check=False):
        captured["command"] = command
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        dashboard_module.subprocess,
        "run",
        fake_run,
    )
    monkeypatch.setattr(dashboard_module, "_streamlit_app_path", lambda: tmp_path / "app.py")

    dashboard_module.launch_dashboard(host="0.0.0.0", port=9999, open_browser=False)

    command = captured["command"]
    assert command[:4] == [dashboard_module.sys.executable, "-m", "streamlit", "run"]
    assert command[4] == str(tmp_path / "app.py")
    assert "--server.address" in command
    assert "0.0.0.0" in command
    assert "--server.port" in command
    assert "9999" in command
    assert "--server.headless" in command


def test_dashboard_launcher_errors_without_streamlit(monkeypatch) -> None:
    dashboard_module = __import__("penny_stock_radar.dashboard", fromlist=["launch_dashboard"])

    monkeypatch.setattr(dashboard_module.importlib.util, "find_spec", lambda name: None)

    try:
        dashboard_module.launch_dashboard()
    except SystemExit as exc:
        message = str(exc)
        assert "Streamlit is not installed" in message
    else:
        raise AssertionError("Expected launch_dashboard() to abort when streamlit is missing.")
