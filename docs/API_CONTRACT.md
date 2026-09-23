# Internal API contract — `/api/internal/v1/bots/{instance}/…`

Engine → Laravel only. Not exposed publicly (nginx returns 404 on the public server; served on 127.0.0.1:8080).

## Required headers (every request)
| Header | Rule |
|---|---|
| `Authorization: Bearer <token>` | equals `TRADING_BOT_INTERNAL_TOKEN` (≥ 32 chars), constant-time compare → else 401 |
| `X-Bot-Instance` | equals `{instance}` → else 403; instance must exist (never auto-created) → else 404 |
| `X-Request-Timestamp` | unix seconds within ±`INTERNAL_API_MAX_SKEW_SECONDS` (300) → else 401 (stale) |
| `X-Request-Id` | `^[A-Za-z0-9:/._#+-]{8,191}$` → else 400. POST replay returns the stored response with `X-Idempotent-Replay: true`; same id on another endpoint → 409 |

Body (POST): JSON including `"bot_instance": "<instance>"`. Validation failures → 422 (engine dead-letters,
never retries). Rate limit: 600/min per instance.

## Endpoints
| Method | Path | Purpose | Idempotency |
|---|---|---|---|
| GET | `/control` | `{enabled, emergency_stop, mode, symbol, timeframe, reconciliation_ack_token, server_time}` — engine fails closed on any error | read |
| POST | `/heartbeat` | health metrics (worker, reconciliation state, ws, last market data/cycle, non-terminal orders, mismatches, outbox backlog) | last-write-wins (not ledgered) |
| POST | `/signals` | strategy output per closed candle | `client_signal_id` UNIQUE |
| POST | `/orders` | order mirror | ignored unless `version` > stored `engine_version` |
| POST | `/fills` | executions | `event_key` UNIQUE (insert-or-ignore) |
| POST | `/positions` | position snapshot | ignored if older than stored `engine_updated_at` |
| POST | `/reconciliation` | run + mismatches; updates bot reconciliation state | `run_key` UNIQUE |
| POST | `/events` | lifecycle / alert events | `X-Request-Id` → `bot_events.idempotency_key` UNIQUE |

Field-level rules: `laravel/app/Http/Requests/Internal/*.php`. The Python test
`tests/integration/test_laravel_contract.py` checks real engine payloads against those files.
