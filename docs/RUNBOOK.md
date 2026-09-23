# Operations and incident runbook

## Daily checks (dashboard)
Control state · reconciliation `HEALTHY` · both worker heartbeats `alive` · WebSocket connected (TESTNET/LIVE) ·
open mismatches `0` · no CRITICAL events · last reconciliation recent (≤ `RECONCILE_INTERVAL_SECONDS`).

## Reconciliation states
| State | Entries | Exits | Meaning / action |
|---|---|---|---|
| HEALTHY | yes | yes | Exchange and ledger agree. |
| CONNECTING | no | no | Worker starting / reconnecting. Wait. |
| RECONCILING | no | no | Snapshot running, or an ambiguous order is inside its grace window. Wait. |
| DEGRADED | no | yes | WebSocket down, REST fine. Check network / `journalctl -u crypto-bot-reconciliation-worker`. |
| HALTED | no | no | Unresolved CRITICAL mismatch. **Investigate before acknowledging.** |

State older than `RECON_STATE_MAX_AGE_SECONDS` counts as untrusted (worker dead ⇒ trading blocked).

## Incidents
**Control plane down** — workers log `control plane unavailable - failing closed`; no orders are placed.
Fix Laravel/nginx/PHP-FPM; trading resumes on the next cycle. The outbox redelivers everything missed.

**HALTED — what each CRITICAL mismatch means**
* `ORDER_STATE_CONFLICT` — ledger and exchange disagree on a terminal order. Compare the order in the
  dashboard with Binance order history (search by client order ID).
* `UNKNOWN_BOT_ORDER` / `UNKNOWN_BOT_FILL` — an order with the bot's prefix exists on Binance but not in the
  ledger (e.g. ledger restored from an old backup, or two engines sharing one key). Stop the other engine.
* `BALANCE_BELOW_POSITION` — the account holds less base asset than the bot's position (manual sell,
  withdrawal, fee asset change). Reconcile the account manually.
* `FILLS_INCOMPLETE`, `EXECUTED_QTY_EXCEEDS_EXCHANGE`, `TERMINAL_ORDER_MISSING_ON_EXCHANGE`, `POSITION_ANOMALY`
  — ledger integrity problem. Keep halted; export `var/engine.sqlite3` for analysis.

Recovery: fix the root cause → dashboard (admin) **Acknowledge reconciliation halt** → engine re-runs
reconciliation → only a clean run returns `HEALTHY`. Acknowledging does not force anything.

**Emergency** — dashboard **Emergency stop** or Telegram `/emergency`. To flatten a position manually,
do it on Binance, then expect `BALANCE_BELOW_POSITION` → HALTED; acknowledge once the account is intended.

**Ambiguous order** (`ORDER_SUBMISSION_AMBIGUOUS` event) — normal on timeouts; resolved automatically by
query. Never re-submit by hand while it is pending.

**Worker crash / restart** — systemd restarts it. On restart the reconciliation worker re-verifies the
exchange before trading can resume; nothing is replayed blindly.

**Dead-lettered outbox messages** — `outbox` rows with `status='DEAD'` failed validation or exhausted
retries. The engine ledger is still authoritative; inspect `last_error`.

## PAPER → TESTNET
1. PAPER: run ≥ 2 weeks; confirm signals/orders/fills/positions in the dashboard match expectations.
2. Create Binance **Spot Testnet** keys (testnet.binance.vision) — TRADE + USER_DATA only.
3. Engine `.env`: `TRADING_MODE=TESTNET`, set `EXCHANGE_API_KEY/SECRET`, keep `PROTECTIVE_STOP_MODE=software`
   first, then switch to `exchange` and validate stop placement/cancellation.
4. Stop workers; move/delete `var/engine.sqlite3` (the PAPER ledger must not be reconciled against an exchange).
5. Start both workers; confirm `HEALTHY`, WebSocket connected, then resume from the dashboard.
6. Exercise: restart workers mid-order, pull the network, emergency stop, partial fills.

## Before LIVE (process checklist — do not skip)
- [ ] TESTNET ran ≥ 4 weeks with `PROTECTIVE_STOP_MODE=exchange`; zero unexplained mismatches.
- [ ] Laravel feature tests + `pytest` pass on the production server (`docs/TESTING.md`).
- [ ] Binance key: no withdrawal permission, IP-restricted to this server.
- [ ] TLS on the dashboard; `/api/internal/*` returns 404 publicly (`curl https://your-domain/api/internal/v1/bots/default/control`).
- [ ] Backups of `var/engine.sqlite3` and the Laravel DB; restore tested.
- [ ] Telegram alerts verified end to end.
- [ ] Smallest allowed position size; `MAX_OPEN_POSITIONS=1`.
- [ ] Then set `TRADING_MODE=LIVE`, `LIVE_TRADING_CONFIRMED=true`, `BINANCE_WS_API_URL` default (production).
LIVE is **not** enabled merely because this code exists.
