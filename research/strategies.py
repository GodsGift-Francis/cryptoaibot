"""Signal generators: V1 (exact live code path) and the competitor baselines it must beat.

Competitor proxies = what common retail bots / platforms run:
  buy_hold  - passive benchmark every active strategy is judged against
  dca       - dollar-cost averaging bots (weekly buys, never sells)
  sma_cross - trend-following bots (50/200 golden/death cross)
  rsi_mr    - mean-reversion bots (buy RSI<30, sell RSI>55)
  donchian  - breakout bots (20-bar high entry, 10-bar low exit; "turtle")
  random    - random entries with V1's exits: the luck benchmark
"""
from __future__ import annotations

import hashlib
import os
import types

import numpy as np
import pandas as pd

import config as live_config
import indicators
import strategy as v1_strategy

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")


def cfg_copy(**over):
    c = types.SimpleNamespace(**{k: getattr(live_config, k) for k in dir(live_config) if k.isupper()})
    for k, v in over.items():
        setattr(c, k, v)
    return c


def warmup_bars(cfg=None) -> int:
    """Closed candles the live engine hands the strategy: it fetches CANDLE_HISTORY_LIMIT candles and,
    with SIGNAL_ON_CLOSED_CANDLES, drops the forming one -> LIMIT - 1. Research must use the same window."""
    cfg = cfg or live_config
    return int(cfg.CANDLE_HISTORY_LIMIT) - (1 if getattr(cfg, "SIGNAL_ON_CLOSED_CANDLES", True) else 0)


def v1_scores(df: pd.DataFrame, cfg=None, window: int | None = None, cache: bool = True, symbol="BTC/USDT") -> np.ndarray:
    """V1 score per closed bar, computed EXACTLY as live: indicators over the last `window` candles
    (the engine fetches CANDLE_HISTORY_LIMIT=300 and drops the forming one), then strategy.analyze on the last row.
    BTC dominance and news sentiment are unavailable historically -> technical-only (0 contribution)."""
    cfg = cfg or cfg_copy()
    window = window or warmup_bars(cfg)
    key = hashlib.sha256(pd.util.hash_pandas_object(df[["timestamp", "close"]], index=False).values.tobytes()
                         + f"{window}{cfg.EMA_FAST}{cfg.EMA_MID}{cfg.EMA_SLOW}{cfg.RSI_PERIOD}{cfg.RSI_OVERSOLD}"
                           f"{cfg.RSI_OVERBOUGHT}{cfg.MACD_FAST}{cfg.MACD_SLOW}{cfg.MACD_SIGNAL}".encode()).hexdigest()[:24]
    path = os.path.join(CACHE_DIR, f"v1_scores_{key}.npy")
    if cache and os.path.exists(path):
        return np.load(path)
    raw = df[["timestamp", "open", "high", "low", "close", "volume"]].reset_index(drop=True)
    scores = np.full(len(raw), np.nan)
    for i in range(window - 1, len(raw)):
        frame = indicators.add_all_indicators(raw.iloc[i - window + 1:i + 1], cfg)
        scores[i] = v1_strategy.analyze(frame, symbol, cfg).score
    if cache:
        os.makedirs(CACHE_DIR, exist_ok=True)
        np.save(path, scores)
    return scores


def thresholds(scores: np.ndarray, buy: float, sell: float) -> np.ndarray:
    out = np.zeros(len(scores))
    out[scores >= buy] = 1
    out[scores <= sell] = -1
    out[np.isnan(scores)] = 0
    return out


def _mask(sig: np.ndarray, start: int) -> np.ndarray:
    sig = sig.astype(float).copy()
    sig[:start] = 0
    return sig


def buy_hold(df, start):
    sig = np.zeros(len(df))
    sig[start] = 1
    return sig


def sma_cross(df, start, fast=50, slow=200):
    c = df["close"]
    f, s = c.rolling(fast).mean(), c.rolling(slow).mean()
    return _mask(np.where(f > s, 1, -1), start)


def rsi_mr(df, start, low=30, exit_=55):
    r = indicators.rsi(df["close"], 14).to_numpy()
    sig = np.where(r < low, 1, np.where(r > exit_, -1, 0))
    return _mask(np.nan_to_num(sig), start)


def donchian(df, start, entry=20, exit_=10):
    hi = df["high"].rolling(entry).max().shift(1)
    lo = df["low"].rolling(exit_).min().shift(1)
    c = df["close"]
    return _mask(np.where(c > hi, 1, np.where(c < lo, -1, 0)), start)


def random_entries(n, start, n_entries, seed):
    rng = np.random.default_rng(seed)
    sig = np.zeros(n)
    idx = rng.choice(np.arange(start, n - 1), size=min(n_entries, max(n - 1 - start, 0)), replace=False)
    sig[idx] = 1
    return sig
