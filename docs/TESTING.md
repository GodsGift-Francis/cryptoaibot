# Tests

## Python (engine) — deterministic, no network, no exchange, no money
```bash
pip install -r requirements-dev.txt
python -m pytest -q tests
```
* `tests/unit` — state machine, fees/averaging/P&L, normalizer + Binance signature test vector,
  config LIVE gate, secret redaction, risk rules, strategy-output equivalence with V1, backtester.
* `tests/integration/test_paper_cycle.py` — PAPER end to end, fail-closed, pause, emergency stop,
  daily-loss, restart recovery, outbox.
* `tests/integration/test_binance_lifecycle.py` — the 15 spec scenarios against a simulated Binance that
  speaks raw Binance JSON and raises real CCXT exception types, plus protective stops and foreign orders.
* `tests/integration/test_websocket_and_worker.py` — signed subscription, stale-ping reconnect,
  `serverShutdown`, subscription failure, backoff bounds, worker state machine across a disconnect.
* `tests/integration/test_laravel_contract.py` — engine payloads vs the Laravel Form Request rules.

## Laravel (control plane) — run on a machine with Composer access
```bash
cd laravel && composer install && php artisan test
```
`tests/Feature/DashboardAuthTest.php` (auth, roles, audit, emergency semantics) and
`tests/Feature/InternalApiTest.php` (token, stale timestamp, instance binding, validation, replay,
fill idempotency, monotonic order mirror).

## Before TESTNET/LIVE
Also run a manual TESTNET drill: restart workers with an open order, disconnect the network, emergency stop.
