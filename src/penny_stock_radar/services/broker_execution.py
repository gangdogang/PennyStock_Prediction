from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
import uuid
from zoneinfo import ZoneInfo

from ..config import AppSettings
from ..db import (
    fetch_execution_order,
    fetch_execution_orders,
    fetch_latest_execution_account,
    fetch_execution_positions,
    fetch_latest_paper_trading_run,
    fetch_paper_orders,
    replace_execution_positions_snapshot,
    upsert_execution_account,
    upsert_execution_orders,
)
from ..providers.broker import BrokerAccount, BrokerOrder, BrokerOrderRequest, BrokerReplaceRequest
from ..providers.kis_mock_broker import build_broker_adapter
from .paper_runtime import PREDICTOR_WEIGHTED_BUCKET
from .paper_trading import PRIMARY_PAPER_STRATEGY
from .trade_plan import TradePlanCandidate, TradePlanService

EASTERN = ZoneInfo("America/New_York")


@dataclass(slots=True)
class BrokerPaperComparisonRow:
    symbol: str
    side: str
    strategy_bucket: str
    execution_client_order_id: str
    execution_status: str
    execution_quantity: int
    execution_limit_price: float | None
    paper_order_id: str | None
    paper_intent: str | None
    paper_action: str | None
    paper_quantity: int | None
    paper_price: float | None
    quantity_delta: int | None
    price_delta: float | None


class BrokerExecutionService:
    def __init__(
        self,
        settings: AppSettings,
        *,
        now_fn: Callable[[], datetime] | None = None,
        export_root: Path | None = None,
        adapter: object | None = None,
    ) -> None:
        self.settings = settings
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.export_root = export_root or settings.paper_trade_dir
        self.trade_plan = TradePlanService(settings, now_fn=self.now_fn, export_root=self.export_root)
        self.adapter = adapter or build_broker_adapter(settings)

    def submit_manual_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: int,
        limit_price: float,
        exchange_code: str | None = None,
        intent: str = "MANUAL",
        strategy_bucket: str = "manual",
        market_phase: str = "manual",
    ) -> BrokerOrder:
        client_order_id = self._submission_client_order_id(
            symbol=symbol,
            market_phase=market_phase,
            strategy_bucket=strategy_bucket,
        )
        self._reject_duplicate_submission(client_order_id)
        request = BrokerOrderRequest(
            client_order_id=client_order_id,
            symbol=symbol.upper(),
            side=side.lower(),
            quantity=quantity,
            limit_price=limit_price,
            exchange_code=exchange_code,
            intent=intent.upper(),
            strategy_bucket=strategy_bucket,
            market_phase=market_phase,
        )
        order = self.adapter.submit_order(request)
        upsert_execution_orders(self.settings.database_path, [order])
        return order

    def submit_trade_plan_candidate(
        self,
        *,
        symbol: str,
        phase: str = "auto",
    ) -> BrokerOrder:
        result = self.trade_plan.generate(phase=phase, export=False)
        target = next(
            (row for row in result.candidates if row.symbol.upper() == symbol.upper()),
            None,
        )
        if target is None:
            raise ValueError(f"No trade-plan candidate found for {symbol.upper()}.")
        request = self._request_from_candidate(target, market_phase=result.market_phase)
        self._reject_duplicate_submission(request.client_order_id)
        order = self.adapter.submit_order(request)
        upsert_execution_orders(self.settings.database_path, [order])
        return order

    def replace_order(
        self,
        *,
        client_order_id: str,
        quantity: int | None = None,
        limit_price: float | None = None,
    ) -> BrokerOrder:
        existing = fetch_execution_order(self.settings.database_path, client_order_id)
        if existing is None:
            raise ValueError(f"Unknown execution order: {client_order_id}")
        request = BrokerReplaceRequest(
            client_order_id=self._client_order_id(symbol=existing.symbol, suffix="replace"),
            new_quantity=quantity,
            new_limit_price=limit_price,
        )
        order = self.adapter.replace_order(existing, request)
        upsert_execution_orders(self.settings.database_path, [order])
        return order

    def cancel_order(self, *, client_order_id: str) -> BrokerOrder:
        existing = fetch_execution_order(self.settings.database_path, client_order_id)
        if existing is None:
            raise ValueError(f"Unknown execution order: {client_order_id}")
        order = self.adapter.cancel_order(
            existing,
            client_order_id=self._client_order_id(symbol=existing.symbol, suffix="cancel"),
        )
        upsert_execution_orders(self.settings.database_path, [order])
        return order

    def refresh_open_orders(self) -> list[BrokerOrder]:
        rows = self.adapter.fetch_open_orders()
        upsert_execution_orders(self.settings.database_path, rows)
        return rows

    def refresh_fills(self, *, market_date: str | None = None) -> list[BrokerOrder]:
        rows = self.adapter.fetch_fills(market_date=market_date)
        upsert_execution_orders(self.settings.database_path, rows)
        return rows

    def refresh_positions(self):
        positions = self.adapter.fetch_positions()
        replace_execution_positions_snapshot(
            self.settings.database_path,
            broker_name=getattr(self.adapter, "adapter_name", "unknown"),
            account_id=getattr(self.adapter, "account_id", ""),
            rows=positions,
        )
        return positions

    def refresh_account(self) -> BrokerAccount:
        account = self.adapter.fetch_account()
        upsert_execution_account(self.settings.database_path, account)
        return account

    def latest_orders(self, *, limit: int = 20, status: str | None = None) -> list[BrokerOrder]:
        return fetch_execution_orders(
            self.settings.database_path,
            broker_name=getattr(self.adapter, "adapter_name", None),
            status=status,
            limit=limit,
        )

    def latest_positions(self):
        return fetch_execution_positions(
            self.settings.database_path,
            broker_name=getattr(self.adapter, "adapter_name", None),
            account_id=getattr(self.adapter, "account_id", None),
        )

    def latest_account(self) -> BrokerAccount | None:
        return fetch_latest_execution_account(
            self.settings.database_path,
            broker_name=getattr(self.adapter, "adapter_name", None),
            account_id=getattr(self.adapter, "account_id", None),
        )

    def compare_with_latest_paper(
        self,
        *,
        limit: int = 20,
        strategy_name: str | None = None,
        paper_buckets: tuple[str, ...] | None = None,
    ) -> list[BrokerPaperComparisonRow]:
        execution_orders = self.latest_orders(limit=max(limit, 1) * 4)
        strategy_names = (
            (strategy_name,)
            if strategy_name
            else (PRIMARY_PAPER_STRATEGY,)
        )
        latest_paper_by_key: dict[tuple[str, str, str], object] = {}
        fallback_paper_by_key: dict[tuple[str, str], object] = {}
        bucket_filter = {bucket for bucket in paper_buckets or ()}
        for current_strategy in strategy_names:
            paper_run = fetch_latest_paper_trading_run(
                self.settings.database_path,
                current_strategy,
                bucket=PREDICTOR_WEIGHTED_BUCKET if current_strategy == PRIMARY_PAPER_STRATEGY else None,
            )
            if paper_run is None:
                continue
            paper_orders = fetch_paper_orders(self.settings.database_path, paper_run.run_id)
            for order in paper_orders:
                if bucket_filter and order.strategy_bucket not in bucket_filter:
                    continue
                bucket_key = order.strategy_bucket or ""
                latest_paper_by_key[(order.symbol.upper(), order.action.upper(), bucket_key)] = order
                fallback_paper_by_key[(order.symbol.upper(), order.action.upper())] = order
        comparisons: list[BrokerPaperComparisonRow] = []
        for execution in execution_orders[: max(limit, 1)]:
            paper_action = "BUY" if execution.side == "buy" else "SELL"
            paper = latest_paper_by_key.get(
                (
                    execution.symbol.upper(),
                    paper_action,
                    execution.strategy_bucket or "",
                )
            ) or fallback_paper_by_key.get((execution.symbol.upper(), paper_action))
            comparisons.append(
                BrokerPaperComparisonRow(
                    symbol=execution.symbol,
                    side=execution.side,
                    strategy_bucket=execution.strategy_bucket,
                    execution_client_order_id=execution.client_order_id,
                    execution_status=execution.status,
                    execution_quantity=execution.quantity,
                    execution_limit_price=execution.limit_price,
                    paper_order_id=getattr(paper, "order_id", None),
                    paper_intent=getattr(paper, "intent", None),
                    paper_action=getattr(paper, "action", None),
                    paper_quantity=getattr(paper, "quantity", None),
                    paper_price=getattr(paper, "price", None),
                    quantity_delta=(
                        execution.quantity - paper.quantity
                        if paper is not None
                        else None
                    ),
                    price_delta=(
                        (execution.limit_price or 0.0) - paper.price
                        if paper is not None and execution.limit_price is not None
                        else None
                    ),
                )
            )
        return comparisons

    def _request_from_candidate(
        self,
        candidate: TradePlanCandidate,
        *,
        market_phase: str,
    ) -> BrokerOrderRequest:
        if candidate.actionability != "actionable":
            raise ValueError(
                f"{candidate.symbol} is not actionable: {', '.join(candidate.blockers or ['unknown'])}"
            )
        if candidate.bucket not in {"predicted_starter", "winner_add"}:
            raise ValueError(
                f"Unsupported broker execution bucket for {candidate.symbol}: {candidate.bucket}"
            )
        if candidate.entry_reference is None or candidate.entry_reference <= 0:
            raise ValueError(f"{candidate.symbol} has no valid entry reference.")
        if candidate.suggested_size <= 0:
            raise ValueError(f"{candidate.symbol} has zero suggested size.")
        return BrokerOrderRequest(
            client_order_id=self._submission_client_order_id(
                symbol=candidate.symbol,
                market_phase=market_phase,
                strategy_bucket=candidate.bucket,
            ),
            symbol=candidate.symbol.upper(),
            side="buy",
            quantity=int(candidate.suggested_size),
            limit_price=float(candidate.entry_reference),
            exchange_code=None,
            intent="ADD" if candidate.bucket == "winner_add" else "ENTRY",
            strategy_bucket=candidate.bucket,
            market_phase=market_phase,
        )

    def _client_order_id(self, *, symbol: str, suffix: str) -> str:
        return f"{symbol.upper()}-{suffix}-{uuid.uuid4().hex[:12]}"

    def _submission_client_order_id(
        self,
        *,
        symbol: str,
        market_phase: str,
        strategy_bucket: str,
    ) -> str:
        market_date = self.now_fn().astimezone(EASTERN).strftime("%Y%m%d")
        return (
            f"{symbol.upper()}-submit-"
            f"{market_phase.strip().lower()}-"
            f"{strategy_bucket.strip().lower()}-"
            f"{market_date}"
        )

    def _reject_duplicate_submission(self, client_order_id: str) -> None:
        if fetch_execution_order(self.settings.database_path, client_order_id) is None:
            return
        raise ValueError(f"Duplicate execution submission blocked: {client_order_id}")
