# Architecture (V1.1)

```
Binance Spot ──REST (CCXT raw endpoints)──┐        ┌── WebSocket API user data stream
                                          │        │   (userDataStream.subscribe.signature)
                         engine/exchanges/binance/ (rest_client, websocket_client, signer, normalizer)
                                          │        │
                     engine/execution/ (Executor: binance.py | paper.py)      workers/reconciliation_worker.py
                                          │                                    │  ├ StreamIngestionService
workers/trading_worker.py                 │                                    │  └ ReconciliationService
  └ TradingCycle ─ StrategyService (V1 strategy.py, unchanged)                 │
               ─ RiskManager (V1 risk_manager.py math)                         │
               ─ MarketDataService (V1 data_fetcher/indicators/sentiment)      │
               ─ OrderService ◄───────────── the ONLY submit + report/fill path ┘
               ─ PositionService ◄────────── the ONLY position / P&L mutation
                          │
          engine/infrastructure/store.py  (SQLite ledger, UNIQUE-key idempotency, outbox)
                          │ outbox (at-least-once, X-Request-Id)
          engine/control_plane/client.py ──HTTP──► Laravel /api/internal/v1  (fail closed on error)
                                                     │
                     Laravel: dashboard (session auth, gates, CSRF, audit) · Telegram webhook · scheduler
```

## Where things live
| Concern | Location |
|---|---|
| Strategy | `strategy.py` (unchanged) behind `engine/strategy/strategy_service.py` |
| Risk | `risk_manager.py` (unchanged math) + `engine/risk/{manager,rules}.py` |
| Order lifecycle / state machine | `engine/domain/models.py` (+ `enums.py`, `events.py`) |
| Order submission | `engine/application/order_service.py` only |
| Position / P&L | `engine/application/position_service.py` only |
| Exchange communication | `engine/exchanges/binance/*`, `engine/execution/*` |
| Reconciliation | `engine/application/reconciliation_service.py`, `workers/reconciliation_worker.py` |
| Persistence | `engine/infrastructure/store.py` (engine), Laravel migrations (mirror + control) |
| Control-plane decisions | `engine/application/trading_cycle.py` (gating), Laravel `BotControlService` |
| Operator controls | Laravel dashboard + Telegram, audited in `audit_logs` |
| Wiring | `engine/bootstrap.py` |

## Why two stores
The engine ledger is authoritative and must keep working (ingesting fills) while Laravel is down; Laravel is
the operator's view and control source. Every engine state change enqueues an outbox message in the **same
transaction**, so the mirror converges after any outage without double-applying.

## Two workers, one ledger
Both workers open the same SQLite ledger. Writers serialize through `BEGIN IMMEDIATE` (cross-process) and an
in-process lock, and every order update re-reads the row inside its transaction, so REST responses and WebSocket
events for the same order never lose updates (`tests/integration/test_concurrent_writers.py`). Outbox delivery is
guarded by a 60 s lease renewed per message: one worker delivers at a time, the other takes over if it dies.

## Strategy selection (AI layer)
`engine/strategy/ai_strategy.build_strategy` returns the unchanged V1 strategy (`STRATEGY_MODE=v1`, default) or
`AIStrategy` (`veto` / `ml`), which wraps V1 with a model validated by `research/ml.py`. The model is loaded only
after SHA-256, feature-set and scikit-learn-version checks; the per-cycle config check fails closed without them.
Strategies only return signals: sizing, stops, reconciliation and order handling are identical in every mode.

## Strategy preservation
`tests/unit/test_risk_and_strategy.py` asserts `StrategyService` output equals V1 `strategy.analyze` exactly
(action, score, reasons) across 72 synthetic scenarios, and pins every strategy/risk constant.

## Documented behavior changes (correctness/safety)
1. Signals evaluate the last **closed** candle (`SIGNAL_ON_CLOSED_CANDLES=true`), matching the backtester.
   V1 evaluated the forming candle (repainting). `false` restores V1.
2. Daily-loss halt blocks **entries only**; V1 also skipped the stop-loss check while halted.
3. Stop-loss check uses the lows of candles that closed after the position opened (plus the forming
   candle), so a pre-entry wick cannot trigger an immediate stop.
4. PAUSED blocks entries but lets exits run; EMERGENCY blocks entries and strategy exits, not stop-loss.
   V1 skipped the whole cycle for both (spec §1.7 / §15).
5. New bot instances start **paused**; unknown instances are never auto-created.

## Pre-existing V1 defects fixed
* `backtester.py` crashed on every run (V1 renamed `RiskState` fields without updating it).
* `laravel/config/app.php` had `'providers' => []`, which replaces Laravel's default framework providers
  (config/app.php is not a merged key) — the Laravel app could not boot.
* `dashboard.py` referenced the removed `config.USE_TESTNET` (AttributeError).
* `tests/test_risk.py` asserted 0.05 where the unchanged V1 formula yields 5.0.
* `composer.json` `dev` script required Node.js (`npx concurrently`); removed.
* V1 BinanceExecutor treated any accepted REST response as filled.

Housekeeping (not a defect): `.gitignore` placeholders were added under `laravel/storage/` and
`bootstrap/cache/` so those required directories survive version control (git drops empty directories).

## Deprecated (kept for compatibility)
`paper_trader.py` (thin wrapper over one gated `TradingCycle`), `run_bot.py` (starts the trading worker),
`dashboard.py` (read-only Streamlit), `trades` table/model (V1 summaries), `README.original.md`, `V1_CHANGELOG.md`.
