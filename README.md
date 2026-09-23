# Crypto Trading Bot — V1.1

Python trading engine + Laravel 13 control plane for Binance Spot. PAPER by default.

> **Status: PAPER-ready, TESTNET-ready for validation, not approved for LIVE.**
> LIVE is refused by code unless explicitly configured, and must not be enabled until the process
> checklist in `docs/RUNBOOK.md` is complete. Nothing here is financial advice.

## What it does
The unchanged V1 multi-factor strategy (EMA 20/50/200, RSI, MACD, BTC dominance, RSS sentiment) produces a
signal; a gated trading worker turns it into an order intent; `OrderService` owns the order lifecycle;
fills drive positions; a separate reconciliation worker keeps an authenticated Binance user-data WebSocket
and re-verifies account state over REST after every connect, restart or anomaly. Trading fails closed.

## Docs
* `docs/ARCHITECTURE.md` — components, where each concern lives, behavior changes, V1 defects fixed
* `docs/ORDER_LIFECYCLE.md` — order → fill → position, disconnect recovery, emergency stop, LIVE gate
* `docs/API_CONTRACT.md` — internal engine ↔ Laravel API
* `docs/RUNBOOK.md` — operations, incidents, PAPER → TESTNET, before-LIVE checklist
* `docs/TESTING.md` — test suites
* `deploy/README.md` — Ubuntu deployment (nginx, PHP-FPM, systemd)
* `research/README.md` — algorithm research pipeline (backtester, walk-forward, gates)

## Quick start — PAPER mode (local)
Requirements: Python 3.11+, PHP 8.3+, Composer. No Node.js.

One command creates both `.env` files with a shared token and prepares the database:
```bash
python scripts/setup_local.py      # then follow the commands it prints
```
Or do it by hand:

```bash
# 1. shared secret
TOKEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")

# 2. Laravel control plane
cd laravel
cp .env.example .env
sed -i "s|^TRADING_BOT_INTERNAL_TOKEN=.*|TRADING_BOT_INTERNAL_TOKEN=$TOKEN|; s|^APP_ENV=.*|APP_ENV=local|; s|^SESSION_SECURE_COOKIE=.*|SESSION_SECURE_COOKIE=false|; s|^DB_DATABASE=.*|DB_DATABASE=$PWD/database/database.sqlite|" .env
composer install
touch database/database.sqlite
php artisan key:generate
php artisan migrate
php artisan db:seed                                  # bot instance "default", starts PAUSED
php artisan bot:create-user you@example.com --role=admin
php artisan serve                                    # http://127.0.0.1:8000  (leave running)

# 3. Python engine (new terminal, project root)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
sed -i "s|^CONTROL_PLANE_TOKEN=.*|CONTROL_PLANE_TOKEN=$TOKEN|" .env   # TRADING_MODE=PAPER is the default
python -m pytest -q tests                            # all tests should pass
python -m workers.trading_worker                     # JSON logs; runs a cycle every LOOP_INTERVAL_SECONDS
```
4. Open http://127.0.0.1:8000, sign in, press **Resume**. Until then every entry is blocked (`bot paused`).
   PAPER mode needs no reconciliation worker and no exchange keys; it fetches public market data.

## Safety model (short)
* Laravel unreachable → no orders. Paused → no entries. Emergency → no entries or strategy exits.
* Reconciliation not `HEALTHY` (and fresh) → no entries. WebSocket down → `DEGRADED` → reconcile before resuming.
* Ambiguous submissions are resolved by querying the exchange with the deterministic clientOrderId, never retried blindly.
* Terminal orders never move backwards; duplicate exchange events never create a second fill or P&L.
* Binance keys: trade permission only, **never withdrawal**, IP-restricted. Secrets are redacted from logs.

## Tests
104 Python tests (unit + integration against a simulated Binance). Laravel feature tests are included and
must be run where Composer can install dependencies — see `docs/TESTING.md`.
