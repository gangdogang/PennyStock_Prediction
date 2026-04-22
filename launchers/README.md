# Launchers

`launchers/` is for double-click or scheduler entry points. The Python CLI remains the source of truth for behavior; launcher files only prepare the local runtime and call that CLI.

## macOS

The repository root keeps Finder-friendly `.command` files:

- `launch_dashboard.command`
- `launch_snapshot.command`
- `launch_ai_supervisor.command`
- `launch_paper_trader.command`
- `live_api_setup.command`
- `start_paper_trader_background.command`
- `paper_trader_status.command`
- `stop_paper_trader_background.command`

`scripts/stop_paper_trader_background.command` is kept as the implementation helper behind the root stop launcher.

## Windows

PowerShell files are canonical. Batch files are only double-click wrappers that forward to the matching `.ps1`.

Interactive launchers:

- `launch_dashboard.ps1` / `.bat`
- `launch_dashboard_lan.ps1` / `.bat`
- `launch_paper_trader.ps1` / `.bat`
- `live_api_setup.ps1` / `.bat`

Paper performance review launcher:

- `run_paper_24h_drive.ps1` / `.bat`

Use `run_paper_24h_drive.ps1` for overnight or 24-hour paper performance checks. It writes run-scoped logs, initial pipeline outputs, paper CSV exports, `launcher_manifest.json`, a database copy, and a `paper-performance.zip` archive under a Drive/OneDrive-style folder instead of overwriting the root `sample_outputs/paper_trading/` directory.

Task Scheduler helpers:

- `install_paper_trader_task.ps1` / `.bat`
- `paper_trader_task_status.ps1` / `.bat`
- `remove_paper_trader_task.ps1` / `.bat`
- `install_ai_supervisor_task.ps1`
- `ai_supervisor_task_status.ps1`
- `remove_ai_supervisor_task.ps1`

Scheduler internals:

- `run_paper_trader_background.ps1`
- `run_ai_supervisor_background.ps1`

Do not start the `run_*_background.ps1` files directly unless you are debugging the scheduled task itself.

## Not Launchers

These are shared scripts or CLI shims, not one-click launchers:

- `scripts/run_full_pipeline.*`
- `scripts/check_quality.*`
- `scripts/psradar`
- `scripts/dashboard`
- `scripts/windows_common.ps1`
