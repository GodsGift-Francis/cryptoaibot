"""Market data service: candles + indicators + regime/sentiment inputs, with validity checks.

Public market data only (no account access). Reuses the unchanged V1
data_fetcher / indicators / sentiment modules.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pandas as pd

import data_fetcher
import indicators
import sentiment as sentiment_mod

_TF = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def timeframe_seconds(tf: str) -> int:
    return int(tf[:-1]) * _TF[tf[-1]]


@dataclass
class MarketSnapshot:
    valid: bool
    reason: str
    candles: pd.DataFrame | None = None       # frame the strategy evaluates (last row = evaluated candle)
    candle_ts: str | None = None               # ISO open time of the evaluated candle (intent idempotency anchor)
    mark_price: Decimal | None = None          # latest traded price (forming candle close)
    signal_price: Decimal | None = None        # close of the evaluated candle (V1 order reference price)
    recent_candles: list | None = None         # [(close_time, low)] for the last two raw candles
    market_data: dict | None = None
    sentiment: dict | None = None
    fetched_at: object = None

    def stop_check_low(self, opened_at) -> Decimal | None:
        """Lowest low among candles that closed after the position opened (the forming
        candle always counts). Prevents a pre-entry wick from triggering the stop."""
        lows = [low for close_time, low in (self.recent_candles or []) if opened_at is None or close_time > opened_at]
        return min(lows) if lows else None


class MarketDataService:
    def __init__(self, cfg, clock, fetch_ohlcv=None, fetch_global=None, fetch_news=None):
        self.cfg = cfg
        self.clock = clock
        self._ohlcv = fetch_ohlcv or self._default_ohlcv
        self._global = fetch_global or data_fetcher.get_global_market_data
        self._news = fetch_news or (lambda: data_fetcher.get_news_headlines(cfg))
        self._exchange = None

    def _default_ohlcv(self, symbol, timeframe, limit):
        if self._exchange is None:
            self._exchange = data_fetcher.get_exchange(self.cfg)
        return data_fetcher.fetch_ohlcv(self._exchange, symbol, timeframe, limit=limit)

    def snapshot(self, symbol: str, with_context: bool = True) -> MarketSnapshot:
        now = self.clock.now()
        try:
            raw = self._ohlcv(symbol, self.cfg.TIMEFRAME, self.cfg.CANDLE_HISTORY_LIMIT)
        except Exception as exc:  # noqa: BLE001
            return MarketSnapshot(False, f"candle fetch failed: {type(exc).__name__}", fetched_at=now)
        if raw is None or len(raw) < self.cfg.EMA_SLOW + 5:
            return MarketSnapshot(False, f"insufficient candles ({0 if raw is None else len(raw)})", fetched_at=now)

        tf = timedelta(seconds=timeframe_seconds(self.cfg.TIMEFRAME))
        ts = pd.to_datetime(raw["timestamp"], utc=True)
        last_open = ts.iloc[-1].to_pydatetime()
        forming = last_open + tf > now
        if self.cfg.SIGNAL_ON_CLOSED_CANDLES and forming:
            closed = raw.iloc[:-1]
        else:
            closed = raw
        evaluated_open = pd.to_datetime(closed["timestamp"].iloc[-1], utc=True).to_pydatetime()
        age = now - (evaluated_open + tf)
        if age > tf * self.cfg.MARKET_DATA_MAX_AGE_CANDLES:
            return MarketSnapshot(False, f"stale market data: evaluated candle closed {int(age.total_seconds())}s ago", fetched_at=now)

        frame = indicators.add_all_indicators(closed.reset_index(drop=True), self.cfg)
        recent = [((pd.to_datetime(t, utc=True).to_pydatetime() + tf), Decimal(str(float(low))))
                  for t, low in zip(raw["timestamp"].iloc[-2:], raw["low"].iloc[-2:])]
        market_data = headlines_sent = None
        if with_context:
            market_data = self._global()
            headlines = self._news()
            headlines_sent = sentiment_mod.score_headlines(headlines) if headlines else None
        return MarketSnapshot(
            valid=True, reason="ok", candles=frame, candle_ts=evaluated_open.isoformat(),
            mark_price=Decimal(str(float(raw["close"].iloc[-1]))),
            signal_price=Decimal(str(float(frame["close"].iloc[-1]))),
            recent_candles=recent,
            market_data=market_data, sentiment=headlines_sent, fetched_at=now,
        )
