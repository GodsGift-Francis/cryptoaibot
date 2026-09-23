"""Stable strategy interface. Wraps the UNCHANGED V1 `strategy.analyze`.

The strategy returns a Signal only. It never sizes, never talks to an
exchange and never submits orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import strategy as v1_strategy


@dataclass(frozen=True)
class MarketContext:
    symbol: str
    candles: object              # pandas DataFrame with indicators; last row = candle being evaluated
    market_data: dict | None = None
    sentiment: dict | None = None


class Strategy(Protocol):
    name: str
    version: str

    def evaluate(self, ctx: MarketContext) -> "v1_strategy.Signal": ...


class MultiFactorStrategy:
    def __init__(self, cfg):
        self.cfg = cfg
        self.name = cfg.STRATEGY_NAME
        self.version = cfg.STRATEGY_VERSION

    def evaluate(self, ctx: MarketContext):
        return v1_strategy.analyze(ctx.candles, ctx.symbol, self.cfg, market_data=ctx.market_data, sentiment=ctx.sentiment)
