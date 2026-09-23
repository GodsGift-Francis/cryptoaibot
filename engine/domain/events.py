"""Normalized exchange-facing value objects.

Everything that comes back from an exchange (REST response, REST query or
WebSocket event) is converted into these types by an exchange-specific
normalizer before any application service sees it.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from .enums import OrderStatus, SubmitOutcomeKind


@dataclass(frozen=True)
class ExchangeOrderReport:
    client_order_id: str
    symbol: str                       # internal symbol, e.g. "BTC/USDT"
    side: str
    order_type: str
    status: OrderStatus
    orig_qty: Decimal
    executed_qty: Decimal
    cum_quote_qty: Decimal
    exchange_order_id: str | None = None
    price: Decimal | None = None
    stop_price: Decimal | None = None
    update_time_ms: int | None = None     # exchange transaction/update time
    reject_reason: str | None = None
    raw: dict = field(default_factory=dict, compare=False)

    @property
    def average_price(self) -> Decimal | None:
        if self.executed_qty > 0:
            return self.cum_quote_qty / self.executed_qty
        return None


@dataclass(frozen=True)
class ExchangeFill:
    exchange_trade_id: str
    exchange_order_id: str | None
    client_order_id: str | None
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    quote_quantity: Decimal
    commission: Decimal
    commission_asset: str | None
    execution_time_ms: int
    event_key: str                   # globally unique idempotency key
    is_maker: bool | None = None


@dataclass(frozen=True)
class BalanceSnapshot:
    balances: dict                   # asset -> {"free": Decimal, "locked": Decimal}
    as_of_ms: int

    def total(self, asset: str) -> Decimal:
        b = self.balances.get(asset)
        if not b:
            return Decimal("0")
        return Decimal(b.get("free", 0)) + Decimal(b.get("locked", 0))


@dataclass(frozen=True)
class SubmitOutcome:
    kind: SubmitOutcomeKind
    report: ExchangeOrderReport | None = None
    fills: tuple = ()
    error: str | None = None
    error_code: int | None = None
