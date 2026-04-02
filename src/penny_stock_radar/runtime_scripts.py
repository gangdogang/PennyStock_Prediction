from __future__ import annotations

import os
from pathlib import Path


def _is_windows() -> bool:
    return os.name == "nt"


def full_refresh_command(project_root: Path) -> list[str]:
    scripts_dir = project_root / "scripts"
    if _is_windows():
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(scripts_dir / "run_full_pipeline.ps1"),
        ]
    return [str(scripts_dir / "run_full_pipeline.sh")]


def full_refresh_command_label() -> str:
    if _is_windows():
        return r".\scripts\run_full_pipeline.ps1"
    return "./scripts/run_full_pipeline.sh"
