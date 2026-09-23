# Algorithm research pipeline (Sprints 0–4)

```bash
python -m research                       # download 12 months (BTC/ETH/SOL 1h), verify, evaluate -> research/out/REPORT.md
python -m research.evaluate --holdout    # ONLY if REPORT.md says GO; runs once on the held-back 3 months, then locks
```
Runs in minutes on a VPS outside the US (Binance blocks US IPs). No API keys needed.

| Sprint | Module | What it guarantees |
|---|---|---|
| 0 Data | `data.py` | 12 complete months from data.binance.vision, SHA-256 verified, µs/ms timestamps normalised, gaps flagged, manifest written |
| 1 Backtester | `backtest.py` | next-open fills, fees + slippage, gap-aware stops, daily-halt reset, √8760 Sharpe; **trade-for-trade parity with the live engine** (`tests/research/test_backtest.py`) |
| 2 Measure | `evaluate.py` | V1 vs buy & hold, weekly DCA, SMA 50/200, RSI mean-reversion, Donchian breakout, and 300 random-entry runs |
| 3 Tune | `evaluate.py` | walk-forward: 3-month train / 1-month test, 125-config grid, compounded out-of-sample result |
| 4 Robustness | `evaluate.py` | 2× costs, neighbouring-config stability, Monte Carlo drawdown, ETH/SOL, one-shot locked holdout |

Split: months 1–9 development, months 10–12 holdout. V1 signals use the exact live code path (indicators over
the last 300 candles + `strategy.analyze`). BTC dominance and news sentiment have no reliable history and are
excluded from backtests.

## Gates (fixed before seeing data — all must pass)
≥ 20 out-of-sample trades · positive OOS after costs · OOS Sharpe above the best competitor over the same months ·
beats 95% of random entries · positive at 2× costs · ≥ 60% of neighbouring configs profitable. Then the holdout
must be positive with drawdown ≤ 1.5× development drawdown.

## Using a GO result (Sprint 5)
Set `SCORE_BUY_THRESHOLD`, `SCORE_SELL_THRESHOLD`, `STOP_LOSS_PCT` in `.env` to the reported values and forward-test
in PAPER → TESTNET. Non-default values tag every signal/order with a distinct `strategy_version`.

## AI layer (`ml.py`)
```bash
python -m research.ml               # purged walk-forward -> research/out/ML_REPORT.md (+ model if a mode passes)
python -m research.ml --holdout     # ONE run on the held-back months, then locked
```
Gradient-boosted trees predict P(next 24h return beats round-trip costs + 0.2%) from 23 causal features
(`engine/strategy/ml_features.py`, the same function the live engine uses). Two modes are evaluated:
**veto** (model may only block V1 buys) and **ml** (model decides entries/exits). Stops, sizing and risk are unchanged.

Leakage defences, each covered by a test: labels start at the next bar's open; training rows are purged when
their label window reaches the predicted period; thresholds are picked on a purged validation month; a
shuffled-label canary must score AUC ≈ 0.5; on pure noise the pipeline must find no edge.

Extra gates for the AI: pooled OOS AUC ≥ 0.52, clean canary, beats V1 default OOS (return and Sharpe), and
**at full allocation, out-earns the best competitor over the same months** (the project's profit goal).

Going live with it (after GO + holdout pass), in the engine `.env`:
```
STRATEGY_MODE=ml            # or veto, whichever the report chose
ML_MODEL_PATH=/opt/crypto-bot/research/out/model/model.joblib
ML_MODEL_SHA256=<from ML_REPORT.md>
```
The engine loads the model only if the hash matches, the features match, and the installed scikit-learn equals the
training version; otherwise it refuses to start (fail closed). Signals are tagged `…+ai` with the model hash.
LLM-based signals are deliberately not backtested: a language model may already know historical prices.

## Timeframe / horizon sweep (`sweep.py`)
```bash
python -m research.sweep            # uses the 1h data already downloaded -> research/out/SWEEP_REPORT.md
```
1h candles proved unpredictable (AUC ~0.52 with and without order flow, and costs on hundreds of trades
swamp that edge). This runs the same pipeline on **1h / 4h / 1d** candles and **12h / 24h / 72h** horizons,
for the rules strategy and both AI modes. Stops scale with bar size and Sharpe is annualised per timeframe.

**Multiple testing:** trying ~12 variants guarantees the best one looks good by luck. The best variant is
therefore compared against the best of the SAME NUMBER of random strategies (95th percentile, on both
Sharpe and return). Only if it beats both bars is it labelled CANDIDATE — which is still not a GO: a
candidate must then be re-run on its own through `research.evaluate` / `research.ml`, pass those gates,
and pass the one-shot holdout before any forward test.
