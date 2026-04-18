from __future__ import annotations

from datetime import datetime
import logging
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..config import AppSettings
from .broker import (
    BrokerAccount,
    BrokerOrder,
    BrokerOrderRequest,
    BrokerPosition,
    BrokerReplaceRequest,
    NullBrokerAdapter,
    broker_utc_now,
)
from .kis_client import KisApiClient
from .listings import KisMasterListingProvider

EASTERN = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)
_MASTER_TO_ORDER_EXCHANGE = {
    "NAS": "NASD",
    "NYS": "NYSE",
    "AMS": "AMEX",
}
_DEFAULT_ORDER_SERVER_CODE = "0"
_DEFAULT_ORDER_TYPE = "00"


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def _first_value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in {None, ""}:
            return data[key]
    return None


def _format_price(value: float | None) -> str:
    if value is None:
        return "0"
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def build_broker_adapter(
    settings: AppSettings,
    *,
    client: httpx.Client | None = None,
):
    if settings.broker_adapter == "null":
        return NullBrokerAdapter("Broker adapter is disabled.")
    if settings.broker_adapter != "kis_mock":
        return NullBrokerAdapter(f"Unsupported broker adapter: {settings.broker_adapter}")
    if not settings.kis_mock_app_key or not settings.kis_mock_app_secret:
        return NullBrokerAdapter("KIS mock broker credentials are missing.")
    if not settings.kis_mock_account_number or not settings.kis_mock_account_product_code:
        return NullBrokerAdapter("KIS mock broker account settings are missing.")
    return KISMockBrokerAdapter(settings, client=client)


class KISMockBrokerAdapter:
    adapter_name = "kis_mock"

    def __init__(
        self,
        settings: AppSettings,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.account_number = settings.kis_mock_account_number or ""
        self.account_product_code = settings.kis_mock_account_product_code
        self.account_id = f"{self.account_number}-{self.account_product_code}"
        self._api_client = KisApiClient(
            settings,
            base_url=settings.kis_mock_base_url,
            app_key=settings.kis_mock_app_key,
            app_secret=settings.kis_mock_app_secret,
            token_cache_name="kis_mock_access_token.json",
            environment="mock",
            client=client,
        )
        self._exchange_by_symbol: dict[str, str] | None = None

    def close(self) -> None:
        self._api_client.close()

    def is_available(self) -> bool:
        return self._api_client.is_configured()

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        self._ensure_daytime_session_allowed(symbol=request.symbol)
        exchange_code = self._resolve_exchange_code(
            symbol=request.symbol,
            explicit_exchange_code=request.exchange_code,
        )
        payload = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "OVRS_EXCG_CD": exchange_code,
            "PDNO": request.symbol.upper(),
            "ORD_QTY": str(int(request.quantity)),
            "OVRS_ORD_UNPR": _format_price(request.limit_price),
            "CTAC_TLNO": self.settings.kis_mock_contact_phone,
            "MGCO_APTM_ODNO": "",
            "ORD_SVR_DVSN_CD": self.settings.kis_mock_order_server_code or _DEFAULT_ORDER_SERVER_CODE,
            "ORD_DVSN": self.settings.kis_mock_order_type or _DEFAULT_ORDER_TYPE,
        }
        tr_id = "TTTS6036U" if request.side == "buy" else "TTTS6037U"
        response = self._api_client.post(
            "/uapi/overseas-stock/v1/trading/daytime-order",
            tr_id=tr_id,
            body=payload,
        )
        return self._order_from_ack(
            request=request,
            payload=payload,
            response_payload=response.payload,
            broker_order_id=self._extract_broker_order_id(response.payload),
            status="ACKNOWLEDGED",
        )

    def replace_order(
        self,
        order: BrokerOrder,
        request: BrokerReplaceRequest,
    ) -> BrokerOrder:
        self._ensure_daytime_session_allowed(symbol=order.symbol)
        if not order.broker_order_id:
            raise RuntimeError("A broker order id is required to replace a KIS mock order.")
        exchange_code = self._resolve_exchange_code(
            symbol=order.symbol,
            explicit_exchange_code=order.exchange_code or None,
        )
        new_quantity = int(request.new_quantity or order.quantity)
        new_limit_price = request.new_limit_price if request.new_limit_price is not None else order.limit_price
        payload = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "OVRS_EXCG_CD": exchange_code,
            "PDNO": order.symbol.upper(),
            "ORGN_ODNO": order.broker_order_id,
            "RVSE_CNCL_DVSN_CD": "01",
            "ORD_QTY": str(new_quantity),
            "OVRS_ORD_UNPR": _format_price(new_limit_price),
            "CTAC_TLNO": self.settings.kis_mock_contact_phone,
            "MGCO_APTM_ODNO": "",
            "ORD_SVR_DVSN_CD": self.settings.kis_mock_order_server_code or _DEFAULT_ORDER_SERVER_CODE,
        }
        response = self._api_client.post(
            "/uapi/overseas-stock/v1/trading/daytime-order-rvsecncl",
            tr_id="TTTS6038U",
            body=payload,
        )
        now = broker_utc_now()
        return BrokerOrder(
            client_order_id=request.client_order_id,
            broker_name=self.adapter_name,
            account_id=self.account_id,
            symbol=order.symbol,
            side=order.side,
            quantity=new_quantity,
            filled_quantity=order.filled_quantity,
            remaining_quantity=max(new_quantity - order.filled_quantity, 0),
            limit_price=new_limit_price,
            avg_fill_price=order.avg_fill_price,
            exchange_code=exchange_code,
            status="REPLACED",
            intent=order.intent,
            strategy_bucket=order.strategy_bucket,
            market_phase=order.market_phase,
            order_type=order.order_type,
            broker_order_id=self._extract_broker_order_id(response.payload) or order.broker_order_id,
            original_broker_order_id=order.broker_order_id,
            request_payload=payload,
            response_payload=response.payload,
            notes=["replace"],
            submitted_at=now,
            updated_at=now,
        )

    def cancel_order(
        self,
        order: BrokerOrder,
        *,
        client_order_id: str,
    ) -> BrokerOrder:
        self._ensure_daytime_session_allowed(symbol=order.symbol)
        if not order.broker_order_id:
            raise RuntimeError("A broker order id is required to cancel a KIS mock order.")
        exchange_code = self._resolve_exchange_code(
            symbol=order.symbol,
            explicit_exchange_code=order.exchange_code or None,
        )
        payload = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "OVRS_EXCG_CD": exchange_code,
            "PDNO": order.symbol.upper(),
            "ORGN_ODNO": order.broker_order_id,
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": str(int(order.remaining_quantity or order.quantity)),
            "OVRS_ORD_UNPR": _format_price(order.limit_price),
            "CTAC_TLNO": self.settings.kis_mock_contact_phone,
            "MGCO_APTM_ODNO": "",
            "ORD_SVR_DVSN_CD": self.settings.kis_mock_order_server_code or _DEFAULT_ORDER_SERVER_CODE,
        }
        response = self._api_client.post(
            "/uapi/overseas-stock/v1/trading/daytime-order-rvsecncl",
            tr_id="TTTS6038U",
            body=payload,
        )
        now = broker_utc_now()
        return BrokerOrder(
            client_order_id=client_order_id,
            broker_name=self.adapter_name,
            account_id=self.account_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            filled_quantity=order.filled_quantity,
            remaining_quantity=max(order.remaining_quantity, 0),
            limit_price=order.limit_price,
            avg_fill_price=order.avg_fill_price,
            exchange_code=exchange_code,
            status="CANCELED",
            intent=order.intent,
            strategy_bucket=order.strategy_bucket,
            market_phase=order.market_phase,
            order_type=order.order_type,
            broker_order_id=self._extract_broker_order_id(response.payload) or order.broker_order_id,
            original_broker_order_id=order.broker_order_id,
            request_payload=payload,
            response_payload=response.payload,
            notes=["cancel"],
            submitted_at=now,
            updated_at=now,
        )

    def fetch_open_orders(self) -> list[BrokerOrder]:
        rows = self._fetch_paginated_rows(
            path="/uapi/overseas-stock/v1/trading/inquire-nccs",
            tr_id="TTTS3018R",
            params={
                "CANO": self.account_number,
                "ACNT_PRDT_CD": self.account_product_code,
                "OVRS_EXCG_CD": self.settings.kis_mock_inquiry_exchange_code,
                "SORT_SQN": "DS",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            },
        )
        return [self._order_from_open_row(row) for row in rows]

    def fetch_fills(self, *, market_date: str | None = None) -> list[BrokerOrder]:
        target_date = market_date or datetime.now(EASTERN).date().strftime("%Y%m%d")
        rows = self._fetch_paginated_rows(
            path="/uapi/overseas-stock/v1/trading/inquire-ccnl",
            tr_id="TTTS3035R",
            params={
                "CANO": self.account_number,
                "ACNT_PRDT_CD": self.account_product_code,
                "PDNO": "%",
                "ORD_STRT_DT": target_date,
                "ORD_END_DT": target_date,
                "SLL_BUY_DVSN": "00",
                "CCLD_NCCS_DVSN": "00",
                "OVRS_EXCG_CD": "",
                "SORT_SQN": "DS",
                "ORD_DT": "",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "CTX_AREA_NK200": "",
                "CTX_AREA_FK200": "",
            },
        )
        return [self._order_from_fill_row(row) for row in rows]

    def fetch_positions(self) -> list[BrokerPosition]:
        positions, _ = self._fetch_balance_rows()
        return [self._position_from_balance_row(row) for row in positions]

    def fetch_account(self) -> BrokerAccount:
        _, summary_rows = self._fetch_balance_rows()
        summary = summary_rows[0] if summary_rows else {}
        return BrokerAccount(
            broker_name=self.adapter_name,
            account_id=self.account_id,
            currency=self.settings.kis_mock_balance_currency_code,
            cash_balance=_coerce_float(
                _first_value(summary, "frcr_dncl_amt_2", "frcr_evlu_amt2", "cash", "dncl_amt")
            ),
            buying_power=_coerce_float(
                _first_value(summary, "frcr_buy_mgn_amt", "buy_psbl_amt", "ovrs_ord_psbl_amt", "buying_power")
            ),
            total_equity=_coerce_float(
                _first_value(summary, "tot_evlu_pfls_amt", "tot_evlu_amt", "total_equity")
            ),
            raw_payload=summary,
            updated_at=broker_utc_now(),
        )

    def _fetch_balance_rows(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        params = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_product_code,
            "OVRS_EXCG_CD": self.settings.kis_mock_balance_exchange_code,
            "TR_CRCY_CD": self.settings.kis_mock_balance_currency_code,
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        positions: list[dict[str, Any]] = []
        summaries: list[dict[str, Any]] = []
        next_fk = ""
        next_nk = ""
        tr_cont = ""
        for _ in range(10):
            response = self._api_client.get(
                "/uapi/overseas-stock/v1/trading/inquire-balance",
                tr_id="TTTS3012R",
                params={
                    **params,
                    "CTX_AREA_FK200": next_fk,
                    "CTX_AREA_NK200": next_nk,
                },
                tr_cont=tr_cont,
            )
            output1 = response.payload.get("output1")
            output2 = response.payload.get("output2")
            if isinstance(output1, list):
                positions.extend(row for row in output1 if isinstance(row, dict))
            elif isinstance(output1, dict) and output1:
                positions.append(output1)
            if isinstance(output2, list):
                summaries.extend(row for row in output2 if isinstance(row, dict))
            elif isinstance(output2, dict) and output2:
                summaries.append(output2)
            tr_cont_header = response.headers.get("tr_cont", "")
            next_fk = str(response.payload.get("ctx_area_fk200") or "")
            next_nk = str(response.payload.get("ctx_area_nk200") or "")
            if tr_cont_header not in {"M", "F"}:
                break
            tr_cont = "N"
        return positions, summaries

    def _fetch_paginated_rows(
        self,
        *,
        path: str,
        tr_id: str,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        next_fk = str(params.get("CTX_AREA_FK200") or "")
        next_nk = str(params.get("CTX_AREA_NK200") or "")
        tr_cont = ""
        for _ in range(10):
            response = self._api_client.get(
                path,
                tr_id=tr_id,
                params={
                    **params,
                    "CTX_AREA_FK200": next_fk,
                    "CTX_AREA_NK200": next_nk,
                },
                tr_cont=tr_cont,
            )
            output = response.payload.get("output")
            if isinstance(output, list):
                rows.extend(item for item in output if isinstance(item, dict))
            elif isinstance(output, dict) and output:
                rows.append(output)
            tr_cont_header = response.headers.get("tr_cont", "")
            next_fk = str(response.payload.get("ctx_area_fk200") or "")
            next_nk = str(response.payload.get("ctx_area_nk200") or "")
            if tr_cont_header not in {"M", "F"}:
                break
            tr_cont = "N"
        return rows

    def _ensure_daytime_session_allowed(self, *, symbol: str) -> None:
        session = self._current_market_session()
        if session != "regular":
            return
        logger.warning(
            "kis_mock daytime-order blocked during regular session: symbol=%s session=%s",
            symbol.upper(),
            session,
        )
        raise ValueError(
            "KIS mock broker daytime-order endpoints are only supported outside the US regular session."
        )

    def _current_market_session(self, now: datetime | None = None) -> str:
        current = (now or broker_utc_now()).astimezone(EASTERN)
        if current.weekday() >= 5:
            return "closed"
        session_time = current.timetz().replace(tzinfo=None)
        if session_time < datetime.strptime("04:00", "%H:%M").time():
            return "closed"
        if session_time < datetime.strptime("09:30", "%H:%M").time():
            return "premarket"
        if session_time < datetime.strptime("16:00", "%H:%M").time():
            return "regular"
        if session_time < datetime.strptime("20:00", "%H:%M").time():
            return "afterhours"
        return "closed"

    def _order_from_ack(
        self,
        *,
        request: BrokerOrderRequest,
        payload: dict[str, Any],
        response_payload: dict[str, Any],
        broker_order_id: str | None,
        status: str,
    ) -> BrokerOrder:
        now = broker_utc_now()
        return BrokerOrder(
            client_order_id=request.client_order_id,
            broker_name=self.adapter_name,
            account_id=self.account_id,
            symbol=request.symbol.upper(),
            side=request.side,
            quantity=request.quantity,
            filled_quantity=0,
            remaining_quantity=request.quantity,
            limit_price=request.limit_price,
            avg_fill_price=None,
            exchange_code=self._resolve_exchange_code(
                symbol=request.symbol,
                explicit_exchange_code=request.exchange_code,
            ),
            status=status,
            intent=request.intent,
            strategy_bucket=request.strategy_bucket,
            market_phase=request.market_phase,
            order_type=request.order_type,
            broker_order_id=broker_order_id,
            request_payload=payload,
            response_payload=response_payload,
            notes=[str(response_payload.get("msg1") or "submitted")],
            submitted_at=request.created_at,
            updated_at=now,
        )

    def _order_from_open_row(self, row: dict[str, Any]) -> BrokerOrder:
        quantity = _coerce_int(_first_value(row, "ord_qty", "ft_ord_qty", "quantity")) or 0
        filled_quantity = _coerce_int(_first_value(row, "tot_ccld_qty", "ccld_qty", "filled_qty")) or 0
        remaining_quantity = (
            _coerce_int(_first_value(row, "nccs_qty", "rmn_qty", "remaining_qty"))
            or max(quantity - filled_quantity, 0)
        )
        broker_order_id = str(_first_value(row, "odno", "ODNO", "ord_no") or "")
        now = broker_utc_now()
        return BrokerOrder(
            client_order_id=str(_first_value(row, "cl_ord_id", "client_order_id") or f"kis:{broker_order_id}"),
            broker_name=self.adapter_name,
            account_id=self.account_id,
            symbol=str(_first_value(row, "pdno", "ovrs_pdno", "symbol") or "").upper(),
            side=self._normalize_side(_first_value(row, "sll_buy_dvsn_cd", "sll_buy_dvsn", "side")),
            quantity=quantity,
            filled_quantity=filled_quantity,
            remaining_quantity=remaining_quantity,
            limit_price=_coerce_float(_first_value(row, "ft_ord_unpr3", "ovrs_ord_unpr", "limit_price")),
            avg_fill_price=_coerce_float(_first_value(row, "avg_ccld_unpr3", "avg_fill_price")),
            exchange_code=str(_first_value(row, "ovrs_excg_cd", "exchange_code") or ""),
            status=self._normalize_order_status(_first_value(row, "ord_stt", "ord_stt_name", "status"), remaining_quantity),
            intent=str(_first_value(row, "intent") or ""),
            strategy_bucket=str(_first_value(row, "strategy_bucket") or ""),
            market_phase=str(_first_value(row, "market_phase") or ""),
            broker_order_id=broker_order_id or None,
            request_payload={},
            response_payload=row,
            notes=[],
            submitted_at=now,
            updated_at=now,
        )

    def _order_from_fill_row(self, row: dict[str, Any]) -> BrokerOrder:
        quantity = _coerce_int(_first_value(row, "ft_ord_qty", "ord_qty", "quantity")) or 0
        filled_quantity = _coerce_int(_first_value(row, "ft_ccld_qty", "tot_ccld_qty", "filled_qty")) or quantity
        broker_order_id = str(_first_value(row, "odno", "ODNO", "ord_no") or "")
        now = broker_utc_now()
        return BrokerOrder(
            client_order_id=str(_first_value(row, "cl_ord_id", "client_order_id") or f"kis:{broker_order_id}"),
            broker_name=self.adapter_name,
            account_id=self.account_id,
            symbol=str(_first_value(row, "pdno", "ovrs_pdno", "symbol") or "").upper(),
            side=self._normalize_side(_first_value(row, "sll_buy_dvsn_cd", "sll_buy_dvsn", "side")),
            quantity=quantity,
            filled_quantity=filled_quantity,
            remaining_quantity=max(quantity - filled_quantity, 0),
            limit_price=_coerce_float(_first_value(row, "ft_ord_unpr3", "ovrs_ord_unpr", "limit_price")),
            avg_fill_price=_coerce_float(_first_value(row, "ft_ccld_unpr3", "avg_ccld_unpr3", "avg_fill_price")),
            exchange_code=str(_first_value(row, "ovrs_excg_cd", "exchange_code") or ""),
            status=self._normalize_fill_status(_first_value(row, "ccld_nccs_dvsn", "status"), filled_quantity, quantity),
            intent=str(_first_value(row, "intent") or ""),
            strategy_bucket=str(_first_value(row, "strategy_bucket") or ""),
            market_phase=str(_first_value(row, "market_phase") or ""),
            broker_order_id=broker_order_id or None,
            request_payload={},
            response_payload=row,
            notes=[],
            submitted_at=now,
            updated_at=now,
        )

    def _position_from_balance_row(self, row: dict[str, Any]) -> BrokerPosition:
        return BrokerPosition(
            broker_name=self.adapter_name,
            account_id=self.account_id,
            symbol=str(_first_value(row, "ovrs_pdno", "pdno", "symbol") or "").upper(),
            exchange_code=str(_first_value(row, "ovrs_excg_cd", "exchange_code") or ""),
            quantity=_coerce_int(_first_value(row, "ovrs_cblc_qty", "cblc_qty", "quantity")) or 0,
            available_quantity=_coerce_int(_first_value(row, "ord_psbl_qty", "sell_psbl_qty", "available_quantity")),
            average_price=_coerce_float(_first_value(row, "pchs_avg_pric", "avg_unpr", "average_price")),
            market_price=_coerce_float(_first_value(row, "now_pric2", "last", "market_price")),
            market_value=_coerce_float(_first_value(row, "frcr_evlu_amt2", "evlu_amt", "market_value")),
            currency=str(_first_value(row, "tr_crcy_cd", "currency") or self.settings.kis_mock_balance_currency_code),
            raw_payload=row,
            updated_at=broker_utc_now(),
        )

    def _extract_broker_order_id(self, payload: dict[str, Any]) -> str | None:
        output = payload.get("output")
        if isinstance(output, list) and output:
            output = output[0]
        if not isinstance(output, dict):
            output = payload
        candidate = _first_value(output, "ODNO", "odno", "ord_no", "order_no", "KRX_FWDG_ORD_ORGNO")
        return str(candidate) if candidate not in {None, ""} else None

    def _resolve_exchange_code(
        self,
        *,
        symbol: str,
        explicit_exchange_code: str | None,
    ) -> str:
        if explicit_exchange_code:
            return self._normalize_exchange_code(explicit_exchange_code)
        exchange = self._exchange_map().get(symbol.upper())
        if exchange is None:
            return self.settings.kis_mock_inquiry_exchange_code
        return exchange

    def _exchange_map(self) -> dict[str, str]:
        if self._exchange_by_symbol is not None:
            return self._exchange_by_symbol
        provider = KisMasterListingProvider(
            exchange_paths={
                "NAS": self.settings.kis_nasdaq_master_path,
                "NYS": self.settings.kis_nyse_master_path,
                "AMS": self.settings.kis_amex_master_path,
            }
        )
        records = provider.fetch()
        self._exchange_by_symbol = {
            record.symbol.upper(): _MASTER_TO_ORDER_EXCHANGE.get(record.exchange.upper(), record.exchange.upper())
            for record in records
            if record.exchange
        }
        return self._exchange_by_symbol

    def _normalize_exchange_code(self, value: str) -> str:
        text = str(value or "").strip().upper()
        return _MASTER_TO_ORDER_EXCHANGE.get(text, text)

    def _normalize_side(self, value: Any) -> str:
        text = str(value or "").strip().upper()
        if text in {"02", "B", "BUY", "매수"}:
            return "buy"
        if text in {"01", "S", "SELL", "매도"}:
            return "sell"
        return text.lower()

    def _normalize_order_status(self, value: Any, remaining_quantity: int) -> str:
        text = str(value or "").strip().upper()
        if text in {"REJECTED", "거부"}:
            return "REJECTED"
        if text in {"CANCELED", "취소"}:
            return "CANCELED"
        if remaining_quantity <= 0:
            return "FILLED"
        if text in {"PARTIAL", "부분체결"}:
            return "PARTIAL"
        return "WORKING"

    def _normalize_fill_status(self, value: Any, filled_quantity: int, quantity: int) -> str:
        text = str(value or "").strip().upper()
        if text in {"01", "FILLED", "체결"} or filled_quantity >= quantity > 0:
            return "FILLED"
        if text in {"02", "PARTIAL", "부분체결"}:
            return "PARTIAL"
        if text in {"03", "WORKING", "미체결"}:
            return "WORKING"
        return "FILLED" if filled_quantity else "WORKING"
