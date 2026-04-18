from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    create_paper_trading_run,
    fetch_execution_orders,
    fetch_execution_positions,
    fetch_latest_execution_account,
    init_database,
    insert_paper_orders,
)
from penny_stock_radar.models import PaperOrder, TradePlanCandidate
from penny_stock_radar.providers.broker import (
    BrokerAccount,
    BrokerOrder,
    BrokerOrderRequest,
    BrokerPosition,
    BrokerReplaceRequest,
)
from penny_stock_radar.services.broker_execution import BrokerExecutionService
from penny_stock_radar.services.multiday_engine import MULTIDAY_PAPER_STRATEGY
from penny_stock_radar.services.trade_plan import TradePlanResult


class FakeBrokerAdapter:
    adapter_name = "fake_broker"
    account_id = "fake-01"

    def __init__(self) -> None:
        self.submit_requests: list[BrokerOrderRequest] = []

    def is_available(self) -> bool:
        return True

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        self.submit_requests.append(request)
        return BrokerOrder(
            client_order_id=request.client_order_id,
            broker_name=self.adapter_name,
            account_id=self.account_id,
            broker_order_id="BRK-1",
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            filled_quantity=0,
            remaining_quantity=request.quantity,
            limit_price=request.limit_price,
            exchange_code=request.exchange_code or "NASD",
            status="ACKNOWLEDGED",
            intent=request.intent,
            strategy_bucket=request.strategy_bucket,
            market_phase=request.market_phase,
            request_payload=request.model_dump(mode="json"),
            response_payload={"broker_order_id": "BRK-1"},
            submitted_at=request.created_at,
            updated_at=request.created_at,
        )

    def replace_order(self, order: BrokerOrder, request: BrokerReplaceRequest) -> BrokerOrder:
        return BrokerOrder(
            client_order_id=request.client_order_id,
            broker_name=self.adapter_name,
            account_id=self.account_id,
            broker_order_id="BRK-2",
            original_broker_order_id=order.broker_order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=request.new_quantity or order.quantity,
            filled_quantity=order.filled_quantity,
            remaining_quantity=request.new_quantity or order.remaining_quantity,
            limit_price=request.new_limit_price or order.limit_price,
            exchange_code=order.exchange_code,
            status="REPLACED",
            intent=order.intent,
            strategy_bucket=order.strategy_bucket,
            market_phase=order.market_phase,
            request_payload=request.model_dump(mode="json"),
            response_payload={"broker_order_id": "BRK-2"},
            submitted_at=request.created_at,
            updated_at=request.created_at,
        )

    def cancel_order(self, order: BrokerOrder, *, client_order_id: str) -> BrokerOrder:
        now = datetime.now(timezone.utc)
        return BrokerOrder(
            client_order_id=client_order_id,
            broker_name=self.adapter_name,
            account_id=self.account_id,
            broker_order_id="BRK-3",
            original_broker_order_id=order.broker_order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            filled_quantity=order.filled_quantity,
            remaining_quantity=order.remaining_quantity,
            limit_price=order.limit_price,
            exchange_code=order.exchange_code,
            status="CANCELED",
            intent=order.intent,
            strategy_bucket=order.strategy_bucket,
            market_phase=order.market_phase,
            request_payload={},
            response_payload={"broker_order_id": "BRK-3"},
            submitted_at=now,
            updated_at=now,
        )

    def fetch_open_orders(self) -> list[BrokerOrder]:
        now = datetime.now(timezone.utc)
        return [
            BrokerOrder(
                client_order_id="open-1",
                broker_name=self.adapter_name,
                account_id=self.account_id,
                broker_order_id="BRK-O1",
                symbol="TSLA",
                side="buy",
                quantity=4,
                filled_quantity=1,
                remaining_quantity=3,
                limit_price=195.0,
                exchange_code="NASD",
                status="WORKING",
                strategy_bucket="predicted_starter",
                submitted_at=now,
                updated_at=now,
            )
        ]

    def fetch_fills(self, *, market_date: str | None = None) -> list[BrokerOrder]:
        now = datetime.now(timezone.utc)
        return [
            BrokerOrder(
                client_order_id="fill-1",
                broker_name=self.adapter_name,
                account_id=self.account_id,
                broker_order_id="BRK-F1",
                symbol="TSLA",
                side="buy",
                quantity=4,
                filled_quantity=4,
                remaining_quantity=0,
                limit_price=195.0,
                avg_fill_price=194.5,
                exchange_code="NASD",
                status="FILLED",
                strategy_bucket="predicted_starter",
                submitted_at=now,
                updated_at=now,
            )
        ]

    def fetch_positions(self) -> list[BrokerPosition]:
        now = datetime.now(timezone.utc)
        return [
            BrokerPosition(
                broker_name=self.adapter_name,
                account_id=self.account_id,
                symbol="TSLA",
                exchange_code="NASD",
                quantity=4,
                available_quantity=4,
                average_price=194.5,
                market_price=196.0,
                market_value=784.0,
                updated_at=now,
            )
        ]

    def fetch_account(self) -> BrokerAccount:
        now = datetime.now(timezone.utc)
        return BrokerAccount(
            broker_name=self.adapter_name,
            account_id=self.account_id,
            cash_balance=9200.0,
            buying_power=8500.0,
            total_equity=9984.0,
            updated_at=now,
        )


def test_broker_execution_service_persists_orders_and_account_snapshots(tmp_path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    settings = AppSettings(db_path=db_path)
    service = BrokerExecutionService(settings, adapter=FakeBrokerAdapter())

    submitted = service.submit_manual_order(
        symbol="TSLA",
        side="buy",
        quantity=4,
        limit_price=195.0,
        strategy_bucket="predicted_starter",
        intent="ENTRY",
        market_phase="premarket",
    )
    replaced = service.replace_order(client_order_id=submitted.client_order_id, quantity=5, limit_price=194.0)
    canceled = service.cancel_order(client_order_id=submitted.client_order_id)
    open_orders = service.refresh_open_orders()
    fills = service.refresh_fills(market_date="20260418")
    positions = service.refresh_positions()
    account = service.refresh_account()

    execution_rows = fetch_execution_orders(db_path, broker_name="fake_broker")
    position_rows = fetch_execution_positions(db_path, broker_name="fake_broker", account_id="fake-01")
    account_row = fetch_latest_execution_account(db_path, broker_name="fake_broker", account_id="fake-01")

    assert submitted.status == "ACKNOWLEDGED"
    assert replaced.status == "REPLACED"
    assert canceled.status == "CANCELED"
    assert len(open_orders) == 1
    assert len(fills) == 1
    assert len(positions) == 1
    assert account.buying_power == 8500.0
    assert len(execution_rows) >= 5
    assert position_rows[0].symbol == "TSLA"
    assert account_row is not None and account_row.total_equity == 9984.0


def test_broker_execution_service_compares_against_latest_paper_orders(tmp_path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    settings = AppSettings(db_path=db_path)
    service = BrokerExecutionService(settings, adapter=FakeBrokerAdapter())
    run = create_paper_trading_run(
        db_path,
        strategy_name="auto_paper_v1",
        initial_capital=10_000.0,
        bucket="predictor_weighted",
    )
    now = datetime(2026, 4, 18, 1, 0, tzinfo=timezone.utc)
    insert_paper_orders(
        db_path,
        [
            PaperOrder(
                order_id="paper-tsla-entry",
                run_id=run.run_id,
                position_id="pos-tsla",
                symbol="TSLA",
                market_phase="premarket",
                action="BUY",
                intent="ENTRY",
                quantity=4,
                price=195.5,
                notional=782.0,
                strategy_bucket="predicted_starter",
                reasons=["predicted_priority_entry"],
                created_at=now,
            )
        ],
    )
    service.submit_manual_order(
        symbol="TSLA",
        side="buy",
        quantity=4,
        limit_price=195.0,
        strategy_bucket="predicted_starter",
        intent="ENTRY",
        market_phase="premarket",
    )

    comparisons = service.compare_with_latest_paper(limit=5)

    assert comparisons
    tsla = next(row for row in comparisons if row.symbol == "TSLA")
    assert tsla.paper_order_id == "paper-tsla-entry"
    assert tsla.execution_status == "ACKNOWLEDGED"
    assert tsla.quantity_delta == 0
    assert tsla.price_delta == -0.5


def test_broker_execution_service_compares_multiday_orders_when_requested(tmp_path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    settings = AppSettings(db_path=db_path)
    now = datetime(2026, 4, 18, 15, 0, tzinfo=timezone.utc)
    service = BrokerExecutionService(
        settings,
        adapter=FakeBrokerAdapter(),
        now_fn=lambda: now,
    )
    run = create_paper_trading_run(
        db_path,
        MULTIDAY_PAPER_STRATEGY,
        settings.paper_initial_capital,
    )
    insert_paper_orders(
        db_path,
        [
            PaperOrder(
                order_id="paper-tsla-add",
                run_id=run.run_id,
                position_id="pos-tsla",
                symbol="TSLA",
                market_phase="regular",
                action="BUY",
                intent="ADD",
                quantity=3,
                price=191.5,
                notional=574.5,
                strategy_bucket="add_1",
                reasons=["winner_add"],
                created_at=now,
            )
        ],
    )
    service.submit_manual_order(
        symbol="TSLA",
        side="buy",
        quantity=3,
        limit_price=191.0,
        strategy_bucket="add_1",
        intent="ADD",
        market_phase="regular",
    )

    comparisons = service.compare_with_latest_paper(
        limit=5,
        strategy_name=MULTIDAY_PAPER_STRATEGY,
        paper_buckets=("starter", "add_1"),
    )

    assert comparisons
    tsla = next(row for row in comparisons if row.symbol == "TSLA")
    assert tsla.paper_order_id == "paper-tsla-add"
    assert tsla.paper_intent == "ADD"
    assert tsla.quantity_delta == 0
    assert tsla.price_delta == -0.5


def test_broker_execution_service_rejects_duplicate_trade_plan_submission(tmp_path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    settings = AppSettings(db_path=db_path)
    now = datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc)
    adapter = FakeBrokerAdapter()
    service = BrokerExecutionService(
        settings,
        adapter=adapter,
        now_fn=lambda: now,
    )
    service.trade_plan = _StubTradePlan(now)

    first = service.submit_trade_plan_candidate(symbol="TSLA", phase="premarket")

    with pytest.raises(ValueError, match="Duplicate execution submission blocked"):
        service.submit_trade_plan_candidate(symbol="TSLA", phase="premarket")

    execution_rows = fetch_execution_orders(db_path, broker_name="fake_broker")

    assert first.client_order_id == "TSLA-submit-premarket-predicted_starter-20260418"
    assert len(adapter.submit_requests) == 1
    assert [row.client_order_id for row in execution_rows] == [first.client_order_id]


class _StubTradePlan:
    def __init__(self, generated_at: datetime) -> None:
        self.generated_at = generated_at

    def generate(self, *, phase: str = "auto", export: bool = True) -> TradePlanResult:
        return TradePlanResult(
            market_phase="premarket",
            regime="hot",
            daily_loss_locked=False,
            current_day_pnl=0.0,
            current_open_risk=0.0,
            candidates=[
                TradePlanCandidate(
                    symbol="TSLA",
                    market_phase="premarket",
                    bucket="predicted_starter",
                    actionability="actionable",
                    entry_reference=195.0,
                    suggested_size=4,
                    regime="hot",
                )
            ],
            csv_path=Path("sample_outputs/trade_plan.csv"),
            checklist_path=Path("sample_outputs/trade_plan.md"),
            generated_at=self.generated_at,
        )
