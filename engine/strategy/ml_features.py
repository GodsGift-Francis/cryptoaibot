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
]
FEATURE_VERSION = "f1"
RAW = ["timestamp", "open", "high", "low", "close", "volume"]


def window_features(window: pd.DataFrame, cfg) -> dict:
    raw = window[RAW].reset_index(drop=True)
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
    }
