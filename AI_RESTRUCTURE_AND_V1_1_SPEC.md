# Crypto Trading Bot — AI Coding Agent Restructure + V1.1 Execution/Reconciliation Specification

## 0. Mission

You are the coding agent responsible for taking the existing `crypto-bot` repository and restructuring it into a maintainable production architecture while preserving the existing trading strategy behavior unless a change is explicitly required for correctness or safety.

The target system is:

- Python trading engine for market analysis and execution.
- Laravel 13 control plane/API/dashboard.
- Binance Spot as the initial exchange.
- Telegram as an operator notification/control channel.
- PAPER -> TESTNET -> LIVE deployment progression.
- No Node.js runtime requirement for production hosting.

This is an engineering task, not a request to invent a new trading strategy.

## 1. Non-negotiable safety rules

1. Never place real-money orders during development or tests.
2. Default trading mode must remain `PAPER`.
3. `LIVE` mode must require an explicit confirmation flag.
4. API credentials must never be committed to source control, logs, exceptions, screenshots, or generated documentation.
5. Binance API keys must be configured without withdrawal permission. IP restrictions should be supported/recommended.
6. If Laravel control-plane state cannot be reached, the trading worker must fail closed and must not trade.
7. Emergency stop must block new entries immediately.
8. The system must distinguish order intent, submitted order, exchange acknowledgement, fills, position state, and realized P&L.
9. Do not assume a REST order response means the order is fully filled.
10. Partial fills, rejected orders, cancelled orders, expired orders, duplicate WebSocket events, disconnects, and process restarts must be handled deterministically.
11. A WebSocket disconnect must put execution into a degraded/reconciliation state rather than silently continuing as if account state were known.
12. Native exchange-side protective orders should be introduced before any serious live-capital deployment; software-only candle-low stop logic is not sufficient as a production safety mechanism.

## 2. Current repository problems to fix

The existing V1 foundation works as a transitional architecture, but it should be reorganized.

Known issues to resolve:

- `paper_trader.py` currently contains too much orchestration logic.
- Execution adapters exist but order lifecycle/reconciliation is incomplete.
- Binance execution is REST/CCXT based but does not yet reconcile order state from a user data stream.
- Full exchange position/balance reconciliation is missing.
- Dashboard authentication is not implemented.
- `BotEvent` exists but event persistence is not consistently used.
- `GLOBAL_TRADING_ENABLED` is not fully enforced in the worker path.
- `MAX_OPEN_POSITIONS` exists but the position model is effectively single-position.
- Streamlit dashboard is retained for transition but is not fully aligned with the new config model.
- Laravel internal API should have stronger request validation, rate limiting, and authorization boundaries.
- Trade records need explicit exchange order identifiers and execution/fill information.
- Order state and trade/fill state need separate concepts.
- The system needs idempotency so the same exchange event cannot create duplicate fills or P&L updates.
- Restart recovery must query exchange state before the worker is allowed to resume trading.

## 3. Target architecture

Use this high-level separation:

```text
crypto-bot/
├── engine/
│   ├── application/
│   │   ├── trading_cycle.py
│   │   ├── order_service.py
│   │   ├── position_service.py
│   │   └── reconciliation_service.py
│   ├── domain/
│   │   ├── models.py
│   │   ├── enums.py
│   │   └── events.py
│   ├── execution/
│   │   ├── base.py
│   │   ├── factory.py
│   │   ├── paper.py
│   │   └── binance.py
│   ├── exchanges/
│   │   └── binance/
│   │       ├── rest_client.py
│   │       ├── websocket_client.py
│   │       ├── signer.py
│   │       └── normalizer.py
│   ├── risk/
│   │   ├── manager.py
│   │   └── rules.py
│   ├── strategy/
│   │   └── strategy_service.py
│   ├── market_data/
│   │   ├── fetcher.py
│   │   └── indicators.py
│   ├── control_plane/
│   │   └── client.py
│   ├── notifications/
│   │   └── telegram.py
│   └── infrastructure/
│       ├── logging.py
│       └── clock.py
├── workers/
│   ├── trading_worker.py
│   └── reconciliation_worker.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── laravel/
│   ├── app/
│   │   ├── Actions/
│   │   ├── Http/
│   │   ├── Models/
│   │   ├── Services/
│   │   └── Console/
│   ├── database/
│   ├── resources/
│   └── routes/
└── deploy/
```

Do not blindly create every directory if it has no implementation. The important requirement is clear separation of responsibilities.

## 4. Preserve the strategy

The existing strategy uses EMA20/50/200, RSI, MACD, BTC dominance, and RSS sentiment inputs.

Do not silently change strategy thresholds, indicators, entry conditions, exit conditions, or risk percentages while restructuring.

If strategy code needs refactoring, move it behind a stable interface such as:

```python
class Strategy:
    def evaluate(self, market_context) -> Signal:
        ...
```

The strategy should produce an intent/signal. It should not directly submit exchange orders.

## 5. Domain model

Create explicit domain concepts.

### OrderIntent
Represents what the strategy wants to do.

Required fields:

- client_intent_id
- bot_instance_id
- symbol
- side
- order_type
- quantity
- limit_price if applicable
- stop_price if applicable
- strategy_name
- strategy_version
- signal_id
- created_at

### Order
Represents an order submitted to an exchange.

Required fields:

- internal id
- bot instance id
- exchange
- symbol
- side
- type
- client_order_id
- exchange_order_id
- requested_quantity
- executed_quantity
- average_fill_price
- cumulative_quote_quantity
- status
- submitted_at
- acknowledged_at
- last_exchange_update_at
- terminal_at
- raw exchange metadata where safe

### Fill
Represents an actual execution.

Required fields:

- internal id
- order id
- exchange trade id if available
- symbol
- side
- quantity
- price
- quote_quantity
- commission
- commission_asset
- execution_time
- unique event key

### Position
Represents the bot's known position state.

Required fields:

- bot instance id
- symbol
- side
- quantity
- average_entry_price
- realized_pnl
- unrealized_pnl
- last_mark_price
- opened_at
- updated_at

### ReconciliationState
Tracks whether the worker has trustworthy exchange state.

States should include at least:

- HEALTHY
- CONNECTING
- DEGRADED
- RECONCILING
- HALTED

The worker must not open new positions unless reconciliation state is healthy.

## 6. Binance execution layer

Implement Binance as an exchange adapter, not as logic scattered throughout the strategy.

Use CCXT where it is useful for normalized REST market/account/order operations, but isolate exchange-specific behavior under `engine/exchanges/binance/`.

The adapter must support:

- create order
- query order
- cancel order
- query open orders
- query balances
- query trades/fills
- query recent order history as needed for recovery
- exchange time/synchronization where required

Every submitted order must use a deterministic client order ID generated by the application. Store the ID before or atomically with the submission workflow so a restart can reconcile the intent.

## 7. Binance WebSocket user-data reconciliation

Implement a dedicated Binance WebSocket client.

Current Binance Spot documentation provides authenticated User Data Stream subscriptions through the Spot WebSocket API, including `userDataStream.subscribe` and signed `userDataStream.subscribe.signature`. The API also exposes order status/history methods for reconciliation. Do not hard-code an obsolete legacy listen-key-only implementation without checking the current Binance documentation. citeturn1search1turn1search0

Requirements:

1. WebSocket connection is independent from strategy evaluation.
2. Reconnect automatically with exponential backoff and jitter.
3. Detect stale connections with a heartbeat/receive timeout.
4. Parse exchange events into internal normalized events.
5. Persist every meaningful order/fill event with an idempotency key.
6. Update order state monotonically; an older event must not overwrite a newer terminal state.
7. Handle duplicate events safely.
8. On reconnect, enter `RECONCILING` before resuming trading.
9. Re-query exchange state for all non-terminal local orders.
10. Reconcile open orders, recent fills, balances, and tracked positions.
11. If local state and exchange state disagree materially, halt new trading and raise an operator alert.
12. Only return to `HEALTHY` after reconciliation succeeds.

Do not assume a single WebSocket event is sufficient to establish the complete account state. Use REST/API queries as the recovery source of truth after disconnects and process restarts.

## 8. Order state machine

Implement an explicit state machine.

Suggested states:

```text
INTENT_CREATED
  -> SUBMITTING
  -> NEW
  -> PARTIALLY_FILLED
  -> FILLED

SUBMITTING -> REJECTED
NEW -> CANCELED
NEW -> EXPIRED
PARTIALLY_FILLED -> CANCELED
PARTIALLY_FILLED -> EXPIRED
```

Terminal states:

- FILLED
- CANCELED
- REJECTED
- EXPIRED

Rules:

- Never move a terminal order back to a non-terminal state.
- Never create a second internal order because a retry occurs.
- If submission result is ambiguous, query the exchange using clientOrderId/order identity before retrying.
- A timeout is not automatically a rejection.

## 9. Idempotency

Create an idempotency strategy for:

- order submission
- WebSocket event ingestion
- fill insertion
- trade/P&L calculation
- Telegram alerts
- Laravel internal API requests

Database uniqueness should enforce idempotency where possible.

Example event key:

```text
binance:{account}:{symbol}:{order_id}:{execution_id}:{event_type}
```

Do not rely only on in-memory deduplication.

## 10. Reconciliation algorithm

Implement this sequence on startup and after WebSocket recovery:

```text
1. Mark worker RECONCILING.
2. Load local non-terminal orders.
3. Query exchange open orders.
4. Query exchange status for locally tracked non-terminal orders.
5. Query recent exchange fills/trades.
6. Query account balances.
7. Rebuild position state from authoritative fills + balances.
8. Compare exchange state with local state.
9. Persist corrections and reconciliation event.
10. If mismatch is unresolved or unsafe, mark HALTED and alert operator.
11. Otherwise mark HEALTHY.
12. Permit strategy-generated entries only after HEALTHY.
```

This must be safe to run repeatedly.

## 11. Stop-loss architecture

The existing bot uses software-triggered candle-low stop logic.

Keep that logic for PAPER/TESTNET while the system is being validated, but refactor risk exits into an execution-aware component.

Before live-capital deployment, implement exchange-side protective order support appropriate to the selected Binance Spot order capabilities. The local software stop should remain a secondary control, not the sole protection.

## 12. Laravel control plane

Laravel remains responsible for:

- operator dashboard
- bot instances
- enable/pause/emergency-stop state
- signals
- orders/trades/fills visibility
- bot events
- health/reconciliation state
- Telegram webhook
- audit trail

Add authentication to the dashboard before public exposure. Laravel provides session-based authentication facilities; use the framework's supported authentication approach rather than inventing custom password handling. citeturn0search4

Add:

- users table
- authenticated dashboard routes
- authorization policy for trading controls
- CSRF protection for browser actions
- rate limiting for internal APIs and Telegram webhook where appropriate
- audit logging for pause/resume/emergency-stop actions

The internal Python API must remain separate from public browser routes.

## 13. Laravel database changes

Add tables/models for:

- orders
- fills
- positions
- reconciliation_runs
- reconciliation_mismatches
- audit_logs

Keep `trades` for strategy-level completed trade summaries if useful, but do not use `trades` as a replacement for raw orders/fills.

Suggested relationships:

```text
BotInstance
  hasMany Signals
  hasMany Orders
  hasMany Positions
  hasMany ReconciliationRuns

Order
  belongsTo BotInstance
  belongsTo Signal (nullable)
  hasMany Fills

Position
  belongsTo BotInstance

ReconciliationRun
  belongsTo BotInstance
  hasMany ReconciliationMismatches
```

## 14. API contract

Version the internal API under `/api/internal/v1/`.

Required endpoints:

```text
GET  /api/internal/v1/bots/{instance}/control
POST /api/internal/v1/bots/{instance}/heartbeat
POST /api/internal/v1/bots/{instance}/signals
POST /api/internal/v1/bots/{instance}/orders
POST /api/internal/v1/bots/{instance}/fills
POST /api/internal/v1/bots/{instance}/reconciliation
POST /api/internal/v1/bots/{instance}/events
```

Use request DTOs/Form Requests and validate every payload.

Every request must include:

- bot instance identity
- timestamp
- request/correlation ID
- authentication token

Reject stale requests where appropriate.

## 15. Control-plane fail-closed behavior

Worker decision order must be:

```text
control state
  -> authentication/config validation
  -> reconciliation state
  -> risk limits
  -> market data validity
  -> strategy signal
  -> order intent
  -> execution
```

If any mandatory prerequisite fails, do not submit a new order.

## 16. Logging/observability

Use structured logs.

Every important operation should carry:

- bot_instance_id
- correlation_id
- client_order_id
- exchange_order_id when known
- symbol
- mode
- event type
- timestamp

Never log API secrets.

Persist important lifecycle events to Laravel `bot_events` and/or an equivalent event/audit store.

Add health metrics for:

- last market-data update
- last strategy cycle
- last heartbeat
- WebSocket connected/disconnected
- last WebSocket event
- last successful reconciliation
- number of non-terminal orders
- number of reconciliation mismatches
- trading mode
- control state

## 17. Worker design

Replace the current monolithic loop with explicit workers/services.

Suggested processes:

1. `trading-worker`
   - evaluates strategy on schedule
   - creates intents
   - asks risk manager for approval
   - submits orders through execution service

2. `reconciliation-worker`
   - maintains Binance user-data WebSocket
   - normalizes order/fill events
   - performs recovery reconciliation

These may run as separate systemd services.

Do not make the strategy worker responsible for maintaining a long-lived WebSocket connection.

## 18. Testing requirements

Create deterministic tests for:

### Unit

- order state transitions
- duplicate event handling
- partial fills
- terminal state protection
- position averaging
- realized P&L
- fee handling
- risk limits
- emergency stop
- control-plane failure
- stale heartbeat
- reconciliation mismatch
- retry after ambiguous order submission

### Integration

Use mocked Binance responses/WebSocket messages.

Test scenarios:

1. BUY fully fills.
2. BUY partially fills then completes.
3. BUY partially fills then cancels.
4. Order is rejected.
5. WebSocket disconnects immediately after order submission.
6. Worker restarts with an open exchange order.
7. Duplicate fill event arrives.
8. Out-of-order events arrive.
9. Local state says open but exchange says filled.
10. Local state says filled but exchange query disagrees.
11. Control plane pauses bot while an order is open.
12. Emergency stop occurs.
13. API/network timeout occurs after order submission.
14. Reconciliation succeeds and trading resumes.
15. Reconciliation fails and trading remains halted.

## 19. Deployment structure

Production should look approximately like:

```text
Nginx
  -> Laravel public/

PHP-FPM
  -> Laravel control plane

systemd
  -> crypto-bot-trading-worker
  -> crypto-bot-reconciliation-worker

MySQL/PostgreSQL
  -> Laravel persistent state

Redis (optional but recommended when queue/cache scale requires it)
  -> queue/cache

Telegram
  -> Laravel webhook

Binance
  -> REST + authenticated WebSocket
```

Do not add Node.js solely for dashboard operation.

## 20. Configuration cleanup

Use one canonical configuration model.

Remove deprecated/duplicated settings such as `USE_TESTNET` once all consumers are migrated.

Canonical mode:

```env
TRADING_MODE=PAPER
```

Allowed values:

- PAPER
- TESTNET
- LIVE

LIVE gate:

```env
LIVE_TRADING_CONFIRMED=false
```

The code must refuse LIVE unless this is explicitly true.

## 21. Backward compatibility

Do not delete useful original components merely because they are old.

During restructuring:

- preserve the original strategy behavior;
- preserve backtesting;
- preserve useful market-data functionality;
- migrate Streamlit only if necessary;
- clearly mark deprecated modules;
- remove dead code only after references are migrated and tests pass.

## 22. Documentation requirements

Update:

- `README.md`
- deployment documentation
- `.env.example`
- architecture documentation
- API contract documentation
- operational runbook
- incident/recovery runbook
- test instructions

Add a document explaining:

```text
How an order moves through the system
How a fill becomes a position
How a disconnect is recovered
How an emergency stop works
How to switch PAPER -> TESTNET
What must be true before LIVE is allowed
```

## 23. Definition of done

Do not declare the restructure complete until all of the following are true:

- Python syntax checks pass.
- Laravel PHP syntax checks pass.
- Laravel dependencies install successfully on PHP 8.3+.
- Database migrations run successfully on a clean database.
- Dashboard authentication works.
- Internal API authentication works.
- Paper mode still works end-to-end.
- Strategy output is unchanged unless a documented bug is fixed.
- Order lifecycle is modeled explicitly.
- Fills are modeled separately from orders.
- WebSocket events are normalized and persisted idempotently.
- Startup/reconnect reconciliation works against mocks.
- Duplicate/out-of-order events do not corrupt state.
- Ambiguous order submission is reconciled before retry.
- Emergency stop prevents new entries.
- Control-plane failure fails closed.
- Tests cover partial fills, cancellations, rejection, disconnects, and restart recovery.
- No secrets are present in repository files.
- Deployment documentation matches the actual project.

## 24. Required implementation workflow for the coding AI

Work in phases and do not make a giant unreviewable rewrite.

### Phase A — inventory

1. Inspect every existing Python and Laravel file.
2. Build a dependency map.
3. Identify duplicated responsibilities.
4. Identify all configuration variables and consumers.
5. Identify every place an order can be submitted.
6. Identify every place position/P&L is calculated.

### Phase B — domain model

Implement Order, Fill, Position, ReconciliationState and state transitions first.

### Phase C — execution boundary

Move exchange-specific code behind interfaces.

### Phase D — persistence

Add Laravel migrations/models/API contracts.

### Phase E — WebSocket

Implement authenticated Binance user-data stream, event normalization, idempotency, reconnect, and reconciliation.

### Phase F — worker split

Separate strategy/trading worker from reconciliation worker.

### Phase G — dashboard/security

Add authentication, authorization, audit logs, health indicators, and reconciliation status.

### Phase H — tests

Run unit and integration tests with mocked exchange events.

### Phase I — deployment

Update systemd/Nginx/env examples/runbooks.

### Phase J — final audit

Search the entire repository for:

- secrets
- deprecated `USE_TESTNET`
- direct exchange calls outside the exchange adapter
- order submissions outside `OrderService`/execution layer
- position mutations outside `PositionService`
- unvalidated internal API routes
- unauthenticated dashboard controls
- code paths that can trade when reconciliation is unhealthy

## 25. Output expected from the coding AI

At the end, provide:

1. A concise architecture summary.
2. A file-by-file change summary.
3. Database schema summary.
4. API endpoint summary.
5. WebSocket/reconciliation design summary.
6. Test results.
7. Deployment changes.
8. Any unresolved issues.
9. Exact commands to install, migrate, test, and run PAPER mode.
10. A clear statement that LIVE trading has not been enabled merely because the implementation exists.

## 26. Important implementation principle

Do not optimize for the fewest files. Optimize for correctness, observability, testability, and safe failure.

Do not replace working code with abstractions that have no tests.

Do not introduce Node.js merely because a frontend package makes it convenient.

Do not rewrite the strategy while claiming to be doing infrastructure work.

Do not declare exchange state known merely because an order submission API returned successfully.

The final architecture must make it obvious where strategy, risk, order execution, exchange communication, reconciliation, persistence, control-plane decisions, and operator controls live.
