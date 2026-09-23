"""ML features, shared by research and the live engine (parity by construction).

`window_features(window, cfg)` takes the exact candle window the live engine hands a strategy
(the last CANDLE_HISTORY_LIMIT - 1 CLOSED candles, raw OHLCV) and returns the feature vector for the
last candle. Everything is causal: only candles inside the window are used.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import indicators
import strategy as v1_strategy

FEATURES = [
    "ret_1", "ret_3", "ret_6", "ret_12", "ret_24", "ret_72",
    "vol_24", "vol_72", "rsi", "macd_hist_pct",
    "dist_ema_fast", "dist_ema_mid", "dist_ema_slow", "ema_stack",
    "bb_pctb", "atr_pct", "range_pct", "volume_z",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos", "v1_score",
    # order flow: who is hitting the market, not just where price went
    "taker_buy_ratio", "taker_ratio_24", "taker_ratio_delta", "trades_z", "avg_trade_size_z", "quote_vol_z",
]
FEATURE_VERSION = "f2"
RAW = ["timestamp", "open", "high", "low", "close", "volume"]
FLOW = ["quote_volume", "trades", "taker_base"]        # from Binance raw klines; ccxt fetch_ohlcv drops these


class MissingOrderFlow(RuntimeError):
    """Raised when order-flow columns are absent. Fail closed: a model trained with them must never
    run on data without them (the engine would silently feed it wrong inputs)."""


def _z(series: np.ndarray, window: int) -> float:
    tail = series[-window:]
    sd = tail.std(ddof=1)
    return float((series[-1] - tail.mean()) / sd) if sd > 0 else 0.0


def window_features(window: pd.DataFrame, cfg) -> dict:
    missing = [c for c in FLOW if c not in window.columns]
    if missing:
        raise MissingOrderFlow(f"candles lack order-flow columns {missing}; Binance raw klines are required")
    raw = window[RAW].reset_index(drop=True)
    flow = window[FLOW].reset_index(drop=True).astype(float)
    ind = indicators.add_all_indicators(raw, cfg)
    last = ind.iloc[-1]
    c = raw["close"].to_numpy(float)
    close = c[-1]

    def ret(k):
        return close / c[-1 - k] - 1 if len(c) > k else np.nan

    logret = np.diff(np.log(c))
    hl = raw["high"].to_numpy(float) - raw["low"].to_numpy(float)
    prev_close = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum.reduce([hl, np.abs(raw["high"].to_numpy(float) - prev_close), np.abs(raw["low"].to_numpy(float) - prev_close)])
    vol = raw["volume"].to_numpy(float)
    v72 = vol[-72:]
    bb_w = last["bb_upper"] - last["bb_lower"]
    ts = pd.Timestamp(raw["timestamp"].iloc[-1])
    hour, dow = ts.hour, ts.dayofweek
    stack = 1.0 if last["ema_fast"] > last["ema_mid"] > last["ema_slow"] else (
        -1.0 if last["ema_fast"] < last["ema_mid"] < last["ema_slow"] else 0.0)
    return {
        "ret_1": ret(1), "ret_3": ret(3), "ret_6": ret(6), "ret_12": ret(12), "ret_24": ret(24), "ret_72": ret(72),
        "vol_24": float(np.std(logret[-24:], ddof=1)), "vol_72": float(np.std(logret[-72:], ddof=1)),
        "rsi": float(last["rsi"]) / 100.0,
        "macd_hist_pct": float(last["macd_hist"]) / close,
        "dist_ema_fast": close / float(last["ema_fast"]) - 1,
        "dist_ema_mid": close / float(last["ema_mid"]) - 1,
        "dist_ema_slow": close / float(last["ema_slow"]) - 1,
        "ema_stack": stack,
        "bb_pctb": float((close - last["bb_lower"]) / bb_w) if bb_w > 0 else 0.5,
        "atr_pct": float(np.mean(tr[-14:])) / close,
        "range_pct": float(hl[-1]) / close,
        "volume_z": float((vol[-1] - v72.mean()) / v72.std(ddof=1)) if v72.std(ddof=1) > 0 else 0.0,
        "hour_sin": math.sin(2 * math.pi * hour / 24), "hour_cos": math.cos(2 * math.pi * hour / 24),
        "dow_sin": math.sin(2 * math.pi * dow / 7), "dow_cos": math.cos(2 * math.pi * dow / 7),
        "v1_score": float(v1_strategy.analyze(ind, "BTC/USDT", cfg).score),
        **_flow_features(flow, vol),
    }


def _flow_features(flow: pd.DataFrame, vol: np.ndarray) -> dict:
    """Aggressive-buy share and activity levels. taker_base = volume bought by the aggressor side,
    so taker_base / volume near 1 means buyers are lifting offers; near 0 means sellers are hitting bids."""
    taker = flow["taker_base"].to_numpy(float)
    trades = flow["trades"].to_numpy(float)
    quote = flow["quote_volume"].to_numpy(float)
    ratio = np.divide(taker, vol, out=np.full(len(vol), 0.5), where=vol > 0)
    avg_size = np.divide(quote, trades, out=np.zeros(len(trades)), where=trades > 0)
    return {
        "taker_buy_ratio": float(ratio[-1]),
        "taker_ratio_24": float(ratio[-24:].mean()),
        "taker_ratio_delta": float(ratio[-24:].mean() - ratio[-72:].mean()),
        "trades_z": _z(trades, 72),
        "avg_trade_size_z": _z(avg_size, 72),
        "quote_vol_z": _z(quote, 72),
    }
