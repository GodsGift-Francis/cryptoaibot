"""Transactional outbox: every message to Laravel or Telegram is first written to
the engine store under a unique idempotency key (inside the same transaction as
the state change it describes), then delivered at-least-once. The idempotency
key doubles as the X-Request-Id, so Laravel deduplicates redeliveries.
"""
from __future__ import annotations

from datetime import timedelta

from engine.domain.models import Fill, Order, Position

CP_KINDS = {"signals", "orders", "fills", "reconciliation", "events", "positions"}


def order_payload(o: Order) -> dict:
    return {
        "client_order_id": o.client_order_id, "client_intent_id": o.client_intent_id,
        "exchange_order_id": o.exchange_order_id, "exchange": o.exchange, "symbol": o.symbol,
        "side": o.side, "type": o.order_type, "purpose": o.purpose.value, "status": o.status.value,
        "requested_quantity": str(o.requested_quantity), "executed_quantity": str(o.executed_quantity),
        "cumulative_quote_quantity": str(o.cumulative_quote_quantity),
        "average_fill_price": None if o.average_fill_price is None else str(o.average_fill_price),
        "limit_price": None if o.limit_price is None else str(o.limit_price),
        "stop_price": None if o.stop_price is None else str(o.stop_price),
        "submission_ambiguous": o.submission_ambiguous, "reject_reason": o.reject_reason,
        "submitted_at": o.submitted_at, "acknowledged_at": o.acknowledged_at,
        "last_exchange_update_at": o.last_exchange_update_at, "terminal_at": o.terminal_at,
        "version": o.version,
    }


def fill_payload(f: Fill) -> dict:
    return {
        "event_key": f.event_key, "client_order_id": f.client_order_id, "exchange_trade_id": f.exchange_trade_id,
        "symbol": f.symbol, "side": f.side, "quantity": str(f.quantity), "price": str(f.price),
        "quote_quantity": str(f.quote_quantity), "commission": str(f.commission),
        "commission_asset": f.commission_asset, "execution_time": f.execution_time,
    }


def position_payload(p: Position) -> dict:
    return {
        "symbol": p.symbol, "side": p.side, "quantity": str(p.quantity),
        "average_entry_price": str(p.average_entry_price), "realized_pnl": str(p.realized_pnl),
        "unrealized_pnl": str(p.unrealized_pnl),
        "last_mark_price": None if p.last_mark_price is None else str(p.last_mark_price),
        "stop_price": None if p.stop_price is None else str(p.stop_price),
        "opened_at": p.opened_at, "updated_at": p.updated_at,
    }


def enqueue_event(store, bot: str, event_type: str, message: str, severity: str = "INFO",
                  payload: dict | None = None, key: str | None = None, alert: bool = False) -> None:
    now = store.now()
    idem = key or f"event:{bot}:{event_type}:{now.isoformat()}"
    store.enqueue("events", idem, {"event_type": event_type, "severity": severity, "message": message,
                                   "payload": payload or {}, "occurred_at": now})
    if alert:
        store.enqueue("telegram", f"tg:{idem}", {"text": f"[{bot}] {severity} {event_type}: {message}"})


class OutboxDispatcher:
    """Delivers the outbox. Both workers run a dispatcher against the same ledger, so delivery is
    guarded by a lease: exactly one process delivers at a time (no duplicate Telegram alerts, no
    concurrent duplicate requests to Laravel); if it dies, the other takes over after LEASE_TTL."""

    LEASE_NAME = "outbox-dispatch"
    LEASE_TTL_SECONDS = 60

    def __init__(self, store, control_plane, notifier, max_attempts: int, logger, owner: str | None = None):
        import os
        import uuid
        self.store = store
        self.cp = control_plane
        self.notifier = notifier
        self.max_attempts = max_attempts
        self.log = logger
        self.owner = owner or f"{os.getpid()}:{uuid.uuid4().hex[:8]}"

    def flush(self, limit: int = 200) -> int:
        if not self.store.acquire_lease(self.LEASE_NAME, self.owner, self.LEASE_TTL_SECONDS):
            return 0                     # another worker is delivering
        sent = 0
        for row in self.store.pending_outbox(limit):
            # Renew before every delivery: a slow flush must never outlive the lease unnoticed.
            if not self.store.acquire_lease(self.LEASE_NAME, self.owner, self.LEASE_TTL_SECONDS):
                self.log.warning("outbox lease lost mid-flush; stopping", extra={"owner": self.owner})
                break
            import json
            payload = json.loads(row["payload_json"])
            try:
                if row["kind"] == "telegram":
                    if self.notifier is not None:
                        self.notifier.send(payload["text"])
                elif row["kind"] in CP_KINDS:
                    self.cp.post(row["kind"], payload, request_id=row["idempotency_key"])
                else:
                    raise ValueError(f"unknown outbox kind {row['kind']}")
                self.store.mark_outbox_sent(row["id"])
                sent += 1
            except Exception as exc:  # noqa: BLE001 - delivery retried later
                from engine.control_plane.client import ControlPlaneRejected
                if isinstance(exc, ControlPlaneRejected):
                    # Poison message: dead-letter it so it cannot block ordered delivery forever.
                    self.store.mark_outbox_failed(row["id"], str(exc), self.store.now(), dead=True)
                    self.log.error("outbox message permanently rejected; dead-lettered",
                                   extra={"kind": row["kind"], "idempotency_key": row["idempotency_key"], "error": str(exc)[:300]})
                    continue
                attempts = row["attempts"] + 1
                delay = min(600, 2 ** min(attempts, 9))
                self.store.mark_outbox_failed(row["id"], str(exc), self.store.now() + timedelta(seconds=delay),
                                              dead=attempts >= self.max_attempts)
                self.log.warning("outbox delivery failed", extra={"kind": row["kind"], "attempts": attempts,
                                                                  "idempotency_key": row["idempotency_key"]})
                if row["kind"] in CP_KINDS:
                    break   # preserve ordering towards the control plane; retry next flush
        return sent
