"""Converts raw Binance Spot payloads (REST + WebSocket user data) into the
engine's normalized types. Nothing outside engine/exchanges/binance/ should
know Binance field names.

Idempotency keys (shared by REST and WebSocket so the same execution arriving
through both paths deduplicates):
    fill:        binance:{account}:{symbol}:{orderId}:{tradeId}:TRADE
    order event: binance:{account}:{symbol}:{orderId}:{executionId}:{execType}
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from engine.domain.enums import OrderStatus
from engine.domain.events import BalanceSnapshot, ExchangeFill, ExchangeOrderReport

_STATUS = {
    "NEW": OrderStatus.NEW,
    "PENDING_NEW": OrderStatus.NEW,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELED,
    "PENDING_CANCEL": OrderStatus.NEW,          # documented as unused; never treat as terminal
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.EXPIRED,
    "EXPIRED_IN_MATCH": OrderStatus.EXPIRED,    # STP expiry
}


def map_status(raw_status: str) -> OrderStatus:
    try:
        return _STATUS[raw_status]
    except KeyError as exc:
        raise UnknownExchangeStatus(raw_status) from exc


class UnknownExchangeStatus(ValueError):
    pass


def _d(value, default="0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    return Decimal(str(value))


def fill_event_key(account: str, exchange_symbol: str, order_id, trade_id) -> str:
    return f"binance:{account}:{exchange_symbol}:{order_id}:{trade_id}:TRADE"


def order_event_key(account: str, exchange_symbol: str, order_id, execution_id, exec_type: str) -> str:
    return f"binance:{account}:{exchange_symbol}:{order_id}:{execution_id}:{exec_type}"


class SymbolMap:
    """Maps Binance symbols (BTCUSDT) <-> internal symbols (BTC/USDT)."""

    def __init__(self, internal_symbols: list[str]):
        self._to_internal = {s.replace("/", ""): s for s in internal_symbols}

    def internal(self, exchange_symbol: str) -> str:
        return self._to_internal.get(exchange_symbol, exchange_symbol)

    @staticmethod
    def exchange(internal_symbol: str) -> str:
        return internal_symbol.replace("/", "")


# --------------------------------------------------------------------- REST
def rest_order_report(raw: dict, symbols: SymbolMap) -> ExchangeOrderReport:
    """Works for POST /api/v3/order (ACK/RESULT/FULL), GET /api/v3/order and DELETE responses."""
    status = map_status(raw["status"])
    return ExchangeOrderReport(
        client_order_id=raw.get("origClientOrderId") if raw.get("status") == "CANCELED" and raw.get("origClientOrderId") else raw["clientOrderId"],
        exchange_order_id=str(raw["orderId"]) if raw.get("orderId") is not None else None,
        symbol=symbols.internal(raw["symbol"]),
        side=raw.get("side", ""),
        order_type=raw.get("type", ""),
        status=status,
        orig_qty=_d(raw.get("origQty")),
        executed_qty=_d(raw.get("executedQty")),
        cum_quote_qty=_d(raw.get("cummulativeQuoteQty")),   # Binance's spelling
        price=_d(raw.get("price")) if raw.get("price") not in (None, "") else None,
        stop_price=_d(raw.get("stopPrice")) if raw.get("stopPrice") not in (None, "", "0.00000000") else None,
        update_time_ms=raw.get("updateTime") or raw.get("transactTime") or raw.get("workingTime"),
        raw={k: v for k, v in raw.items() if k != "fills"},
    )


def rest_response_fills(raw: dict, account: str, symbols: SymbolMap) -> list[ExchangeFill]:
    """FULL response `fills` array from order placement."""
    out = []
    for f in raw.get("fills") or []:
        qty, price = _d(f["qty"]), _d(f["price"])
        out.append(ExchangeFill(
            exchange_trade_id=str(f["tradeId"]),
            exchange_order_id=str(raw["orderId"]),
            client_order_id=raw.get("clientOrderId"),
            symbol=symbols.internal(raw["symbol"]),
            side=raw.get("side", ""),
            quantity=qty, price=price, quote_quantity=qty * price,
            commission=_d(f.get("commission")), commission_asset=f.get("commissionAsset"),
            execution_time_ms=int(raw.get("transactTime") or 0),
            event_key=fill_event_key(account, raw["symbol"], raw["orderId"], f["tradeId"]),
        ))
    return out


def rest_trade_fill(raw: dict, account: str, symbols: SymbolMap, client_order_id: str | None = None) -> ExchangeFill:
    """GET /api/v3/myTrades row."""
    return ExchangeFill(
        exchange_trade_id=str(raw["id"]),
        exchange_order_id=str(raw["orderId"]),
        client_order_id=client_order_id,
        symbol=symbols.internal(raw["symbol"]),
        side="BUY" if raw.get("isBuyer") else "SELL",
        quantity=_d(raw["qty"]), price=_d(raw["price"]), quote_quantity=_d(raw.get("quoteQty")),
        commission=_d(raw.get("commission")), commission_asset=raw.get("commissionAsset"),
        execution_time_ms=int(raw["time"]),
        event_key=fill_event_key(account, raw["symbol"], raw["orderId"], raw["id"]),
        is_maker=raw.get("isMaker"),
    )


def rest_balances(raw_account: dict, as_of_ms: int) -> BalanceSnapshot:
    balances = {b["asset"]: {"free": _d(b["free"]), "locked": _d(b["locked"])} for b in raw_account.get("balances", [])}
    return BalanceSnapshot(balances=balances, as_of_ms=int(raw_account.get("updateTime") or as_of_ms))


# --------------------------------------------------------------------- WebSocket user data stream
@dataclass(frozen=True)
class NormalizedStreamEvent:
    kind: str                       # ORDER | BALANCE | TERMINATED | IGNORED
    event_key: str
    event_type: str
    report: ExchangeOrderReport | None = None
    fill: ExchangeFill | None = None
    balances: BalanceSnapshot | None = None


def unwrap(message: dict) -> tuple[int | None, dict | None]:
    """Current WS API frames are {"subscriptionId": n, "event": {...}}."""
    if "event" in message and isinstance(message["event"], dict):
        return message.get("subscriptionId"), message["event"]
    if "e" in message:                     # tolerate unwrapped legacy shape in fixtures
        return None, message
    return None, None


def stream_event(event: dict, account: str, symbols: SymbolMap) -> NormalizedStreamEvent:
    etype = event.get("e")
    if etype == "executionReport":
        exch_symbol = event["s"]
        # On cancellation, `c` is the cancel request's ID and `C` holds the original order's ID.
        client_id = event.get("C") or event["c"]
        report = ExchangeOrderReport(
            client_order_id=client_id,
            exchange_order_id=str(event["i"]),
            symbol=symbols.internal(exch_symbol),
            side=event.get("S", ""),
            order_type=event.get("o", ""),
            status=map_status(event["X"]),
            orig_qty=_d(event.get("q")),
            executed_qty=_d(event.get("z")),
            cum_quote_qty=_d(event.get("Z")),
            price=_d(event.get("p")) if event.get("p") else None,
            stop_price=_d(event.get("P")) if event.get("P") not in (None, "", "0.00000000") else None,
            update_time_ms=event.get("T") or event.get("E"),
            reject_reason=None if event.get("r") in (None, "NONE") else event.get("r"),
            raw={k: event.get(k) for k in ("e", "E", "s", "c", "C", "S", "o", "x", "X", "r", "i", "z", "Z", "T", "I")},
        )
        fill = None
        if event.get("x") == "TRADE" and int(event.get("t", -1)) >= 0:
            qty, price = _d(event.get("l")), _d(event.get("L"))
            fill = ExchangeFill(
                exchange_trade_id=str(event["t"]),
                exchange_order_id=str(event["i"]),
                client_order_id=client_id,
                symbol=symbols.internal(exch_symbol),
                side=event.get("S", ""),
                quantity=qty, price=price,
                quote_quantity=_d(event.get("Y")) if event.get("Y") is not None else qty * price,
                commission=_d(event.get("n")), commission_asset=event.get("N"),
                execution_time_ms=int(event.get("T") or event.get("E")),
                event_key=fill_event_key(account, exch_symbol, event["i"], event["t"]),
                is_maker=event.get("m"),
            )
        key = order_event_key(account, exch_symbol, event["i"], event.get("I", event.get("E")), event.get("x", ""))
        return NormalizedStreamEvent("ORDER", key, etype, report=report, fill=fill)
    if etype == "outboundAccountPosition":
        balances = {b["a"]: {"free": _d(b["f"]), "locked": _d(b["l"])} for b in event.get("B", [])}
        key = f"binance:{account}:account:{event.get('u')}:{event.get('E')}"
        return NormalizedStreamEvent("BALANCE", key, etype, balances=BalanceSnapshot(balances, int(event.get("u") or event.get("E"))))
    if etype == "eventStreamTerminated":
        return NormalizedStreamEvent("TERMINATED", f"binance:{account}:terminated:{event.get('E')}", etype)
    return NormalizedStreamEvent("IGNORED", f"binance:{account}:{etype}:{event.get('E')}", str(etype))
