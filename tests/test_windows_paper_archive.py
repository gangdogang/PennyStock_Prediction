from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest


def test_windows_snapshot_archive_includes_database_from_manifest_when_run_db_dir_is_empty(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is not available in this test environment.")

    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "launchers" / "windows" / "archive_paper_run_snapshot.ps1"
    drive_root = tmp_path / "drive"
    run_id = "paper_run_fixture"
    run_root = drive_root / "paper_runs" / run_id
    output_path = run_root / "archives" / "snapshot.zip"
    database_path = tmp_path / "live.sqlite3"

    (run_root / "paper_trading").mkdir(parents=True)
    (run_root / "logs").mkdir()
    (run_root / "pipeline_outputs").mkdir()
    (run_root / "database").mkdir()
    (run_root / "archives").mkdir()
    (run_root / "paper_trading" / "paper_trade_log.csv").write_text("symbol\nAAA\n", encoding="utf-8")
    (run_root / "logs" / "paper.log").write_text("ok\n", encoding="utf-8")
    (run_root / "pipeline_outputs" / "radar_summary.json").write_text("{}\n", encoding="utf-8")
    database_path.write_bytes(b"sqlite fixture")
    (run_root / "launcher_manifest.json").write_text(
        json.dumps({"database_path": str(database_path)}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-DriveRoot",
            str(drive_root),
            "-RunId",
            run_id,
            "-OutputPath",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    with ZipFile(output_path) as archive:
        names = set(archive.namelist())
        snapshot_manifest = json.loads(archive.read("snapshot_manifest.json"))

    assert "paper_trading/paper_trade_log.csv" in names
    assert "logs/paper.log" in names
    assert "pipeline_outputs/radar_summary.json" in names
    assert "launcher_manifest.json" in names
    assert "database/live.sqlite3" in names
    assert snapshot_manifest["database"]["included"] is True
    assert "database" in snapshot_manifest["contents"]
