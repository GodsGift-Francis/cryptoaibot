"""
Technical indicators, implemented directly on pandas Series so this module
has zero dependencies beyond pandas/numpy.
"""
import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder's smoothing
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    return result.fillna(50)  # neutral while warming up


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(series: pd.Series, period: int = 20, num_std: float = 2.0):
    sma = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = sma + num_std * std
    lower = sma - num_std * std
    return upper, sma, lower


def add_all_indicators(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """Adds every indicator the strategy needs as new columns on a copy of df."""
    out = df.copy()
    out["ema_fast"] = ema(out["close"], cfg.EMA_FAST)
    out["ema_mid"] = ema(out["close"], cfg.EMA_MID)
    out["ema_slow"] = ema(out["close"], cfg.EMA_SLOW)
    out["rsi"] = rsi(out["close"], cfg.RSI_PERIOD)
    macd_line, signal_line, hist = macd(out["close"], cfg.MACD_FAST, cfg.MACD_SLOW, cfg.MACD_SIGNAL)
    out["macd"] = macd_line
    out["macd_signal"] = signal_line
    out["macd_hist"] = hist
    upper, mid, lower = bollinger_bands(out["close"])
    out["bb_upper"] = upper
    out["bb_mid"] = mid
    out["bb_lower"] = lower
    return out
