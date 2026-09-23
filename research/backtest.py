"""Sprint 1 - a backtester that doesn't flatter the strategy.

Fixes the V1 backtester's known problems:
  * signal on the CLOSED candle, fill at the NEXT candle's open (no same-bar lookahead)
  * fees on both sides + slippage on every fill
  * stop fills at the stop, or at the open if the market gapped through it (never better)
  * the stop can trigger on the entry candle itself (entry is at the open, the low comes after)
  * daily-loss halt resets every UTC day and blocks entries only (matches the V1.1 engine)
  * Sharpe/Sortino annualised with the real number of bars per year (8760 for 1h, not 365)
  * no candle cap: runs over whatever history it is given

Signals: array aligned with `df`; +1 = BUY, -1 = SELL, 0 = HOLD, evaluated at that bar's close.
Long-only, one position at a time (like the engine).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

import risk_manager


@dataclass(frozen=True)
class BTConfig:
    initial: float = 1000.0
    fee_rate: float = 0.001            # Binance spot taker, no BNB discount
    slippage_bps: float = 5.0          # applied against us on every fill
    sizing: str = "risk"               # "risk" = V1 formula (risk_pct / stop_pct of equity); "all_in"
    risk_pct: float = 1.0
    stop_pct: float | None = 3.0       # None = no stop
    daily_loss_pct: float | None = 5.0
    max_hold_bars: int | None = None   # time exit (used by the random-entry benchmark)
    bars_per_year: float = 8760.0

    def with_(self, **kw) -> "BTConfig":
        return replace(self, **kw)


@dataclass
class BTResult:
    equity: pd.Series
    trades: pd.DataFrame
    metrics: dict = field(default_factory=dict)


def run(df: pd.DataFrame, signals, cfg: BTConfig = BTConfig()) -> BTResult:
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    ts = pd.to_datetime(df["timestamp"], utc=True)
    days = ts.dt.floor("D").to_numpy()
    sig = np.nan_to_num(np.asarray(signals, dtype=float))
    n = len(df)
    slip = cfg.slippage_bps / 1e4

    cash, qty, entry_px, stop_px, entry_i, entry_fee = cfg.initial, 0.0, 0.0, None, -1, 0.0
    equity = np.empty(n)
    trades = []
    day_start_eq, cur_day, halted, prev_eq = cfg.initial, None, False, cfg.initial
    fees_paid = 0.0

    def close_position(i, raw_px, reason):
        nonlocal cash, qty, fees_paid
        px = raw_px * (1 - slip)
        proceeds = qty * px
        fee = proceeds * cfg.fee_rate
        cash += proceeds - fee
        fees_paid += fee
        pnl = proceeds - fee - (qty * entry_px + entry_fee)
        trades.append((ts.iloc[entry_i], entry_px, ts.iloc[i], px, qty, pnl, pnl / (qty * entry_px + entry_fee), reason, i - entry_i))
        qty = 0.0

    for i in range(n):
        if days[i] != cur_day:                      # new UTC day: reset the daily-loss baseline
            cur_day, day_start_eq, halted = days[i], prev_eq, False

        # 1. act at this bar's open on the previous bar's signal
        if i > 0:
            s = sig[i - 1]
            if qty > 0 and (s < 0 or (cfg.max_hold_bars and i - entry_i >= cfg.max_hold_bars)):
                close_position(i, o[i], "signal" if s < 0 else "time")
            elif qty == 0 and s > 0 and not halted:
                px = o[i] * (1 + slip)
                if cfg.sizing == "risk" and cfg.stop_pct:
                    units = risk_manager.position_size(cash, px, cfg.risk_pct, cfg.stop_pct)   # V1 formula
                    notional = min(units * px, cash / (1 + cfg.fee_rate))
                else:
                    notional = cash / (1 + cfg.fee_rate)
                if notional > 0:
                    entry_fee = notional * cfg.fee_rate
                    cash -= notional + entry_fee
                    fees_paid += entry_fee
                    qty, entry_px, entry_i = notional / px, px, i
                    stop_px = px * (1 - cfg.stop_pct / 100) if cfg.stop_pct else None

        # 2. intrabar stop (can fire on the entry bar: entry is at the open)
        if qty > 0 and stop_px is not None and l[i] <= stop_px:
            close_position(i, min(stop_px, o[i]), "stop")

        # 3. mark to market at the close; daily-loss halt blocks further entries today
        eq = cash + qty * c[i]
        equity[i] = eq
        prev_eq = eq
        if cfg.daily_loss_pct and eq < day_start_eq * (1 - cfg.daily_loss_pct / 100):
            halted = True

    if qty > 0:                                   # open at the end: report it, marked to market
        close_position(n - 1, c[-1], "end")
        equity[-1] = cash

    tdf = pd.DataFrame(trades, columns=["entry_time", "entry_price", "exit_time", "exit_price", "qty", "pnl",
                                        "return", "reason", "bars"])
    eq_s = pd.Series(equity, index=ts)
    res = BTResult(eq_s, tdf)
    res.metrics = metrics(eq_s, tdf, cfg, fees_paid)
    return res


EMPTY_METRICS = {"total_return": 0.0, "cagr": 0.0, "max_drawdown": 0.0, "sharpe": 0.0, "sortino": 0.0,
                 "calmar": 0.0, "trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "avg_trade_return": 0.0,
                 "exposure": 0.0, "fees_paid": 0.0, "final_equity": 0.0}


def metrics(equity: pd.Series, trades: pd.DataFrame, cfg: BTConfig, fees_paid: float = 0.0) -> dict:
    eq = equity.to_numpy(float)
    if len(eq) == 0:                      # empty window (e.g. a fold shorter than the indicator warmup)
        return {**EMPTY_METRICS, "final_equity": cfg.initial}
    prev = eq[:-1]
    rets = np.divide(np.diff(eq), prev, out=np.zeros(len(prev)), where=prev > 0) if len(eq) > 1 else np.array([0.0])
    years = max(len(eq) / cfg.bars_per_year, 1e-9)
    total = eq[-1] / eq[0] - 1 if eq[0] > 0 else 0.0          # a zero starting balance can't lose or gain
    peak = np.maximum.accumulate(eq)
    dd = np.divide(eq - peak, peak, out=np.zeros(len(eq)), where=peak > 0)
    max_dd = float(dd.min())
    sd = rets.std(ddof=1) if len(rets) > 1 else 0.0
    downside = rets[rets < 0]
    dsd = math.sqrt((downside ** 2).mean()) if len(downside) else 0.0
    ann = math.sqrt(cfg.bars_per_year)
    wins = trades[trades["pnl"] > 0]["pnl"].sum() if len(trades) else 0.0
    losses = -trades[trades["pnl"] < 0]["pnl"].sum() if len(trades) else 0.0
    cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    exposure = float(trades["bars"].sum()) / max(len(eq), 1) if len(trades) else 0.0
    return {
        "total_return": total, "cagr": cagr, "max_drawdown": max_dd,
        "sharpe": float(rets.mean() / sd * ann) if sd > 0 else 0.0,
        "sortino": float(rets.mean() / dsd * ann) if dsd > 0 else 0.0,
        "calmar": float(cagr / abs(max_dd)) if max_dd < 0 else 0.0,
        "trades": int(len(trades)),
        "win_rate": float((trades["pnl"] > 0).mean()) if len(trades) else 0.0,
        "profit_factor": float(wins / losses) if losses > 0 else (float("inf") if wins > 0 else 0.0),
        "avg_trade_return": float(trades["return"].mean()) if len(trades) else 0.0,
        "exposure": exposure, "fees_paid": fees_paid, "final_equity": float(eq[-1]),
    }


def run_dca(df: pd.DataFrame, cfg: BTConfig = BTConfig(), every_bars: int = 168) -> BTResult:
    """Dollar-cost averaging competitor: equal quote buys every `every_bars`, never sells."""
    c, o = df["close"].to_numpy(float), df["open"].to_numpy(float)
    ts = pd.to_datetime(df["timestamp"], utc=True)
    n = len(df)
    buys = list(range(0, n, every_bars))
    per_buy = cfg.initial / len(buys)
    cash, qty, fees = cfg.initial, 0.0, 0.0
    eq = np.empty(n)
    bset = set(buys)
    for i in range(n):
        if i in bset:
            px = o[i] * (1 + cfg.slippage_bps / 1e4)
            notional = min(per_buy, cash) / (1 + cfg.fee_rate)
            fee = notional * cfg.fee_rate
            cash -= notional + fee
            fees += fee
            qty += notional / px
        eq[i] = cash + qty * c[i]
    eq_s = pd.Series(eq, index=ts)
    empty = pd.DataFrame(columns=["entry_time", "entry_price", "exit_time", "exit_price", "qty", "pnl", "return", "reason", "bars"])
    res = BTResult(eq_s, empty)
    res.metrics = metrics(eq_s, empty, cfg, fees)
    res.metrics["exposure"] = 1.0
    return res
