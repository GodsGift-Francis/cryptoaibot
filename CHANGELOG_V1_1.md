# V1.1 — execution lifecycle, reconciliation, secure control plane

Engine
- Explicit domain: OrderIntent, Order (monotonic state machine), Fill, Position, ReconciliationState.
- SQLite engine ledger with UNIQUE-key idempotency (intents, orders, fills, events, outbox).
- OrderService: persist-before-submit, deterministic clientOrderId, ambiguous-submission query, no blind retry.
- PositionService: sole position/P&L mutator; base/quote/other-asset fee handling; rebuild from fills.
- Binance adapter isolated under engine/exchanges/binance (CCXT raw REST, WS API user data stream via
  userDataStream.subscribe.signature, HMAC signer verified against Binance's test vector, normalizer).
- ReconciliationService (spec §10) + StreamIngestionService; sticky HALTED requiring operator acknowledgement.
- Trading and reconciliation workers split; fail-closed gating; exchange-side STOP_LOSS_LIMIT protective stops.
- Structured JSON logging with secret redaction; transactional outbox to Laravel and Telegram.
- Canonical config: TRADING_MODE only (USE_TESTNET removed); LIVE requires LIVE_TRADING_CONFIRMED=true and
  PROTECTIVE_STOP_MODE=exchange.

Laravel
- Versioned, validated internal API (/api/internal/v1) with token, instance binding, timestamp freshness,
  X-Request-Id replay protection and rate limiting.
- Session authentication, admin/operator/viewer gates, CSRF, audit log for every control action.
- Tables: users, sessions, orders, fills, positions, reconciliation_runs, reconciliation_mismatches,
  audit_logs, internal_api_requests; health fields on bot_instances.
- Dashboard: health, reconciliation, orders, fills, positions, mismatches, events, audit.

Fixed pre-existing V1 defects and documented behavior changes: see docs/ARCHITECTURE.md.
