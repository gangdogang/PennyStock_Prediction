from __future__ import annotations

import typer

from ..ai_supervisor import (
    default_automation_status_path,
    format_automation_status_text,
    load_automation_status,
)
from ..config import get_settings
from ..db import (
    fetch_execution_orders,
    fetch_execution_positions,
    fetch_latest_execution_account,
    fetch_latest_paper_strategy_runs,
    fetch_latest_paper_trading_run,
    fetch_paper_orders,
    fetch_paper_positions,
    init_database,
)
from ..services.broker_execution import BrokerExecutionService
from ..services.premkt_predictor import PremktPredictor
from ..services.trade_plan import TradePlanService
from ..services.watchlist_builder import WatchlistBuilder
from .automation import app as automation_app
from .backtest import app as backtest_app
from .broker import app as broker_app
from .common import console
from .discovery import app as discovery_app
from .paper import app as paper_app
from .premkt import app as premkt_app
from .replay import app as replay_app

app = typer.Typer(help="Penny stock radar CLI.")
app.add_typer(premkt_app)
app.add_typer(discovery_app)
app.add_typer(backtest_app)
app.add_typer(replay_app)
app.add_typer(automation_app)
app.add_typer(paper_app)
app.add_typer(broker_app)

__all__ = [
    "BrokerExecutionService",
    "PremktPredictor",
    "TradePlanService",
    "WatchlistBuilder",
    "app",
    "console",
    "default_automation_status_path",
    "fetch_execution_orders",
    "fetch_execution_positions",
    "fetch_latest_execution_account",
    "fetch_latest_paper_strategy_runs",
    "fetch_latest_paper_trading_run",
    "fetch_paper_orders",
    "fetch_paper_positions",
    "format_automation_status_text",
    "get_settings",
    "init_database",
    "load_automation_status",
]
