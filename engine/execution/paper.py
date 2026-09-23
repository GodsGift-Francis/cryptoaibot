"""PAPER executor: a deterministic simulated exchange.

It goes through exactly the same OrderService / PositionService path as a real
exchange (intent -> order -> acknowledgement -> fill -> position), so PAPER
mode exercises the production lifecycle. The simulated order book is persisted
in the engine store, so restarts and reconciliation behave like the real thing.

Fill model (unchanged from V1): market orders fill fully and immediately at the
order's reference price (last close for strategy orders, the stop price for
stop exits). Fees default to 0 as in V1 (PAPER_FEE_RATE).
"""
from __future__ import annotations

from decimal import Decimal

from engine.domain.enums import OrderStatus, OrderType, SubmitOutcomeKind
from engine.domain.events import BalanceSnapshot, ExchangeFill, ExchangeOrderReport, SubmitOutcome
from engine.domain.models import Order, SymbolRules

from .base import Executor


class PaperExecutor(Executor):
    exchange_name = "paper"

    def __init__(self, store, fee_rate: float, cash_fn, clock):
        self.store = store
        self.fee_rate = Decimal(str(fee_rate))
        self.cash_fn = cash_fn          # (symbol) -> (quote_asset, Decimal cash, base_asset, Decimal base_qty)
        self.clock = clock

    def symbol_rules(self, symbol: str) -> SymbolRules:
        base, quote = symbol.split("/")
        return SymbolRules(symbol=symbol, exchange_symbol=symbol.replace("/", ""), base_asset=base, quote_asset=quote)

    def server_time_ms(self) -> int:
        return self.clock.now_ms()

    def _key(self, coid: str) -> str:
        return f"paper_order:{coid}"

    def submit(self, order: Order, rules: SymbolRules) -> SubmitOutcome:
        existing = self.store.kv_get(self._key(order.client_order_id))
        if existing:   # exchange-side duplicate protection, like Binance for open/known IDs
            return SubmitOutcome(SubmitOutcomeKind.REJECTED, error="Duplicate order sent.", error_code=-2010)
        if order.order_type != OrderType.MARKET.value:
            return SubmitOutcome(SubmitOutcomeKind.REJECTED, error="PAPER supports MARKET orders only")
        price = order.reference_price
        if price is None or price <= 0:
            return SubmitOutcome(SubmitOutcomeKind.REJECTED, error="PAPER order needs a reference price")
        qty = rules.round_qty(order.requested_quantity)
        quote = qty * price
        now_ms = self.clock.now_ms()
        record = {
            "client_order_id": order.client_order_id, "exchange_order_id": f"P{order.id}",
            "symbol": order.symbol, "side": order.side, "type": order.order_type, "status": "FILLED",
            "orig_qty": str(qty), "executed_qty": str(qty), "cum_quote": str(quote), "time": now_ms,
            "trade": {"id": f"PT{order.id}", "qty": str(qty), "price": str(price), "quote": str(quote),
                      "commission": str(quote * self.fee_rate), "commission_asset": rules.quote_asset},
        }
        self.store.kv_set(self._key(order.client_order_id), record)
        return SubmitOutcome(SubmitOutcomeKind.ACKNOWLEDGED, report=self._report(record), fills=tuple(self._fills(record)))

    def _report(self, r: dict) -> ExchangeOrderReport:
        return ExchangeOrderReport(
            client_order_id=r["client_order_id"], exchange_order_id=r["exchange_order_id"], symbol=r["symbol"],
            side=r["side"], order_type=r["type"], status=OrderStatus(r["status"]), orig_qty=Decimal(r["orig_qty"]),
            executed_qty=Decimal(r["executed_qty"]), cum_quote_qty=Decimal(r["cum_quote"]), update_time_ms=r["time"],
        )

    def _fills(self, r: dict) -> list[ExchangeFill]:
        t = r["trade"]
        return [ExchangeFill(
            exchange_trade_id=t["id"], exchange_order_id=r["exchange_order_id"], client_order_id=r["client_order_id"],
            symbol=r["symbol"], side=r["side"], quantity=Decimal(t["qty"]), price=Decimal(t["price"]),
            quote_quantity=Decimal(t["quote"]), commission=Decimal(t["commission"]),
            commission_asset=t["commission_asset"], execution_time_ms=r["time"],
            event_key=f"paper:{r['symbol']}:{r['exchange_order_id']}:{t['id']}:TRADE",
        )]

    def query_order(self, order: Order) -> ExchangeOrderReport | None:
        r = self.store.kv_get(self._key(order.client_order_id))
        return self._report(r) if r else None

    def query_order_by_exchange_id(self, symbol, exchange_order_id):
        return None

    def query_fills(self, order: Order) -> list[ExchangeFill]:
        r = self.store.kv_get(self._key(order.client_order_id))
        return self._fills(r) if r else []

    def cancel(self, order: Order) -> SubmitOutcome:
        return SubmitOutcome(SubmitOutcomeKind.REJECTED, error="PAPER orders fill immediately; nothing to cancel")

    def open_orders(self, symbol: str) -> list[ExchangeOrderReport]:
        return []

    def recent_fills(self, symbol: str, since_ms: int) -> list[ExchangeFill]:
        return []

    def balances(self) -> BalanceSnapshot:
        quote_asset, cash, base_asset, base_qty = self.cash_fn()
        return BalanceSnapshot({quote_asset: {"free": cash, "locked": Decimal("0")},
                                base_asset: {"free": base_qty, "locked": Decimal("0")}}, self.clock.now_ms())
