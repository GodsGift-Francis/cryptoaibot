# Crypto Trading Bot — Multi-Factor, Paper-Trading-First

A rules-based bot that combines trend, momentum, market regime (BTC dominance),
and free news sentiment into one explainable signal — plus a visual dashboard,
a backtester, and hard risk limits. Every decision it makes is a specific,
inspectable reason (see `strategy.py`), not a black box.

## What's actually in here

| File | Purpose |
|---|---|
| `config.py` | All settings in one place |
| `data_fetcher.py` | Price candles (ccxt), BTC dominance (CoinGecko, free), news (RSS, free) |
| `indicators.py` | EMA, RSI, MACD, Bollinger Bands |
| `sentiment.py` | Scores news headlines with VADER (free, offline) |
| `strategy.py` | Combines everything into BUY / SELL / HOLD + reasons |
| `risk_manager.py` | Position sizing, stop-loss, daily loss circuit breaker |
| `backtester.py` | Replays history through the same strategy code |
| `paper_trader.py` | One decision cycle: fetch → analyze → risk-check → act → log |
| `run_bot.py` | Headless loop — run this continuously on a server/Pi |
| `dashboard.py` | Streamlit UI — charts, live signal, backtest, trade log |

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env: add exchange API key/secret (Trade permission ONLY — never withdrawal)
```

Run the dashboard:
```bash
streamlit run dashboard.py
```

Run the headless bot (for actual unattended automation):
```bash
python run_bot.py
```

## Before you touch real money

1. **Stay on `USE_TESTNET = True` in `config.py` first.** This uses the exchange's
   sandbox with fake funds. Binance, Bybit, and others all offer free testnets.
2. **Backtest on the "Backtest" tab** across at least a year of data. Look at
   max drawdown as closely as total return — a strategy that occasionally loses
   40% is not "profitable" in any way that matters to your actual money.
3. **Run in paper mode against live prices for a few weeks** (`USE_TESTNET = True`
   still simulates trades against real-time prices via `paper_trader.py`'s
   internal balance tracking — no real orders are ever sent in this mode).
4. **Only then**, if you still want to: set `USE_TESTNET = False` **and**
   `LIVE_TRADING_CONFIRMED = True` in `config.py`. Both switches exist on purpose —
   it should never be an accident.
5. **Start with the smallest size the exchange allows.** Scale up slowly, if at all.

## Security

- Your API key needs **Trade** permission only. Never enable withdrawal.
- IP-whitelist the key if your exchange supports it.
- `.env` holds your real secrets — it's git-ignored by default in most setups;
  never share it or commit it.

## Honest limitations (read this)

- **The backtester is technical-only.** Free historical news/sentiment data at
  any real depth doesn't exist, so backtest results won't include the
  sentiment factor that live/paper mode does. Treat backtest numbers as a
  sanity check on the trend/momentum logic, not a promise of live performance.
- **"Multi-factor" here means four factors**, not a hedge fund's research
  stack: trend structure, RSI/MACD momentum, BTC dominance regime, and
  keyword-based news sentiment. It's genuinely more than a single-indicator
  bot, but it is not deep on-chain analysis, order-flow data, or anything
  requiring paid data feeds.
- **This is not financial advice**, and past backtest/paper performance does
  not predict future results. Crypto markets are volatile and you can lose
  your full principal. Only trade what you can afford to lose.
- **Nothing here runs by itself.** You (or a server you control) need to keep
  `run_bot.py` running for it to act as an "automatic" bot — it's your code,
  running under your control, not a hosted service.
