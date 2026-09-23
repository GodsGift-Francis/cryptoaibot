"""Execution boundary. Application services talk to exchanges ONLY through this interface.

An executor never decides what a response *means* for positions or P&L; it
returns normalized reports/fills and the OrderService applies them.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from engine.domain.events import BalanceSnapshot, ExchangeFill, ExchangeOrderReport, SubmitOutcome
from engine.domain.models import Order, SymbolRules


class Executor(ABC):
    exchange_name: str = "unknown"

    @abstractmethod
    def symbol_rules(self, symbol: str) -> SymbolRules: ...

    @abstractmethod
    def submit(self, order: Order, rules: SymbolRules) -> SubmitOutcome:
        """Send a new order. Must NOT retry internally: ambiguity is resolved by the caller via query_order."""

    @abstractmethod
    def query_order(self, order: Order) -> ExchangeOrderReport | None:
        """Authoritative status by clientOrderId. None means the exchange has no such order."""

    @abstractmethod
    def query_order_by_exchange_id(self, symbol: str, exchange_order_id: str) -> ExchangeOrderReport | None: ...

    @abstractmethod
    def query_fills(self, order: Order) -> list[ExchangeFill]: ...

    @abstractmethod
    def cancel(self, order: Order) -> SubmitOutcome: ...

    @abstractmethod
    def open_orders(self, symbol: str) -> list[ExchangeOrderReport]: ...

    @abstractmethod
    def recent_fills(self, symbol: str, since_ms: int) -> list[ExchangeFill]: ...

    @abstractmethod
    def balances(self) -> BalanceSnapshot: ...

    @abstractmethod
    def server_time_ms(self) -> int: ...
