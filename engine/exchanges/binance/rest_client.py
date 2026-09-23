"""Binance Spot REST client.

Uses CCXT's *raw* (implicit) Binance endpoints so we get Binance's exact
fields (status, executedQty, cummulativeQuoteQty, tradeId ...) while CCXT
handles request signing (it signs the percent-encoded query string, which is
what Binance requires since the 2026-01-15 REST change), time sync and rate
limiting.

This is the ONLY module that talks to Binance REST for account/order data.
"""
from __future__ import annotations

from typing import Protocol

try:
    import ccxt
except ImportError:  # pragma: no cover - exercised only when ccxt is missing
    ccxt = None


class BinanceRestClient(Protocol):
    def new_order(self, params: dict) -> dict: ...
    def get_order(self, exchange_symbol: str, orig_client_order_id: str | None = None, order_id: str | None = None) -> dict: ...
    def cancel_order(self, exchange_symbol: str, orig_client_order_id: str) -> dict: ...
    def open_orders(self, exchange_symbol: str) -> list[dict]: ...
    def my_trades(self, exchange_symbol: str, order_id: str | None = None, start_time_ms: int | None = None, from_id: int | None = None, limit: int = 1000) -> list[dict]: ...
    def account(self) -> dict: ...
    def exchange_info(self, exchange_symbol: str) -> dict: ...
    def server_time_ms(self) -> int: ...


class OrderNotFound(Exception):
    """Binance -2013 / ccxt.OrderNotFound: the exchange has no such order."""


class CcxtBinanceRestClient:
    def __init__(self, api_key: str, api_secret: str, testnet: bool, timeout_seconds: int = 10):
        if ccxt is None:
            raise RuntimeError("ccxt is not installed. Run: pip install -r requirements.txt")
        self._ex = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "timeout": timeout_seconds * 1000,
            "options": {"adjustForTimeDifference": True, "defaultType": "spot"},
        })
        if testnet:
            self._ex.set_sandbox_mode(True)

    # CCXT maps Binance -2013 to OrderNotFound; convert to our own type so the
    # executor does not depend on CCXT exception classes for that case.
    def _call(self, fn, params):
        try:
            return fn(params)
        except Exception as exc:  # noqa: BLE001 - classified by the executor
            if ccxt is not None and isinstance(exc, ccxt.OrderNotFound):
                raise OrderNotFound(str(exc)) from exc
            raise

    def new_order(self, params: dict) -> dict:
        return self._call(self._ex.private_post_order, params)

    def get_order(self, exchange_symbol, orig_client_order_id=None, order_id=None) -> dict:
        params = {"symbol": exchange_symbol}
        if orig_client_order_id:
            params["origClientOrderId"] = orig_client_order_id
        else:
            params["orderId"] = order_id
        return self._call(self._ex.private_get_order, params)

    def cancel_order(self, exchange_symbol, orig_client_order_id) -> dict:
        return self._call(self._ex.private_delete_order, {"symbol": exchange_symbol, "origClientOrderId": orig_client_order_id})

    def open_orders(self, exchange_symbol) -> list[dict]:
        return self._call(self._ex.private_get_openorders, {"symbol": exchange_symbol})

    def my_trades(self, exchange_symbol, order_id=None, start_time_ms=None, from_id=None, limit=1000) -> list[dict]:
        # Supported Binance combinations only: symbol+orderId, symbol+startTime, symbol+fromId.
        params = {"symbol": exchange_symbol, "limit": limit}
        if order_id is not None:
            params["orderId"] = order_id
        elif from_id is not None:
            params["fromId"] = from_id
        elif start_time_ms is not None:
            params["startTime"] = start_time_ms
        return self._call(self._ex.private_get_mytrades, params)

    def account(self) -> dict:
        return self._call(self._ex.private_get_account, {"omitZeroBalances": "true"})

    def exchange_info(self, exchange_symbol) -> dict:
        info = self._call(self._ex.public_get_exchangeinfo, {"symbol": exchange_symbol})
        return info["symbols"][0]

    def server_time_ms(self) -> int:
        return int(self._call(self._ex.public_get_time, {})["serverTime"])


def is_definitive_rejection(exc: Exception) -> bool:
    """True only when the exchange has certainly NOT accepted the order.

    Anything we cannot prove was refused (timeouts, network errors, 5xx,
    -1007 backend timeout, rate limiting, unknown errors) is AMBIGUOUS and must
    be resolved by querying the exchange with the clientOrderId.
    """
    if ccxt is None:
        return False
    definitive = (ccxt.InsufficientFunds, ccxt.InvalidOrder, ccxt.BadSymbol, ccxt.AuthenticationError,
                  ccxt.PermissionDenied, ccxt.BadRequest, ccxt.AccountSuspended)
    ambiguous = (ccxt.NetworkError,)      # RequestTimeout, ExchangeNotAvailable, DDoSProtection, ...
    if isinstance(exc, ambiguous):
        return False
    return isinstance(exc, definitive)


def error_code(exc: Exception) -> int | None:
    import re
    m = re.search(r'"code"\s*:\s*(-?\d+)', str(exc))
    return int(m.group(1)) if m else None
