"""Shared deterministic fixtures. No network, no real exchange, no real money."""
from __future__ import annotations

import os
import sys
import types
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import ccxt
import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config as real_config  # noqa: E402
from engine import bootstrap  # noqa: E402
from engine.exchanges.binance.rest_client import OrderNotFound  # noqa: E402
from engine.execution.binance import BinanceExecutor  # noqa: E402
from engine.infrastructure.clock import FakeClock  # noqa: E402
from engine.infrastructure.store import Store  # noqa: E402
from engine.market_data.fetcher import MarketSnapshot  # noqa: E402
from engine.strategy.strategy_service import MultiFactorStrategy  # noqa: E402

TOKEN = "t" * 40


def make_cfg(**overrides):
    cfg = types.SimpleNamespace(**{k: getattr(real_config, k) for k in dir(real_config) if k.isupper()})
    cfg.CONTROL_PLANE_TOKEN = TOKEN
    cfg.TRADING_MODE = "PAPER"
    cfg.API_KEY = cfg.API_SECRET = ""
    cfg.BOT_INSTANCE_ID = "test-bot"
    cfg.SYMBOL = "BTC/USDT"
    cfg.PROTECTIVE_STOP_MODE = "software"
    cfg.BINANCE_WS_API_URL = "wss://ws-api.testnet.binance.vision/ws-api/v3"
    cfg.TELEGRAM_BOT_TOKEN = cfg.TELEGRAM_CHAT_ID = ""
    for k, v in overrides.items():
        setattr(cfg, k, v)
    cfg.validate_runtime = lambda c=None: real_config.validate_runtime(c or cfg)
    return cfg


# ---------------------------------------------------------------------------- control plane
class FakeControlPlane:
    def __init__(self):
        self.control = {"enabled": True, "emergency_stop": False, "reconciliation_ack_token": None}
        self.down = False
        self.posts: list[tuple[str, dict, str]] = []
        self.heartbeats: list[dict] = []

    def get_control(self):
        if self.down:
            raise ConnectionError("control plane down")
        return dict(self.control)

    def heartbeat(self, payload):
        if self.down:
            raise ConnectionError("control plane down")
        self.heartbeats.append(payload)
        return {"ok": True}

    def post(self, kind, payload, request_id):
        if self.down:
            raise ConnectionError("control plane down")
        self.posts.append((kind, payload, request_id))
        return {"ok": True}


class NullNotifier:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


# ---------------------------------------------------------------------------- market / strategy
@dataclass
class ScriptedSignal:
    action: str
    score: float = 0.0
    reasons: tuple = ()


class ScriptedStrategy:
    name, version = "scripted", "0"

    def __init__(self):
        self.next = "HOLD"

    def evaluate(self, ctx):
        return ScriptedSignal(self.next, 3.0 if self.next == "BUY" else (-3.0 if self.next == "SELL" else 0.0), ["scripted"])


class FakeMarket:
    """Scripted snapshots. `price` is the mark/signal price; `low` the recent low."""

    def __init__(self, clock, price="100", low=None):
        self.clock = clock
        self.price = Decimal(price)
        self.low = Decimal(low) if low else None
        self.valid = True
        self.candle_index = 0

    def snapshot(self, symbol, with_context=True):
        if not self.valid:
            return MarketSnapshot(False, "scripted invalid", fetched_at=self.clock.now())
        candle_open = self.clock.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        low = self.low if self.low is not None else self.price
        return MarketSnapshot(
            valid=True, reason="ok", candles=pd.DataFrame(), candle_ts=f"{candle_open.isoformat()}#{self.candle_index}",
            mark_price=self.price, signal_price=self.price,
            recent_candles=[(self.clock.now() + timedelta(minutes=30), low)], fetched_at=self.clock.now())


def synthetic_ohlcv(n=320, seed=7, end=None, tf_hours=1, drift=0.0005):
    rng = np.random.default_rng(seed)
    closes = 30000 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) * (1 + rng.uniform(0, 0.004, n))
    lows = np.minimum(opens, closes) * (1 - rng.uniform(0, 0.004, n))
    end = end or pd.Timestamp("2026-09-22T12:00:00Z")
    ts = pd.date_range(end=end, periods=n, freq=f"{tf_hours}h")
    return pd.DataFrame({"timestamp": ts.tz_localize(None) if ts.tz is not None else ts, "open": opens, "high": highs,
                         "low": lows, "close": closes, "volume": rng.uniform(1, 10, n)})


# ---------------------------------------------------------------------------- fake Binance (raw REST payloads)
class FakeBinanceRest:
    """Simulates Binance Spot REST at the raw-JSON level, raising real CCXT exception types.

    `next_submit` controls the next new_order: fill | partial | new | reject | timeout_accepted | timeout_lost | expire
    """

    def __init__(self, clock, base="BTC", quote="USDT", quote_balance="10000", base_balance="0"):
        self.clock = clock
        self.orders: dict[str, dict] = {}
        self.trades: list[dict] = []
        self.balances = {base: [Decimal(base_balance), Decimal(0)], quote: [Decimal(quote_balance), Decimal(0)]}
        self.base, self.quote = base, quote
        self.next_submit = "fill"
        self.fill_price = Decimal("100")
        self.partial_fraction = Decimal("0.4")
        self.commission_rate = Decimal("0.001")
        self._oid = 1000
        self._tid = 5000
        self.calls: list[str] = []

    # helpers ------------------------------------------------------------
    def _now(self):
        return self.clock.now_ms()

    def _order_by_id(self, oid):
        for o in self.orders.values():
            if str(o["orderId"]) == str(oid):
                return o
        return None

    def execute(self, coid: str, qty: Decimal, price: Decimal | None = None) -> dict:
        """Executes part of an order; returns the WS executionReport event for it."""
        o = self.orders[coid]
        price = price or self.fill_price
        qty = min(Decimal(qty), Decimal(o["origQty"]) - Decimal(o["executedQty"]))
        self._tid += 1
        commission = (qty * self.commission_rate) if o["side"] == "BUY" else (qty * price * self.commission_rate)
        c_asset = self.base if o["side"] == "BUY" else self.quote
        trade = {"symbol": o["symbol"], "id": self._tid, "orderId": o["orderId"], "price": str(price), "qty": str(qty),
                 "quoteQty": str(qty * price), "commission": str(commission), "commissionAsset": c_asset,
                 "time": self._now(), "isBuyer": o["side"] == "BUY", "isMaker": False}
        self.trades.append(trade)
        o["executedQty"] = str(Decimal(o["executedQty"]) + qty)
        o["cummulativeQuoteQty"] = str(Decimal(o["cummulativeQuoteQty"]) + qty * price)
        o["status"] = "FILLED" if Decimal(o["executedQty"]) >= Decimal(o["origQty"]) else "PARTIALLY_FILLED"
        o["updateTime"] = self._now()
        if o["side"] == "BUY":
            self.balances[self.base][0] += qty - commission
            self.balances[self.quote][0] -= qty * price
        else:
            self.balances[self.base][0] -= qty
            self.balances[self.quote][0] += qty * price - commission
        return self.execution_report(coid, "TRADE", trade)

    def cancel_on_exchange(self, coid: str) -> dict:
        o = self.orders[coid]
        o["status"] = "CANCELED"
        o["updateTime"] = self._now()
        return self.execution_report(coid, "CANCELED")

    def execution_report(self, coid, exec_type, trade=None) -> dict:
        o = self.orders[coid]
        self._tid += 1
        ev = {"e": "executionReport", "E": self._now(), "s": o["symbol"], "c": coid, "S": o["side"], "o": o["type"],
              "q": o["origQty"], "p": o["price"], "P": o.get("stopPrice", "0.00000000"), "C": "", "x": exec_type,
              "X": o["status"], "r": "NONE", "i": o["orderId"], "z": o["executedQty"], "Z": o["cummulativeQuoteQty"],
              "T": self._now(), "I": self._tid, "t": -1, "l": "0", "L": "0", "n": "0", "N": None}
        if exec_type == "CANCELED":
            ev["c"], ev["C"] = "cancel-req-xyz", coid
        if trade:
            ev.update({"t": trade["id"], "l": trade["qty"], "L": trade["price"], "n": trade["commission"],
                       "N": trade["commissionAsset"], "Y": trade["quoteQty"]})
        return {"subscriptionId": 0, "event": ev}

    def _full_response(self, o):
        fills = [{"price": t["price"], "qty": t["qty"], "commission": t["commission"],
                  "commissionAsset": t["commissionAsset"], "tradeId": t["id"]}
                 for t in self.trades if t["orderId"] == o["orderId"]]
        return {**o, "transactTime": self._now(), "fills": fills}

    # REST protocol ------------------------------------------------------
    def new_order(self, params):
        self.calls.append("new_order")
        mode, self.next_submit = self.next_submit, "fill"
        coid = params["newClientOrderId"]
        if mode == "reject":
            raise ccxt.InsufficientFunds('binance {"code":-2010,"msg":"Account has insufficient balance for requested action."}')
        if mode == "timeout_lost":
            raise ccxt.RequestTimeout("binance POST https://testnet.binance.vision/api/v3/order request timeout")
        if coid in self.orders and self.orders[coid]["status"] in ("NEW", "PARTIALLY_FILLED"):
            raise ccxt.InvalidOrder('binance {"code":-2010,"msg":"Duplicate order sent."}')
        self._oid += 1
        o = {"symbol": params["symbol"], "orderId": self._oid, "orderListId": -1, "clientOrderId": coid,
             "price": params.get("price", "0.00000000"), "origQty": params["quantity"], "executedQty": "0",
             "cummulativeQuoteQty": "0", "status": "NEW", "timeInForce": params.get("timeInForce", "GTC"),
             "type": params["type"], "side": params["side"], "stopPrice": params.get("stopPrice", "0.00000000"),
             "updateTime": self._now(), "workingTime": self._now()}
        self.orders[coid] = o
        qty = Decimal(params["quantity"])
        if params["type"] == "MARKET":
            if mode in ("fill", "timeout_accepted"):
                self.execute(coid, qty)
            elif mode == "partial":
                self.execute(coid, (qty * self.partial_fraction).quantize(Decimal("0.00001")))
            elif mode == "expire":
                self.execute(coid, (qty * self.partial_fraction).quantize(Decimal("0.00001")))
                o["status"] = "EXPIRED"
        if mode == "timeout_accepted":
            raise ccxt.RequestTimeout("binance request timeout (order actually accepted)")
        return self._full_response(o)

    def get_order(self, exchange_symbol, orig_client_order_id=None, order_id=None):
        self.calls.append("get_order")
        o = self.orders.get(orig_client_order_id) if orig_client_order_id else self._order_by_id(order_id)
        if o is None:
            raise OrderNotFound('binance {"code":-2013,"msg":"Order does not exist."}')
        return dict(o)

    def cancel_order(self, exchange_symbol, orig_client_order_id):
        self.calls.append("cancel_order")
        o = self.orders.get(orig_client_order_id)
        if o is None:
            raise OrderNotFound("-2013")
        if o["status"] not in ("NEW", "PARTIALLY_FILLED"):
            raise ccxt.InvalidOrder('binance {"code":-2011,"msg":"Unknown order sent."}')
        o["status"] = "CANCELED"
        return {**o, "origClientOrderId": orig_client_order_id, "clientOrderId": "cancel-xyz"}

    def open_orders(self, exchange_symbol):
        return [dict(o) for o in self.orders.values() if o["status"] in ("NEW", "PARTIALLY_FILLED")]

    def my_trades(self, exchange_symbol, order_id=None, start_time_ms=None, from_id=None, limit=1000):
        rows = [t for t in self.trades if t["symbol"] == exchange_symbol]
        if order_id is not None:
            rows = [t for t in rows if str(t["orderId"]) == str(order_id)]
        elif from_id is not None:
            rows = [t for t in rows if t["id"] >= from_id]
        elif start_time_ms is not None:
            rows = [t for t in rows if t["time"] >= start_time_ms]
        return rows[:limit]

    def account(self):
        return {"updateTime": self._now(), "balances": [
            {"asset": a, "free": str(v[0]), "locked": str(v[1])} for a, v in self.balances.items()]}

    def exchange_info(self, exchange_symbol):
        return {"symbol": exchange_symbol, "baseAsset": self.base, "quoteAsset": self.quote, "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001"},
            {"filterType": "NOTIONAL", "minNotional": "5"}]}

    def server_time_ms(self):
        return self._now()


# ---------------------------------------------------------------------------- harness
class Harness:
    def __init__(self, tmp_path, mode="PAPER", db_name="engine.sqlite3", clock=None, rest=None, cp=None, **cfg_overrides):
        extra = {}
        if mode != "PAPER":
            extra = {"API_KEY": "k" * 64, "API_SECRET": "s" * 64}
        self.cfg = make_cfg(TRADING_MODE=mode, ENGINE_DB_PATH=str(tmp_path / db_name), **extra, **cfg_overrides)
        self.clock = clock or FakeClock()
        self.cp = cp or FakeControlPlane()
        self.notifier = NullNotifier()
        self.market = FakeMarket(self.clock)
        self.store = Store(self.cfg.ENGINE_DB_PATH, clock=self.clock.now)
        self.rest = None
        executor = None
        if mode != "PAPER":
            self.rest = rest or FakeBinanceRest(self.clock)
            executor = BinanceExecutor(self.rest, "test", [self.cfg.SYMBOL])
        self.s = bootstrap.build(self.cfg, "test", clock=self.clock, store=self.store, executor=executor,
                                 control_plane=self.cp, market=self.market, notifier=self.notifier)
        self.strategy = ScriptedStrategy()
        self.s.cycle.strategy = self.strategy

    def healthy(self):
        """Exchange modes: run a full reconciliation (what the reconciliation worker does)."""
        return self.s.reconciliation.run("test")

    def cycle(self, signal="HOLD"):
        self.strategy.next = signal
        self.market.candle_index += 1
        return self.s.cycle.run_once()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def paper(tmp_path):
    return Harness(tmp_path, "PAPER")


@pytest.fixture
def testnet(tmp_path):
    return Harness(tmp_path, "TESTNET")


@pytest.fixture
def real_strategy():
    return MultiFactorStrategy(make_cfg())
