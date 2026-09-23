# How the system trades, recovers and stops

## How an order moves through the system
1. **Signal** — `TradingCycle` asks `MultiFactorStrategy` (the unchanged V1 `strategy.analyze`) for BUY/SELL/HOLD
   on the last *closed* candle. The strategy never sizes or submits.
2. **Gates** — in order: control plane reachable → config valid → reconciliation state → risk limits →
   market data valid → no order already in flight. Any failure = no new order.
3. **Intent** — a deterministic `client_intent_id` is built from bot, symbol, candle (or position open time
   for exits) and purpose, plus an attempt counter that only advances after a zero-fill terminal failure.
   Same decision → same intent → never a second order.
4. **Persist before network** — `OrderService.submit_intent` inserts the intent and an `orders` row in state
   `SUBMITTING` in one transaction, with `clientOrderId = <prefix>-<sha256(intent)>` (≤36 chars).
5. **Submit** — the executor sends it once (Binance: `POST /api/v3/order`, `newOrderRespType=FULL`).
6. **Outcome**
   * *Acknowledged*: the response's status and fills are applied through the same monotonic path used for
     WebSocket and REST data. A response is **never** assumed to mean FILLED.
   * *Rejected* (definitive exchange refusal, e.g. -2010): order → `REJECTED`.
   * *Ambiguous* (timeout, network, 5xx, -1007): order stays `SUBMITTING`, flagged, and the exchange is
     **queried by clientOrderId** before anything else happens. Found → adopt exchange state. Not found →
     stays pending (no trading) until `AMBIGUOUS_ORDER_GRACE_SECONDS` has passed, then `REJECTED
     (NOT_FOUND_ON_EXCHANGE)`. If the exchange later reports that order anyway, it is a CRITICAL conflict.
7. **Lifecycle** — `SUBMITTING → NEW → PARTIALLY_FILLED → FILLED`, or `→ CANCELED / EXPIRED / REJECTED`.
   Terminal states never move backwards; executed quantity never decreases; stale/out-of-order reports are
   ignored; contradictory terminal reports raise `ORDER_STATE_CONFLICT` and halt trading.

## How a fill becomes a position
* Every execution (REST `FULL` response, `myTrades`, or WebSocket `executionReport` with `x=TRADE`) is
  normalized to one idempotency key: `binance:{account}:{symbol}:{orderId}:{tradeId}:TRADE`.
* `OrderService.ingest_fill` inserts the fill (UNIQUE `event_key`) **and** calls
  `PositionService.apply_fill` in the same transaction. A duplicate event inserts nothing and changes nothing.
* `PositionService` (the only code that mutates positions): BUY adds quantity net of base-asset commission
  and raises average cost by quote-asset commission; SELL realizes `proceeds − avg_entry × delivered`.
  Commission in other assets (e.g. BNB) is tracked separately, not converted.
* A position is a pure function of its fills; reconciliation rebuilds it from scratch and must agree.
* Laravel receives orders, fills and positions through the engine outbox (at-least-once, deduplicated by
  `X-Request-Id`, orders mirrored monotonically by engine version).

## How a disconnect is recovered
1. WebSocket closes, a `ping` goes unanswered, or `serverShutdown`/`eventStreamTerminated` arrives →
   reconciliation state `DEGRADED` (entries blocked; exits allowed because REST is still trusted).
2. Reconnect with exponential backoff + jitter → `CONNECTING` → re-sign `userDataStream.subscribe.signature`.
3. After the subscription is acknowledged (so no new event can be missed) → `RECONCILING` → full REST
   reconciliation: open orders, every local non-terminal order, recently terminal orders, `myTrades`
   since the last success (paginated), balances, position rebuild, balance sanity check.
4. Clean → `HEALTHY` (entries allowed again). Ambiguous order still in grace → stays `RECONCILING`.
   Unresolved CRITICAL mismatch → `HALTED` + Telegram/Laravel alert.
5. A restart is the same path: persisted state is ignored once older than `RECON_STATE_MAX_AGE_SECONDS`,
   so the trading worker blocks until the reconciliation worker has re-verified the exchange.

## How an emergency stop works
* Dashboard (operator/admin) or Telegram `/emergency` → `BotControlService` sets `emergency_stop=true`,
  `enabled=false`, writes an audit log and a bot event.
* The trading worker reads control state at the start of **every** cycle: no entries, no strategy exits.
  Stop-loss exits and resting exchange-side stops remain active (they only reduce risk).
* `/resume` is refused while the emergency stop is engaged. Only an **admin** can clear it (dashboard),
  and clearing leaves the bot **paused**; resuming is a separate deliberate action.
* If Laravel is unreachable the worker places **no orders at all** (fail closed).

## How to switch PAPER → TESTNET
See `docs/RUNBOOK.md` → "PAPER → TESTNET".

## What must be true before LIVE is allowed
Enforced by code (`config.validate_runtime`): `TRADING_MODE=LIVE`, `LIVE_TRADING_CONFIRMED=true`,
`PROTECTIVE_STOP_MODE=exchange`, API credentials present, production WS URL only in LIVE, strong
control-plane token. Required by process (not enforceable in code) — see `docs/RUNBOOK.md` → "Before LIVE".
