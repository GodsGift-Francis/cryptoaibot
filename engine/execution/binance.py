"""Binance Spot executor: order placement / query / cancel via the REST client.

Critical rule: an accepted REST response is an ACKNOWLEDGEMENT, not a fill.
Status and fills in the response are passed to the OrderService, which applies
them through the same monotonic path used for WebSocket and reconciliation data.
"""
from __future__ import annotations

from decimal import Decimal

from engine.domain.enums import OrderType, SubmitOutcomeKind
from engine.domain.events import BalanceSnapshot, ExchangeFill, ExchangeOrderReport, SubmitOutcome
from engine.domain.models import Order, SymbolRules
from engine.exchanges.binance import normalizer as nz
from engine.exchanges.binance.rest_client import BinanceRestClient, OrderNotFound, error_code, is_definitive_rejection

from .base import Executor


def _fmt(value: Decimal) -> str:
    return format(Decimal(value).normalize(), "f")


class BinanceExecutor(Executor):
    exchange_name = "binance"

    def __init__(self, rest: BinanceRestClient, account_label: str, symbols: list[str], classify=is_definitive_rejection):
        self.rest = rest
        self.account = account_label
        self.symbols = nz.SymbolMap(symbols)
        self._rules: dict[str, SymbolRules] = {}
        self._is_definitive = classify

    # ------------------------------------------------------------------ metadata
    def symbol_rules(self, symbol: str) -> SymbolRules:
        if symbol not in self._rules:
            info = self.rest.exchange_info(nz.SymbolMap.exchange(symbol))
            f = {x["filterType"]: x for x in info.get("filters", [])}
            notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
            self._rules[symbol] = SymbolRules(
                symbol=symbol, exchange_symbol=info["symbol"],
                base_asset=info["baseAsset"], quote_asset=info["quoteAsset"],
                step_size=Decimal(f.get("LOT_SIZE", {}).get("stepSize", "0.00000001")),
                min_qty=Decimal(f.get("LOT_SIZE", {}).get("minQty", "0")),
                tick_size=Decimal(f.get("PRICE_FILTER", {}).get("tickSize", "0.00000001")),
                min_notional=Decimal(notional.get("minNotional", "0")),
            )
        return self._rules[symbol]

    def server_time_ms(self) -> int:
        return self.rest.server_time_ms()

    # ------------------------------------------------------------------ orders
    def submit(self, order: Order, rules: SymbolRules) -> SubmitOutcome:
        params = {
            "symbol": rules.exchange_symbol,
            "side": order.side,
            "type": order.order_type,
            "quantity": _fmt(rules.round_qty(order.requested_quantity)),
            "newClientOrderId": order.client_order_id,
            "newOrderRespType": "FULL",
        }
        if order.order_type == OrderType.STOP_LOSS_LIMIT.value:
            params.update({"timeInForce": "GTC", "price": _fmt(rules.round_price(order.limit_price)),
                           "stopPrice": _fmt(rules.round_price(order.stop_price))})
        elif order.order_type == OrderType.LIMIT.value:
            params.update({"timeInForce": "GTC", "price": _fmt(rules.round_price(order.limit_price))})
        try:
            raw = self.rest.new_order(params)
        except Exception as exc:  # noqa: BLE001 - classified below
            kind = SubmitOutcomeKind.REJECTED if self._is_definitive(exc) else SubmitOutcomeKind.AMBIGUOUS
            return SubmitOutcome(kind, error=type(exc).__name__ + ": " + str(exc)[:300], error_code=error_code(exc))
        report = nz.rest_order_report(raw, self.symbols)
        fills = tuple(nz.rest_response_fills(raw, self.account, self.symbols))
        return SubmitOutcome(SubmitOutcomeKind.ACKNOWLEDGED, report=report, fills=fills)

    def query_order(self, order: Order) -> ExchangeOrderReport | None:
        try:
            raw = self.rest.get_order(nz.SymbolMap.exchange(order.symbol), orig_client_order_id=order.client_order_id)
        except OrderNotFound:
            return None
        return nz.rest_order_report(raw, self.symbols)

    def query_order_by_exchange_id(self, symbol: str, exchange_order_id: str) -> ExchangeOrderReport | None:
        try:
            raw = self.rest.get_order(nz.SymbolMap.exchange(symbol), order_id=exchange_order_id)
        except OrderNotFound:
            return None
        return nz.rest_order_report(raw, self.symbols)

    def query_fills(self, order: Order) -> list[ExchangeFill]:
        if not order.exchange_order_id:
            return []
        rows = self.rest.my_trades(nz.SymbolMap.exchange(order.symbol), order_id=order.exchange_order_id)
        return [nz.rest_trade_fill(r, self.account, self.symbols, order.client_order_id) for r in rows]

    def cancel(self, order: Order) -> SubmitOutcome:
        try:
            raw = self.rest.cancel_order(nz.SymbolMap.exchange(order.symbol), order.client_order_id)
        except OrderNotFound:
            return SubmitOutcome(SubmitOutcomeKind.REJECTED, error="order not found on exchange")
        except Exception as exc:  # noqa: BLE001
            kind = SubmitOutcomeKind.REJECTED if self._is_definitive(exc) else SubmitOutcomeKind.AMBIGUOUS
            return SubmitOutcome(kind, error=str(exc)[:300], error_code=error_code(exc))
        return SubmitOutcome(SubmitOutcomeKind.ACKNOWLEDGED, report=nz.rest_order_report(raw, self.symbols))

    def open_orders(self, symbol: str) -> list[ExchangeOrderReport]:
        return [nz.rest_order_report(r, self.symbols) for r in self.rest.open_orders(nz.SymbolMap.exchange(symbol))]

    def recent_fills(self, symbol: str, since_ms: int, page_limit: int = 1000, max_pages: int = 50) -> list[ExchangeFill]:
        """All account trades since `since_ms`, oldest first, paginated by trade id (never silently truncated)."""
        exch = nz.SymbolMap.exchange(symbol)
        rows = self.rest.my_trades(exch, start_time_ms=since_ms, limit=page_limit)
        out = list(rows)
        pages = 1
        while len(rows) >= page_limit:
            if pages >= max_pages:
                raise RuntimeError(f"recent_fills exceeded {max_pages} pages; widen reconciliation cadence")
            rows = self.rest.my_trades(exch, from_id=int(rows[-1]["id"]) + 1, limit=page_limit)
            out.extend(rows)
            pages += 1
        return [nz.rest_trade_fill(r, self.account, self.symbols) for r in out]

    def balances(self) -> BalanceSnapshot:
        return nz.rest_balances(self.rest.account(), self.server_time_ms())
