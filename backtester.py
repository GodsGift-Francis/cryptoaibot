"""
Replays historical candles through the exact same strategy + risk_manager
code that live/paper trading uses, so a backtest result actually reflects
what the bot would have done - no separate "backtest-only" logic to drift
out of sync.

Honest limitation: free historical news/sentiment data doesn't really exist,
so the backtest is technical-only (trend + momentum + BTC dominance are not
included here). Paper and live trading add sentiment on top. Keep that in
mind when comparing a backtest result to live performance.
"""
import pandas as pd

import indicators
import risk_manager
import strategy


def _compute_stats(equity_df: pd.DataFrame, trades: list, initial_balance: float) -> dict:
    if equity_df.empty:
        return {}
    final_equity = equity_df["equity"].iloc[-1]
    total_return_pct = (final_equity / initial_balance - 1) * 100
    running_max = equity_df["equity"].cummax()
    drawdown = (equity_df["equity"] - running_max) / running_max * 100
    max_drawdown_pct = drawdown.min()

    closed_trades = [t for t in trades if t["side"].startswith("SELL")]
    wins = [t for t in closed_trades if t["pnl"] > 0]
    win_rate = (len(wins) / len(closed_trades) * 100) if closed_trades else 0.0

    returns = equity_df["equity"].pct_change().dropna()
    sharpe_approx = float(returns.mean() / returns.std() * (365 ** 0.5)) if returns.std() > 0 else 0.0

    return {
        "final_equity": round(float(final_equity), 2),
        "total_return_pct": round(float(total_return_pct), 2),
        "max_drawdown_pct": round(float(max_drawdown_pct), 2),
        "num_trades": len(closed_trades),
        "win_rate_pct": round(win_rate, 2),
        "sharpe_approx": round(sharpe_approx, 2),
    }


def run_backtest(df: pd.DataFrame, symbol: str, cfg, initial_balance: float | None = None) -> dict:
    df = indicators.add_all_indicators(df, cfg)
    balance = initial_balance if initial_balance is not None else cfg.STARTING_PAPER_BALANCE
    position_qty = 0.0
    entry_price = None
    stop_price = None
    equity_curve = []
    trades = []
    # V1.1 fix: V1 renamed RiskState fields to equity/day_start_equity but never updated this call,
    # so the backtester crashed on every run. Rename only; logic unchanged.
    state = risk_manager.RiskState(equity=balance, day_start_equity=balance)

    warmup = max(cfg.EMA_SLOW, cfg.RSI_PERIOD, cfg.MACD_SLOW) + 5
    if len(df) <= warmup:
        raise ValueError(f"Need more than {warmup} candles to backtest (got {len(df)}).")

    for i in range(warmup, len(df)):
        window = df.iloc[: i + 1]
        row = window.iloc[-1]
        price = row["close"]

        equity = balance + position_qty * price
        equity_curve.append({"timestamp": row["timestamp"], "equity": equity})

        # Stop-loss check happens before new signals, using the candle's low.
        if position_qty > 0 and stop_price is not None and row["low"] <= stop_price:
            pnl = position_qty * (stop_price - entry_price)
            balance += position_qty * stop_price
            trades.append({"timestamp": row["timestamp"], "side": "SELL (stop loss)",
                            "price": stop_price, "pnl": pnl})
            position_qty, entry_price, stop_price = 0.0, None, None
            continue

        signal = strategy.analyze(window, symbol, cfg, market_data=None, sentiment=None)

        if signal.action == "BUY" and position_qty == 0 and not state.halted:
            qty = risk_manager.position_size(balance, price, cfg.RISK_PER_TRADE_PCT, cfg.STOP_LOSS_PCT)
            qty = min(qty, balance / price)
            if qty > 0:
                position_qty = qty
                entry_price = price
                stop_price = risk_manager.stop_loss_price(price, "long", cfg.STOP_LOSS_PCT)
                balance -= qty * price
                trades.append({"timestamp": row["timestamp"], "side": "BUY", "price": price, "pnl": 0.0})

        elif signal.action == "SELL" and position_qty > 0:
            pnl = position_qty * (price - entry_price)
            balance += position_qty * price
            trades.append({"timestamp": row["timestamp"], "side": "SELL", "price": price, "pnl": pnl})
            position_qty, entry_price, stop_price = 0.0, None, None

        state.equity = balance + position_qty * price
        risk_manager.check_daily_loss_limit(state, cfg.DAILY_LOSS_LIMIT_PCT)

    if position_qty > 0:
        last_row = df.iloc[-1]
        pnl = position_qty * (last_row["close"] - entry_price)
        balance += position_qty * last_row["close"]
        trades.append({"timestamp": last_row["timestamp"], "side": "SELL (final close)",
                        "price": last_row["close"], "pnl": pnl})

    equity_df = pd.DataFrame(equity_curve)
    trades_df = pd.DataFrame(trades)
    stats = _compute_stats(equity_df, trades, initial_balance if initial_balance is not None else cfg.STARTING_PAPER_BALANCE)
    return {"equity_curve": equity_df, "trades": trades_df, "stats": stats}
