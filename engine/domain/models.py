"""Core domain entities and the order state machine.

Rules enforced here (and only here):
  * terminal orders never move back to a non-terminal state;
  * status only moves forward in rank (older/out-of-order events are ignored);
  * executed quantity and cumulative quote quantity never decrease.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal

from .enums import OrderPurpose, OrderStatus, ReconciliationStatus, TERMINAL_STATUSES
from .events import ExchangeOrderReport

ZERO = Decimal("0")

# Allowed forward transitions. Same-state updates (e.g. another partial fill)
# are handled separately as "refreshes".
ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.INTENT_CREATED: frozenset({OrderStatus.SUBMITTING, OrderStatus.REJECTED}),
    OrderStatus.SUBMITTING: frozenset({
        OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED,
        OrderStatus.REJECTED, OrderStatus.CANCELED, OrderStatus.EXPIRED,
    }),
    OrderStatus.NEW: frozenset({
        OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED,
    }),
    OrderStatus.PARTIALLY_FILLED: frozenset({
        OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED,
    }),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}

_RANK = {
    OrderStatus.INTENT_CREATED: 0,
    OrderStatus.SUBMITTING: 1,
    OrderStatus.NEW: 2,
    OrderStatus.PARTIALLY_FILLED: 3,
    OrderStatus.FILLED: 4,
    OrderStatus.CANCELED: 4,
    OrderStatus.REJECTED: 4,
    OrderStatus.EXPIRED: 4,
}


class IllegalTransition(Exception):
    pass


def can_transition(current: OrderStatus, new: OrderStatus) -> bool:
    return new in ALLOWED_TRANSITIONS[current]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_client_order_id(prefix: str, client_intent_id: str) -> str:
    """Deterministic Binance-compatible clientOrderId (<=36 chars, [A-Za-z0-9_-]).

    The same intent always yields the same ID, so a restart or retry can never
    produce a second, differently-identified exchange order for one intent.
    """
    digest = hashlib.sha256(client_intent_id.encode("utf-8")).hexdigest()
    safe_prefix = "".join(ch for ch in prefix if ch.isalnum())[:8] or "cb"
    return f"{safe_prefix}-{digest[: 35 - len(safe_prefix)]}"


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


@dataclass(frozen=True)
class SymbolRules:
    symbol: str
    exchange_symbol: str
    base_asset: str
    quote_asset: str
    step_size: Decimal = Decimal("0.00000001")
    min_qty: Decimal = Decimal("0")
    tick_size: Decimal = Decimal("0.00000001")
    min_notional: Decimal = Decimal("0")

    def round_qty(self, qty: Decimal) -> Decimal:
        return floor_to_step(Decimal(qty), self.step_size)

    def round_price(self, price: Decimal) -> Decimal:
        return floor_to_step(Decimal(price), self.tick_size)

    def is_tradeable(self, qty: Decimal, price: Decimal) -> bool:
        if qty <= 0 or qty < self.min_qty:
            return False
        return qty * Decimal(price) >= self.min_notional


@dataclass
class OrderIntent:
    client_intent_id: str
    bot_instance_id: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    purpose: OrderPurpose
    strategy_name: str
    strategy_version: str
    signal_id: str | None = None
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    reference_price: Decimal | None = None   # mark price used for sizing / paper fills
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Order:
    bot_instance_id: str
    exchange: str
    symbol: str
    side: str
    order_type: str
    purpose: OrderPurpose
    client_order_id: str
    client_intent_id: str
    requested_quantity: Decimal
    status: OrderStatus = OrderStatus.INTENT_CREATED
    id: int | None = None
    exchange_order_id: str | None = None
    executed_quantity: Decimal = ZERO
    cumulative_quote_quantity: Decimal = ZERO
    average_fill_price: Decimal | None = None
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    reference_price: Decimal | None = None
    submission_ambiguous: bool = False
    reject_reason: str | None = None
    submitted_at: datetime | None = None
    acknowledged_at: datetime | None = None
    last_exchange_update_at: datetime | None = None
    last_exchange_update_ms: int | None = None
    terminal_at: datetime | None = None
    raw: dict = field(default_factory=dict)
    version: int = 0

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def mark_submitting(self, now: datetime) -> None:
        self._transition(OrderStatus.SUBMITTING, now)
        self.submitted_at = now

    def mark_rejected(self, reason: str, now: datetime) -> bool:
        if self.is_terminal:
            return False
        self._transition(OrderStatus.REJECTED, now)
        self.reject_reason = reason
        return True

    def apply_report(self, report: ExchangeOrderReport, now: datetime) -> bool:
        """Apply an exchange report monotonically. Returns True if anything changed."""
        changed = False
        if report.exchange_order_id and not self.exchange_order_id:
            self.exchange_order_id = report.exchange_order_id
            changed = True

        # Quantities are monotonic regardless of status ordering: a late
        # PARTIALLY_FILLED report can never reduce what a FILLED report said.
        if report.executed_qty > self.executed_quantity:
            self.executed_quantity = report.executed_qty
            changed = True
        if report.cum_quote_qty > self.cumulative_quote_quantity:
            self.cumulative_quote_quantity = report.cum_quote_qty
            changed = True
        if changed and self.executed_quantity > 0:
            self.average_fill_price = self.cumulative_quote_quantity / self.executed_quantity

        new_status = report.status
        if (self.status == OrderStatus.REJECTED and (self.reject_reason or "").startswith(NOT_FOUND_REASON)
                and new_status != OrderStatus.REJECTED):
            # We concluded the order never reached the exchange, but the exchange
            # now reports it. That is a correctness breach, never a silent update.
            raise IllegalTransition(f"{self.client_order_id}: locally REJECTED(not found) but exchange reports {new_status.value}")
        if new_status != self.status and _RANK[new_status] > _RANK[self.status] and can_transition(self.status, new_status):
            self._transition(new_status, now)
            if new_status == OrderStatus.REJECTED:
                self.reject_reason = report.reject_reason
            changed = True
        elif new_status != self.status and _RANK[new_status] >= _RANK[self.status] and self.is_terminal:
            # Conflicting terminal report (e.g. local REJECTED vs exchange FILLED).
            # Never silently overwrite; the reconciliation service flags this.
            raise IllegalTransition(f"{self.client_order_id}: terminal {self.status.value} vs exchange {new_status.value}")

        if report.update_time_ms and (self.last_exchange_update_ms is None or report.update_time_ms > self.last_exchange_update_ms):
            self.last_exchange_update_ms = report.update_time_ms
            self.last_exchange_update_at = datetime.fromtimestamp(report.update_time_ms / 1000, tz=timezone.utc)
            changed = True
        if self.acknowledged_at is None and self.status not in (OrderStatus.INTENT_CREATED, OrderStatus.SUBMITTING, OrderStatus.REJECTED):
            self.acknowledged_at = now
            changed = True
        if changed:
            self.raw = {k: v for k, v in (report.raw or {}).items() if k not in _UNSAFE_RAW_KEYS}
        return changed

    def _transition(self, new: OrderStatus, now: datetime) -> None:
        if new == self.status:
            return
        if not can_transition(self.status, new):
            raise IllegalTransition(f"{self.client_order_id}: {self.status.value} -> {new.value}")
        self.status = new
        if new in TERMINAL_STATUSES:
            self.terminal_at = now


NOT_FOUND_REASON = "NOT_FOUND_ON_EXCHANGE"
_UNSAFE_RAW_KEYS = frozenset({"apiKey", "signature", "secret"})


@dataclass
class Fill:
    order_id: int
    client_order_id: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    quote_quantity: Decimal
    commission: Decimal
    commission_asset: str | None
    execution_time: datetime
    event_key: str
    exchange_trade_id: str | None = None
    id: int | None = None


@dataclass
class Position:
    bot_instance_id: str
    symbol: str
    side: str = "LONG"
    quantity: Decimal = ZERO
    average_entry_price: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    unrealized_pnl: Decimal = ZERO
    last_mark_price: Decimal | None = None
    stop_price: Decimal | None = None
    fees_other: dict = field(default_factory=dict)   # commission in non base/quote assets
    opened_at: datetime | None = None
    updated_at: datetime | None = None

    def is_open(self, rules: SymbolRules | None = None) -> bool:
        if rules is None:
            return self.quantity > Decimal("1e-12")
        return self.quantity > 0 and self.quantity >= max(rules.min_qty, rules.step_size)

    def mark(self, price: Decimal) -> None:
        self.last_mark_price = Decimal(price)
        self.unrealized_pnl = (self.last_mark_price - self.average_entry_price) * self.quantity if self.quantity > 0 else ZERO


@dataclass
class ReconciliationState:
    bot_instance_id: str
    status: ReconciliationStatus
    reason: str = ""
    updated_at: datetime | None = None
    last_success_at: datetime | None = None
    ws_connected: bool = False
    last_ws_event_at: datetime | None = None
    last_ack_token: str | None = None

    def allows_entries(self, now: datetime, max_age_seconds: int) -> bool:
        return self.status == ReconciliationStatus.HEALTHY and self._fresh(now, max_age_seconds)

    def allows_exits(self, now: datetime, max_age_seconds: int) -> bool:
        # A WebSocket drop (DEGRADED) keeps REST available, so risk-reducing
        # exits stay permitted; unknown/unsafe states block everything.
        return self.status in (ReconciliationStatus.HEALTHY, ReconciliationStatus.DEGRADED) and self._fresh(now, max_age_seconds)

    def _fresh(self, now: datetime, max_age_seconds: int) -> bool:
        return self.updated_at is not None and (now - self.updated_at).total_seconds() <= max_age_seconds
