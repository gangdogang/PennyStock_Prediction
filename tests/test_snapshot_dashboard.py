from __future__ import annotations

from pathlib import Path


def test_launch_snapshot_dashboard_exports_and_opens(monkeypatch, tmp_path: Path) -> None:
    module = __import__(
        "penny_stock_radar.snapshot_dashboard",
        fromlist=["launch_snapshot_dashboard"],
    )

    captured: dict[str, object] = {}

    class FakeBuilder:
        def export_html(self, database_path: Path, output_path: Path, limit: int = 20) -> str:
            captured["database_path"] = database_path
            captured["output_path"] = output_path
            captured["limit"] = limit
            output_path.write_text("<html>preview</html>", encoding="utf-8")
            return "<html>preview</html>"

    monkeypatch.setattr(module, "ReportBuilder", FakeBuilder)

    def fake_open(uri: str) -> bool:
        captured["uri"] = uri
        return True

    monkeypatch.setattr(module.webbrowser, "open", fake_open)

    db_path = tmp_path / "radar.sqlite3"
    output_path = tmp_path / "dashboard.html"
    html_path, opened = module.launch_snapshot_dashboard(
        db_path,
        output_path=output_path,
        limit=7,
        open_browser=True,
    )

    assert opened is True
    assert html_path == output_path.resolve()
    assert captured["database_path"] == db_path
    assert captured["output_path"] == output_path
    assert captured["limit"] == 7
    assert captured["uri"] == output_path.resolve().as_uri()


def test_launch_snapshot_dashboard_skips_browser_when_requested(monkeypatch, tmp_path: Path) -> None:
    module = __import__(
        "penny_stock_radar.snapshot_dashboard",
        fromlist=["launch_snapshot_dashboard"],
    )

    class FakeBuilder:
        def export_html(self, database_path: Path, output_path: Path, limit: int = 20) -> str:
            output_path.write_text("<html>preview</html>", encoding="utf-8")
            return "<html>preview</html>"

    monkeypatch.setattr(module, "ReportBuilder", FakeBuilder)

    def fail_open(uri: str) -> bool:
        raise AssertionError("Browser should not open when open_browser=False")

    monkeypatch.setattr(module.webbrowser, "open", fail_open)

    output_path = tmp_path / "dashboard.html"
    html_path, opened = module.launch_snapshot_dashboard(
        tmp_path / "radar.sqlite3",
        output_path=output_path,
        open_browser=False,
    )

    assert html_path == output_path.resolve()
    assert opened is False
