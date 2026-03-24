from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


def _streamlit_app_path() -> Path:
    return Path(__file__).resolve().parent / "ui" / "app.py"


def build_streamlit_command(
    host: str = "localhost",
    port: int = 8501,
    open_browser: bool = True,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(_streamlit_app_path()),
        "--server.address",
        host,
        "--server.port",
        str(port),
    ]
    if not open_browser:
        command.extend(["--server.headless", "true"])
    return command


def launch_dashboard(
    host: str = "localhost",
    port: int = 8501,
    open_browser: bool = True,
) -> None:
    if importlib.util.find_spec("streamlit") is None:
        raise SystemExit(
            "Streamlit is not installed. Install the optional UI extra with "
            "`pip install -e '.[ui]'`, then run `psradar dashboard` again."
        )

    command = build_streamlit_command(host=host, port=port, open_browser=open_browser)
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> None:
    launch_dashboard()


if __name__ == "__main__":
    main()
